"""Per-device risk scoring — deterministic rules, no model, no guessing.

Two properties matter more than the rule list itself:

**Every score is explainable.** A finding carries the port that triggered it and
a sentence saying why it matters. A number the user cannot interrogate is worse
than no number, because it invites trust it has not earned.

**A port means different things on different devices.** Port 80 on a router is
an admin panel; port 80 on a laptop is a dev server. The same evidence ordering
as `identity.py`: what a device *is* changes what its open ports imply.

The scan covers ~110 common TCP ports, so a clean result means "nothing risky in
what we looked at" — never "safe". A device we have not scanned is `unknown`,
not `ok`; that distinction is the whole point of scoring honestly.
"""

from __future__ import annotations

# Weight per severity. The gaps are deliberately wide: one genuinely bad finding
# should outrank a pile of minor ones rather than being averaged away by them.
WEIGHTS = {"high": 40, "medium": 18, "low": 6}

BAND_RISK = 35
BAND_WATCH = 10

# Device types whose web server is a management interface rather than content.
_MANAGED = {"router", "camera", "printer", "iot"}

# Vendors with a long history of shipping devices whose management interface is
# reachable with the credentials printed in the manual. Being on this list is
# not an accusation about a specific unit — it raises an exposed admin port from
# "notable" to "check this one".
_DEFAULT_CRED_VENDORS = (
    "hikvision", "dahua", "reolink", "foscam", "tp-link", "d-link",
    "tenda", "zyxel", "netgear", "tuya", "xiongmai", "shenzhen",
)

_MGMT_PORTS = {23, 80, 8080, 8443, 554, 9100, 7547}


def _finding(id_: str, severity: str, port: int | None, title: str, detail: str) -> dict:
    return {
        "id": id_,
        "severity": severity,
        "port": port,
        "title": title,
        "detail": detail,
        "weight": WEIGHTS[severity],
    }


def evaluate(device: dict, ports: list[dict]) -> dict:
    """Score one device. `ports` is the open-port list from the store."""
    open_ports = {int(p["port"]): p for p in ports if p.get("status", "open") == "open"}
    device_type = (device.get("device_type") or "unknown").lower()
    vendor = (device.get("vendor") or "").lower()
    findings: list[dict] = []

    def has(*numbers: int) -> int | None:
        return next((n for n in numbers if n in open_ports), None)

    # --- cleartext protocols: the finding is the protocol, not the service ---
    if 23 in open_ports:
        findings.append(_finding(
            "telnet", "high", 23, "Telnet is open",
            "Telnet sends its login and every keystroke in clear text. Anyone on "
            "this network can read them. There is no safe way to use it.",
        ))
    if 21 in open_ports:
        findings.append(_finding(
            "ftp", "medium", 21, "FTP is open",
            "Plain FTP transfers credentials and files unencrypted. Check whether "
            "it allows anonymous login.",
        ))
    port = has(512, 513, 514)
    if port:
        findings.append(_finding(
            "rservices", "high", port, "Berkeley r-services are open",
            "rlogin/rsh/rexec authenticate on trusted hostnames alone and are "
            "unencrypted. Nothing built this century should expose them.",
        ))

    # --- remote control ---
    if 5900 in open_ports:
        findings.append(_finding(
            "vnc", "high", 5900, "VNC is open",
            "VNC is frequently left with no password or a shared one, and the "
            "protocol itself is unencrypted. This is full control of the screen.",
        ))
    if 3389 in open_ports:
        findings.append(_finding(
            "rdp", "medium", 3389, "Remote Desktop is open",
            "RDP is encrypted, but it is the single most brute-forced service "
            "there is. Worth confirming you meant to enable it.",
        ))

    # --- file sharing ---
    port = has(445, 139)
    if port:
        findings.append(_finding(
            "smb", "medium", port, "SMB file sharing is exposed",
            "Windows file sharing is reachable from the whole subnet. That is "
            "normal on a home PC — it matters because it is how ransomware "
            "spreads sideways once one machine is compromised.",
        ))

    # --- databases: never meant to face a network at all ---
    if 6379 in open_ports:
        findings.append(_finding(
            "redis", "high", 6379, "Redis is reachable",
            "Redis has no authentication by default. If this is the default "
            "config, anyone on the LAN can read and write the whole dataset.",
        ))
    port = has(3306, 5432, 27017, 9200, 1433)
    if port:
        findings.append(_finding(
            "database", "medium", port, f"A database is listening on {port}",
            "Databases normally bind to localhost. Reachable from the subnet "
            "means the bind address was widened, deliberately or not.",
        ))

    # --- cameras ---
    if 554 in open_ports:
        findings.append(_finding(
            "rtsp", "medium", 554, "RTSP video stream is open",
            "The camera serves its stream on the network. Many models accept "
            "the stream URL without credentials.",
        ))

    # --- management web interfaces (context-dependent, see module docstring) ---
    port = has(80, 8080, 8081)
    if port:
        entry = open_ports[port]
        banner = (entry.get("banner") or "").lower()
        realm = "realm" in banner or "unauthorized" in banner
        if device_type in _MANAGED or realm:
            findings.append(_finding(
                "http_admin", "medium", port,
                "Management interface over plain HTTP",
                "The admin page is served without TLS, so its password crosses "
                "the network in the clear every time you log in.",
            ))
    if 7547 in open_ports:
        findings.append(_finding(
            "cwmp", "low", 7547, "TR-069 remote management is open",
            "This is the port your ISP manages the router through. It belongs on "
            "the WAN side; exposed to the LAN it is extra attack surface.",
        ))
    if 1900 in open_ports:
        findings.append(_finding(
            "upnp", "low", 1900, "UPnP is exposed over TCP",
            "UPnP lets devices open holes in the router's firewall without "
            "asking. Convenient, and a long-standing source of accidental exposure.",
        ))

    # --- vendor context raises an exposed admin port ---
    if vendor and any(v in vendor for v in _DEFAULT_CRED_VENDORS):
        port = has(*_MGMT_PORTS)
        if port:
            findings.append(_finding(
                "default_creds", "high", port,
                "Admin port open on a default-credential-prone vendor",
                f"{device.get('vendor')} devices have historically shipped with "
                "documented default logins. With an admin port reachable, this is "
                "worth verifying by hand.",
            ))

    # --- shape of the surface, independent of any single port ---
    if len(open_ports) >= 12:
        findings.append(_finding(
            "surface", "low", None, f"{len(open_ports)} open ports",
            "A broad listening surface on one host. Not a fault by itself, but "
            "each service is one more thing that needs patching.",
        ))

    findings.sort(key=lambda f: (-f["weight"], f["port"] or 0))
    score = min(100, sum(f["weight"] for f in findings))
    return {
        "score": score,
        "band": band_for(score, scanned=bool(device.get("last_port_scan"))),
        "findings": findings,
    }


def band_for(score: int, scanned: bool = True) -> str:
    """ok | watch | risk | unknown.

    An unscanned device is `unknown`, never `ok`. Reporting "no findings" for a
    host we never probed would be inventing reassurance out of missing data —
    the same mistake as calling every device "new" on the first scan.
    """
    if not scanned:
        return "unknown"
    if score >= BAND_RISK:
        return "risk"
    if score >= BAND_WATCH:
        return "watch"
    return "ok"


def summarize(result: dict) -> str:
    """One line for an alert or a tooltip."""
    findings = result.get("findings") or []
    if not findings:
        return "No risky services found in the scanned ports."
    head = findings[0]["title"]
    extra = len(findings) - 1
    return f"{head}{f' (+{extra} more)' if extra else ''}"
