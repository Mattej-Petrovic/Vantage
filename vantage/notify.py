"""Windows toast delivery, gated by the user's alert_delivery setting.

Delivery is a setting because the alerts that matter are rare and the ones that
don't are constant: a phone rejoining the Wi-Fi is not worth a notification, and
an app that cries wolf gets muted entirely. Only events the user opted into ever
leave the window.

Toasts are best-effort. `win11toast` reaches into WinRT, which can fail on a
locked session, inside a packaged exe, or when Focus Assist is on — and none of
those are worth surfacing as an application error.
"""

from __future__ import annotations

import threading

APP_ID = "Vantage"

# What each delivery mode is allowed to raise a toast for. `new_device` is the
# only one on by default: it is the event that means something is on your
# network that was not before.
_TOAST_TYPES = {
    "off": frozenset(),
    "in_app": frozenset(),
    "toast": frozenset({"new_device"}),
    "both": frozenset({"new_device"}),
}

_TITLES = {
    "new_device": "New device on your network",
    "new_port": "A device opened a port",
    "risk_raised": "Risk level increased",
    "device_offline": "Device went offline",
}


class Notifier:
    """Fires Windows toasts on a worker thread, honouring the settings."""

    def __init__(self, store):
        self.store = store
        self._toast = None
        self._unavailable: str | None = None

    def _loaded(self):
        """Import win11toast lazily — it pulls in WinRT and is slow to load."""
        if self._toast is None and self._unavailable is None:
            try:
                from win11toast import notify  # noqa: PLC0415 — deliberate lazy import

                self._toast = notify
            except Exception as exc:
                self._unavailable = str(exc)
        return self._toast

    def enabled_types(self) -> frozenset[str]:
        mode = self.store.get_setting("alert_delivery", "in_app") or "in_app"
        allowed = set(_TOAST_TYPES.get(mode, frozenset()))
        # The extra event types are opt-in on top of the mode, so a user who
        # wants port changes announced can have them without also opting into
        # every future alert type we add.
        if allowed:
            for extra in ("new_port", "risk_raised"):
                if self.store.get_setting(f"toast_{extra}", "off") == "on":
                    allowed.add(extra)
        return frozenset(allowed)

    def wants(self, type_: str) -> bool:
        return type_ in self.enabled_types()

    def send(self, type_: str, message: str) -> None:
        if not self.wants(type_):
            return
        notify = self._loaded()
        if not notify:
            return
        title = _TITLES.get(type_, "Vantage")

        def run() -> None:
            try:
                notify(title, message, app_id=APP_ID, duration="short")
            except Exception as exc:  # a failed toast must never touch the scan
                self._unavailable = str(exc)

        # win11toast blocks while the notification is on screen; the scan thread
        # cannot afford to wait several seconds for it.
        threading.Thread(target=run, name="vantage-toast", daemon=True).start()

    @property
    def available(self) -> bool:
        return self._loaded() is not None
