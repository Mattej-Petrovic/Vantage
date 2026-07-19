<h1 align="center">Vantage</h1>

<p align="center">
  <strong>See your home network the way a defender would.</strong>
</p>

<p align="center">
  A Windows desktop app that maps every device on your LAN in real time, identifies
  what each one is, scores how exposed it is, and tells you the moment something new
  appears.
</p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-0078D4" />
  <img alt="Python" src="https://img.shields.io/badge/python-3.12-3776AB" />
  <img alt="No admin required" src="https://img.shields.io/badge/admin_rights-not_required-0E9F6E" />
  <img alt="No driver" src="https://img.shields.io/badge/npcap-not_required-0E9F6E" />
  <img alt="Offline" src="https://img.shields.io/badge/cloud-none-0E9F6E" />
  <img alt="License" src="https://img.shields.io/badge/license-MIT-555" />
</p>

<!--
  SCREENSHOTS — add these and delete this comment block to make them render:
    docs/map-dark.png     the live map, dark theme, a populated network
    docs/detail.png       a device selected, detail panel open with ports + risk
    docs/report.png       the exported HTML report
  Then uncomment:

  <p align="center">
    <img src="docs/map-dark.png" width="880" alt="The live network map" />
  </p>
  <p align="center">
    <img src="docs/detail.png" width="440" alt="Device detail panel" />
    <img src="docs/report.png" width="440" alt="Exported report" />
  </p>
-->

---

## The problem

Your router already has a device list. It is a table of MAC addresses behind a login
you visit twice a year, and it answers exactly one question: *what is connected right
now?*

It does not tell you that the "unknown" device in row nine is a smart plug. It does
not tell you that your printer has been serving an unauthenticated admin page over
plain HTTP for eight months. It does not tell you that something joined at 03:12 last
Tuesday and left forty minutes later. And it will never tell you when any of that
changes, because nobody is watching.

Vantage watches. It runs in the tray, sweeps the subnet on a schedule, and treats your
network as something with a *history* rather than a snapshot.

## What it does

**Maps the network, live.** A force-directed graph with the router at the centre and
every device orbiting it. Devices fade in when they join, dim when they leave, and
pulse in time with how fast they actually answer.

**Tells you what things are.** Vendor from the MAC address, hostnames from mDNS, SSDP
and NetBIOS, device type inferred from all of it plus the ports that are open. A wall
of hex becomes "Living-Room-TV", "HP LaserJet", "unrecognised device, 2 ports open".

**Scores exposure, and explains itself.** 111 common TCP ports per device, banner
grabbed where possible, then deterministic rules — no black box, no model. Every score
comes with the sentence that produced it: *"VNC is frequently left with no password or
a shared one."* You can argue with a rule. That is the point.

**Notices change.** New device on the network. A device that opened a port it did not
have last scan — the signal that an IoT box started phoning home, or that something is
wrong. Alerts land in-app, as Windows toasts, or both. You choose.

**Remembers.** First seen, last seen, a presence timeline per device. The question is
rarely "who is on my network" and usually "who *was*".

**Exports.** One self-contained HTML report — inventory, posture, and the map — that
you can send to someone who does not have the app.

**Wake-on-LAN.** Works on any router, no login required.

## Design decisions

The interesting part of this project is not what it does. It is what it refuses to do,
and why.

<table>
<tr><td width="30%"><strong>No admin rights.<br/>No Npcap driver.</strong></td>
<td>The obvious way to build this is scapy and raw sockets. That needs a packet-capture
driver installed, and now the "just double-click it" tool has an installer, a reboot,
and an antivirus warning. So discovery runs on ICMP through the Windows stack via
<code>ctypes</code>, with the ARP table read back for MACs. It costs a little
completeness — a device that sleeps through a sweep can be missed. That trade is worth
it. A security tool nobody can be bothered to install protects nobody.</td></tr>

