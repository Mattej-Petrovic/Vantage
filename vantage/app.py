"""Vantage entrypoint: pywebview window + tray + monitor lifecycle.

The window is a view onto a monitor that outlives it. Closing the window hides
it and the scan loop keeps running from the tray; only Quit ends the process.
That is the intended tray behaviour, and it is also the only thing that makes the alerts
worth anything — an alert about a device that joined while the app was shut is
an alert nobody gets.
"""

from __future__ import annotations

import sys
import threading

import webview

from .api import JsApi
from .monitor import Monitor
from .notify import Notifier
from .paths import icon_path, web_path
from .store import Store
from .tray import Tray

WINDOW_TITLE = "Vantage"
MIN_SIZE = (1100, 680)
DEFAULT_SIZE = (1440, 900)
APP_USER_MODEL_ID = "MattejPetrovic.Vantage"


def main() -> int:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_USER_MODEL_ID
            )
        except Exception:
            pass

    store = Store()
    pending: list[dict] = []
    notifier = Notifier(store)
    tray: Tray | None = None
    tray_available = False
    quitting = False
    monitor_started = False
    services_started = False
    services_lock = threading.Lock()
    start_timer: threading.Timer | None = None
    startup_timer: threading.Timer | None = None

    def on_event(event: dict) -> None:
        # Events can fire before the window exists; hold them until it does.
        if api.window:
            api.push(event)
        else:
            pending.append(event)

        message = event.get("message")
        if message:
            type_ = "new_device" if event.get("is_new") else event.get("type")
            notifier.send(type_, message)

        if event.get("type") in ("scan_complete", "status"):
            snapshot_status(event)

    def snapshot_status(event: dict) -> None:
        snapshot = event.get("snapshot")
        if snapshot and tray:
            devices = snapshot.get("devices", [])
            tray.set_status(sum(1 for d in devices if d.get("online")), len(devices))

    monitor = Monitor(store, on_event=on_event)
    api = JsApi(store, monitor)

    window = webview.create_window(
        WINDOW_TITLE,
        str(web_path("index.html")),
        js_api=api,
        width=DEFAULT_SIZE[0],
        height=DEFAULT_SIZE[1],
        min_size=MIN_SIZE,
        background_color="#0E1016",
        text_select=False,
        frameless=False,
        resizable=True,
    )
    api.attach(window)

    # ---------- tray ----------

    def open_window() -> None:
        try:
            window.show()
            window.restore()
        except Exception:
            pass

    def quit_app() -> None:
        nonlocal quitting
        quitting = True
        if tray:
            tray.stop()
        monitor.stop()
        try:
            window.destroy()
        except Exception:
            pass

    def toggle_pause(paused: bool) -> None:
        monitor.set_paused(paused)

    tray = Tray(
        on_open=open_window,
        on_rescan=monitor.rescan,
        on_toggle_pause=toggle_pause,
        on_quit=quit_app,
    )

    def request_close() -> None:
        """Hide to the tray unless the user turned that off."""
        if store.get_setting("close_to_tray", "on") == "on" and tray_available:
            window.hide()
        else:
            quit_app()

    api.on_close_request = request_close
    api.on_paused_changed = lambda paused: tray and tray.set_paused(paused)

    def start_services() -> None:
        nonlocal services_started, tray_available
        with services_lock:
            if services_started:
                return
            services_started = True

        tray_available = tray.start()
        if not tray_available:
            # No tray means no background monitoring, so closing the window has
            # to mean quitting — hiding it would leave a process nobody can
            # reach.
            store.set_setting("close_to_tray", "off")

        on_start()
        # Toast probing imports WinRT plumbing, so keep it off the UI startup
        # path and behind monitor startup. Notification sends still lazy-load
        # it if this has not finished.
        api.toast_available = notifier.available

    api.on_ui_ready = start_services

    def on_start() -> None:
        nonlocal monitor_started, start_timer
        if monitor_started:
            return
        monitor_started = True
        for event in pending:
            api.push(event)
        pending.clear()
        # Let the WebView finish its first paint and become interactive before
        # the first LAN sweep starts doing network I/O.
        start_timer = threading.Timer(6.0, monitor.start)
        start_timer.daemon = True
        start_timer.start()

    def on_closing() -> bool:
        nonlocal quitting
        if quitting:
            return True
        if store.get_setting("close_to_tray", "on") == "on" and tray_available:
            window.hide()
            return False
        quitting = True
        return True

    def on_closed() -> None:
        # Reached when the window is genuinely destroyed, not when hidden.
        if startup_timer:
            startup_timer.cancel()
        if start_timer:
            start_timer.cancel()
        monitor.stop()
        if tray:
            tray.stop()
        store.close()

    window.events.closing += on_closing
    window.events.closed += on_closed

    startup_timer = threading.Timer(12.0, start_services)
    startup_timer.daemon = True
    startup_timer.start()

    icon = icon_path()
    webview.start(debug="--debug" in sys.argv, icon=str(icon) if icon.exists() else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
