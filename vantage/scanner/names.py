"""Hostname resolution: mDNS, SSDP/UPnP and NetBIOS.

All three are plain UDP from an ephemeral port — no admin, no driver, no extra
dependency. We already know the IP addresses; what we want is a name for each
one, so each probe here is deliberately address-first:

- **mDNS**: a reverse PTR query for `4.3.2.1.in-addr.arpa` on 224.0.0.251:5353.
  Service browsing (what `zeroconf` is built for) would find services and leave
  us to map them back to addresses; the reverse query answers the actual
  question in one packet.
- **SSDP**: one M-SEARCH to the multicast group, then fetch each responder's
  device description for its `friendlyName` — this is where "Living Room TV"
  comes from.
- **NetBIOS**: a node-status query to udp/137, which still answers on Windows
  machines and most NAS boxes.

Every function is best-effort and returns None rather than raising: a device
that stays anonymous is a normal outcome, not an error.
"""

from __future__ import annotations

import re
import socket
import struct
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900
NETBIOS_PORT = 137


# ---------- shared DNS wire helpers ----------


def _encode_labels(name: str) -> bytes:
    out = b""
    for label in name.split("."):
        if label:
            out += bytes([len(label)]) + label.encode("ascii")
    return out + b"\x00"


def _read_name(data: bytes, offset: int, depth: int = 0) -> tuple[str, int]:
    """Decode a DNS name, following compression pointers."""
    labels: list[str] = []
    while offset < len(data) and depth < 10:
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:  # pointer: name continues elsewhere
            pointer = struct.unpack_from("!H", data, offset)[0] & 0x3FFF
            suffix, _ = _read_name(data, pointer, depth + 1)
            labels.append(suffix)
            offset += 2
            return ".".join(l for l in labels if l), offset
        offset += 1
        labels.append(data[offset : offset + length].decode("utf-8", "replace"))
        offset += length
    return ".".join(l for l in labels if l), offset