<tr><td><strong>No fake bandwidth.</strong></td>
<td>Every network visualiser shows fat animated pipes with bytes flowing through them.
On a switched network that data does not exist without router or SPAN access — so those
tools are, without exception, showing you an animation. Vantage pulses each node at a
rate driven by real ping RTT, and calls it <em>responsiveness</em>, never throughput.
It is a less impressive demo. It is not a lie.</td></tr>

<tr><td><strong>No ARP spoofing.<br/>No deauth.</strong></td>
<td>There was a version of this that could kick any device off any network without
touching the router. That version is an attack tool wearing a dashboard. Those
techniques are worth learning — in a lab built for them, not smuggled into something
that looks like a home utility.</td></tr>

<tr><td><strong>No cloud. No account.<br/>No telemetry.</strong></td>
<td>A complete inventory of every device in your home, with open ports and weak points
listed, is precisely the document you would least like to upload anywhere. It stays in
a SQLite file in your <code>%APPDATA%</code>. There is no server to opt out of.</td></tr>
</table>

### The feature I researched, finished, and then deleted

Vantage was specified with one more feature: **block a device**, by driving the
router's own parental-control API.

I reverse-engineered it. I instrumented the router's web interface, captured the real
request sequence, and found that blocking is not a call you make — it is a five-step
transaction. I also found three defects in the vendor's own UI along the way: one step
fails on every single run and the interface silently swallows the error, unblocking one
device switches parental control off for *every* device, and rules reference object ids
the server has not assigned yet.

One unknown was left, and it was one request away from solved.

I dropped the feature.

Not because it was hard — because once I could finally price it honestly, the price was
not the code. The code was about an hour. The price was re-capturing an undocumented
protocol by hand every time the vendor ships firmware, forever, to buy one feature that
works on *one* router, needs an admin password, and turns a visibility tool into an
intervention tool.

Cheap to build and cheap to own are different questions, and you cannot answer the
second one from the outside. Dropping it early, when it merely *looked* messy, would
have been right by accident — I would never have known about the three defects. Building
it early, on the assumption it was a simple flag, would have shipped something that cuts
off the wrong device. The research is not wasted when the answer turns out to be no. The
research is what makes the no trustworthy.

Removing it cost one rewritten spec section and one uninstall, because it had been
designed behind a capability gate from the start and nothing else depended on it. That
was not luck — that is what "optional" is supposed to mean.

The full reasoning is preserved in [`SPEC.md` §8](SPEC.md), rewritten rather than
deleted. A section that is deleted reads like it was never considered.

## Honest limitations

- **No per-device traffic volume.** See above — it is not measurable from a normal
  machine on a switched network, so it is not shown.
- **Discovery is best-effort.** A sleeping device, or one that drops all ICMP, can be
  missed in a sweep. Your router would still list it. This is the deliberate cost of
  requiring no driver and no privileges.
- **Port scanning is a scan.** It is aimed at your own network by default and nowhere
  else. Point it at a network you do not own and that is on you, not the tool.
- **Windows only.** The discovery path is built on Windows APIs directly.

## Install

Download `Vantage.exe` from [Releases](../../releases), double-click it. No installer,
no admin prompt, no driver.

<details>
<summary>Or run from source</summary>

```bash
git clone https://github.com/Mattej-Petrovic/Vantage.git
cd Vantage
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python tools/fetch_oui.py        # downloads the IEEE vendor database
python run.py
```

Requires Python 3.12+ on Windows 10 or 11.
</details>

## How it is built

Python 3.12 backend, a native window via pywebview, and a vanilla HTML/CSS/JS frontend
with the map rendered on canvas over a `d3-force` simulation — no framework, because the
map is a 60fps physics animation and a render loop fighting a virtual DOM is a fight it
does not need. State lives in SQLite. Four runtime dependencies. d3 and the fonts are
vendored locally, so the app never makes an outbound request.

The full architecture, data model, and the reasoning behind each choice is in
[`SPEC.md`](SPEC.md) — written before the first line of code and published as it was
written, with the places the build diverged marked inline.

## License

MIT — see [LICENSE](LICENSE).
