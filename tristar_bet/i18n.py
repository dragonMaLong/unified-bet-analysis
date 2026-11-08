"""Interface language.

Source strings are the Chinese texts written in the code; ``tr()`` returns the
text for the current language from ``tristar_bet/locales/<code>.json`` (a flat
``{"中文": "translation"}`` map) and falls back to the Chinese source.  Strings
with values use ``str.format`` placeholders: ``tr("共 {0} 个点", count)``.

Changing the language never needs a restart: ``set_language`` notifies
listeners (the main window re-translates its widgets and redraws the results).
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path
from typing import Callable

SOURCE_LANGUAGE = "zh"
# Code -> name shown in the language menu, written in that language so that
# a reader who does not know Chinese can still find their own language.
LANGUAGES = {"zh": "简体中文", "en": "English"}

_current = SOURCE_LANGUAGE
_catalogs: dict[str, dict[str, str]] = {}
_reverse: dict[str, dict[str, str]] = {}
_listeners: list[Callable[[str, str], None]] = []


def _locale_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    bundled = base / "tristar_bet" / "locales"
    return bundled if bundled.is_dir() else Path(__file__).resolve().parent / "locales"


def catalog(language: str) -> dict[str, str]:
    if language == SOURCE_LANGUAGE:
        return {}
    if language not in _catalogs:
        path = _locale_dir() / f"{language}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _catalogs[language] = {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}
    return _catalogs[language]


def current_language() -> str:
    return _current


def normalize_language(value: object) -> str:
    text = str(value or "")
    return text if text in LANGUAGES else SOURCE_LANGUAGE


def tr(text: str, *args: object) -> str:
    """Text in the current language; ``args`` fill ``{0}``, ``{1}``... placeholders."""
    translated = catalog(_current).get(text, text) if _current != SOURCE_LANGUAGE else text
    if args:
        try:
            return translated.format(*args)
        except (IndexError, KeyError, ValueError):
            return text.format(*args)
    return translated


# One Chinese text can need different translations by use ("软件更新" is a
# dialog title and, shorter, a toolbar button).  Such entries are stored under
# "<context>\x04<text>" (the gettext convention); Chinese shows only <text>.
CONTEXT_SEPARATOR = "\x04"


def trc(context: str, text: str, *args: object) -> str:
    """``tr`` for a text whose translation depends on where it is used."""
    if _current == SOURCE_LANGUAGE:
        translated = text
    else:
        entries = catalog(_current)
        translated = entries.get(context + CONTEXT_SEPARATOR + text) or entries.get(text, text)
    return translated.format(*args) if args else translated


def tr_in(language: str, text: str, *args: object) -> str:
    """``tr`` for an explicit language (exports that must not depend on the UI state)."""
    translated = catalog(language).get(text, text) if language != SOURCE_LANGUAGE else text
    return translated.format(*args) if args else translated


def source_text(text: str, language: str | None = None) -> str | None:
    """Chinese source of a static text currently shown in ``language`` (None if unknown)."""
    language = language or _current
    if language == SOURCE_LANGUAGE:
        return text if text in catalog("en") else None
    if language not in _reverse:
        _reverse[language] = {}
        for source, translated in catalog(language).items():
            _reverse[language].setdefault(translated, source)
    return _reverse[language].get(text)


_PLACEHOLDER = re.compile(r"\{(\d+)(?:![rsa])?(?::[^{}]*)?\}")
_templates: dict[str, list[tuple[str, str, re.Pattern]]] = {}


def _shown(source: str, language: str) -> str:
    plain = source.split(CONTEXT_SEPARATOR, 1)[-1]
    if language == SOURCE_LANGUAGE:
        return plain
    entries = catalog(language)
    return entries.get(source) or entries.get(plain, plain)


def _template_patterns(language: str) -> list[tuple[str, str, re.Pattern]]:
    """(first literal, source, regex) for every source string with placeholders, as shown in ``language``."""
    if language not in _templates:
        sources = set(catalog("en")) | set(catalog(language))  # every catalog lists the same sources
        patterns = []
        for source in sources:
            if not _PLACEHOLDER.search(source):
                continue
            shown = _shown(source, language).replace("{{", "\x00").replace("}}", "\x01")
            parts = _PLACEHOLDER.split(shown)
            literals = [part.replace("\x00", "{").replace("\x01", "}") for part in parts[0::2]]
            # Templates such as "{0}：{1}" carry no recognisable text; never guess with them.
            if len(re.sub(r"[\s\W\d_]", "", "".join(literals))) < 2:
                continue
            regex, seen = "", set()
            for index, part in enumerate(parts):
                if index % 2 == 0:
                    regex += re.escape(literals[index // 2])
                elif part in seen:
                    regex += f"(?P=a{part})"
                else:
                    seen.add(part)
                    regex += f"(?P<a{part}>.+?)"
            patterns.append((literals[0], source, re.compile(regex, re.S)))
        _templates[language] = patterns
    return _templates[language]


def retranslate_text(text: str, old: str, new: str, _depth: int = 0) -> str:
    """Translate a text shown in ``old`` into ``new``; unknown texts are returned unchanged.

    Exact texts are looked up directly; texts built from a template with values
    (``"可导入 3 个，待导入 0 个"``) are matched against the templates and the
    values are carried over (and translated themselves when they are known texts).
    """
    if not text or old == new or _depth > 3:
        return text
    source = source_text(text, old)
    if source is not None:
        return _shown(source, new)
    for first, source, pattern in _template_patterns(old):
        if first and not text.startswith(first):
            continue
        match = pattern.fullmatch(text)
        if match is None:
            continue
        values = {int(key[1:]): retranslate_text(value, old, new, _depth + 1) for key, value in match.groupdict().items()}
        target = _PLACEHOLDER.sub(lambda m: "{" + m.group(1) + "}", _shown(source, new).replace("{{", "\x00").replace("}}", "\x01"))
        try:
            return target.format(*[values.get(i, "") for i in range(max(values, default=-1) + 1)]).replace("\x00", "{").replace("\x01", "}")
        except (IndexError, KeyError, ValueError):
            return text
    return text


def add_listener(callback: Callable[[str, str], None]) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def remove_listener(callback: Callable[[str, str], None]) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def set_language(language: str) -> bool:
    """Switch the interface language; returns True when it changed."""
    global _current
    language = normalize_language(language)
    if language == _current:
        return False
    old, _current = _current, language
    for callback in list(_listeners):
        # One failing window must not leave the others in the old language;
        # the error is reported, and the listener stays for the next switch.
        try:
            callback(old, language)
        except Exception:
            traceback.print_exc()
    return True
