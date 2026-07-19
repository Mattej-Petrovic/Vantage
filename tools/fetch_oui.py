"""Fetch the IEEE OUI database into vantage/data/ (SPEC §7).

Run once after cloning, and again before packaging:

    python tools/fetch_oui.py

Without it Vantage still runs — oui.py falls back to a small built-in vendor
subset — but most devices will show "Unknown vendor".
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

URL = "https://standards-oui.ieee.org/oui/oui.csv"
TARGET = Path(__file__).resolve().parent.parent / "vantage" / "data" / "oui.csv"


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {URL}")
    try:
        with urllib.request.urlopen(URL, timeout=60) as response:
            data = response.read()
    except OSError as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        print("Vantage will fall back to its built-in vendor subset.", file=sys.stderr)
        return 1

    if len(data) < 100_000 or b"Organization Name" not in data[:200]:
        print("Downloaded file does not look like the OUI CSV; keeping the old one.", file=sys.stderr)
        return 1

    TARGET.write_bytes(data)
    lines = data.count(b"\n")
    print(f"Wrote {TARGET} ({len(data) / 1_048_576:.1f} MB, {lines:,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
