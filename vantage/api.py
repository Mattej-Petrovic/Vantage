"""JsApi bridge: what the frontend may call, and how events reach the UI."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import webview

from . import report
from .monitor import Monitor
from .paths import db_path
from .store import Store


class JsApi:
    """Exposed to JavaScript as `window.pywebview.api`."""

    def __init__(self, store: Store, monitor: Monitor):
        self.store = store
        self.monitor = monitor
        self.window = None
        self._maximized = False
        self._lock = threading.Lock()
        # Set by app.py once the tray exists: closing the window hides it
        # instead of quitting when monitoring should continue in the background.
        self.on_close_request = None
        self.on_paused_changed = None
        self.on_ui_ready = None
        self.toast_available = False

    def attach(self, window) -> None:
        self.window = window

    # ---------- push ----------

    def push(self, event: dict) -> None:
        """Send a backend event to the frontend."""
        if not self.window:
            return
        payload = json.dumps(event, default=str)
        # Must end in a JSON-serializable value: pywebview tries to marshal
        # whatever the script evaluates to, and a DOM/native object sends it
        # into a recursive introspection loop.
        script = (
            "(function(){try{window.vantage&&window.vantage.event(%s)}"
            "catch(e){console.error(e)}})(); null;" % payload
        )
        with self._lock:
            try:
                self.window.evaluate_js(script)
            except Exception:
                pass  # window closing mid-scan is not an error worth surfacing

    # ---------- called from JS ----------

    # Window buttons still route through JS when the custom titlebar is enabled.

    def window_minimize(self) -> bool:
        if self.window:
            self.window.minimize()
        return True

    def window_toggle_maximize(self) -> bool:
        if not self.window:
            return False
        if self._maximized:
            self.window.restore()
        else:
            self.window.maximize()
        self._maximized = not self._maximized
        return self._maximized

    def window_close(self) -> bool:
        if self.on_close_request:
            self.on_close_request()
        elif self.window:
            self.window.destroy()
        return True

    def get_initial(self) -> dict:
        return {
            "snapshot": self.monitor.empty_snapshot(),
            "settings": self.store.get_settings(),
            "db_path": str(db_path()),
            # The settings UI must not offer toasts it cannot deliver.
            "toast_available": self.toast_available,
        }

    def ui_ready(self) -> bool:
        if self.on_ui_ready:
            threading.Thread(
                target=self.on_ui_ready,
                name="vantage-ui-ready",
                daemon=True,
            ).start()
        return True

    def get_snapshot(self) -> dict:
        return self.monitor.snapshot()

    def rescan(self) -> bool:
        self.monitor.rescan()
        return True

    def set_paused(self, paused: bool) -> bool:
        self.monitor.set_paused(bool(paused))
        # The tray menu shows a checkmark for the same state; pausing from the
        # window has to move it, or the two disagree about what the app is doing.
        if self.on_paused_changed:
            self.on_paused_changed(bool(paused))
        return True

    def select_interface(self, interface_id: str) -> bool:
        return self.monitor.select_interface(interface_id)

    def rename_device(self, mac: str, name: str) -> bool:
        self.store.set_custom_name(mac, (name or "").strip() or None)
        self.push({"type": "scan_complete", "snapshot": self.monitor.snapshot()})
        return True

    def set_trust(self, mac: str, status: str) -> bool:
        try:
            self.store.set_trust(mac, status)
        except ValueError:
            return False
        return True

    def set_notes(self, mac: str, notes: str) -> bool:
        self.store.set_notes(mac, notes or None)
        return True

    def get_history(self, mac: str, limit: int = 200) -> list[dict]:
        return self.store.history(mac, limit)

    def get_device(self, mac: str) -> dict:
        return {
            "ips": self.store.device_ips(mac),
            "presence": self.store.presence_ratio(mac),
            "history": self.store.history(mac, 200),
            "ports": self.store.device_ports(mac, include_closed=True),
            "risk": self.monitor.risk_for(mac),
            # A week of presence: enough to show a pattern, small enough that
            # the timeline stays readable at a glance.
            "timeline": self.store.presence_segments(mac, int(time.time()) - 7 * 86400),
        }

    def wake(self, mac: str) -> dict:
        """Send a Wake-on-LAN magic packet."""
        return self.monitor.wake(mac)

    def scan_ports(self, mac: str) -> dict:
        """Rescan one device's ports on demand, from the detail panel."""
        return self.monitor.scan_ports_now(mac)

    # ---------- export ----------

    def export_snapshot(self, kind: str, map_png: str | None = None) -> dict:
        """Write the map as PNG, or the full inventory as a standalone report.

        `map_png` is a data URL the frontend produced from the live canvas, so
        the image is exactly what was on screen rather than a re-render that
        might disagree with it.
        """
        image = report.decode_data_url(map_png)
        if kind == "png" and not image:
            return {"ok": False, "error": "The map has not drawn anything yet."}

        stamp = time.strftime("%Y-%m-%d_%H%M", time.localtime())
        suffix = "png" if kind == "png" else "html"
        path = self._ask_save_path(f"vantage-{stamp}.{suffix}", suffix)
        if not path:
            return {"ok": False, "cancelled": True}

        try:
            if kind == "png":
                Path(path).write_bytes(image)
            else:
                snapshot = self.monitor.snapshot()
                devices = snapshot["devices"]
                findings = {d["mac"]: self.monitor.risk_for(d["mac"]) for d in devices}
                html = report.build_html(
                    devices, findings, self.monitor.interface, image
                )
                Path(path).write_text(html, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": str(path)}

    def _ask_save_path(self, filename: str, suffix: str) -> str | None:
        if not self.window:
            return None
        label = "PNG image (*.png)" if suffix == "png" else "HTML report (*.html)"
        # `webview.SAVE_DIALOG` still works but warns on every call; the enum is
        # the supported spelling from pywebview 5 onward.
        save = getattr(webview, "FileDialog", None)
        dialog_type = save.SAVE if save else webview.SAVE_DIALOG
        try:
            result = self.window.create_file_dialog(
                dialog_type,
                directory=str(Path.home() / "Documents"),
                save_filename=filename,
                file_types=(label,),
            )
        except Exception:
            return None
        # pywebview returns a string on some platforms and a one-item sequence
        # on others; both mean the same thing here.
        if isinstance(result, (list, tuple)):
            result = result[0] if result else None
        if not result:
            return None
        # A file_types filter does not guarantee the extension is present — the
        # user can type any name they like into the dialog.
        if not str(result).lower().endswith(f".{suffix}"):
            result = f"{result}.{suffix}"
        return os.fspath(result)

    def get_settings(self) -> dict:
        return self.store.get_settings()

    def set_setting(self, key: str, value) -> bool:
        self.store.set_setting(key, str(value))
        return True

    def get_alerts(self) -> list[dict]:
        return self.store.recent_alerts(100)

    def acknowledge_alerts(self) -> bool:
        self.store.acknowledge_alerts()
        return True
