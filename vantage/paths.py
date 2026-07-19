"""Where Vantage reads bundled assets and writes user data.

Bundled files live next to the code (or inside the PyInstaller onefile temp
dir); user data always lives in %APPDATA%\\Vantage so the app works when
installed read-only under Program Files.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Vantage"


def bundle_root() -> Path:
    """Directory holding web/ and data/ — differs under PyInstaller onefile."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent


def web_path(*parts: str) -> Path:
    return bundle_root().joinpath("web", *parts)


def data_path(*parts: str) -> Path:
    return bundle_root().joinpath("data", *parts)


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return user_data_dir() / "vantage.db"
