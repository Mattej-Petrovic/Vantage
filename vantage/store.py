"""SQLite persistence — app schema, WAL mode, thread-safe.

Phase 1 writes devices / device_ips / observations / settings; ports and alerts
are created now so later phases need no migration.
"""

from __future__ import annotations

import sqlite3
import threading
import time

from .paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  mac          TEXT PRIMARY KEY,
  vendor       TEXT,
  device_type  TEXT,
  custom_name  TEXT,
  hostname     TEXT,
  trust_status TEXT DEFAULT 'unknown',
  first_seen   INTEGER NOT NULL,
  last_seen    INTEGER NOT NULL,
  notes        TEXT
);

CREATE TABLE IF NOT EXISTS device_ips (
  mac        TEXT NOT NULL,
  ip         TEXT NOT NULL,
  last_seen  INTEGER NOT NULL,
  PRIMARY KEY (mac, ip)
);

CREATE TABLE IF NOT EXISTS observations (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  mac       TEXT NOT NULL,
  ts        INTEGER NOT NULL,
  ip        TEXT,
  rtt_ms    REAL,
  online    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ports (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  mac        TEXT NOT NULL,
  port       INTEGER NOT NULL,
  proto      TEXT DEFAULT 'tcp',
  service    TEXT,
  banner     TEXT,
  status     TEXT DEFAULT 'open',
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL,
  UNIQUE(mac, port, proto)
);

CREATE TABLE IF NOT EXISTS alerts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           INTEGER NOT NULL,
  type         TEXT NOT NULL,
  mac          TEXT,
  detail       TEXT,
  acknowledged INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_mac_ts ON observations(mac, ts);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
"""

DEFAULT_SETTINGS = {
    "scan_interval": "30",
    "theme": "system",
    "interface_id": "",
    "alert_delivery": "in_app",
    "port_scan": "on",
    # Toasts beyond new devices are opt-in: they fire often enough that leaving
    # them on by default would train the user to dismiss all of them.
    "toast_new_port": "off",
    "toast_risk_raised": "off",
    "close_to_tray": "on",
}


class Store:
    """One connection guarded by a lock — scans run on a background thread."""

    def __init__(self, path=None):
        self.path = str(path or db_path())
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._migrate()
        self._seed_settings()

    def _migrate(self) -> None:
        """Additive column migrations. A Phase 1 database must keep working."""
        with self._lock:
            existing = {
                row["name"] for row in self._conn.execute("PRAGMA table_info(devices)")
            }
            for column, ddl in (
                ("last_port_scan", "INTEGER"),
                ("model", "TEXT"),
                ("name_source", "TEXT"),
                ("risk_score", "INTEGER"),
                ("risk_band", "TEXT"),
            ):
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE devices ADD COLUMN {column} {ddl}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- settings ----------

    def _seed_settings(self) -> None:
        with self._lock:
            for key, value in DEFAULT_SETTINGS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
                )
            self._conn.commit()

    def get_settings(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    # ---------- devices ----------

    def upsert_device(
        self,
        mac: str,
        ip: str | None,
        vendor: str | None,
        rtt_ms: float | None,
        ts: int | None = None,
        device_type: str | None = None,
        hostname: str | None = None,
    ) -> bool:
        """Record a sighting. Returns True if this MAC was never seen before."""
        ts = ts or int(time.time())
        with self._lock:
            row = self._conn.execute(
                "SELECT mac FROM devices WHERE mac = ?", (mac,)
            ).fetchone()
            is_new = row is None

            if is_new:
                self._conn.execute(
                    "INSERT INTO devices(mac, vendor, device_type, hostname, "
                    "first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                    (mac, vendor, device_type, hostname, ts, ts),
                )
            else:
                self._conn.execute(
                    "UPDATE devices SET last_seen = ?, "
                    "vendor = COALESCE(vendor, ?), "
                    "device_type = COALESCE(?, device_type), "
                    "hostname = COALESCE(?, hostname) WHERE mac = ?",
                    (ts, vendor, device_type, hostname, mac),
                )

            if ip:
                self._conn.execute(
                    "INSERT INTO device_ips(mac, ip, last_seen) VALUES (?, ?, ?) "
                    "ON CONFLICT(mac, ip) DO UPDATE SET last_seen = excluded.last_seen",
                    (mac, ip, ts),
                )

            self._conn.execute(
                "INSERT INTO observations(mac, ts, ip, rtt_ms, online) VALUES (?, ?, ?, ?, 1)",
                (mac, ts, ip, rtt_ms),
            )
            self._conn.commit()
        return is_new

    def mark_offline(self, macs: list[str], ts: int | None = None) -> None:
        if not macs:
            return
        ts = ts or int(time.time())
        with self._lock:
            self._conn.executemany(
                "INSERT INTO observations(mac, ts, ip, rtt_ms, online) VALUES (?, ?, NULL, NULL, 0)",
                [(mac, ts) for mac in macs],
            )
            self._conn.commit()

    def set_custom_name(self, mac: str, name: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE devices SET custom_name = ? WHERE mac = ?", (name or None, mac)
            )
            self._conn.commit()

    def set_trust(self, mac: str, status: str) -> None:
        if status not in ("trusted", "unknown", "blocked"):
            raise ValueError(f"invalid trust status: {status}")
        with self._lock:
            self._conn.execute(
                "UPDATE devices SET trust_status = ? WHERE mac = ?", (status, mac)
            )
            self._conn.commit()

    def set_notes(self, mac: str, notes: str | None) -> None:
        with self._lock:
            self._conn.execute("UPDATE devices SET notes = ? WHERE mac = ?", (notes, mac))
            self._conn.commit()

    def set_hostname(
        self, mac: str, hostname: str, source: str | None = None, model: str | None = None
    ) -> None:
        """Unconditional write — the caller decides whether it is an upgrade."""
        with self._lock:
            self._conn.execute(
                "UPDATE devices SET hostname = ?, name_source = ?, "
                "model = COALESCE(?, model) WHERE mac = ?",
                (hostname, source, model, mac),
            )
            self._conn.commit()

    def set_device_type(self, mac: str, device_type: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE devices SET device_type = ? WHERE mac = ?", (device_type, mac)
            )
            self._conn.commit()

    def set_risk(self, mac: str, score: int, band: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE devices SET risk_score = ?, risk_band = ? WHERE mac = ?",
                (int(score), band, mac),
            )
            self._conn.commit()

    # ---------- ports ----------

    def record_ports(self, mac: str, results: list[dict], ts: int | None = None) -> list[dict]:
        """Persist a port-scan result. Returns the ports that were not open before.

        Ports that have disappeared are marked closed rather than deleted — the
        history of what a device *used* to expose is the interesting part.
        """
        ts = ts or int(time.time())
        with self._lock:
            previous = {
                row["port"]: dict(row)
                for row in self._conn.execute(
                    "SELECT port, proto, status FROM ports WHERE mac = ? AND proto = 'tcp'",
                    (mac,),
                )
            }
            newly_open: list[dict] = []
            seen: set[int] = set()

            for result in results:
                port = int(result["port"])
                seen.add(port)
                before = previous.get(port)
                if before is None or before["status"] != "open":
                    newly_open.append(result)
                self._conn.execute(
                    "INSERT INTO ports(mac, port, proto, service, banner, status, "
                    "first_seen, last_seen) VALUES (?, ?, 'tcp', ?, ?, 'open', ?, ?) "
                    "ON CONFLICT(mac, port, proto) DO UPDATE SET "
                    "  service = excluded.service, "
                    "  banner  = COALESCE(excluded.banner, ports.banner), "
                    "  status  = 'open', last_seen = excluded.last_seen",
                    (mac, port, result.get("service"), result.get("banner"), ts, ts),
                )

            closed = [p for p, row in previous.items() if p not in seen and row["status"] == "open"]
            if closed:
                self._conn.executemany(
                    "UPDATE ports SET status = 'closed', last_seen = ? "
                    "WHERE mac = ? AND port = ? AND proto = 'tcp'",
                    [(ts, mac, port) for port in closed],
                )

            self._conn.execute(
                "UPDATE devices SET last_port_scan = ? WHERE mac = ?", (ts, mac)
            )
            self._conn.commit()
        return newly_open

    def device_ports(self, mac: str, include_closed: bool = False) -> list[dict]:
        query = "SELECT * FROM ports WHERE mac = ?"
        if not include_closed:
            query += " AND status = 'open'"
        query += " ORDER BY port ASC"
        with self._lock:
            rows = self._conn.execute(query, (mac,)).fetchall()
        return [dict(r) for r in rows]

    def open_port_counts(self) -> dict[str, int]:
        """{mac: open port count} — cheap enough to fold into every snapshot."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT mac, COUNT(*) AS n FROM ports WHERE status = 'open' GROUP BY mac"
            ).fetchall()
        return {r["mac"]: int(r["n"]) for r in rows}

    def all_devices(self) -> list[dict]:
        """Every known device with its current IP and latest RTT."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT d.*,
                       (SELECT ip FROM device_ips i WHERE i.mac = d.mac
                         ORDER BY i.last_seen DESC LIMIT 1) AS ip,
                       (SELECT rtt_ms FROM observations o WHERE o.mac = d.mac
                         AND o.online = 1 ORDER BY o.ts DESC LIMIT 1) AS rtt_ms
                FROM devices d ORDER BY d.first_seen ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def device_ips(self, mac: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ip, last_seen FROM device_ips WHERE mac = ? ORDER BY last_seen DESC",
                (mac,),
            ).fetchall()
        return [dict(r) for r in rows]

    def history(self, mac: str, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, ip, rtt_ms, online FROM observations WHERE mac = ? "
                "ORDER BY ts DESC LIMIT ?",
                (mac, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def presence_segments(self, mac: str, since: int | None = None) -> list[dict]:
        """Collapse observations into up/down runs for the timeline.

        Observations are one row per scan, so a device that was online all day
        is hundreds of rows saying the same thing. What the user wants to read
        is "up 09:12 → 17:40", so runs of the same state are merged here rather
        than in the frontend, which would otherwise ship the raw rows across the
        bridge just to throw most of them away.
        """
        since = since or 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, online FROM observations WHERE mac = ? AND ts >= ? "
                "ORDER BY ts ASC",
                (mac, since),
            ).fetchall()
        if not rows:
            return []

        segments: list[dict] = []
        for row in rows:
            online = bool(row["online"])
            if segments and segments[-1]["online"] == online:
                segments[-1]["end"] = row["ts"]
            else:
                segments.append({"online": online, "start": row["ts"], "end": row["ts"]})
        return segments

    def presence_ratio(self, mac: str) -> float:
        """Share of observations where the device was online (0..1)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT AVG(online) AS ratio FROM observations WHERE mac = ?", (mac,)
            ).fetchone()
        return float(row["ratio"] or 0.0)

    # ---------- alerts ----------

    def add_alert(self, type_: str, mac: str | None, detail: str, ts: int | None = None) -> int:
        ts = ts or int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO alerts(ts, type, mac, detail) VALUES (?, ?, ?, ?)",
                (ts, type_, mac, detail),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent_alerts(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def unacknowledged_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM alerts WHERE acknowledged = 0"
            ).fetchone()
        return int(row["n"])

    def acknowledge_alerts(self) -> None:
        with self._lock:
            self._conn.execute("UPDATE alerts SET acknowledged = 1 WHERE acknowledged = 0")
            self._conn.commit()
