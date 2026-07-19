# Vantage — Build Specification

> **This is the specification Vantage was built from, preserved as written.**
> It was finished before the first line of code, and it is published unedited
> apart from removing details about my own home network. Where the finished build
> diverged from this document, the code and the README are authoritative and the
> divergence is called out inline — a spec that quietly rewrites itself to match
> the build is worth nothing as a record of what was decided up front.

> **Original framing, kept for context:** this is the single source of truth for
> building Vantage. A fresh session should be able to build the whole app from
> this document cold, without re-deriving decisions. Everything here was decided
> deliberately in a planning session — do not silently re-scope. If something is
> genuinely underspecified, the "Open items" section at the end lists what is
> still open; treat everything else as locked.

---

## 1. What Vantage is (and is not)

**Vantage** is a Windows desktop app that scans your home LAN, identifies every
device on it, and renders it as a **live, animated map** — a force-directed graph
with the router at the center and devices orbiting it, nodes pulsing as they
respond, lighting up when they join, dimming when they leave. Click a device to
see its vendor, MAC, IP, open ports, risk flags, and first-seen/last-seen
history. It watches the network continuously from the system tray and alerts you
when something new or suspicious appears.

The framing is **defensive**: not "draw my network" but "see my network the way a
defender would." The map is the surface; the value is *identity + posture +
change over time*.

### In scope (v1)
- LAN discovery (no admin rights, no Npcap driver needed)
- Device identity: vendor (OUI), device-type inference, mDNS/SSDP/NetBIOS names, custom renaming
- Per-device port scan with service/banner detection
- Security posture: per-device risk score, risky-port flags, **new-port alerts**
- Live force-directed map with honest RTT-driven pulse animation
- Continuous background monitoring from the system tray
- First-seen/last-seen + presence history timeline
- **New-device alerts** (in-app + optional Windows toast, user-configurable)
- Wake-on-LAN (universal action, no router login)
- Snapshot/report export (optional polish)
- ~~**Block** a device — router-specific~~ — dropped, see §8

### Explicitly NOT in scope
- **No ARP spoofing / deauth / MITM.** Those are the offensive path (was "option B").
  Kept out deliberately to keep Vantage an honest view-and-manage tool, not an
  attack tool. Those techniques are worth learning — in a lab built for them, not
  smuggled into a home-network utility.
- **No fake bandwidth.** On a switched network you cannot see per-device byte
  counts without router/SPAN access. Node pulse is **responsiveness (ping RTT)**,
  labelled as activity — never presented as measured throughput.
- **No universal auto-block.** Block was always router-specific and optional. The
  app is fully functional without it — and it shipped without it (§8).
- No cloud, no accounts, no telemetry. Everything is local.

---

## 2. Stack and why

| Layer | Choice | Why |
|---|---|---|
| Backend / logic | **Python 3.12** | Best fit for network work; matches the rest of my toolchain. |
| Discovery | **ICMP via `ctypes` `IcmpSendEcho` + `arp -a`** | No admin rights, **no Npcap**. The exe "just works" on double-click. |
| UI shell | **pywebview** | Native window wrapping an HTML/CSS/JS frontend — beautiful UI, no browser chrome. |
| Frontend | **Vanilla HTML/CSS/JS** (no framework) | The map is a high-frequency canvas/physics animation; a framework's re-renders hurt it. Self-contained, fast, one bundle. |
| Map rendering | **d3-force** for physics + **Canvas** for drawing | d3-force gives the force-directed layout; canvas (not SVG/DOM) keeps 30–60fps with pulses. Bundle d3 locally (no CDN). |
| Storage | **SQLite** (stdlib `sqlite3`) | Local, zero-config, WAL mode. |
| Tray | **pystray** + **Pillow** | System-tray icon, background monitoring, toast triggers. |
| Toasts | **win11toast** (or `windows-toasts`) | Native Windows notifications. |
| ~~Router block (P2)~~ | ~~vendor API client~~ | **Dropped — see §8.** No dependency shipped. |
| Packaging | **PyInstaller** (onefile, windowed) | Single `Vantage.exe`. |

### The no-Npcap discovery decision (important)
We deliberately do **not** use scapy/raw sockets, because those need the Npcap
driver installed — a dependency that breaks "just double-click the exe."

