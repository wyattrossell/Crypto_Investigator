# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build specification for the installed (Windows) build.

Build with:  python tools/build_release.py
         or: python -m PyInstaller packaging/crypto_investigator.spec --noconfirm

Produces dist/CryptoInvestigator/ - a self-contained program folder with
CryptoInvestigator.exe (windowed: no console) and everything it needs.
The Inno Setup script (packaging/installer.iss) wraps that folder into a
setup.exe. Case data is NEVER part of the build: at run time the program
stores it under %LOCALAPPDATA%\\CryptoInvestigator\\data (see app/config.py).
"""

from pathlib import Path

SPEC_DIR = Path(SPECPATH)
ROOT = SPEC_DIR.parent

block_cipher = None

# Read-only resources bundled next to the code (see config.BUNDLE_DIR).
datas = [
    (str(ROOT / "app" / "static"), "app/static"),
    (str(ROOT / "data" / "labels" / "exchange_labels_seed.json"),
     "data/labels"),
    (str(ROOT / "data" / "labels" / "exchange_compliance.json"),
     "data/labels"),
    (str(ROOT / "data" / "labels" / "custodian_contacts.json"),
     "data/labels"),
    (str(ROOT / "assets" / "crypto_investigator.ico"), "assets"),
    (str(ROOT / "VERSION"), "."),
]

# Modules imported dynamically (by string) that static analysis misses.
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "pystray._win32",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "yaml",
    "reportlab.graphics.barcode.common",
    "reportlab.graphics.barcode.code128",
    "reportlab.graphics.barcode.code93",
    "reportlab.graphics.barcode.code39",
    "reportlab.graphics.barcode.usps",
    "reportlab.graphics.barcode.usps4s",
    "reportlab.graphics.barcode.ecc200datamatrix",
]

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "_tkinter", "matplotlib", "numpy", "scipy", "pandas",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "jupyter",
        "unittest", "pydoc", "doctest", "test", "tests",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CryptoInvestigator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                      # no console window, ever
    disable_windowed_traceback=False,
    icon=str(ROOT / "assets" / "crypto_investigator.ico"),
    version=str(SPEC_DIR / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="CryptoInvestigator",
)