def _clean(name: str | None) -> str | None:
    """Trim the noise mDNS/NetBIOS append to an otherwise good name."""
    if not name:
        return None
    # NetBIOS pads with NULs and mDNS can hand back control characters.
    name = "".join(c for c in name if c.isprintable()).strip().strip(".")
    for suffix in (".local", ".lan", ".home", ".home.arpa"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    name = name.strip()
    if not name or len(name) > 63:
        return None
    # A name that is just the address tells us nothing we did not have.
    if re.fullmatch(r"[\d.]+", name):
        return None
    return name


# ---------- mDNS ----------


def _reverse_arpa(ip: str) -> str:
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


# Service types worth asking about: between them they cover phones, laptops,
# TVs, speakers, printers and most smart-home bridges.
_MDNS_QUERIES = (
    "_services._dns-sd._udp.local",
    "_device-info._tcp.local",
    "_airplay._tcp.local",
    "_raop._tcp.local",
    "_googlecast._tcp.local",
    "_spotify-connect._tcp.local",
    "_companion-link._tcp.local",
    "_homekit._tcp.local",
    "_ipp._tcp.local",
    "_printer._tcp.local",
    "_smb._tcp.local",
    "_http._tcp.local",
    "_workstation._tcp.local",
)

# Chromecast-style identifiers: technically a hostname, useless as a label.
_OPAQUE_RE = re.compile(
    r"^(?:[0-9a-f]{12,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-?[0-9a-f]*)$",
    re.I,
)


def mdns_batch(ips: list[str], timeout: float = 4.0) -> dict[str, str]:
    """Harvest mDNS names for a whole subnet in one listening round.

    Reverse PTR (`4.3.2.1.in-addr.arpa`) is the textbook lookup but modern iOS
    and macOS stopped answering it, so it finds almost nothing in practice. What
    does work is asking about service types and reading the **A records** every
    responder attaches — that is a hostname bound to an address, which is
    exactly the question. SRV and TXT records then upgrade opaque hostnames
    (`a75950a6904cd69a.local`) to the name the owner actually chose.

    One socket bound to 5353 and joined to the group, because responders answer
    by multicast. Binding the standard port alongside the OS resolver needs
    SO_REUSEADDR; if the OS refuses, we simply find no mDNS names.
    """
    found: dict[str, str] = {}
    if not ips:
        return found

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", MDNS_PORT))
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                socket.inet_aton(MDNS_GROUP) + socket.inet_aton("0.0.0.0"),
            )
        except OSError:
            return found
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.4)

        for query in _MDNS_QUERIES:
            packet = (
                struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
                + _encode_labels(query)
                + struct.pack("!HH", 12, 1)  # PTR, IN
            )
            try:
                sock.sendto(packet, (MDNS_GROUP, MDNS_PORT))
            except OSError:
                pass
        # Reverse queries too — cheap, and some Linux/NAS responders still honour them.
        for ip in ips:
            packet = (
                struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
                + _encode_labels(_reverse_arpa(ip))
                + struct.pack("!HH", 12, 1 | 0x8000)  # PTR, IN + unicast-response
            )
            try:
                sock.sendto(packet, (MDNS_GROUP, MDNS_PORT))
            except OSError:
                pass

        wanted = set(ips)
        reverse = {_reverse_arpa(ip).lower(): ip for ip in ips}
        addresses: dict[str, str] = {}   # ip -> host.local
        srv: dict[str, str] = {}         # instance -> target host
        friendly: dict[str, str] = {}    # instance -> TXT friendly name

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            _harvest(data, wanted, reverse, found, addresses, srv, friendly)
    finally:
        sock.close()

    # Upgrade opaque hostnames using the service records that point at them.
    hosts_to_instance = {}
    for instance, target in srv.items():
        hosts_to_instance.setdefault(target.lower(), instance)

    for ip, host in addresses.items():
        if ip in found:
            continue
        label = _clean(host)
        if label and not _OPAQUE_RE.match(label):
            found[ip] = label
            continue
        instance = hosts_to_instance.get(host.lower())
        better = friendly.get(instance) if instance else None
        if not better and instance:
            better = instance.split(".")[0]
        better = _clean(better)
        if better and not _OPAQUE_RE.match(better):
            found[ip] = better
        # An opaque hostname is deliberately dropped rather than kept as a
        # fallback: "408f720d-1a59-bcae-…" is worse than the vendor name the
        # device already has. If a real name shows up in a later round it wins.

    return found


def _harvest(
    data: bytes,
    wanted: set[str],
    reverse: dict[str, str],
    found: dict[str, str],
    addresses: dict[str, str],
    srv: dict[str, str],
    friendly: dict[str, str],
) -> None:
    """Pull every record we care about out of one mDNS packet."""
    try:
        qdcount, ancount, nscount, arcount = struct.unpack_from("!HHHH", data, 4)
        offset = 12
        for _ in range(qdcount):
            _, offset = _read_name(data, offset)
            offset += 4
        for _ in range(ancount + nscount + arcount):
            record_name, offset = _read_name(data, offset)
            rtype, _rclass, _ttl, rdlength = struct.unpack_from("!HHIH", data, offset)
            offset += 10
            end = offset + rdlength

            if rtype == 1 and rdlength == 4:  # A
                ip = socket.inet_ntoa(data[offset : offset + 4])
                if ip in wanted:
                    addresses.setdefault(ip, record_name)
            elif rtype == 12:  # PTR
                target, _ = _read_name(data, offset)
                ip = reverse.get(record_name.lower())
                if ip and ip not in found:
                    cleaned = _clean(target)
                    if cleaned:
                        found[ip] = cleaned
            elif rtype == 33 and rdlength > 6:  # SRV: priority, weight, port, target
                target, _ = _read_name(data, offset + 6)
                if target:
                    srv.setdefault(record_name, target)
            elif rtype == 16:  # TXT
                name = _txt_friendly_name(data[offset:end])
                if name:
                    friendly.setdefault(record_name, name)

            offset = end
    except (struct.error, IndexError, OSError):
        pass