**Discovery mechanism (locked):**
1. Determine the active interface + its subnet (see §5.1).
2. **Liveness sweep:** ping every host in the subnet concurrently using
   `ctypes` → `iphlpapi.IcmpSendEcho` (ICMP echo through the Windows stack, **no
   admin, no Npcap**, fast via a thread pool). Fallback: `subprocess` to `ping`
   if `IcmpSendEcho` is unavailable.
3. **MAC resolution:** the ping populates the OS ARP/neighbor cache; read it via
   `arp -a` (parse) or PowerShell `Get-NetNeighbor`. For hosts that answer ping
   but have no ARP entry yet, force resolution with a throwaway TCP connect to a
   common port (populates ARP even on connection-refused).
4. A device is "present" if it has a MAC in the neighbor table on this subnet.
   IP-only-no-MAC hosts off-subnet (e.g. the WAN) are handled separately.

This is reliable on a LAN, needs no privileges, and ships in one exe.

---

## 3. Architecture

```
vantage/
├── app.py                 # entrypoint: pywebview window + tray lifecycle
├── api.py                 # JsApi bridge: methods the frontend calls; pushes events to JS
├── monitor.py             # scan loop orchestrator; diffs scans → emits events (new device, new port, offline)
├── store.py               # SQLite layer (schema, migrations, queries)
├── risk.py                # per-device risk scoring rules
├── scanner/
│   ├── interfaces.py      # enumerate NICs, pick active one (has default gateway), expose subnet
│   ├── sweep.py           # IcmpSendEcho ping sweep (threaded) + ARP/neighbor table parse
│   ├── ports.py           # threaded TCP connect scan + light banner grab
│   ├── oui.py             # MAC → vendor (bundled IEEE oui.csv)
│   └── identity.py        # mDNS + SSDP (UPnP) + NetBIOS name resolution → friendly names + type hints
│                          #   (built on the stdlib — the planned `zeroconf` dep was not needed)
├── actions/
│   ├── wol.py             # Wake-on-LAN magic packet (universal, no router)
├── web/
│   ├── index.html
│   ├── styles.css         # design tokens + components (see §6)
│   ├── app.js             # app state, panels, settings, event handling
│   ├── map.js             # d3-force + canvas render loop + pulse animation
│   └── vendor/            # d3-force + its 3 deps, bundled locally (no CDN — CSP/offline)
├── data/
│   └── oui.csv            # IEEE OUI database, bundled at build time
├── requirements.txt
├── build.spec             # PyInstaller spec
└── SPEC.md                # this file
```

