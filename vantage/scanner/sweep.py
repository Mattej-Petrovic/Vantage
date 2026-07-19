"""Liveness sweep + MAC resolution — no admin rights, no Npcap.

ICMP goes through the Windows stack via iphlpapi.IcmpSendEcho, which needs no
raw sockets and therefore no elevation and no capture driver. The echo traffic
populates the OS neighbor cache; we then read MACs out of `arp -a`.
"""

from __future__ import annotations

import ctypes
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from ctypes import POINTER, Structure, byref, c_void_p, wintypes

# iphlpapi ICMP status codes we treat as "host answered"
IP_SUCCESS = 0

CREATE_NO_WINDOW = 0x08000000

_MAC_RE = re.compile(
    r"(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})"
)


class IP_OPTION_INFORMATION(Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", POINTER(ctypes.c_ubyte)),
    ]


class ICMP_ECHO_REPLY(Structure):
    _fields_ = [
        ("Address", wintypes.ULONG),
        ("Status", wintypes.ULONG),
        ("RoundTripTime", wintypes.ULONG),
        ("DataSize", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("Data", c_void_p),
        ("Options", IP_OPTION_INFORMATION),
    ]


class _Icmp:
    """Lazy holder for the iphlpapi handles, so import never fails hard."""

    def __init__(self) -> None:
        self.available = False
        try:
            self.dll = ctypes.WinDLL("iphlpapi.dll")
            self.dll.IcmpCreateFile.restype = wintypes.HANDLE
            self.dll.IcmpSendEcho.restype = wintypes.DWORD
            self.dll.IcmpSendEcho.argtypes = [
                wintypes.HANDLE,
                wintypes.ULONG,
                c_void_p,
                wintypes.WORD,
                POINTER(IP_OPTION_INFORMATION),
                c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            self.dll.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
            self.available = True
        except (OSError, AttributeError):
            self.dll = None


_ICMP = _Icmp()

_PAYLOAD = b"vantage-probe"
_REPLY_SIZE = ctypes.sizeof(ICMP_ECHO_REPLY) + len(_PAYLOAD) + 64


def ping(ip: str, timeout_ms: int = 700) -> float | None:
    """Round-trip time in ms, or None if the host did not answer."""
    if _ICMP.available:
        rtt = _ping_icmp_api(ip, timeout_ms)
        if rtt is not None:
            return rtt
        return None
    return _ping_subprocess(ip, timeout_ms)


def _ping_icmp_api(ip: str, timeout_ms: int) -> float | None:
    handle = _ICMP.dll.IcmpCreateFile()
    if handle == wintypes.HANDLE(-1).value or not handle:
        return None
    try:
        dest = ctypes.c_ulong(int.from_bytes(socket.inet_aton(ip), "little"))
        payload = ctypes.create_string_buffer(_PAYLOAD)
        reply = ctypes.create_string_buffer(_REPLY_SIZE)
        n = _ICMP.dll.IcmpSendEcho(
            handle,
            dest,
            ctypes.cast(payload, c_void_p),
            len(_PAYLOAD),
            None,
            ctypes.cast(reply, c_void_p),
            _REPLY_SIZE,
            timeout_ms,
        )
        if n == 0:
            return None
        echo = ctypes.cast(reply, POINTER(ICMP_ECHO_REPLY)).contents
        if echo.Status != IP_SUCCESS:
            return None
        # The API reports whole milliseconds; sub-ms replies come back as 0.
        return float(echo.RoundTripTime) or 0.4
    finally:
        _ICMP.dll.IcmpCloseHandle(handle)


def _ping_subprocess(ip: str, timeout_ms: int) -> float | None:
    """Fallback when iphlpapi is unavailable: shell out to ping.exe."""
    try:
        out = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout_ms), ip],
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000 + 2,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    m = re.search(r"[=<]\s*(\d+)\s*ms", out)
    if m and ("TTL=" in out or "ttl=" in out):
        return float(m.group(1)) or 0.4
    return None


