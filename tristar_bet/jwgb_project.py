"""Lossless reader for the observed PAS 10.06 JWGBP project container.

Derived from five public research files accompanying DOI 10.1039/D4RA05672H.
The container is lowercase ASCII hex with (byte index modulo 48) subtracted
from each hex character. Decoding exposes XML pressure/calibration records,
not evaluated uptake. This module does not claim quantitative compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET

MAX_PROJECT_BYTES = 16 * 1024 * 1024
_HEX = frozenset(b"0123456789abcdef")


@dataclass(frozen=True)
class JwgbProjectDocument:
    xml: bytes
    root: ET.Element
    source_sha256: str
    source_bytes: int


def decode_jwgb_project(data: bytes) -> JwgbProjectDocument:
    """Strictly decode the observed envelope, then validate the whole XML.

    No lossy character replacement, whitespace skipping, or wrapped byte
    arithmetic: every encoded byte must map to a lowercase hex character.
    Unknown formats fail rather than being guessed into an isotherm.
    """
    if not data or len(data) % 2 or len(data) > MAX_PROJECT_BYTES:
        raise ValueError("JWGBP 编码长度无效或超过 16 MiB")
    hexadecimal = bytearray(len(data))
    for index, value in enumerate(data):
        character = value + index % 48
        if character not in _HEX:
            raise ValueError(f"JWGBP 编码在字节 {index} 处无效")
        hexadecimal[index] = character
    xml = bytes.fromhex(hexadecimal.decode("ascii"))
    try:
        text = xml.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("JWGBP 不支持 DTD 或实体声明")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError) as exc:
        raise ValueError("JWGBP 解码后不是完整的 UTF-8 XML") from exc
    if root.tag != "doc" or root.get("Ver") != "1":
        raise ValueError("尚未验证此 JWGBP 文档版本")
    for name in ("Config", "Data"):
        section = root.find(name)
        if section is None or section.get("Ver") != "1":
            raise ValueError(f"JWGBP 缺少或不支持 {name} 数据块")
    return JwgbProjectDocument(xml, root, hashlib.sha256(data).hexdigest(), len(data))


def read_jwgb_project(path: str | Path) -> JwgbProjectDocument:
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_PROJECT_BYTES + 1)
    return decode_jwgb_project(data)