### Data flow
- `monitor.py` runs a scan cycle every N seconds (configurable, default 30s):
  `interfaces` → `sweep` (who's up + MACs) → for new/changed hosts run `ports` +
  `identity` → `risk` scores it → `store` persists observations → diff vs. last
  state produces **events**.
- Events (`device_joined`, `device_left`, `new_port`, `risk_raised`) go to
  `api.py`, which (a) pushes them to the frontend via
  `window.evaluate_js(...)` for live UI updates and (b) fires Windows toasts if
  the user enabled them.
- The frontend calls backend methods through the pywebview `JsApi` (e.g.
  `get_snapshot()`, `rename_device(mac, name)`, `set_trust(mac, status)`,
  `wake(mac)`, ~~`block(mac)`~~, `get_history(mac)`, `get_settings()`,
  `set_setting(k, v)`, `rescan()`).

---

## 4. Data model (SQLite)

```sql
-- One row per physical device, keyed by MAC (survives IP changes)
CREATE TABLE devices (
  mac          TEXT PRIMARY KEY,
  vendor       TEXT,               -- from OUI
  device_type  TEXT,               -- inferred: phone|laptop|desktop|tv|iot|camera|printer|router|unknown
  custom_name  TEXT,               -- user-set, overrides discovered name in UI
  hostname     TEXT,               -- discovered via mDNS/NetBIOS
  trust_status TEXT DEFAULT 'unknown', -- trusted|unknown|blocked
  first_seen   INTEGER NOT NULL,   -- epoch seconds
  last_seen    INTEGER NOT NULL,
  notes        TEXT
);

-- A device can roam IPs; track current + history
CREATE TABLE device_ips (
  mac        TEXT NOT NULL,
  ip         TEXT NOT NULL,
  last_seen  INTEGER NOT NULL,
  PRIMARY KEY (mac, ip)
);

-- Every scan observation, for presence timeline + honest pulse (RTT)
CREATE TABLE observations (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  mac       TEXT NOT NULL,
  ts        INTEGER NOT NULL,
  ip        TEXT,
  rtt_ms    REAL,                  -- drives pulse speed; NULL if up-but-no-RTT
  online    INTEGER NOT NULL       -- 1/0
);

CREATE TABLE ports (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  mac        TEXT NOT NULL,
  port       INTEGER NOT NULL,
  proto      TEXT DEFAULT 'tcp',
  service    TEXT,                 -- guessed from port + banner
  banner     TEXT,
  status     TEXT DEFAULT 'open',  -- open|closed
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL,
  UNIQUE(mac, port, proto)
);

CREATE TABLE alerts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           INTEGER NOT NULL,
  type         TEXT NOT NULL,      -- new_device|new_port|device_offline|risk_raised
  mac          TEXT,
  detail       TEXT,               -- human-readable
  acknowledged INTEGER DEFAULT 0
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);
```

**Storage location:** `%APPDATA%\Vantage\vantage.db` (create dir on first run).
Never write into the exe's own folder (breaks under Program Files / read-only).

---

## 5. Features in detail

### 5.1 Interface selection (handles ethernet + wifi at once)
A desktop is often on **ethernet and wifi simultaneously**. On startup:
- Enumerate NICs with an IPv4 address and a default gateway.
- Auto-pick the one with the active default route; if both are on the same subnet,
  pick either and de-dupe the local machine (it may appear under two MACs — mark
  both as "This PC").
- Expose a dropdown in the top bar so the user can switch interface/subnet.
- Derive the scan range from the chosen interface's IP + netmask (e.g. /24).

### 5.2 Discovery + liveness
Per §2 no-Npcap mechanism. Default scan interval 30s (setting: 10–300s). The
router node is the device whose IP == the default gateway; render it central and
larger.

### 5.3 Identity (make the map readable, not just dots)
For each device, resolve the best available name and type:
- **OUI → vendor** (bundled IEEE `oui.csv`, lookup by first 3 MAC octets).
- **mDNS/Bonjour** (`zeroconf`), **SSDP/UPnP** (multicast M-SEARCH), **NetBIOS**
  name query → friendly hostnames ("Matte-iPhone", "Living-Room-TV").
- **Type inference** from vendor + open ports + hostname:
  e.g. Espressif/Tuya + few ports → `iot`; open 554/RTSP → `camera`;
  open 631/9100 → `printer`; Apple/Samsung mobile OUI → `phone`; gateway IP →
  `router`. Each type maps to an icon on the map.
- **custom_name** always wins in the UI when set.

### 5.4 Port scan + posture
- Threaded TCP connect scan of a **top-common-ports list** (~100 ports: 21, 22,
  23, 25, 53, 80, 139, 443, 445, 554, 631, 1900, 3389, 8080, 8443, 9100, etc.).
  Full-range is a setting, off by default (slow).
- Light banner grab on connect (read a few bytes) to name the service.
- **Risk scoring (`risk.py`)** — deterministic rules, explainable (no LLM):
  - telnet (23) open → high
  - unauthenticated/plain HTTP admin panel (80/8080) → medium
  - SMB (445/139) exposed → medium
  - UPnP (1900) exposed → low/medium
  - RTSP camera (554) open → medium
  - likely-default-credential IoT vendor + open mgmt port → high
  - Score aggregates to a node color band (ok / watch / risk).
- **New-port alert:** if a device opens a port it didn't have last scan → alert
  (`new_port`). This is the "IoT phoning home / possible compromise" signal.

### 5.5 The live map (maxed — this is the centerpiece)
- **d3-force** layout: gateway node pinned/weighted to center, devices as
  charged nodes with link forces to the router, mild collision so labels don't
  overlap. Rendered on **canvas** in a `requestAnimationFrame` loop.
- **Nodes:** circle + device-type icon + label (custom_name || hostname ||
  vendor || IP). Size hints at role (router largest). Color by trust/risk:
  neutral = known/ok, accent halo = selected, amber = unknown/new, red ring =
  risk.
- **Honest pulse:** each successful poll emits a soft expanding ring from the
  node; **pulse frequency ∝ 1/RTT** (faster responders pulse more lively). This
  is the "living network" effect — driven by real responsiveness, never fake
  bytes.
- **Join/leave:** new device animates in with an amber attention ring; a device
  that goes offline fades to dim/greyed and its edge thins, but stays on the map
  (history matters) until pruned.
- Edges are thin, slightly curved, subtly animated toward the router.
- Interactions: hover → tooltip; click → select + open detail panel; drag to
  reposition (pins the node); scroll to zoom, drag background to pan.
- Fully light/dark aware (colors from CSS tokens read into canvas).

### 5.6 Detail panel (slides in on select)
Shows: editable name, type + icon, trust toggle (trusted/unknown/blocked),
vendor, all IPs, MAC, first-seen, last-seen, total uptime/presence %, open ports
list with per-port risk flags, aggregate risk score with the reasons, and
**actions**: Rename, Wake-on-LAN, mark Trusted/Untrusted, add note. (Block was
specified here too; it is dropped — see §8.)

### 5.7 Monitoring, tray, alerts
- Runs in the **system tray**; closing the window minimizes to tray, monitoring
  continues. Tray menu: Open, Rescan now, Pause monitoring, Quit.
- **Alerts** stored in `alerts` table, shown in an in-app Alerts view + a bell
  badge. **Delivery is a user setting**: `off` / `in-app
  only` / `windows toast` / `both`. Toasts fire for `new_device` and optionally
  `new_port` / `risk_raised`.

### 5.8 History / timeline
Per-device presence timeline from `observations` (joined/left, presence pattern —
"unknown MAC appeared 03:12 Tue, up 40 min"). A global timeline view of network
events. Cheap, high-value, uses data we already store.

### 5.9 Wake-on-LAN (universal action)
Send a WoL magic packet to a device's MAC (UDP broadcast to :9). Works for any
router — no login. Button in the detail panel; only meaningful for wired/WoL-capable
devices but harmless otherwise.

### 5.10 Snapshot export (polish, optional)
Export the current map + device inventory as a PNG and/or a simple PDF/HTML
report — a shareable artifact, not just a screenshot.

---

## 6. Design spec — "Clean modern" (do not invent a different look)

Aesthetic direction: **clean modern**, soft, product-grade — not hacker-cyber.
Light **and** dark, with a toggle; respect OS theme on first run.

### Layout
- **Top bar:** app name/logo (left), interface selector, scan status + last-scan
  time, device count, alerts bell, theme toggle, settings (right).
- **Left sidebar:** searchable/filterable device list (filter by type, trust,
  risk, online/offline). Selecting syncs with the map.
- **Center:** the live map, dominant.
- **Right:** detail panel, slides in when a device is selected, slides out on
  deselect.

### Tokens (define as CSS custom properties; canvas reads them too)
- **Radius:** cards/panels 14–16px, controls 10px.
- **Spacing:** 4px base scale, generous whitespace.
- **Shadows:** soft, low-opacity, layered (not hard borders).
- **Type:** UI = **Inter** (bundle locally; system-ui fallback). Monospace for
  MAC/IP/ports = **JetBrains Mono** / `ui-monospace`. Clear type scale
  (12/14/16/20/28).
- **Color — light:** near-white bg (#FAFAFB), white cards, slate text
  (#1E2230), one accent (indigo/blue ~#4F6BFF). Semantic: ok green, watch amber,
  risk red — desaturated, modern.
- **Color — dark:** deep neutral bg (#0E1016), elevated cards (#171A22), light
  slate text, same accent tuned brighter for dark. Semantic tuned for dark.
- Motion: 150–250ms ease for panels/toggles; the map animates continuously but
  gently. Respect `prefers-reduced-motion` (reduce pulses).

### Quality bar
This must look genuinely polished — it's a portfolio piece. No default-browser
form controls, no unstyled scrollbars, consistent iconography (one icon set,
e.g. Lucide as inlined SVG), aligned grid, real empty/loading states. If unsure,
lean minimal and let the live map be the visual hero.

---

## 7. Build & packaging
- `requirements.txt`: `pywebview`, `pystray`, `Pillow`, `zeroconf`,
  `win11toast` (or `windows-toasts`), a router API client (P2). d3 is bundled as
  a local JS file, not pip.
  > **Diverged in the build:** `zeroconf` was dropped — mDNS/SSDP/NetBIOS
  > discovery is implemented on the standard library, so identity costs no
  > dependency. The router client was dropped with §8. Shipped dependencies are
  > `pywebview`, `pystray`, `Pillow`, `win11toast`.
- **OUI data:** at build time, download IEEE `oui.csv`
  (`https://standards-oui.ieee.org/oui/oui.csv`, ~ a few MB) into `data/` and
  bundle it; ship a small fallback subset if the download fails. Runtime lookup
  is offline.
- **PyInstaller:** onefile, `--windowed` (no console), bundle `web/` and `data/`
  as datas, set app icon. Output: `Vantage.exe`.
- **No admin required** at runtime (that's the whole point of the discovery
  choice) — verify this holds on a clean machine.
- Verify on Windows 11.

---

## 8. Block — OUT OF SCOPE

**Dropped after the mechanism was fully reverse-engineered.** This section is
rewritten rather than deleted, because the reasoning is the useful part, and a
removed section reads like the feature was never considered.

Vantage ships as a visibility tool. Block was always the one feature that would
have made it an intervention tool, and it was always the one feature that could
not be built without the vendor's cooperation. Everything else in this spec works
without admin rights, without a driver, and on any router. This did not.

**What the research established.** I did reverse-engineer it, against the
ISP-supplied router on my own network, by instrumenting that router's own web UI
and recording the request sequence it produced:

- The transport was never the problem — authenticated API access to the router
  was verified working.
- Blocking is not a call. It is a **five-step transaction** over the router's
  parental-control tree — create a time-slot list, create a rule with
  `WANAccess: "DROP"`, enable the service — with no `block(mac)` anywhere.
- The router's own web UI has **three defects** in that sequence: one step fails
  every single time and the UI silently ignores the error, unblocking one device
  switches parental control off *globally*, and rules reference object ids the
  server has not assigned yet.
- **One binding was never resolved:** how a MAC maps to the address-list entry a
  rule points at. That binding decides *which* device loses its connection, so
  shipping without it risked cutting off the wrong device — or every device.

**Why dropped rather than finished.** The remaining unknown was one read away.
But the cost had stopped being code — the code was maybe an hour. The cost was
repeated manual capture against undocumented firmware, forever, because the next
firmware update re-rolls all of it. That is an ongoing tax, not a one-off, and it
buys the single feature that works on *one* router, with an admin password, until
the vendor changes their mind.

Cheap-to-build and cheap-to-own are different questions, and you cannot answer
the second one from outside. So: research until the estimate is real, then let the
estimate decide — including deciding to stop one step from done.

It cost one rewritten spec section and one uninstall, because §8 was specified
behind a capability gate from the start and nothing else depended on it. That was
not luck. Optional features should be built so that "optional" survives contact
with reality.

**Still binding:** no generic block. A universal kick means ARP spoofing or
deauth — the offensive path §2 excludes. Nothing in this section is a licence to
reach for that.

---

## 9. Phasing (suggested build order)
1. **Core discovery + map:** interfaces → sweep → OUI → SQLite → d3-force canvas
   map with live join/leave + pulse. (This alone is a real, demoable product.)
2. **Identity + detail panel:** mDNS/SSDP/NetBIOS, type icons, custom naming,
   port scan, detail panel.
3. **Posture + monitoring:** risk scoring, new-port/new-device alerts, tray +
   background + toast settings, history timeline, Wake-on-LAN.
4. **Polish:** design pass to portfolio grade, snapshot/report export.
5. ~~**Block:** router-specific, behind the capability gate.~~
   **Dropped — see §8.** The product is complete at step 4, and steps 1–4 shipped.

---

## 10. Open items (the only things not locked)
- ~~Exact call sequence for the router block — must be captured from the live
  router (§8). Cannot be known in advance.~~ Captured, then dropped — see §8.
- Final top-ports list contents (start from the ~100 common set above; tune).
- Icon set choice (recommend Lucide, inlined) and the exact accent hue — pick
  within the clean-modern tokens in §6.
- Whether to bundle Inter/JetBrains Mono or fall back to system fonts if size is
  a concern (bundling preferred for consistent look).

Everything else in this document is a locked decision from planning. Build to it.
