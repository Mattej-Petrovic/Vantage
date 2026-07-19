# PyInstaller spec for Vantage — build with:  pyinstaller build.spec
#
# Onefile + windowed. paths.bundle_root() resolves to sys._MEIPASS when frozen,
# so web/ and data/ are placed at the bundle root, NOT under vantage/.

from pathlib import Path

block_cipher = None

root = Path(SPECPATH)
oui = root / "vantage" / "data" / "oui.csv"

datas = [(str(root / "vantage" / "web"), "web")]
if oui.exists():
    datas.append((str(oui), "data"))
else:
    # Not fatal: the app falls back to a small built-in vendor subset. But a
    # release build should ship the full database — run tools/fetch_oui.py first.
    print("build.spec: WARNING — vantage/data/oui.csv missing, "
          "run `python tools/fetch_oui.py` for a complete release build.")

a = Analysis(
    ["run.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=["pystray._win32", "PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data", "test"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Vantage",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "docs" / "vantage.ico") if (root / "docs" / "vantage.ico").exists() else None,
)
