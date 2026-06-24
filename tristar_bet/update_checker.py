from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .version import __version__


DEFAULT_UPDATE_REPOSITORY = "dragonMaLong/unified-bet-analysis"
GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/{repo}/releases/latest"


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    update_available: bool
    release_url: str
    download_url: str
    asset_name: str
    release_name: str
    release_notes: str
    published_at: str


class UpdateCheckError(RuntimeError):
    """Raised when the update endpoint cannot be reached or parsed."""


def check_for_update(
    current_version: str = __version__,
    *,
    repository: str = DEFAULT_UPDATE_REPOSITORY,
    timeout: float = 8.0,
) -> UpdateInfo:
    payload = _fetch_latest_release(repository, timeout=timeout)
    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise UpdateCheckError("GitHub Release 没有版本标签。")

    latest_version = _clean_version(tag_name)
    release_url = str(payload.get("html_url") or "").strip()
    assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
    asset = _choose_download_asset(assets)

    return UpdateInfo(
        current_version=_clean_version(current_version),
        latest_version=latest_version,
        update_available=compare_versions(latest_version, current_version) > 0,
        release_url=release_url,
        download_url=str(asset.get("browser_download_url") or "").strip() if asset else "",
        asset_name=str(asset.get("name") or "").strip() if asset else "",
        release_name=str(payload.get("name") or tag_name).strip(),
        release_notes=str(payload.get("body") or "").strip(),
        published_at=str(payload.get("published_at") or "").strip(),
    )


def compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    max_len = max(len(left_parts), len(right_parts), 3)
    left_parts.extend([0] * (max_len - len(left_parts)))
    right_parts.extend([0] * (max_len - len(right_parts)))
    if left_parts > right_parts:
        return 1
    if left_parts < right_parts:
        return -1
    return 0


def _fetch_latest_release(repository: str, *, timeout: float) -> dict[str, Any]:
    url = GITHUB_LATEST_RELEASE_URL.format(repo=repository.strip())
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Unified-BET-Analysis/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateCheckError("没有找到可用的 GitHub Release。请先在仓库里发布一个 Release。") from exc
        raise UpdateCheckError(f"GitHub 返回 HTTP {exc.code}。") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateCheckError(f"无法连接 GitHub：{reason}") from exc
    except TimeoutError as exc:
        raise UpdateCheckError("连接 GitHub 超时。") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateCheckError(f"无法读取更新信息：{exc}") from exc


def _choose_download_asset(assets: list[Any]) -> dict[str, Any] | None:
    candidates = [asset for asset in assets if isinstance(asset, dict)]
    if not candidates:
        return None

    preferred_suffixes = (".exe", ".msi", ".zip", ".7z")
    for suffix in preferred_suffixes:
        for asset in candidates:
            name = str(asset.get("name") or "").lower()
            if name.endswith(suffix) and asset.get("browser_download_url"):
                return asset

    for asset in candidates:
        if asset.get("browser_download_url"):
            return asset
    return None


def _clean_version(version: str) -> str:
    text = str(version or "").strip()
    if text.lower().startswith("version "):
        text = text[8:].strip()
    if text[:1].lower() == "v":
        text = text[1:]
    return text


def _version_parts(version: str) -> list[int]:
    text = _clean_version(version)
    match = re.match(r"(\d+(?:\.\d+)*)", text)
    if not match:
        return [0]
    return [int(part) for part in match.group(1).split(".")]
