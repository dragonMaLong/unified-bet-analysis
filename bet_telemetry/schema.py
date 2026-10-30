"""Strict, allowlisted telemetry protocol: never accept free-form user data."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from uuid import UUID

SCHEMA_VERSION = 1
MAX_BATCH_EVENTS = 30
MAX_BODY_BYTES = 65536
EXTENSIONS = {"smp", "raw", "dat", "qps", "xls", "xlsx", "xlsm", "other"}
PARSERS = {"micromeritics", "bel", "quantachrome", "jwgb", "excel", "unknown"}
FEATURES = {"bet", "langmuir", "t_plot", "bjh", "dh", "hk", "dft"}


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError("Unexpected or missing fields")


def _choice(value, choices):
    if type(value) is not str or value not in choices:
        raise ValueError("Invalid category")
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Invalid integer")
    return value


def _uuid(value):
    if type(value) is not str:
        raise ValueError("Invalid identifier")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise ValueError("Invalid identifier") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("Expected canonical random UUID")
    return value


def _timestamp(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ValueError("Invalid UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if not 2000 <= parsed.year <= 2100:
            raise ValueError("Unsupported year")
    except ValueError:
        raise ValueError("Invalid UTC timestamp") from None
    return value


def validate_event(event: dict) -> dict:
    """Return a fresh safe event, or reject the whole event without echoing values."""
    _keys(event, ("event_id", "occurred_at", "name", "data"))
    _uuid(event["event_id"])
    _timestamp(event["occurred_at"])
    name = _choice(event["name"], {"startup", "import_file", "feature_view", "export"})
    data = event["data"]
    if name == "startup":
        _keys(data, ("os", "os_version", "architecture", "screen_width", "screen_height", "scale"))
        _choice(data["os"], {"Windows", "Linux", "Darwin", "other"})
        if type(data["os_version"]) is not str or not re.fullmatch(r"[0-9.]{1,32}|unknown", data["os_version"]):
            raise ValueError("Invalid OS version")
        _choice(data["architecture"], {"x86", "x86_64", "arm64", "other"})
        _integer(data["screen_width"], 0, 32768)
        _integer(data["screen_height"], 0, 32768)
        if type(data["scale"]) not in (float, int) or not 0.5 <= data["scale"] <= 8 or not math.isfinite(data["scale"]):
            raise ValueError("Invalid display scale")
    elif name == "feature_view":
        _keys(data, ("feature", "surface"))
        _choice(data["feature"], FEATURES)
        _choice(data["surface"], {"plot", "results"})
    else:
        common = ("status", "duration_ms", "error_code")
        if name == "import_file":
            _keys(data, (*common, "extension", "parser"))
            _choice(data["extension"], EXTENSIONS)
            _choice(data["parser"], PARSERS)
            _choice(data["error_code"], {"none", "io_error", "parse_error", "no_points", "unexpected_error"})
        else:
            _keys(data, (*common, "format"))
            _choice(data["format"], {"xlsx"})
            _choice(data["error_code"], {"none", "write_error", "unexpected_error"})
        _choice(data["status"], {"success", "failure"})
        _integer(data["duration_ms"], 0, 86400000)
        if (data["status"] == "success") != (data["error_code"] == "none"):
            raise ValueError("Inconsistent result")
    return {"event_id": event["event_id"], "occurred_at": event["occurred_at"], "name": name, "data": dict(data)}


def validate_batch(batch: dict) -> dict:
    _keys(batch, ("schema_version", "install_id", "session_id", "app_version", "events"))
    if type(batch["schema_version"]) is not int or batch["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported schema")
    _uuid(batch["install_id"])
    _uuid(batch["session_id"])
    version = batch["app_version"]
    if type(version) is not str or not re.fullmatch(r"[0-9][0-9A-Za-z.+_-]{0,31}", version):
        raise ValueError("Invalid app version")
    events = batch["events"]
    if type(events) is not list or not 1 <= len(events) <= MAX_BATCH_EVENTS:
        raise ValueError("Invalid batch length")
    safe_events = [validate_event(event) for event in events]
    if len({event["event_id"] for event in safe_events}) != len(safe_events):
        raise ValueError("Duplicate event in batch")
    return {**batch, "events": safe_events}
