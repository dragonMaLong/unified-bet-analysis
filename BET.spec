# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Micromeritics BET analysis app.

Build a single-file windowed executable:
    pyinstaller --noconfirm BET.spec

The bundled reference thickness curves (tristar_bet/reference_data) and the
app logo (tristar_bet/assets) are collected so the .exe runs standalone,
without the Micromeritics TriStar II Plus software installed.
"""
from pathlib import Path

# PyInstaller normally reports missing imports as warnings and still creates an EXE.
# Fail early when an isolated build directory omitted a local runtime package.
for package in ("tristar_bet", "bet_telemetry"):
    if not (Path(SPECPATH) / package / "__init__.py").is_file():
        raise RuntimeError(f"Missing runtime package in build source: {package}")

datas = [
    ("tristar_bet/reference_data", "tristar_bet/reference_data"),
    ("tristar_bet/assets", "tristar_bet/assets"),
    ("DFT-NLDFT-Models.zip", "."),
]

# Optional public intake address; never include server administration credentials.
if Path("telemetry_config.json").is_file():
    datas.append(("telemetry_config.json", "."))

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
        "astropy",
        "bokeh",
        "dask",
        "distributed",
        "IPython",
        "jedi",
        "jupyter",
        "jupyter_client",
        "jupyter_core",
        "jupyter_server",
        "matplotlib",
        "nbconvert",
        "nbformat",
        "notebook",
        "numba",
        "pandas",
        "panel",
        "plotly",
        "pyarrow",
        "pytest",
        "scipy",
        "skimage",
        "sklearn",
        "sphinx",
        "statsmodels",
        "sympy",
        "tensorflow",
        "torch",
        "xarray",
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
