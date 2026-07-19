"""NIC enumeration via GetAdaptersAddresses (no subprocess, no console flash).

Exposes the active IPv4 interfaces that have a default gateway, so the scanner
knows which subnet to sweep and which node on the map is the router.
"""

from __future__ import annotations

import ctypes
import ipaddress
import socket
from ctypes import POINTER, Structure, Union, byref, c_void_p, wintypes

AF_UNSPEC = 0
AF_INET = 2
AF_INET6 = 23

GAA_FLAG_INCLUDE_GATEWAYS = 0x0080
GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_SKIP_DNS_SERVER = 0x0008

ERROR_BUFFER_OVERFLOW = 111
IF_OPER_STATUS_UP = 1
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_TUNNEL = 131
IF_TYPE_IEEE80211 = 71

MAX_ADAPTER_ADDRESS_LENGTH = 8


class SOCKADDR(Structure):
    # sa_data as c_ubyte (not c_char): ctypes truncates c_char arrays at the
    # first NUL, which mangles binary addresses.
    _fields_ = [("sa_family", wintypes.USHORT), ("sa_data", ctypes.c_ubyte * 26)]


class SOCKET_ADDRESS(Structure):
    _fields_ = [("lpSockaddr", POINTER(SOCKADDR)), ("iSockaddrLength", ctypes.c_int)]


class _UNICAST_HEAD(Structure):
    _fields_ = [("Length", wintypes.ULONG), ("Flags", wintypes.DWORD)]


class _UNICAST_UNION(Union):
    _fields_ = [("Alignment", ctypes.c_ulonglong), ("s", _UNICAST_HEAD)]


class IP_ADAPTER_UNICAST_ADDRESS(Structure):
    pass


IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("u", _UNICAST_UNION),
    ("Next", POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
    ("PrefixOrigin", ctypes.c_int),
    ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
    ("ValidLifetime", wintypes.ULONG),
    ("PreferredLifetime", wintypes.ULONG),
    ("LeaseLifetime", wintypes.ULONG),
    ("OnLinkPrefixLength", ctypes.c_ubyte),
]


class _GATEWAY_HEAD(Structure):
    _fields_ = [("Length", wintypes.ULONG), ("Reserved", wintypes.DWORD)]


class _GATEWAY_UNION(Union):
    _fields_ = [("Alignment", ctypes.c_ulonglong), ("s", _GATEWAY_HEAD)]


class IP_ADAPTER_GATEWAY_ADDRESS(Structure):
    pass