def _txt_friendly_name(rdata: bytes) -> str | None:
    """`fn=` (Chromecast) or `nm=`/`md=` — the name a user actually set."""
    pos = 0
    values: dict[str, str] = {}
    while pos < len(rdata):
        length = rdata[pos]
        pos += 1
        chunk = rdata[pos : pos + length].decode("utf-8", "replace")
        pos += length
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            values[key.strip().lower()] = value.strip()
    for key in ("fn", "nm", "n", "md", "ty"):
        if values.get(key):
            return values[key]
    return None


# ---------- SSDP / UPnP ----------

_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_GROUP}:{SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: ssdp:all\r\n"
    "USER-AGENT: Vantage/1.0 UPnP/1.1\r\n"
    "\r\n"
).encode()


def ssdp_discover(timeout: float = 3.0) -> dict[str, dict]:
    """One multicast M-SEARCH. Returns {ip: {server, locations}}.

    A device answers once per advertised service and the description documents
    differ in quality — a router will happily hand back its WPS stack before its
    actual identity — so keep every location and let the caller pick.
    """
    found: dict[str, dict] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        sock.sendto(_MSEARCH, (SSDP_GROUP, SSDP_PORT))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            ip = addr[0]
            headers = _parse_headers(data)
            entry = found.setdefault(ip, {"server": None, "locations": []})
            entry["server"] = entry["server"] or headers.get("server")
            location = headers.get("location")
            if location and location not in entry["locations"]:
                entry["locations"].append(location)
    except OSError:
        pass
    finally:
        sock.close()
    return found


