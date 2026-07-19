"""Device identity: what kind of thing is this? (§5.3)

Evidence is combined in order of how much it actually proves. An open port is a
statement about what a device *does* and beats a vendor guess — Apple makes
phones, laptops and TV boxes, but only a printer answers on 9100. Hostnames sit
in between: strong when they contain a real word, worthless when the vendor
generated them.
"""

from __future__ import annotations

import re

TYPES = (
    "router",
    "phone",
    "laptop",
    "desktop",
    "tv",
    "iot",
    "camera",
    "printer",
    "unknown",
)

# Vendor substring -> device type. Ordered: first match wins.
_VENDOR_HINTS: tuple[tuple[str, str], ...] = (
    ("sagemcom", "router"),
    ("technicolor", "router"),
    ("zyxel", "router"),
    ("tp-link", "router"),
    ("ubiquiti", "router"),
    ("mikrotik", "router"),
    ("netgear", "router"),
    ("asustek", "router"),
    ("espressif", "iot"),
    ("tuya", "iot"),
    ("shelly", "iot"),
    ("signify", "iot"),
    ("philips", "iot"),
    ("nest labs", "iot"),
    ("ikea", "iot"),
    ("sonoff", "iot"),
    ("itead", "iot"),
    ("hikvision", "camera"),
    ("dahua", "camera"),
    ("axis communications", "camera"),
    ("reolink", "camera"),
    ("brother", "printer"),
    ("hewlett packard", "printer"),
    ("canon", "printer"),
    ("epson", "printer"),
    ("seiko epson", "printer"),
    ("vestel", "tv"),
    ("commscope", "tv"),
    ("arris", "tv"),
    ("chromecast", "tv"),
    ("amazon technologies", "tv"),
    ("sonos", "tv"),
    ("roku", "tv"),
    ("vizio", "tv"),
    ("lg electronics", "tv"),
    ("sony", "tv"),
    ("nintendo", "tv"),
    ("apple", "phone"),
    ("samsung", "phone"),
    ("xiaomi", "phone"),
    ("huawei", "phone"),
    ("oneplus", "phone"),
    ("google", "phone"),
    ("motorola", "phone"),
    ("intel", "laptop"),
    ("rivet", "desktop"),
    ("killer", "desktop"),
    ("dell", "laptop"),
    ("lenovo", "laptop"),
    ("micro-star", "desktop"),
    ("asrock", "desktop"),
    ("gigabyte", "desktop"),
    ("raspberry pi", "iot"),
)


# Ports that only ever belong to one kind of device. Nothing but a printer
# listens on 9100, so these outrank every other signal including the hostname.
_PORT_HINTS_STRONG: tuple[tuple[int, str], ...] = (
    (9100, "printer"),   # JetDirect
    (631, "printer"),    # IPP
    (515, "printer"),    # LPD
    (554, "camera"),     # RTSP
    (8060, "tv"),        # Roku ECP
    (7547, "router"),    # TR-069 CWMP
    (5683, "iot"),       # CoAP
    (1883, "iot"),       # MQTT
    (8883, "iot"),
    (8123, "iot"),       # Home Assistant
)

# Ports that describe a *capability* rather than an identity. A MacBook running
# an AirPlay receiver listens on 7000; that makes it a laptop that can receive
# AirPlay, not a TV. These only get a vote once the hostname has had its say.
_PORT_HINTS_WEAK: tuple[tuple[int, str], ...] = (
    (8009, "tv"),        # Chromecast
    (7000, "tv"),        # AirPlay
    (3689, "tv"),        # DAAP
    (32400, "tv"),       # Plex
    (5555, "phone"),     # Android debug bridge
    (3389, "desktop"),   # RDP
    (445, "desktop"),    # SMB
    (139, "desktop"),    # NetBIOS session
)

# Hostname substring -> device type. Checked before vendor: a device the owner
# named "kitchen-printer" has told us more than its OUI can.
_HOSTNAME_HINTS: tuple[tuple[str, str], ...] = (
    ("iphone", "phone"), ("ipad", "phone"), ("galaxy", "phone"),
    ("pixel", "phone"), ("oneplus", "phone"), ("android", "phone"),
    ("macbook", "laptop"), ("laptop", "laptop"), ("thinkpad", "laptop"),
    ("desktop", "desktop"), ("-pc", "desktop"), ("workstation", "desktop"),
    ("chromecast", "tv"), ("shield", "tv"), ("roku", "tv"), ("firetv", "tv"),
    ("appletv", "tv"), ("apple-tv", "tv"), ("bravia", "tv"), ("samsungtv", "tv"),
    ("tv", "tv"), ("speaker", "tv"), ("sonos", "tv"), ("echo", "tv"),
    ("homepod", "tv"), ("nintendo", "tv"), ("playstation", "tv"), ("xbox", "tv"),
    ("printer", "printer"), ("officejet", "printer"), ("envy", "printer"),
    ("deskjet", "printer"), ("laserjet", "printer"), ("brother", "printer"),
    ("camera", "camera"), ("cam-", "camera"), ("ipcam", "camera"),
    ("doorbell", "camera"), ("nvr", "camera"),
    ("router", "router"), ("gateway", "router"), ("openwrt", "router"),
    ("raspberry", "iot"), ("raspberrypi", "iot"), ("shelly", "iot"),
    ("tasmota", "iot"), ("esp-", "iot"), ("esp32", "iot"), ("hue", "iot"),
    ("nas", "desktop"), ("synology", "desktop"), ("truenas", "desktop"),
)

# A hostname the vendor generated tells us nothing about what the device is.
_UNINFORMATIVE_HOST = re.compile(r"^[0-9a-f-]{8,}$", re.I)


def guess_type(
    vendor: str | None,
    *,
    is_gateway: bool = False,
    is_local: bool = False,
    hostname: str | None = None,
    ports: list[int] | None = None,
    model: str | None = None,
) -> str:
    """Best-effort device type from role, open ports, hostname and vendor."""
    if is_gateway:
        return "router"
    if is_local:
        return "desktop"

    open_ports = set(ports or ())

    # Decisive ports first — nothing else can outweigh them.
    for port, type_ in _PORT_HINTS_STRONG:
        if port in open_ports:
            return type_

    # Then names, which are what a human or a vendor deliberately chose.
    for text in (hostname, model):
        if text and not _UNINFORMATIVE_HOST.match(text):
            low = text.lower()
            for needle, type_ in _HOSTNAME_HINTS:
                if needle in low:
                    return type_

    # Then capability ports, which narrow the field without settling it.
    for port, type_ in _PORT_HINTS_WEAK:
        if port in open_ports:
            return type_

    if vendor:
        v = vendor.lower()
        for needle, type_ in _VENDOR_HINTS:
            if needle in v:
                return type_

    # A device with no open ports is most often a phone with everything closed,
    # but it could equally be a firewalled laptop — so say so, don't guess.
    return "unknown"
