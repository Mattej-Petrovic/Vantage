"""Wake-on-LAN — a magic packet, no router login, no privileges.

The packet is six 0xFF bytes followed by the target MAC repeated sixteen times.
It is not addressed to the device (a sleeping device has no IP), it is broadcast
and recognised by the NIC's own firmware, which is why this works for any router.

There is no acknowledgement in the protocol. Nothing sends a reply, so "sent"
is all we can honestly report — whether the device wakes depends on WoL being
enabled in its firmware, and the UI has to say so rather than imply success.
"""

from __future__ import annotations

import re
import socket

# Port 9 (discard) is the convention; 7 (echo) is the older one. Some NICs only
# listen on one, and the packet is 102 bytes, so sending both costs nothing.
PORTS = (9, 7)

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-]?)(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}$")


def magic_packet(mac: str) -> bytes:
    """Build the packet. Raises ValueError on a MAC we cannot parse."""
    if not _MAC_RE.match(mac.strip()):
        raise ValueError(f"Not a MAC address: {mac}")
    raw = bytes.fromhex(re.sub(r"[:-]", "", mac.strip()))
    return b"\xff" * 6 + raw * 16


def broadcast_addresses(interface: dict | None) -> list[str]:
    """Subnet-directed broadcast first, then the global one as a fallback.

    255.255.255.255 never leaves the first hop and some stacks drop it outright,
    so the subnet broadcast (e.g. 192.168.1.255) is the one that usually works.
    """
    addresses = []
    if interface:
        ip, prefix = interface.get("ip"), interface.get("prefix")
        if ip and prefix:
            try:
                ip_int = int.from_bytes(socket.inet_aton(ip), "big")
                mask_int = (0xFFFFFFFF << (32 - int(prefix))) & 0xFFFFFFFF
                directed = (ip_int & mask_int) | (~mask_int & 0xFFFFFFFF)
                addresses.append(socket.inet_ntoa(directed.to_bytes(4, "big")))
            except (OSError, ValueError):
                pass
    addresses.append("255.255.255.255")
    return addresses


def wake(mac: str, interface: dict | None = None) -> dict:
    """Send the magic packet. Returns where it went, not whether it worked."""
    try:
        packet = magic_packet(mac)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    targets = broadcast_addresses(interface)
    sent: list[str] = []
    last_error: str | None = None

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        for address in targets:
            for port in PORTS:
                try:
                    sock.sendto(packet, (address, port))
                    sent.append(f"{address}:{port}")
                except OSError as exc:
                    last_error = str(exc)
    finally:
        sock.close()

    if not sent:
        return {"ok": False, "error": last_error or "Could not send the packet."}
    return {"ok": True, "sent_to": sent}