def _parse_headers(data: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in data.decode("utf-8", "replace").split("\r\n")[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return headers


_FRIENDLY_RE = re.compile(r"<friendlyName>(.*?)</friendlyName>", re.I | re.S)
_MODEL_RE = re.compile(r"<modelName>(.*?)</modelName>", re.I | re.S)


def upnp_description(location: str, timeout: float = 2.0) -> dict:
    """Fetch a UPnP device description for its friendly name and model."""
    if not location:
        return {}
    try:
        request = urllib.request.Request(location, headers={"User-Agent": "Vantage"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(65536).decode("utf-8", "replace")
    except Exception:
        return {}
    friendly = _FRIENDLY_RE.search(body)
    model = _MODEL_RE.search(body)
    return {
        "friendly_name": _clean(friendly.group(1)) if friendly else None,
        "model": model.group(1).strip() if model else None,
    }


# ---------- NetBIOS ----------


def netbios_name(ip: str, timeout: float = 0.6) -> str | None:
    """NBSTAT node-status query -> the machine's NetBIOS name."""
    # "*" padded to 16 bytes, then first-level encoded (each nibble + 'A').
    raw = b"*" + b"\x00" * 15
    encoded = bytearray()
    for byte in raw:
        encoded.append((byte >> 4) + 0x41)
        encoded.append((byte & 0x0F) + 0x41)
    packet = (
        struct.pack("!HHHHHH", 0x4E53, 0x0000, 1, 0, 0, 0)
        + bytes([len(encoded)])
        + bytes(encoded)
        + b"\x00"
        + struct.pack("!HH", 0x21, 0x0001)  # NBSTAT, IN
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(packet, (ip, NETBIOS_PORT))
        data, _ = sock.recvfrom(2048)
    except OSError:
        return None
    finally:
        sock.close()
    return _clean(_parse_nbstat(data))


def _parse_nbstat(data: bytes) -> str | None:
    try:
        # A positive node-status response carries no question section, so the
        # counts in the header are the only safe way to find the answer.
        qdcount, ancount = struct.unpack_from("!HH", data, 4)
        if not ancount:
            return None
        offset = 12
        for _ in range(qdcount):
            _, offset = _read_name(data, offset)
            offset += 4  # qtype + qclass
        _, offset = _read_name(data, offset)  # answer name
        offset += 10  # type, class, ttl, rdlength
        count = data[offset]
        offset += 1
        for _ in range(count):
            name = data[offset : offset + 15].decode("ascii", "replace").strip()
            suffix = data[offset + 15]
            flags = struct.unpack_from("!H", data, offset + 16)[0]
            offset += 18
            is_group = bool(flags & 0x8000)
            # Suffix 0x00 on a unique name is the workstation itself; group
            # entries are the workgroup/domain, which is not this device.
            if suffix == 0x00 and not is_group and name:
                return name
    except (IndexError, struct.error):
        return None
    return None


# ---------- combined ----------


# Names that are protocol boilerplate rather than the device's own identity.
_GENERIC_NAMES = {
    "wfadevice", "wfa device", "wps", "upnp device", "router", "gateway",
    "residential gateway", "internet gateway device", "broadband router",
    "wandevice", "wanconnectiondevice", "landevice", "unknown", "device",
}


def _is_generic(name: str | None) -> bool:
    return not name or name.strip().lower() in _GENERIC_NAMES


def is_opaque(name: str | None) -> bool:
    """True for machine identifiers that are technically names but tell you nothing."""
    return bool(name) and bool(_OPAQUE_RE.match(name.strip()))


def needs_name(current: str | None) -> bool:
    """True while a better hostname is still worth looking for."""
    return not current or is_opaque(current) or _is_generic(current)


def is_better_name(new: str | None, current: str | None) -> bool:
    """Should `new` replace `current`?

    mDNS answers vary between rounds — the same Chromecast may report
    `a75950a6904cd69a` once and `Android` the next time. Without this, whichever
    name arrived first would stick forever, including the useless ones.
    """
    if not new or new == current:
        return False
    if not current:
        return True
    if is_opaque(current) and not is_opaque(new):
        return True
    if _is_generic(current) and not _is_generic(new):
        return True
    return False


def resolve_names(ips: list[str], workers: int = 24, use_ssdp: bool = True) -> dict[str, dict]:
    """Resolve a batch of addresses. Returns {ip: {hostname, source, model}}."""
    result: dict[str, dict] = {ip: {} for ip in ips}

    # SSDP is one multicast round for the whole subnet, so do it once up front.
    if use_ssdp:
        announcements = ssdp_discover()
        jobs = [
            (ip, info, location)
            for ip, info in announcements.items()
            if ip in result
            for location in info.get("locations", [])[:4]
        ]
        if jobs:
            with ThreadPoolExecutor(max_workers=min(12, len(jobs))) as pool:
                described = list(pool.map(lambda j: upnp_description(j[2]), jobs))
            for (ip, info, _location), description in zip(jobs, described):
                name = description.get("friendly_name")
                current = result.get(ip) or {}
                if info.get("server") and not current.get("server"):
                    current["server"] = info["server"]
                    current["source"] = current.get("source") or "ssdp"
                # A real name beats a generic one; never let boilerplate win.
                if name and not _is_generic(name) and _is_generic(current.get("hostname")):
                    current.update(
                        hostname=name, source="ssdp", model=description.get("model")
                    )
                result[ip] = current

    # mDNS is also one round for the whole subnet.
    pending = [ip for ip in ips if not (result.get(ip) or {}).get("hostname")]
    for ip, hostname in mdns_batch(pending).items():
        result[ip] = {**(result.get(ip) or {}), "hostname": hostname, "source": "mdns"}

    # NetBIOS is the per-host fallback: Windows machines and most NAS boxes.
    pending = [ip for ip in ips if not (result.get(ip) or {}).get("hostname")]
    if pending:
        with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as pool:
            for ip, name in zip(pending, pool.map(netbios_name, pending)):
                if name:
                    result[ip] = {**(result.get(ip) or {}), "hostname": name, "source": "netbios"}

    return {ip: info for ip, info in result.items() if info}
