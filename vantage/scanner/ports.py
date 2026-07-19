"""Threaded TCP connect scan + light banner grab.

Plain `socket.connect_ex` on a thread pool: no raw sockets, no admin, no Npcap —
the same constraint that shapes the sweep. A connect scan is noisier than
SYN scanning but it is the only kind available to an unprivileged process, and on
your own LAN that trade is free.
"""

from __future__ import annotations

import socket
import ssl
from concurrent.futures import ThreadPoolExecutor

# ~100 common ports, ordered by how
# likely they are to say something about *what a device is*, not by number.
TOP_PORTS: tuple[int, ...] = (
    21, 22, 23, 25, 53, 67, 69, 80, 81, 88, 110, 111, 119, 123, 135, 137, 139,
    143, 161, 179, 389, 427, 443, 445, 465, 500, 515, 520, 548, 554, 587, 593,
    623, 631, 636, 873, 902, 993, 995, 1080, 1194, 1433, 1521, 1723, 1883, 1900,
    2000, 2049, 2082, 2083, 2086, 2087, 2096, 2181, 2375, 2376, 3000, 3128,
    3260, 3306, 3389, 3478, 3689, 4444, 4567, 5000, 5001, 5060, 5061, 5222,
    5353, 5432, 5555, 5601, 5666, 5672, 5683, 5900, 5901, 6000, 6379, 6667,
    7000, 7070, 7547, 8000, 8008, 8009, 8060, 8080, 8081, 8083, 8086, 8088,
    8123, 8181, 8200, 8443, 8500, 8883, 8888, 9000, 9090, 9100, 9200, 9300,
    10000, 11211, 27017, 32400, 49152,
)

# Port -> service name. Only what a banner cannot tell us better.
SERVICES: dict[int, str] = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns", 67: "dhcp",
    69: "tftp", 80: "http", 81: "http-alt", 88: "kerberos", 110: "pop3",
    111: "rpcbind", 119: "nntp", 123: "ntp", 135: "msrpc", 137: "netbios-ns",
    139: "netbios-ssn", 143: "imap", 161: "snmp", 179: "bgp", 389: "ldap",
    427: "slp", 443: "https", 445: "smb", 465: "smtps", 500: "isakmp",
    515: "printer", 520: "rip", 548: "afp", 554: "rtsp", 587: "submission",
    593: "rpc-http", 623: "ipmi", 631: "ipp", 636: "ldaps", 873: "rsync",
    902: "vmware", 993: "imaps", 995: "pop3s", 1080: "socks", 1194: "openvpn",
    1433: "mssql", 1521: "oracle", 1723: "pptp", 1883: "mqtt", 1900: "upnp",
    2000: "cisco-sccp", 2049: "nfs", 2082: "cpanel", 2083: "cpanel-ssl",
    2086: "whm", 2087: "whm-ssl", 2096: "webmail-ssl", 2181: "zookeeper",
    2375: "docker", 2376: "docker-tls", 3000: "http-dev", 3128: "squid",
    3260: "iscsi", 3306: "mysql", 3389: "rdp", 3478: "stun", 3689: "daap",
    4444: "metasploit", 4567: "http-alt", 5000: "upnp-http", 5001: "http-alt",
    5060: "sip", 5061: "sip-tls", 5222: "xmpp", 5353: "mdns",
    5432: "postgresql", 5555: "adb", 5601: "kibana", 5666: "nrpe",
    5672: "amqp", 5683: "coap", 5900: "vnc", 5901: "vnc-1", 6000: "x11",
    6379: "redis", 6667: "irc", 7000: "airplay", 7070: "rtsp-alt",
    # 8008/8009 are AJP by IANA, but on a home LAN they are Chromecast.
    7547: "tr-069", 8000: "http-alt", 8008: "chromecast", 8009: "chromecast",
    8060: "roku-ecp", 8080: "http-proxy", 8081: "http-alt", 8083: "http-alt",
    8086: "influxdb", 8088: "http-alt", 8123: "home-assistant", 8181: "http-alt",
    8200: "plex-dlna", 8443: "https-alt", 8500: "consul", 8883: "mqtt-tls",
    8888: "http-alt", 9000: "http-alt", 9090: "http-alt", 9100: "jetdirect",
    9200: "elasticsearch", 9300: "elasticsearch", 10000: "webmin",
    11211: "memcached", 27017: "mongodb", 32400: "plex", 49152: "upnp-alt",
}

