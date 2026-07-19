"""Generate docs/vantage.ico — the app icon Windows shows in the taskbar.

Run: python tools/make_icon.py

The mark is the same radar used in the title bar (web/icons.js): two open arcs,
a sweep line, a centre dot. It is drawn on a filled indigo tile rather than as
bare line art, because a taskbar icon is composited against wallpaper at 16px —
thin strokes on transparency disappear there, a coloured tile stays findable.

Every size is rendered from scratch at 8x and downsampled, rather than resizing
one master. Downsampling a 256px master to 16px thins the strokes into grey mush;
re-drawing lets the small sizes carry proportionally heavier strokes and survive.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "vantage.ico"

# --accent from the design tokens (§6), with a slight vertical fall for depth.
TOP = (92, 119, 255)
BOTTOM = (59, 79, 224)
MARK = (255, 255, 255, 255)

# Windows picks the closest size; supplying the small ones explicitly stops it
# from producing its own bad downscale of the 256.
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

SS = 8  # supersample factor


def _arc_with_caps(draw, cx, cy, r, start, end, width, fill):
    """PIL arcs have flat ends; stamp circles on the endpoints for round caps."""
    draw.arc(
        [cx - r, cy - r, cx + r, cy + r],
        math.degrees(start),
        math.degrees(end),
        fill=fill,
        width=width,
    )
    half = width / 2
    for angle in (start, end):
        px = cx + r * math.cos(angle)
        py = cy + r * math.sin(angle)
        draw.ellipse([px - half, py - half, px + half, py + half], fill=fill)


def _tile(size: int) -> Image.Image:
    """One icon at one size, drawn large and reduced."""
    n = size * SS
    tile = Image.new("RGBA", (n, n), (0, 0, 0, 0))

    # Vertical gradient, clipped to a rounded square.
    gradient = Image.new("RGBA", (1, n))
    for y in range(n):
        t = y / max(n - 1, 1)
        gradient.putpixel(
            (0, y),
            (
                round(TOP[0] + (BOTTOM[0] - TOP[0]) * t),
                round(TOP[1] + (BOTTOM[1] - TOP[1]) * t),
                round(TOP[2] + (BOTTOM[2] - TOP[2]) * t),
                255,
            ),
        )
    gradient = gradient.resize((n, n))

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, n - 1, n - 1], radius=round(n * 0.215), fill=255
    )
    tile.paste(gradient, (0, 0), mask)

    # The radar mark. The title-bar version leaves its gap at the top, which is
    # fine inline at 24px but reads as a letter U once it is alone on a tile —
    # so here the rings are nearly closed and the notch sits at the upper right,
    # where the sweep needle exits. That is what makes it read as a radar.
    draw = ImageDraw.Draw(tile)
    scale = n / 24 * 0.66
    cx = cy = n / 2

    # Small sizes need proportionally heavier strokes to survive downsampling.
    weight = 2.6 if size <= 20 else (2.3 if size <= 32 else 2.0)
    stroke = max(round(weight * scale), 1)

    notch = math.radians(322)          # upper right, where the needle points
    half_gap = math.radians(30)

    _arc_with_caps(
        draw, cx, cy, 9 * scale,
        notch + half_gap, notch - half_gap + 2 * math.pi,
        stroke, MARK,
    )
    # Below ~20px the inner ring collapses into the outer one; dropping it keeps
    # the small icon clean instead of muddy.
    if size > 20:
        _arc_with_caps(
            draw, cx, cy, 5.1 * scale,
            notch + half_gap * 1.35, notch - half_gap * 1.35 + 2 * math.pi,
            stroke, MARK,
        )

    # Sweep needle, from the centre out through the notch.
    reach = 11.2 * scale
    draw.line(
        [cx, cy, cx + reach * math.cos(notch), cy + reach * math.sin(notch)],
        fill=MARK,
        width=stroke,
    )
    tip = stroke / 2
    tx, ty = cx + reach * math.cos(notch), cy + reach * math.sin(notch)
    draw.ellipse([tx - tip, ty - tip, tx + tip, ty + tip], fill=MARK)

    dot = max(1.7 * scale, stroke * 0.95)
    draw.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=MARK)

    return tile.resize((size, size), Image.LANCZOS)


def _write_ico(frames: list[Image.Image], path: Path) -> None:
    """Assemble the .ico by hand.

    Pillow's ICO writer takes one image and derives the other sizes from it,
    which would throw away the individually drawn frames above — the entire
    reason the small sizes look sharp. So the container is written directly.
    Each frame is stored as PNG, which every Windows since Vista reads.
    """
    import struct
    from io import BytesIO

    blobs = []
    for frame in frames:
        buffer = BytesIO()
        frame.save(buffer, format="PNG")
        blobs.append(buffer.getvalue())

    header = struct.pack("<HHH", 0, 1, len(frames))          # reserved, type=icon, count
    offset = len(header) + 16 * len(frames)                   # data starts after the directory

    directory = b""
    for frame, blob in zip(frames, blobs):
        w, h = frame.size
        directory += struct.pack(
            "<BBBBHHII",
            0 if w >= 256 else w,   # 0 means 256 in this format
            0 if h >= 256 else h,
            0,                      # palette size, 0 for truecolour
            0,                      # reserved
            1,                      # colour planes
            32,                     # bits per pixel
            len(blob),
            offset,
        )
        offset += len(blob)

    path.write_bytes(header + directory + b"".join(blobs))


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [_tile(s) for s in SIZES]
    _write_ico(frames, OUT)
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size:,} bytes, "
          f"{len(SIZES)} sizes: {', '.join(str(s) for s in SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