def tcp_nudge(ip: str, ports: tuple[int, ...] = (80, 443, 22, 445), timeout: float = 0.35) -> bool:
    """Force an ARP entry for a host that answered ping but has no MAC yet.

    A refused connection still completes the layer-2 exchange, which is all we
    need — so we do not care whether the port is actually open.
    """
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((ip, port))
            return True
        except (ConnectionRefusedError, OSError):
            # refused == host is there and ARP is now populated
            pass
        finally:
            s.close()
    return False


def read_neighbor_table() -> dict[str, str]:
    """Parse `arp -a` into {ip: MAC}. Locale-independent (matches on shape)."""
    try:
        out = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}

    table: dict[str, str] = {}
    for ip, mac in _MAC_RE.findall(out):
        mac = mac.replace("-", ":").upper()
        if mac in ("FF:FF:FF:FF:FF:FF", "00:00:00:00:00:00"):
            continue
        if ip.startswith("224.") or ip.startswith("239.") or ip.endswith(".255"):
            continue  # multicast / broadcast pseudo-entries
        table[ip] = mac
    return table


def sweep(hosts: list[str], workers: int = 64, timeout_ms: int = 700) -> dict[str, dict]:
    """Ping every host concurrently, then attach MACs from the neighbor cache.

    Returns {ip: {"ip", "rtt_ms", "mac"}} for hosts that are up. Hosts that
    answered but have no MAC entry are nudged over TCP and re-read once.
    """
    alive: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ip, rtt in zip(hosts, pool.map(lambda h: ping(h, timeout_ms), hosts)):
            if rtt is not None:
                alive[ip] = {"ip": ip, "rtt_ms": rtt, "mac": None}

    neighbors = read_neighbor_table()
    for ip, entry in alive.items():
        entry["mac"] = neighbors.get(ip)

    missing = [ip for ip, e in alive.items() if not e["mac"]]
    if missing:
        with ThreadPoolExecutor(max_workers=min(32, len(missing))) as pool:
            list(pool.map(tcp_nudge, missing))
        neighbors = read_neighbor_table()
        for ip in missing:
            alive[ip]["mac"] = neighbors.get(ip)

    # A host in the ARP cache that never answered ping is still present on the
    # LAN (many devices drop ICMP) — include it with no RTT.
    return alive


def sweep_interface(
    iface: dict,
    hosts: list[str],
    *,
    workers: int = 64,
    timeout_ms: int = 700,
) -> dict[str, dict]:
    """Sweep a subnet and fold in ARP-only hosts that ignored our ping."""
    alive = sweep(hosts, workers=workers, timeout_ms=timeout_ms)

    host_set = set(hosts)
    for ip, mac in read_neighbor_table().items():
        if ip in host_set and ip not in alive:
            alive[ip] = {"ip": ip, "rtt_ms": None, "mac": mac}

    # This machine never appears in its own ARP cache, and it may sit on the
    # subnet twice (ethernet + wifi). Fill those MACs in from the NIC table.
    from . import interfaces as _interfaces

    for local in _interfaces.list_interfaces():
        lip = local.get("ip")
        if not lip or lip not in host_set:
            continue
        entry = alive.setdefault(lip, {"ip": lip, "rtt_ms": 0.4, "mac": None})
        if not entry["mac"]:
            entry["mac"] = local.get("mac")
        entry["is_local"] = True
    return alive


def neighbor_snapshot_interface(iface: dict, hosts: list[str]) -> dict[str, dict]:
    """Fast startup inventory from the existing neighbor table.

    This intentionally does not ping the whole subnet. It gives the UI immediate
    known-device context while the heavier active sweep waits for a later cycle.
    """
    host_set = set(hosts)
    alive = {
        ip: {"ip": ip, "rtt_ms": None, "mac": mac}
        for ip, mac in read_neighbor_table().items()
        if ip in host_set
    }

    from . import interfaces as _interfaces

    for local in _interfaces.list_interfaces():
        lip = local.get("ip")
        if not lip or lip not in host_set:
            continue
        entry = alive.setdefault(lip, {"ip": lip, "rtt_ms": 0.4, "mac": None})
        if not entry["mac"]:
            entry["mac"] = local.get("mac")
        entry["is_local"] = True
    return alive
