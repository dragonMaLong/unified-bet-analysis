from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


API_BASE = "https://gitee.com/api/v5"


class GiteeApiError(RuntimeError):
    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create/update a Gitee Release and upload assets.")
    parser.add_argument("--owner", default="dragonMalong")
    parser.add_argument("--repo", default="unified-bet-analysis")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--target", default="main")
    parser.add_argument("--file", action="append", required=True, help="Asset file to upload; repeat for multiple files.")
    parser.add_argument("--token-env", default="GITEE_TOKEN")
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise GiteeApiError(f"请先设置环境变量 {args.token_env}。")

    files = [Path(path).expanduser().resolve() for path in args.file]
    for path in files:
        if not path.exists():
            raise GiteeApiError(f"附件不存在：{path}")

    release = get_release_by_tag(args.owner, args.repo, args.tag, token)
    if release is None:
        release = create_release(
            args.owner,
            args.repo,
            args.tag,
            args.name or args.tag,
            args.body or f"Windows executable build for {args.tag}.",
            args.target,
            token,
        )
        print(f"created release {args.tag} id={release['id']}")
    else:
        print(f"found release {args.tag} id={release['id']}")

    release_id = int(release["id"])
    existing_assets = list_release_assets(args.owner, args.repo, release_id, token)
    for path in files:
        remove_existing_asset(args.owner, args.repo, release_id, path.name, existing_assets, token)
        uploaded = upload_asset(args.owner, args.repo, release_id, path, token)
        print(f"uploaded {path.name} id={uploaded.get('id')} sha256={sha256_file(path)}")

    return 0


def get_release_by_tag(owner: str, repo: str, tag: str, token: str) -> dict[str, Any] | None:
    path = f"/repos/{quote(owner)}/{quote(repo)}/releases/tags/{quote(tag)}"
    payload = request_json("GET", path, token=token, token_in_query=True)
    return payload if isinstance(payload, dict) else None


def create_release(
    owner: str,
    repo: str,
    tag: str,
    name: str,
    body: str,
    target: str,
    token: str,
) -> dict[str, Any]:
    data = {
        "access_token": token,
        "tag_name": tag,
        "name": name,
        "body": body,
        "target_commitish": target,
        "prerelease": "false",
    }
    payload = request_json("POST", f"/repos/{quote(owner)}/{quote(repo)}/releases", data=data)
    if not isinstance(payload, dict) or not payload.get("id"):
        raise GiteeApiError(f"创建 Release 失败：{payload!r}")
    return payload


def list_release_assets(owner: str, repo: str, release_id: int, token: str) -> list[dict[str, Any]]:
    path = f"/repos/{quote(owner)}/{quote(repo)}/releases/{release_id}/attach_files"
    payload = request_json("GET", path, token=token, token_in_query=True)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def remove_existing_asset(
    owner: str,
    repo: str,
    release_id: int,
    filename: str,
    assets: list[dict[str, Any]],
    token: str,
) -> None:
    for asset in assets:
        name = str(asset.get("name") or asset.get("filename") or "")
        asset_id = asset.get("id")
        if name == filename and asset_id:
            path = f"/repos/{quote(owner)}/{quote(repo)}/releases/{release_id}/attach_files/{asset_id}"
            request_json("DELETE", path, token=token, token_in_query=True)
            print(f"deleted existing asset {filename} id={asset_id}")


def upload_asset(owner: str, repo: str, release_id: int, file_path: Path, token: str) -> dict[str, Any]:
    path = f"/repos/{quote(owner)}/{quote(repo)}/releases/{release_id}/attach_files"
    fields = {"access_token": token}
    files = {"file": file_path}
    payload = request_multipart("POST", path, fields=fields, files=files)
    if not isinstance(payload, dict) or not payload.get("id"):
        raise GiteeApiError(f"上传附件失败：{payload!r}")
    return payload


def request_json(
    method: str,
    path: str,
    *,
    data: dict[str, str] | None = None,
    token: str = "",
    token_in_query: bool = False,
) -> Any:
    url = API_BASE + path
    if token_in_query:
        url += "?" + urllib.parse.urlencode({"access_token": token})
    body = None
    headers = {"Accept": "application/json", "User-Agent": "Unified-BET-Gitee-Uploader"}
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise GiteeApiError(f"Gitee API HTTP {exc.code}: {raw[:500]}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise GiteeApiError(f"Gitee API 返回了非 JSON 内容：{raw[:200]!r}") from exc


def request_multipart(
    method: str,
    path: str,
    *,
    fields: dict[str, str],
    files: dict[str, Path],
) -> Any:
    boundary = "----UnifiedBET" + uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    for name, file_path in files.items():
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{file_path.name}"\r\n'.encode()
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(file_path.read_bytes())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    request = urllib.request.Request(
        API_BASE + path,
        data=bytes(body),
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Unified-BET-Gitee-Uploader",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise GiteeApiError(f"Gitee API HTTP {exc.code}: {raw[:500]}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise GiteeApiError(f"Gitee API 返回了非 JSON 内容：{raw[:200]!r}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GiteeApiError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
