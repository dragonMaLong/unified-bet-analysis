# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Micromeritics BET analysis app.

Build a single-file windowed executable:
    pyinstaller --noconfirm BET.spec

The bundled reference thickness curves (tristar_bet/reference_data) and the
app logo (tristar_bet/assets) are collected so the .exe runs standalone,
without the Micromeritics TriStar II Plus software installed.
"""
datas = [
    ("tristar_bet/reference_data", "tristar_bet/reference_data"),
    ("tristar_bet/assets", "tristar_bet/assets"),
]

hiddenimports = []

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "PySide6_Addons",
        "PySide6_Essentials",
        "shiboken6",
        "PyQt6",
        "PySide2",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BET-DragonScience",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="tristar_bet/assets/BET-logo.ico",
)