IP_ADAPTER_GATEWAY_ADDRESS._fields_ = [
    ("u", _GATEWAY_UNION),
    ("Next", POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
]


class _ADAPTER_HEAD(Structure):
    _fields_ = [("Length", wintypes.ULONG), ("IfIndex", wintypes.DWORD)]


class _ADAPTER_UNION(Union):
    _fields_ = [("Alignment", ctypes.c_ulonglong), ("s", _ADAPTER_HEAD)]


class IP_ADAPTER_ADDRESSES(Structure):
    pass


IP_ADAPTER_ADDRESSES._fields_ = [
    ("u", _ADAPTER_UNION),
    ("Next", POINTER(IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", c_void_p),
    ("FirstMulticastAddress", c_void_p),
    ("FirstDnsServerAddress", c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * MAX_ADAPTER_ADDRESS_LENGTH),
    ("PhysicalAddressLength", wintypes.ULONG),
    ("Flags", wintypes.ULONG),
    ("Mtu", wintypes.ULONG),
    ("IfType", wintypes.DWORD),
    ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", wintypes.DWORD),
    ("ZoneIndices", wintypes.ULONG * 16),
    ("FirstPrefix", c_void_p),
    ("TransmitLinkSpeed", ctypes.c_ulonglong),
    ("ReceiveLinkSpeed", ctypes.c_ulonglong),
    ("FirstWinsServerAddress", c_void_p),
    ("FirstGatewayAddress", POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
]


def _sockaddr_to_ip(sa_ptr) -> str | None:
    """Read an IPv4 dotted-quad out of a SOCKADDR pointer (IPv6 ignored)."""
    if not sa_ptr:
        return None
    sa = sa_ptr.contents
    if sa.sa_family != AF_INET:
        return None
    # sa_data: 2 bytes port, then 4 bytes address
    raw = bytes(bytearray(sa.sa_data[2:6]))
    return socket.inet_ntoa(raw)


def _format_mac(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw)


def list_interfaces() -> list[dict]:
    """Return usable IPv4 interfaces, most likely-active first.

    Each entry: name, description, mac, ip, prefix, cidr, gateway, is_wifi,
    host_count, id.
    """
    iphlpapi = ctypes.WinDLL("iphlpapi.dll")
    flags = (
        GAA_FLAG_INCLUDE_GATEWAYS
        | GAA_FLAG_SKIP_ANYCAST
        | GAA_FLAG_SKIP_MULTICAST
        | GAA_FLAG_SKIP_DNS_SERVER
    )

    size = wintypes.ULONG(15000)
    buf = ctypes.create_string_buffer(size.value)
    ret = iphlpapi.GetAdaptersAddresses(AF_INET, flags, None, buf, byref(size))
    if ret == ERROR_BUFFER_OVERFLOW:
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetAdaptersAddresses(AF_INET, flags, None, buf, byref(size))
    if ret != 0:
        return []

    results: list[dict] = []
    adapter = ctypes.cast(buf, POINTER(IP_ADAPTER_ADDRESSES))
    while adapter:
        a = adapter.contents
        adapter = a.Next

        if a.OperStatus != IF_OPER_STATUS_UP:
            continue
        if a.IfType in (IF_TYPE_SOFTWARE_LOOPBACK, IF_TYPE_TUNNEL):
            continue

        gateway = None
        gw = a.FirstGatewayAddress
        while gw:
            ip = _sockaddr_to_ip(gw.contents.Address.lpSockaddr)
            if ip and ip != "0.0.0.0":
                gateway = ip
                break
            gw = gw.contents.Next
        if not gateway:
            continue  # no default route -> not a network we can meaningfully scan

        uni = a.FirstUnicastAddress
        while uni:
            u = uni.contents
            uni = u.Next
            ip = _sockaddr_to_ip(u.Address.lpSockaddr)
            if not ip or ip.startswith("169.254."):
                continue
            prefix = int(u.OnLinkPrefixLength)
            if not 8 <= prefix <= 32:
                continue
            try:
                net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
            except ValueError:
                continue

            mac_len = min(int(a.PhysicalAddressLength), MAX_ADAPTER_ADDRESS_LENGTH)
            mac = _format_mac(bytes(bytearray(a.PhysicalAddress[:mac_len]))) if mac_len else ""

            results.append(
                {
                    "id": f"{a.u.s.IfIndex}-{ip}",
                    "if_index": int(a.u.s.IfIndex),
                    "name": a.FriendlyName or "",
                    "description": a.Description or "",
                    "mac": mac,
                    "ip": ip,
                    "prefix": prefix,
                    "cidr": str(net),
                    "gateway": gateway,
                    "is_wifi": a.IfType == IF_TYPE_IEEE80211,
                    "host_count": max(net.num_addresses - 2, 0),
                }
            )
            break  # one IPv4 per adapter is enough

    # Prefer wired over wifi, then smaller subnets (faster, more likely the real LAN)
    results.sort(key=lambda i: (i["is_wifi"], i["host_count"]))
    return results


def pick_default(interfaces: list[dict] | None = None) -> dict | None:
    """The interface we scan unless the user picks another one."""
    ifaces = interfaces if interfaces is not None else list_interfaces()
    if not ifaces:
        return None
    # Skip absurdly large ranges by default (a /16 sweep is not a 30s scan).
    sane = [i for i in ifaces if i["host_count"] <= 4096]
    return (sane or ifaces)[0]


def hosts_for(iface: dict, max_hosts: int = 4096) -> list[str]:
    """Every scannable host address in the interface's subnet."""
    net = ipaddress.ip_network(iface["cidr"], strict=False)
    out = []
    for host in net.hosts():
        out.append(str(host))
        if len(out) >= max_hosts:
            break
    return out