# Ports we speak enough of to pull a useful banner out of.
_HTTP_PORTS = {80, 81, 443, 591, 2082, 2083, 2086, 2087, 2096, 3000, 4567,
               5000, 5001, 5601, 7547, 8000, 8008, 8060, 8080, 8081, 8083,
               8086, 8088, 8123, 8181, 8200, 8443, 8888, 9000, 9090, 9200,
               10000, 32400}
_TLS_PORTS = {443, 465, 636, 993, 995, 2083, 2087, 2096, 5061, 8443, 8883}
# Services that greet you first — just read, do not write.
_GREETING_PORTS = {21, 22, 23, 25, 110, 119, 143, 587, 3306, 5432, 6667}


def service_name(port: int) -> str:
    return SERVICES.get(port, "unknown")


def _grab_banner(sock: socket.socket, ip: str, port: int, timeout: float) -> str | None:
    """A few bytes of whatever the service says about itself. Best effort."""
    sock.settimeout(timeout)
    try:
        if port in _TLS_PORTS:
            # We want the certificate's identity, not the payload — an unverified
            # handshake is correct here: self-signed is the norm on a LAN.
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with ctx.wrap_socket(sock, server_hostname=ip) as tls:
                cert = tls.getpeercert()
                subject = ""
                for field in (cert or {}).get("subject", ()):
                    for key, value in field:
                        if key == "commonName":
                            subject = value
                proto = tls.version() or "TLS"
                return f"{proto} {subject}".strip() if subject else proto
        if port in _HTTP_PORTS:
            sock.sendall(
                b"HEAD / HTTP/1.0\r\nHost: %s\r\nUser-Agent: Vantage\r\n\r\n"
                % ip.encode()
            )
        data = sock.recv(256)
        if not data:
            return None
        if port in _HTTP_PORTS:
            return _http_identity(data)
        return data.decode("utf-8", "replace").strip().splitlines()[0][:120] or None
    except (OSError, ssl.SSLError, IndexError):
        return None


def _http_identity(data: bytes) -> str | None:
    """Server: / WWW-Authenticate: realm is what names an embedded web UI."""
    text = data.decode("utf-8", "replace")
    server = realm = None
    for line in text.split("\r\n"):
        low = line.lower()
        if low.startswith("server:"):
            server = line.split(":", 1)[1].strip()
        elif low.startswith("www-authenticate:") and "realm=" in low:
            realm = line.split("realm=", 1)[1].strip().strip('"').split('"')[0]
    parts = [p for p in (server, f'realm "{realm}"' if realm else None) if p]
    return " · ".join(parts)[:120] or None


def scan_port(ip: str, port: int, timeout: float = 0.6, banner: bool = True) -> dict | None:
    """Returns {port, service, banner} if open, else None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        if sock.connect_ex((ip, port)) != 0:
            return None
        text = _grab_banner(sock, ip, port, timeout) if banner else None
        return {"port": port, "proto": "tcp", "service": service_name(port), "banner": text}
    except OSError:
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass


def scan_host(
    ip: str,
    ports: tuple[int, ...] | list[int] = TOP_PORTS,
    timeout: float = 0.6,
    workers: int = 64,
    banner: bool = True,
) -> list[dict]:
    """Open ports on one host, lowest first."""
    if not ip:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(ports))) as pool:
        results = pool.map(lambda p: scan_port(ip, p, timeout, banner), ports)
    return sorted((r for r in results if r), key=lambda r: r["port"])
