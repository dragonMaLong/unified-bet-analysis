"""Build from a complete ASCII-path source snapshot and check the frozen GUI.

Run using the release build virtual environment, for example:
  python scripts/build_windows_release.py --stage C:\\temp\\bet-release-133-r3
The stage must be new. Verified artifacts remain under stage/dist for publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGES = ("tristar_bet", "bet_telemetry")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path)
    args = parser.parse_args()
    stage = args.stage.resolve()
    if not str(stage).isascii():
        parser.error("Use an ASCII-only build stage")
    if stage.exists():
        parser.error("Build stage already exists; choose a new directory")
    files = [Path(name) for name in ("app.py", "BET.spec", "DFT-NLDFT-Models.zip")]
    if (ROOT / "telemetry_config.json").is_file():
        files.append(Path("telemetry_config.json"))
    for package in RUNTIME_PACKAGES:
        if not (ROOT / package / "__init__.py").is_file():
            raise RuntimeError(f"Missing required package: {package}")
        files.extend(path.relative_to(ROOT) for path in (ROOT / package).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    stage.mkdir(parents=True)
    hashes = {}
    for relative in files:
        source, target = ROOT / relative, stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashes[relative.as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
    (stage / "build_source_hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    with (stage / "build.log").open("wb") as log:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "BET.spec"],
                       cwd=stage, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)

    from PyInstaller.archive.readers import CArchiveReader
    exe = stage / "dist/BET-DragonScience.exe"
    archive = CArchiveReader(str(exe))
    pyz = archive.open_embedded_archive(next(key for key in archive.toc if key.endswith(".pyz")))
    for module in ("bet_telemetry", "bet_telemetry.schema", "tristar_bet.ui.main_window"):
        if module not in pyz.toc:
            raise RuntimeError(f"Missing frozen module: {module}")
    if any(name.startswith("telemetry_server") for name in pyz.toc):
        raise RuntimeError("Server code unexpectedly included in desktop executable")
    for flag, name in (("--self-test-ui", "frozen_ui.json"), ("--self-test-dft", "frozen_dft.json")):
        report_path = stage / name
        # Run away from the source tree so local modules cannot conceal missing files.
        subprocess.run([str(exe), flag, str(report_path)], cwd=stage / "dist", env=env, check=True, timeout=180)
        if not json.loads(report_path.read_text(encoding="utf-8"))["ok"]:
            raise RuntimeError(f"Frozen smoke check failed: {name}")
        print(f"Passed {name}", flush=True)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    (exe.parent / "SHA256SUMS.txt").write_text(digest + "  " + exe.name + "\n", encoding="ascii")
    print(f"Verified executable: {exe}\nSHA256: {digest}", flush=True)


if __name__ == "__main__":
    main()
