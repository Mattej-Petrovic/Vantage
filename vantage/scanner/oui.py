"""MAC -> vendor, from the bundled IEEE OUI database. Fully offline."""

from __future__ import annotations

import csv
import threading
from pathlib import Path

from ..paths import data_path

_lock = threading.Lock()
_table: dict[str, str] | None = None

# Enough to keep the map readable if the bundled CSV is ever missing.
_FALLBACK = {
    "20:9A:7D": "Sagemcom Broadband SAS",
    "98:EE:CB": "Rivet Networks (Killer)",
    "00:1A:11": "Google, Inc.",
    "3C:5A:B4": "Google, Inc.",
    "F4:F5:D8": "Google, Inc.",
    "B8:27:EB": "Raspberry Pi Foundation",
    "DC:A6:32": "Raspberry Pi Trading Ltd",
    "E4:5F:01": "Raspberry Pi Trading Ltd",
    "AC:DE:48": "Apple, Inc.",
    "F0:18:98": "Apple, Inc.",
    "A4:83:E7": "Apple, Inc.",
    "5C:F9:38": "Apple, Inc.",
    "00:1E:C2": "Apple, Inc.",
    "24:F5:AA": "Samsung Electronics",
    "8C:77:12": "Samsung Electronics",
    "B0:BE:83": "Samsung Electronics",
    "50:32:37": "Intel Corporate",
    "94:E6:F7": "Intel Corporate",
    "A4:C3:F0": "Intel Corporate",
    "24:6F:28": "Espressif Inc.",
    "8C:AA:B5": "Espressif Inc.",
    "EC:FA:BC": "Espressif Inc.",
    "10:D5:61": "Tuya Smart Inc.",
    "18:B4:30": "Nest Labs Inc.",
    "00:17:88": "Signify (Philips Hue)",
    "EC:B5:FA": "Signify (Philips Hue)",
    "00:04:20": "Sony Corporation",
    "70:54:B4": "Sonos, Inc.",
    "5C:AA:FD": "Sonos, Inc.",
}


def _normalize(mac: str) -> str:
    return mac.replace("-", ":").upper()


def _prefix(mac: str) -> str:
    return _normalize(mac)[:8]


def _load() -> dict[str, str]:
    global _table
    with _lock:
        if _table is not None:
            return _table

        table = dict(_FALLBACK)
        csv_path: Path = data_path("oui.csv")
        if csv_path.exists():
            try:
                with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                    for row in csv.DictReader(fh):
                        assignment = (row.get("Assignment") or "").strip().upper()
                        org = (row.get("Organization Name") or "").strip()
                        if len(assignment) == 6 and org:
                            key = ":".join(
                                assignment[i : i + 2] for i in range(0, 6, 2)
                            )
                            table[key] = org
            except (OSError, csv.Error):
                pass
        _table = table
        return _table


def is_locally_administered(mac: str) -> bool:
    """True for randomized/private MACs — phones rotate these for privacy.

    Bit 1 of the first octet marks a locally administered address, which means
    no OUI will ever match it. Worth telling the user rather than showing
    'Unknown vendor'.
    """
    try:
        first = int(_normalize(mac)[:2], 16)
    except ValueError:
        return False
    return bool(first & 0b10)


def lookup(mac: str | None) -> str | None:
    """Vendor name for a MAC, or None if unknown."""
    if not mac:
        return None
    if is_locally_administered(mac):
        return None
    return _load().get(_prefix(mac))


def describe(mac: str | None) -> str:
    """Human-facing vendor string, never empty."""
    if not mac:
        return "Unknown"
    if is_locally_administered(mac):
        return "Randomized MAC"
    return lookup(mac) or "Unknown vendor"


def entry_count() -> int:
    return len(_load())
