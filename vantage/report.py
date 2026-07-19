"""Snapshot export (§5.10): the inventory and the map as one standalone file.

A report is read later, by someone who was not there when it was generated —
often the person who wrote it, months on. That changes what it has to carry.
Every number needs its method attached, because "3 devices at risk" is
meaningless without "out of how many, measured how". So the header states what
was swept, with what, and what was not looked at at all.

The output is a single self-contained HTML file: styles inlined, the map
embedded as a data URI, no network fetches. It opens anywhere, survives being
emailed, and prints to PDF through the browser — which is why there is no PDF
dependency here. Print rules are part of the stylesheet, not an afterthought.
"""

from __future__ import annotations

import base64
import binascii
import html
import time

BAND_LABEL = {
    "risk": "Risk",
    "watch": "Watch",
    "ok": "No findings",
    "unknown": "Not scanned",
}

BAND_COLOR = {
    "risk": "#D9534F",
    "watch": "#D9922B",
    "ok": "#2E9E6B",
    "unknown": "#8A90A2",
}

SEVERITY_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}


def decode_data_url(data_url: str | None) -> bytes | None:
    """Pull the bytes out of a `data:image/png;base64,...` URL from the canvas."""
    if not data_url or "," not in data_url:
        return None
    header, _, payload = data_url.partition(",")
    if "base64" not in header:
        return None
    try:
        return base64.b64decode(payload)
    except (binascii.Error, ValueError):
        return None


def _fmt_time(ts: int | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))


def _fmt_date(ts: int | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d", time.localtime(int(ts)))


def _esc(value) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def build_html(
    devices: list[dict],
    findings_by_mac: dict[str, dict],
    interface: dict | None = None,
    map_png: bytes | None = None,
    generated: int | None = None,
) -> str:
    """Render the whole report. `devices` is a monitor snapshot's device list."""
    generated = generated or int(time.time())
    online = [d for d in devices if d.get("online")]
    scanned = [d for d in devices if d.get("port_scanned")]

    counts = {band: 0 for band in BAND_LABEL}
    for device in devices:
        counts[device.get("risk_band") or "unknown"] = (
            counts.get(device.get("risk_band") or "unknown", 0) + 1
        )

    iface_name = (interface or {}).get("name") or "unknown interface"
    subnet = (interface or {}).get("cidr") or (interface or {}).get("ip") or "—"

    parts: list[str] = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>Vantage report — {_esc(_fmt_date(generated))}</title>",
        f"<style>{_STYLES}</style></head><body><div class='page'>",
        "<header class='head'>",
        "<div><div class='brand'>Vantage</div>",
        "<div class='sub'>Network inventory and posture report</div></div>",
        f"<div class='when'>{_esc(_fmt_time(generated))}</div>",
        "</header>",
        # The method note is not decoration. Every count below is only true
        # within these limits, so it precedes the counts rather than following
        # them as a footnote nobody reaches.
        "<section class='method'>",
        f"<p><strong>Scope.</strong> Swept <code>{_esc(subnet)}</code> on "
        f"<strong>{_esc(iface_name)}</strong> by ICMP echo, resolving names over "
        "mDNS, SSDP and NetBIOS. No packet capture, no ARP spoofing, no admin "
        "rights — so a device that ignores ping and never appears in the ARP "
        "table will not be listed here.</p>",
        f"<p><strong>Depth.</strong> {len(scanned)} of {len(devices)} devices had "
        "their ports probed across roughly 110 common TCP ports. A device with no "
        "findings is one where nothing risky turned up <em>in what was checked</em> "
        "— it is not a clean bill of health, and a device that was never probed is "
        "reported as <em>not scanned</em> rather than as clear.</p>",
        "</section>",
        "<section class='cards'>",
        _card(str(len(devices)), "Known devices"),
        _card(str(len(online)), "Online now"),
        _card(str(counts.get("risk", 0)), "At risk", "risk"),
        _card(str(counts.get("watch", 0)), "Worth a look", "watch"),
        _card(str(counts.get("unknown", 0)), "Not scanned", "unknown"),
        "</section>",
    ]

    if map_png:
        encoded = base64.b64encode(map_png).decode("ascii")
        parts += [
            "<section class='block'><h2>Network map</h2>",
            f"<img class='map' alt='Network map' src='data:image/png;base64,{encoded}'>",
            "<p class='caption'>The map as it appeared when this report was "
            "generated. Node size reflects role, not traffic — Vantage does not "
            "measure per-device bandwidth.</p></section>",
        ]

    parts += ["<section class='block'><h2>Devices</h2>", _table(devices), "</section>"]

    flagged = [
        (device, findings_by_mac.get(device["mac"], {}))
        for device in devices
        if findings_by_mac.get(device["mac"], {}).get("findings")
    ]
    flagged.sort(key=lambda pair: -(pair[1].get("score") or 0))
    if flagged:
        parts += ["<section class='block'><h2>Findings</h2>"]
        for device, result in flagged:
            parts.append(_findings_block(device, result))
        parts.append("</section>")

    parts += [
        "<footer class='foot'>Generated locally by Vantage. Nothing in this "
        "report left this machine.</footer>",
        "</div></body></html>",
    ]
    return "".join(parts)


