"""Scan-loop orchestrator: sweep -> persist -> diff -> emit events."""

from __future__ import annotations

import threading
import time
from typing import Callable

from . import risk
from .actions import wol
from .scanner import identity, interfaces, names, oui, ports, sweep
from .store import Store

# Ordered worst-last, so "did this get worse?" is a comparison and not a table
# of special cases. An unknown device is not better than an ok one — it is
# simply unrated, which is why it sorts below everything.
_BAND_ORDER = ("unknown", "ok", "watch", "risk")

# How long a device's port scan stays fresh, and how many hosts we are willing
# to scan in one cycle. Port scanning is the expensive part of a cycle, so it is
# rationed: over a few minutes every device gets covered without any single
# scan overrunning the interval.
PORT_SCAN_TTL = 900  # 15 minutes
PORT_SCAN_BUDGET = 4

Event = dict
EventSink = Callable[[Event], None]


class Monitor:
    """Runs scan cycles on a background thread and reports what changed."""

    def __init__(self, store: Store, on_event: EventSink | None = None):
        self.store = store
        self.on_event = on_event or (lambda event: None)

        self.interface: dict | None = None
        self.interfaces: list[dict] = []
        self.online: dict[str, dict] = {}  # mac -> live device dict
        self.last_scan_ts: int | None = None
        self.scanning = False
        self.paused = False
        self.error: str | None = None

        # The first scan after launch finds every device "joining", because we
        # started with an empty picture — not because anything moved. That is
        # inventory, exactly like the first scan ever, so it is announced the
        # same way: not at all. A MAC we have genuinely never seen still alerts.
        self._session_baseline = True

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()

        self.refresh_interfaces()

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vantage-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def rescan(self) -> None:
        """Ask the loop to scan right now instead of waiting out the interval."""
        self._wake.set()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if not paused:
            self._wake.set()
        self._emit({"type": "status", "status": self.status()})

    @property
    def interval(self) -> int:
        try:
            return max(10, min(300, int(self.store.get_setting("scan_interval", "30"))))
        except (TypeError, ValueError):
            return 30

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.paused:
                try:
                    self.scan_once()
                except Exception as exc:  # a scan failure must not kill the loop
                    self.error = str(exc)
                    self._emit({"type": "error", "message": str(exc)})
            self._wake.wait(timeout=self.interval)
            self._wake.clear()

    # ---------- interfaces ----------

    def refresh_interfaces(self) -> list[dict]:
        self.interfaces = interfaces.list_interfaces()
        saved = self.store.get_setting("interface_id", "")
        chosen = next((i for i in self.interfaces if i["id"] == saved), None)
        self.interface = chosen or interfaces.pick_default(self.interfaces)
        return self.interfaces

    def select_interface(self, interface_id: str) -> bool:
        match = next((i for i in self.interfaces if i["id"] == interface_id), None)
        if not match:
            return False
        with self._lock:
            self.interface = match
            self.online.clear()
            # Switching subnets rebuilds the picture from nothing, so the next
            # scan is a baseline for the same reason a fresh launch is.
            self._session_baseline = True
        self.store.set_setting("interface_id", interface_id)
        self.rescan()
        return True

    # ---------- scanning ----------

    def scan_once(self) -> dict:
        """One full cycle. Returns the snapshot it produced."""
        if not self.interface:
            self.refresh_interfaces()
        iface = self.interface
        if not iface:
            raise RuntimeError("No active network interface with a default gateway")

        self.scanning = True
        self.error = None
        self._emit({"type": "status", "status": self.status()})
        try:
            hosts = interfaces.hosts_for(iface)
            alive = sweep.sweep_interface(iface, hosts)
            ts = int(time.time())

            local_ips = {i["ip"] for i in self.interfaces}
            local_macs = {i["mac"] for i in self.interfaces if i["mac"]}
            gateway = iface.get("gateway")

            seen: dict[str, dict] = {}
            for ip, entry in alive.items():
                mac = entry.get("mac")
                if not mac:
                    continue  # no layer-2 identity: not a device we can track
                mac = mac.upper()

                is_gateway = ip == gateway
                is_local = ip in local_ips or mac in local_macs
                vendor = oui.lookup(mac)
                device_type = identity.guess_type(
                    vendor, is_gateway=is_gateway, is_local=is_local
                )

                is_new = self.store.upsert_device(
                    mac,
                    ip,
                    vendor,
                    entry.get("rtt_ms"),
                    ts=ts,
                    device_type=device_type,
                )
                seen[mac] = {
                    "mac": mac,
                    "ip": ip,
                    "rtt_ms": entry.get("rtt_ms"),
                    "is_gateway": is_gateway,
                    "is_local": is_local,
                    "is_new": is_new,
                }

            # Everything present on the very first scan is the existing
            # inventory, not an intrusion. Anything appearing after that
            # baseline is what "new" should actually mean.
            first_run = not self.store.get_setting("baseline_ts")
            if first_run:
                self.store.set_setting("baseline_ts", str(ts))

            self._diff(seen, ts, first_run=first_run)
            self.last_scan_ts = ts
        finally:
            self.scanning = False
            self._emit({"type": "status", "status": self.status()})

        # Identity work runs after the map is already live, so a slow mDNS round
        # or port scan never delays what the user sees.
        try:
            self._enrich(seen, ts)
        except Exception as exc:  # enrichment is a bonus, never a scan failure
            self._emit({"type": "error", "message": f"Identity scan failed: {exc}"})
        return self.snapshot()

    # ---------- enrichment (phase 2) ----------

    def _enrich(self, seen: dict[str, dict], ts: int) -> None:
        if not seen:
            return
        stored = {d["mac"]: d for d in self.store.all_devices()}
        changed = self._enrich_names(seen, stored)
        changed |= self._enrich_ports(seen, stored, ts)
        # Re-type last and against freshly read records: a hostname learned in
        # this same cycle has to be able to influence the type, and the port
        # scan will not run again for another TTL to fix it later.
        changed |= self._retype(seen)
        # Risk runs after typing for the same reason typing runs after names:
        # port 80 scores differently on a router than on a laptop, so scoring a
        # device before its type settles would grade it against the wrong rules.
        changed |= self._score_risk(seen, ts)
        if changed:
            self._emit({"type": "scan_complete", "snapshot": self.snapshot()})

    def _retype(self, seen: dict[str, dict]) -> bool:
        fresh = {d["mac"]: d for d in self.store.all_devices()}
        changed = False
        for mac, live in seen.items():
            record = fresh.get(mac) or {}
            open_ports = [p["port"] for p in self.store.device_ports(mac)]
            device_type = identity.guess_type(
                record.get("vendor") or oui.lookup(mac),
                is_gateway=bool(live.get("is_gateway")),
                is_local=bool(live.get("is_local")),
                hostname=record.get("hostname"),
                ports=open_ports,
                model=record.get("model"),
            )
            if device_type != "unknown" and device_type != record.get("device_type"):
                self.store.set_device_type(mac, device_type)
                changed = True
        return changed

    def _score_risk(self, seen: dict[str, dict], ts: int) -> bool:
        """Re-score every seen device and alert when its band gets worse."""
        fresh = {d["mac"]: d for d in self.store.all_devices()}
        changed = False
        for mac, live in seen.items():
            record = fresh.get(mac)
            if not record or not record.get("last_port_scan"):
                continue  # never probed: `unknown`, and nothing to say about it
            result = risk.evaluate(record, self.store.device_ports(mac))
            before = record.get("risk_band")
            if result["band"] == before and result["score"] == (record.get("risk_score") or 0):
                continue

            self.store.set_risk(mac, result["score"], result["band"])
            changed = True

            # Only a worse band is news. A score that ticks up inside the same
            # band is noise, and the first rating of a device is its baseline —
            # the same rule as new devices and new ports.
            if before is None or _BAND_ORDER.index(result["band"]) <= _BAND_ORDER.index(before):
                continue
            name = self.display_name(record, live)
            detail = f"{name}: {risk.summarize(result)}"
            self.store.add_alert("risk_raised", mac, detail, ts)
            self._emit({"type": "risk_raised", "mac": mac, "message": detail,
                        "band": result["band"]})
        return changed

    def wake(self, mac: str) -> dict:
        """Wake-on-LAN. Broadcast on the interface we are actually scanning."""
        return wol.wake(mac, self.interface)

    def risk_for(self, mac: str) -> dict:
        """Full explained score for the detail panel."""
        record = next((d for d in self.store.all_devices() if d["mac"] == mac), None)
        if not record:
            return {"score": 0, "band": "unknown", "findings": []}
        return risk.evaluate(record, self.store.device_ports(mac))

    def _enrich_names(self, seen: dict[str, dict], stored: dict[str, dict]) -> bool:
        """Resolve hostnames for anything that does not have a good one yet."""
        pending = {
            live["ip"]: mac
            for mac, live in seen.items()
            if live.get("ip") and names.needs_name((stored.get(mac) or {}).get("hostname"))
        }
        if not pending:
            return False

        resolved = names.resolve_names(list(pending))
        changed = False
        for ip, info in resolved.items():
            mac = pending.get(ip)
            hostname = info.get("hostname")
            if not mac or not hostname:
                continue
            current = (stored.get(mac) or {}).get("hostname")
            if names.is_better_name(hostname, current):
                self.store.set_hostname(mac, hostname, info.get("source"), info.get("model"))
                changed = True
        return changed

    def _enrich_ports(self, seen: dict[str, dict], stored: dict[str, dict], ts: int) -> bool:
        """Port-scan the devices whose results are missing or stale."""
        if self.store.get_setting("port_scan", "on") != "on":
            return False

        due: list[tuple[str, dict]] = []
        for mac, live in seen.items():
            if not live.get("ip"):
                continue
            last = (stored.get(mac) or {}).get("last_port_scan")
            if last is None or ts - int(last) > PORT_SCAN_TTL:
                # Never-scanned devices first, then the stalest.
                due.append((mac, live))
        if not due:
            return False
        due.sort(key=lambda item: (stored.get(item[0]) or {}).get("last_port_scan") or 0)

        changed = False
        for mac, live in due[:PORT_SCAN_BUDGET]:
            if self._stop.is_set():
                break
            record = stored.get(mac) or {}
            first_scan = record.get("last_port_scan") is None
            results = ports.scan_host(live["ip"])
            newly_open = self.store.record_ports(mac, results, ts=ts)
            changed = True

            # The first port scan of a device is its baseline, exactly like the
            # first network scan: everything is "new" and none of it is news.
            if newly_open and not first_scan:
                name = self.display_name(record, live)
                for result in newly_open:
                    detail = (
                        f"{name} opened port {result['port']}"
                        f" ({result.get('service') or 'unknown'})"
                    )
                    self.store.add_alert("new_port", mac, detail, ts)
                    self._emit({"type": "new_port", "mac": mac, "port": result,
                                "name": name, "message": detail})
        return changed

    def _diff(self, seen: dict[str, dict], ts: int, first_run: bool = False) -> None:
        with self._lock:
            previous = set(self.online)
            current = set(seen)
            joined = current - previous
            left = previous - current
            self.online = seen

        if left:
            self.store.mark_offline(sorted(left), ts=ts)

        devices = {d["mac"]: d for d in self.store.all_devices()}

        session_baseline = self._session_baseline
        self._session_baseline = False

        for mac in sorted(joined):
            device = self._present(mac, devices.get(mac, {}), seen[mac])
            # The baseline scan is inventory, not events — no alert storm on
            # first run.
            is_new = seen[mac]["is_new"] and not first_run
            # On the session's first scan, only a MAC we have never seen is
            # worth reporting. Everything else was already there before Vantage
            # started looking, and saying "came online" about it would be false.
            if session_baseline and not is_new:
                continue
            message = (
                self.describe_new(device) if is_new
                else f"{device['name']} came online"
            )
            if is_new:
                self.store.add_alert(
                    "new_device", mac, f"New device on the network: {message}", ts
                )
            if not first_run:
                self._emit({
                    "type": "device_joined", "device": device,
                    "is_new": is_new, "message": message,
                })

        for mac in sorted(left):
            device = self._present(mac, devices.get(mac, {}), {"mac": mac})
            self._emit({"type": "device_left", "device": device})

        self._emit({"type": "scan_complete", "snapshot": self.snapshot(devices)})

    def scan_ports_now(self, mac: str) -> dict:
        """On-demand port scan for one device (the detail panel's button)."""
        with self._lock:
            live = dict(self.online.get(mac) or {})
        record = next((d for d in self.store.all_devices() if d["mac"] == mac), None)
        ip = live.get("ip") or (record or {}).get("ip")
        if not ip:
            return {"ok": False, "error": "No current IP for this device."}

        ts = int(time.time())
        first_scan = (record or {}).get("last_port_scan") is None
        results = ports.scan_host(ip)
        newly_open = self.store.record_ports(mac, results, ts=ts)

        if newly_open and not first_scan:
            name = self.display_name(record or {}, live)
            for result in newly_open:
                self.store.add_alert(
                    "new_port",
                    mac,
                    f"{name} opened port {result['port']} ({result.get('service') or 'unknown'})",
                    ts,
                )

        self._emit({"type": "scan_complete", "snapshot": self.snapshot()})
        return {"ok": True, "ports": self.store.device_ports(mac, include_closed=True)}

    # ---------- presentation ----------

    @staticmethod
    def describe_new(device: dict) -> str:
        """How a newly-seen device is announced.

        Two things this has to get right. The name falls back to the IP when
        nothing better is known, and pairing that with the IP again produced
        "192.168.1.74 (192.168.1.74)" in the alert list.

        The second is the one that matters. Phones rotate their Wi-Fi MAC on
        purpose, so a device with a randomized address and no vendor is far more
        often your own phone picking a new identity than a stranger. It is still
        worth announcing — we genuinely cannot tell the two apart — but the
        alert should say which situation it is likely to be, or the feature
        cries wolf every time an iPhone reconnects.
        """
        name, ip = device.get("name"), device.get("ip")
        label = name if name and name != ip else (ip or device.get("mac") or "unknown device")
        if name and ip and name != ip:
            label = f"{label} ({ip})"
        # Keyed off the MAC itself, not the vendor string: `oui.describe` never
        # returns empty — it hands back "Randomized MAC" — so testing the vendor
        # for absence silently never fired.
        if device.get("randomized_mac"):
            label += " — private Wi-Fi address, often a phone rotating its MAC"
        return label

    @staticmethod
    def display_name(device: dict, live: dict) -> str:
        return (
            device.get("custom_name")
            or device.get("hostname")
            or device.get("vendor")
            or live.get("ip")
            or device.get("mac")
            or "Unknown device"
        )

    def baseline_ts(self) -> int:
        try:
            return int(self.store.get_setting("baseline_ts") or 0)
        except (TypeError, ValueError):
            return 0

    def _present(
        self, mac: str, device: dict, live: dict, port_counts: dict[str, int] | None = None
    ) -> dict:
        """Merge stored record + live scan state into what the UI renders."""
        baseline = self.baseline_ts()
        first_seen = device.get("first_seen") or 0
        merged = {
            "mac": mac,
            "ip": live.get("ip") or device.get("ip"),
            "vendor": device.get("vendor") or oui.describe(mac),
            "device_type": device.get("device_type") or "unknown",
            "custom_name": device.get("custom_name"),
            "hostname": device.get("hostname"),
            "name_source": device.get("name_source"),
            "model": device.get("model"),
            "open_ports": (port_counts or {}).get(mac, 0),
            "port_scanned": device.get("last_port_scan") is not None,
            "risk_score": device.get("risk_score") or 0,
            # Falls back to `unknown` rather than `ok`: a device we have not
            # probed has not been cleared, it has just not been looked at.
            "risk_band": device.get("risk_band")
            or risk.band_for(0, scanned=device.get("last_port_scan") is not None),
            "trust_status": device.get("trust_status") or "unknown",
            "first_seen": device.get("first_seen"),
            "last_seen": device.get("last_seen"),
            "notes": device.get("notes"),
            "rtt_ms": live.get("rtt_ms") if live.get("rtt_ms") is not None else device.get("rtt_ms"),
            "online": mac in self.online,
            "is_gateway": bool(live.get("is_gateway")),
            "is_local": bool(live.get("is_local")),
            "randomized_mac": oui.is_locally_administered(mac),
            # Appeared after the baseline scan — i.e. genuinely showed up while
            # Vantage was watching.
            "is_new": bool(baseline and first_seen > baseline),
        }
        merged["name"] = self.display_name(device, merged)
        return merged

    def status(self) -> dict:
        return {
            "scanning": self.scanning,
            "paused": self.paused,
            "last_scan_ts": self.last_scan_ts,
            "interval": self.interval,
            "error": self.error,
            "interface": self.interface,
            "interfaces": self.interfaces,
            "online_count": len(self.online),
        }

    def snapshot(self, devices: dict[str, dict] | None = None) -> dict:
        if devices is None:
            devices = {d["mac"]: d for d in self.store.all_devices()}
        with self._lock:
            live = dict(self.online)

        port_counts = self.store.open_port_counts()
        entries = []
        for mac, device in devices.items():
            entries.append(self._present(mac, device, live.get(mac, {}), port_counts))
        # Router first, then online devices, then by name.
        entries.sort(key=lambda d: (not d["is_gateway"], not d["online"], d["name"].lower()))

        return {
            "devices": entries,
            "gateway": self.interface.get("gateway") if self.interface else None,
            "status": self.status(),
            "alerts": self.store.recent_alerts(50),
            "unread_alerts": self.store.unacknowledged_count(),
        }

    def _emit(self, event: Event) -> None:
        try:
            self.on_event(event)
        except Exception:
            pass  # the UI bridge must never break the scan loop
