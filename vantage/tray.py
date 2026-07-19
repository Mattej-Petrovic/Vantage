"""System tray icon (§5.7): the app keeps watching after the window closes.

This is what turns Vantage from a viewer into a monitor. Closing the window
hides it and the scan loop keeps running; only Quit from this menu actually
stops it. The distinction has to be visible, which is why the tooltip carries
the live device count and the paused state — a tray icon that looks identical
whether it is working or not teaches the user to ignore it.

pystray owns a Win32 message loop, so it runs on its own thread. pywebview must
have the main thread.
"""

from __future__ import annotations

import math
import threading

from PIL import Image, ImageDraw

ACCENT = (79, 107, 255, 255)   # --accent from the design tokens (§6)
MUTED = (120, 130, 160, 255)

_NOTCH = math.radians(322)     # upper right, where the sweep needle exits
_HALF_GAP = math.radians(30)


def _arc(draw, cx, cy, r, start, end, width, fill):
    """Arc with round caps — PIL leaves arc ends square, which looks cheap."""
    draw.arc(
        [cx - r, cy - r, cx + r, cy + r],
        math.degrees(start), math.degrees(end),
        fill=fill, width=width,
    )
    half = width / 2
    for angle in (start, end):
        px, py = cx + r * math.cos(angle), cy + r * math.sin(angle)
        draw.ellipse([px - half, py - half, px + half, py + half], fill=fill)


def _icon_image(paused: bool = False) -> Image.Image:
    """The radar mark, matching the app icon (see tools/make_icon.py).

    Drawn at 8x and reduced, because PIL does not antialias — at tray size the
    difference between this and drawing directly is the difference between a
    crisp mark and a jagged one. Transparent rather than tiled: this sits among
    other tray glyphs on the taskbar, not on the wallpaper.
    """
    size, ss = 32, 8
    n = size * ss
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = MUTED if paused else ACCENT

    cx = cy = n / 2
    scale = n / 24 * 0.92          # fills more of the box than the app icon does
    stroke = max(round(2.3 * scale), 1)

    _arc(draw, cx, cy, 9 * scale,
         _NOTCH + _HALF_GAP, _NOTCH - _HALF_GAP + 2 * math.pi, stroke, color)
    _arc(draw, cx, cy, 5.1 * scale,
         _NOTCH + _HALF_GAP * 1.35, _NOTCH - _HALF_GAP * 1.35 + 2 * math.pi,
         stroke, color)

    reach = 11.2 * scale
    tx, ty = cx + reach * math.cos(_NOTCH), cy + reach * math.sin(_NOTCH)
    draw.line([cx, cy, tx, ty], fill=color, width=stroke)
    tip = stroke / 2
    draw.ellipse([tx - tip, ty - tip, tx + tip, ty + tip], fill=color)

    dot = max(1.7 * scale, stroke * 0.95)
    draw.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=color)

    return image.resize((size, size), Image.LANCZOS)


class Tray:
    """Wraps pystray so the rest of the app never imports it."""

    def __init__(self, *, on_open, on_rescan, on_toggle_pause, on_quit):
        self.on_open = on_open
        self.on_rescan = on_rescan
        self.on_toggle_pause = on_toggle_pause
        self.on_quit = on_quit

        self.paused = False
        self._icon = None
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    def start(self) -> bool:
        """Returns False if the tray is unavailable — the app still works."""
        try:
            import pystray  # noqa: PLC0415 — lazy: pulls in Win32 plumbing
        except Exception as exc:
            self._error = str(exc)
            return False

        menu = pystray.Menu(
            pystray.MenuItem("Open Vantage", self._open, default=True),
            pystray.MenuItem("Scan now", self._rescan),
            pystray.MenuItem(
                "Pause monitoring", self._toggle_pause, checked=lambda _: self.paused
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )
        self._icon = pystray.Icon("vantage", _icon_image(), "Vantage", menu)
        self._thread = threading.Thread(
            target=self._icon.run, name="vantage-tray", daemon=True
        )
        self._thread.start()
        return True

    # pystray hands the icon and the menu item to every callback; the app's own
    # handlers do not care about either.

    def _open(self, *_) -> None:
        self.on_open()

    def _rescan(self, *_) -> None:
        self.on_rescan()

    def _toggle_pause(self, *_) -> None:
        self.paused = not self.paused
        self.on_toggle_pause(self.paused)
        self.refresh()

    def _quit(self, *_) -> None:
        self.on_quit()

    def set_paused(self, paused: bool) -> None:
        """Keep the checkmark true when pausing happened in the window."""
        self.paused = paused
        self.refresh()

    def set_status(self, online: int, total: int) -> None:
        if not self._icon:
            return
        state = "paused" if self.paused else f"{online} of {total} devices online"
        try:
            self._icon.title = f"Vantage — {state}"
        except Exception:
            pass

    def refresh(self) -> None:
        if not self._icon:
            return
        try:
            self._icon.icon = _icon_image(self.paused)
            self._icon.update_menu()
        except Exception:
            pass

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