def _card(value: str, label: str, band: str | None = None) -> str:
    color = f" style='color:{BAND_COLOR[band]}'" if band else ""
    return (
        f"<div class='card'><div class='card-value'{color}>{_esc(value)}</div>"
        f"<div class='card-label'>{_esc(label)}</div></div>"
    )


def _table(devices: list[dict]) -> str:
    rows = [
        "<table><thead><tr>"
        "<th>Device</th><th>IP</th><th>MAC</th><th>Vendor</th><th>Type</th>"
        "<th>Trust</th><th class='num'>Ports</th><th>Posture</th>"
        "<th>First seen</th><th>Last seen</th>"
        "</tr></thead><tbody>"
    ]
    for device in devices:
        band = device.get("risk_band") or "unknown"
        # The score is only meaningful next to a band that was actually
        # computed; printing "0" for an unscanned device would read as a
        # measured zero.
        score = f" {int(device.get('risk_score') or 0)}" if band in ("risk", "watch") else ""
        status = "online" if device.get("online") else "offline"
        rows.append(
            f"<tr class='is-{status}'>"
            f"<td><span class='dot dot-{status}'></span>{_esc(device.get('name'))}</td>"
            f"<td class='mono'>{_esc(device.get('ip'))}</td>"
            f"<td class='mono'>{_esc(device.get('mac'))}</td>"
            f"<td>{_esc(device.get('vendor'))}</td>"
            f"<td>{_esc(device.get('device_type'))}</td>"
            f"<td>{_esc(device.get('trust_status'))}</td>"
            f"<td class='num'>{_esc(device.get('open_ports', 0))}</td>"
            f"<td><span class='band' style='color:{BAND_COLOR[band]}'>"
            f"{_esc(BAND_LABEL[band])}{html.escape(score)}</span></td>"
            f"<td class='when-cell'>{_esc(_fmt_date(device.get('first_seen')))}</td>"
            f"<td class='when-cell'>{_esc(_fmt_time(device.get('last_seen')))}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "".join(rows)


def _findings_block(device: dict, result: dict) -> str:
    band = result.get("band") or "unknown"
    items = [
        "<div class='finding-group'>",
        f"<h3>{_esc(device.get('name'))} "
        f"<span class='mono muted'>{_esc(device.get('ip'))}</span> "
        f"<span class='band' style='color:{BAND_COLOR[band]}'>"
        f"{_esc(BAND_LABEL[band])} {_esc(result.get('score', 0))}</span></h3>",
    ]
    for finding in result.get("findings", []):
        severity = finding.get("severity", "low")
        port = f" · port {finding['port']}" if finding.get("port") else ""
        items.append(
            "<div class='finding'>"
            f"<div class='sev sev-{_esc(severity)}'>"
            f"{_esc(SEVERITY_LABEL.get(severity, severity))}</div>"
            f"<div><div class='finding-title'>{_esc(finding.get('title'))}"
            f"<span class='muted'>{_esc(port)}</span></div>"
            f"<div class='finding-detail'>{_esc(finding.get('detail'))}</div></div>"
            "</div>"
        )
    items.append("</div>")
    return "".join(items)


_STYLES = """
*{box-sizing:border-box}
body{margin:0;background:#F4F5F8;color:#1E2230;
  font:14px/1.55 "Segoe UI",system-ui,-apple-system,sans-serif}
.page{max-width:1080px;margin:0 auto;padding:40px 36px 64px;background:#fff;
  min-height:100vh;box-shadow:0 1px 40px rgba(20,25,45,.07)}
.mono{font-family:"JetBrains Mono",ui-monospace,Consolas,monospace;font-size:12.5px}
.muted{color:#8A90A2;font-weight:400}
.head{display:flex;align-items:flex-end;justify-content:space-between;
  padding-bottom:18px;border-bottom:2px solid #1E2230}
.brand{font-size:27px;font-weight:700;letter-spacing:-.02em}
.sub{color:#5C6377;font-size:13.5px;margin-top:2px}
.when{color:#8A90A2;font-size:13px}
.method{margin:22px 0 26px;padding:16px 18px;background:#F7F8FB;
  border-left:3px solid #4F6BFF;border-radius:0 10px 10px 0}
.method p{margin:0 0 8px}
.method p:last-child{margin:0}
.method code{background:#EDEFF5;padding:1px 5px;border-radius:4px;font-size:12.5px}
.cards{display:flex;gap:12px;margin-bottom:32px;flex-wrap:wrap}
.card{flex:1 1 150px;padding:14px 16px;border:1px solid #E4E7EF;border-radius:12px}
.card-value{font-size:26px;font-weight:650;letter-spacing:-.02em}
.card-label{color:#6B7286;font-size:12px;margin-top:2px}
.block{margin-bottom:34px;break-inside:avoid}
h2{font-size:17px;margin:0 0 14px;letter-spacing:-.01em}
h3{font-size:14.5px;margin:0 0 10px;display:flex;align-items:baseline;gap:8px;
  flex-wrap:wrap}
.map{width:100%;border:1px solid #E4E7EF;border-radius:12px;display:block}
.caption{color:#8A90A2;font-size:12px;margin:8px 0 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
  color:#8A90A2;font-weight:600;padding:0 10px 8px;border-bottom:1px solid #E4E7EF}
td{padding:9px 10px;border-bottom:1px solid #F0F2F7;vertical-align:top}
tr.is-offline td{color:#8A90A2}
.num{text-align:right}
.when-cell{white-space:nowrap;font-size:12.5px;color:#6B7286}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;
  margin-right:7px;vertical-align:middle;background:#C7CBD8}
.dot-online{background:#2E9E6B}
.band{font-weight:600;font-size:12.5px;white-space:nowrap}
.finding-group{margin-bottom:20px;break-inside:avoid}
.finding{display:grid;grid-template-columns:64px 1fr;gap:12px;padding:9px 0;
  border-top:1px solid #F0F2F7}
.sev{font-size:10.5px;font-weight:700;text-transform:uppercase;
  letter-spacing:.05em;padding-top:2px}
.sev-high{color:#D9534F}.sev-medium{color:#D9922B}.sev-low{color:#8A90A2}
.finding-title{font-weight:600}
.finding-detail{color:#5C6377;font-size:13px;margin-top:2px}
.foot{color:#A0A5B5;font-size:11.5px;border-top:1px solid #E4E7EF;padding-top:14px}
@media print{
  body{background:#fff}
  .page{box-shadow:none;max-width:none;padding:0}
  .block,.finding-group,tr{break-inside:avoid}
}
"""
