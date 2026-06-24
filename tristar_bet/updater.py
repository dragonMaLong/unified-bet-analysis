from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .update_checker import UpdateInfo


ProgressCallback = Callable[[int, int], None]


class UpdateDownloadError(RuntimeError):
    """Raised when an update package cannot be downloaded or verified."""


def download_update(info: UpdateInfo, *, progress_callback: ProgressCallback | None = None) -> Path:
    url = (info.download_url or "").strip()
    if not url:
        raise UpdateDownloadError("更新信息中没有可下载的安装包链接。")

    download_dir = _download_dir()
    download_dir.mkdir(parents=True, exist_ok=True)
    target = download_dir / _download_filename(info)
    partial = target.with_name(target.name + ".part")
    expected_sha256 = (info.sha256 or "").strip().lower()

    if target.exists() and expected_sha256 and _sha256_file(target) == expected_sha256:
        size = target.stat().st_size
        if progress_callback is not None:
            progress_callback(size, size)
        return target

    if partial.exists():
        partial.unlink()

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream, */*",
            "User-Agent": f"Unified-BET-Updater/{info.current_version}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            digest = hashlib.sha256()
            downloaded = 0
            with partial.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total)
    except urllib.error.HTTPError as exc:
        _remove_quietly(partial)
        raise UpdateDownloadError(f"下载安装包失败：服务器返回 HTTP {exc.code}。") from exc
    except urllib.error.URLError as exc:
        _remove_quietly(partial)
        reason = getattr(exc, "reason", exc)
        raise UpdateDownloadError(f"下载安装包失败：无法连接更新源：{reason}") from exc
    except TimeoutError as exc:
        _remove_quietly(partial)
        raise UpdateDownloadError("下载安装包失败：连接超时。") from exc
    except OSError as exc:
        _remove_quietly(partial)
        raise UpdateDownloadError(f"下载安装包失败：{exc}") from exc

    actual_sha256 = digest.hexdigest()
    if expected_sha256 and actual_sha256 != expected_sha256:
        _remove_quietly(partial)
        raise UpdateDownloadError(
            "安装包校验失败：下载文件的 SHA256 与更新清单不一致。"
        )

    if target.exists():
        target.unlink()
    partial.replace(target)
    return target


def launch_update_and_exit(downloaded_exe: Path) -> None:
    exe_path = Path(downloaded_exe).resolve()
    if not exe_path.exists():
        raise UpdateDownloadError(f"找不到已下载的安装包：{exe_path}")

    if getattr(sys, "frozen", False):
        _launch_replacement_script(exe_path)
    else:
        subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
            close_fds=True,
            creationflags=_windows_detached_flags(),
        )


def _download_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "UnifiedBET" / "updates"
    return Path.home() / ".unified_bet" / "updates"


def _download_filename(info: UpdateInfo) -> str:
    raw_name = (info.asset_name or "").strip() or "Micromeritics-BET.exe"
    suffix = Path(raw_name).suffix or ".exe"
    version = re.sub(r"[^0-9A-Za-z._-]+", "_", info.latest_version or "update")
    return f"Micromeritics-BET-v{version}{suffix}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_quietly(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _launch_replacement_script(downloaded_exe: Path) -> None:
    current_exe = Path(sys.executable).resolve()
    script_dir = _download_dir()
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "apply_update.ps1"
    backup_path = current_exe.with_name(current_exe.name + ".old")
    script_path.write_text(
        _replacement_script_text(current_exe, downloaded_exe, backup_path),
        encoding="utf-8-sig",
    )
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        cwd=str(script_dir),
        close_fds=True,
        creationflags=_windows_detached_flags(),
    )


def _replacement_script_text(current_exe: Path, downloaded_exe: Path, backup_path: Path) -> str:
    return f"""$ErrorActionPreference = 'Stop'
$OldPath = {_ps_single_quoted(str(current_exe))}
$NewPath = {_ps_single_quoted(str(downloaded_exe))}
$BackupPath = {_ps_single_quoted(str(backup_path))}
$TargetDir = Split-Path -Parent $OldPath
$NewDir = Split-Path -Parent $NewPath

for ($i = 0; $i -lt 40; $i++) {{
    Start-Sleep -Milliseconds 500
    try {{
        if (Test-Path -LiteralPath $BackupPath) {{
            Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
        }}
        Move-Item -LiteralPath $OldPath -Destination $BackupPath -Force
        Move-Item -LiteralPath $NewPath -Destination $OldPath -Force
        Start-Process -FilePath $OldPath -WorkingDirectory $TargetDir
        Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
        exit 0
    }} catch {{
        if ((-not (Test-Path -LiteralPath $OldPath)) -and (Test-Path -LiteralPath $BackupPath)) {{
            try {{
                Move-Item -LiteralPath $BackupPath -Destination $OldPath -Force
            }} catch {{}}
        }}
    }}
}}

Start-Process -FilePath $NewPath -WorkingDirectory $NewDir
"""


def _ps_single_quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _windows_detached_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
