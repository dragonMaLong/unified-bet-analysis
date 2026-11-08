from __future__ import annotations

import ipaddress
import calendar
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from bet_telemetry.schema import APP_IDS, APP_LABELS, MAX_BODY_BYTES, validate_batch
from telemetry_server.ip_location import enrich_ip_locations


EVENT_RETENTION_DAYS = None
IP_RETENTION_DAYS = None
DAY = 86400
MAX_DATABASE_BYTES = 512 * 1024 * 1024
IP_PAGE_SIZE = 200
SESSION_PAGE_SIZE = 50
WINDOW_LABELS = {30: "近30天", 90: "近90天", 180: "近半年（180天）", 365: "近1年（365天）"}
ALL_WINDOW_LABEL = "全部"
PARSER_LABELS = {"micromeritics": "麦克 Micromeritics", "bel": "MicrotracBEL", "quantachrome": "康塔 Quantachrome",
                 "jwgb": "精微高博 JWGB", "excel": "Excel 报告", "unknown": "未识别"}
INSTRUMENT_LABELS = {"tristar_ii_3020": "TriStar II 3020", "tristar_ii_plus": "TriStar II Plus",
                     "asap_2460": "ASAP 2460", "asap_2020_plus": "ASAP 2020 Plus", "asap_2020": "ASAP 2020",
                     "asap_2010": "ASAP 2010", "3flex": "3Flex", "micromeritics_other": "麦克其他型号",
                     "belsorp": "BELSORP", "quantachrome": "Quantachrome", "jwgb": "JWGB", "bsd": "贝士德 BSD",
                     "excel": "Excel 报告（型号未知）", "other": "其他",
                     "autopore_v": "AutoPore V", "autopore_iv": "AutoPore IV", "autopore_other": "AutoPore 其他型号"}
# Chart colours per application (BET keeps the page accent; MIP is yellow).
APP_COLORS = {"bet": "#2f7de1", "mercury_smp": "#e0a100"}
OS_LABELS = {"Windows": "Windows", "Linux": "Linux", "Darwin": "macOS", "other": "其他"}
DISPLAY_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    install_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    app_version TEXT NOT NULL,
    name TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    received_at INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    status TEXT,
    extension TEXT,
    feature TEXT,
    surface TEXT,
    export_format TEXT,
    error_code TEXT,
    duration_ms INTEGER,
    app TEXT NOT NULL DEFAULT 'bet'
);
CREATE INDEX IF NOT EXISTS events_time ON events(occurred_at);
CREATE INDEX IF NOT EXISTS events_name_time ON events(name, occurred_at);
CREATE INDEX IF NOT EXISTS events_install_time ON events(install_id, occurred_at);
CREATE TABLE IF NOT EXISTS ip_observations (
    install_id TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    app_version TEXT NOT NULL,
    last_seen_at INTEGER NOT NULL,
    PRIMARY KEY (install_id, ip_address, app_version)
);
CREATE INDEX IF NOT EXISTS ips_last_seen ON ip_observations(last_seen_at);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, name);
-- Connection IP per program run.  Older rows predate this table; their IP is
-- inferred from ip_observations of the same installation and version.
CREATE TABLE IF NOT EXISTS session_ips (
    session_id TEXT PRIMARY KEY,
    install_id TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);
"""


@contextmanager
def _database(path):
    connection = sqlite3.connect(str(path), timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA secure_delete = ON")
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        # Applies on every connection. SQLite returns SQLITE_FULL instead of
        # growing a new database beyond the service's fixed 512 MiB budget.
        connection.execute(f"PRAGMA max_page_count = {max(1, MAX_DATABASE_BYTES // page_size)}")
        connection.execute("PRAGMA journal_size_limit = 16777216")
        connection.execute("PRAGMA wal_autocheckpoint = 1000")
        yield connection
    finally:
        connection.close()


def initialize_database(path):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _database(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(_SCHEMA)
        _add_app_column(connection)
    if os.name != "nt":
        path.chmod(0o600)
    return str(path)


def _add_app_column(connection):
    """Rows stored before MIP joined this server are BET rows (``app`` defaults to 'bet').

    Several workers may start at once: the check and ALTER run under one write lock.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        if "app" not in columns:
            connection.execute("ALTER TABLE events ADD COLUMN app TEXT NOT NULL DEFAULT 'bet'")
        connection.execute("CREATE INDEX IF NOT EXISTS events_app_time ON events(app, occurred_at)")
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def purge_database(path, *, now=None):
    """Maintain the WAL without deleting retained events or IP observations.

    Keep the existing CLI entry point and result fields for deployed timers.
    ``now`` remains accepted for compatibility; retention is now indefinite.
    """
    with _database(path) as connection:
        checkpoint = tuple(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
    return {"events_deleted": 0, "ip_observations_deleted": 0, "checkpoint_busy": bool(checkpoint[0])}


class _RateGuard:
    """Bound both request rate and memory; Nginx is the outer rate limiter."""

    def __init__(self, per_minute, global_per_minute, max_keys=4096):
        self.per_minute = per_minute
        self.global_per_minute = global_per_minute
        self.max_keys = max_keys
        self.window = None
        self.total = 0
        self.clients = OrderedDict()
        self.key = secrets.token_bytes(32)
        self.lock = threading.Lock()

    def allow(self, key, now):
        window = int(now) // 60
        # Rate-limit buckets need no raw IP retained in idle process memory.
        key = hashlib.blake2b(str(key).encode("utf-8"), key=self.key, digest_size=16).digest()
        with self.lock:
            if self.window != window:
                self.window, self.total = window, 0
                self.clients.clear()
            if self.total >= self.global_per_minute:
                return False
            count = self.clients.get(key, 0)
            if count >= self.per_minute:
                return False
            self.total += 1
            self.clients[key] = count + 1
            self.clients.move_to_end(key)
            if len(self.clients) > self.max_keys:
                self.clients.popitem(last=False)
            return True


def _parsed_ip(value):
    if not isinstance(value, str) or "%" in value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _client_ip(trust_proxy):
    peer = request.environ.get("REMOTE_ADDR", "")
    if trust_proxy and peer in {"127.0.0.1", "::1"}:
        # Exactly one local reverse proxy, which must overwrite this header.
        # Never accept a comma-separated chain or trust arbitrary remote peers.
        forwarded = request.headers.get("X-Forwarded-For", "")
        if "," not in forwarded:
            parsed = _parsed_ip(forwarded)
            if parsed is not None:
                return parsed
    return _parsed_ip(peer)


def _utc_text(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_text(timestamp):
    return datetime.fromtimestamp(timestamp, DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _event_time(value):
    return calendar.timegm(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").timetuple())


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError("Non-finite JSON number")


def _save_batch(path, batch, ip_address, now):
    app_id = batch.get("app_id", "bet")
    rows = []
    for event in batch["events"]:
        occurred_at = _event_time(event["occurred_at"])
        data = event["data"]
        rows.append((event["event_id"], batch["install_id"], batch["session_id"],
                     batch["app_version"], event["name"], occurred_at, now,
                     json.dumps(data, ensure_ascii=True, separators=(",", ":")),
                     data.get("status"), data.get("extension"), data.get("feature"),
                     data.get("surface"), data.get("format"), data.get("error_code"), data.get("duration_ms"), app_id))
    with _database(path) as connection:
        with connection:
            connection.executemany("""INSERT OR IGNORE INTO events (event_id, install_id, session_id, app_version,
                name, occurred_at, received_at, data_json, status, extension, feature, surface, export_format,
                error_code, duration_ms, app) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
            if ip_address is not None:
                connection.execute("""INSERT INTO ip_observations VALUES (?, ?, ?, ?)
                    ON CONFLICT(install_id, ip_address, app_version)
                    DO UPDATE SET last_seen_at = excluded.last_seen_at""",
                    (batch["install_id"], ip_address, batch["app_version"], now))
                # First IP seen for a run is kept: a queued batch from an earlier run may
                # arrive later from another network, but the run's own startup batch came first.
                connection.execute("""INSERT INTO session_ips VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET last_seen_at = excluded.last_seen_at""",
                    (batch["session_id"], batch["install_id"], ip_address, now, now))


def _city_label(location):
    """City-level label ("上海", "苏州"); country for foreign addresses; special labels unchanged."""
    parts = [part for part in str(location or "").split(" · ") if part]
    if len(parts) <= 1:
        return parts[0] if parts else "未知"
    label = parts[2] if len(parts) >= 3 else parts[1]
    if parts[0] != "中国" and len(parts) < 3:
        label = parts[0]
    return label[:-1] if label.endswith("市") and len(label) > 2 else label


def _os_label(os_name, os_version):
    if os_name == "Windows" and isinstance(os_version, str):
        fields = os_version.split(".")
        if len(fields) >= 3 and fields[0] == "10" and fields[2].isdigit():
            return "Windows 11" if int(fields[2]) >= 22000 else "Windows 10"
        if fields and fields[0] in {"6", "10"}:
            return {"6": "Windows 7/8", "10": "Windows 10"}.get(fields[0], "Windows")
    return OS_LABELS.get(os_name, os_name or "未知")


def _duration_label(seconds):
    if seconds is None:
        return "未知"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分"
    if minutes:
        return f"{minutes}'{seconds:02d}\""
    return f"{seconds}秒"


def _chart(series, *, width=1040, height=230, label_every=1):
    """Coordinates for line charts drawn as inline SVG (the admin page runs no script).

    ``series`` is a list of {"app", "points": [{"label", "count"}]} sharing the same x labels;
    all lines share one y scale.  ``total``/``peak`` cover every series together.
    """
    left, right, top, bottom = 44, 14, 14, 30
    labels = [point["label"] for point in series[0]["points"]] if series else []
    combined = [sum(item["points"][index]["count"] for item in series) for index in range(len(labels))]
    peak = max((point["count"] for item in series for point in item["points"]), default=0)
    step, magnitude = 1, 1
    while True:  # 1, 2, 5, 10, 20, 50 ...: at most five grid intervals above zero
        for factor in (1, 2, 5):
            step = factor * magnitude
            if step * 5 >= peak:
                break
        else:
            magnitude *= 10
            continue
        break
    top_value = step * max(1, -(-peak // step))
    plot_width, plot_height = width - left - right, height - top - bottom
    count = len(labels)
    x_step = plot_width / (count - 1) if count > 1 else 0
    xs = [round(left + (x_step * index if count > 1 else plot_width / 2), 1) for index in range(count)]
    base = top + plot_height
    lines = []
    for item in series:
        coords = [{"x": xs[index], "y": round(top + plot_height * (1 - point["count"] / top_value), 1),
                   "label": point["label"], "count": point["count"]} for index, point in enumerate(item["points"])]
        line = " ".join(f"{c['x']},{c['y']}" for c in coords)
        values = [point["count"] for point in item["points"]]
        lines.append({"app": item["app"], "app_label": APP_LABELS.get(item["app"], item["app"]),
                      "color": APP_COLORS.get(item["app"], "#6b7889"), "points": coords, "line": line,
                      "area": f"{coords[0]['x']},{base} {line} {coords[-1]['x']},{base}" if coords else "",
                      "total": sum(values), "peak": max(values, default=0)})
    x_labels = [{"x": xs[index], "label": label} for index, label in enumerate(labels)
                if index % label_every == 0 or index == count - 1]
    ticks = [{"value": value, "y": round(top + plot_height * (1 - value / top_value), 1)}
             for value in range(0, top_value + 1, step)]
    return {"width": width, "height": height, "left": left, "right": width - right, "base": base,
            "series": lines, "x_labels": x_labels, "ticks": ticks, "dense": count > 62,
            "total": sum(combined), "peak": max(combined, default=0)}


REGION_OTHER = "其他"
_REGION_SUFFIXES = ("壮族自治区", "回族自治区", "维吾尔自治区", "特别行政区", "自治区", "省", "市")


def _short_region(name):
    for suffix in _REGION_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            return name[: -len(suffix)]
    return name


def _region_parts(location):
    """(province, city) for a mainland-China location, else (REGION_OTHER, label)."""
    parts = [part for part in str(location or "").split(" · ") if part]
    if len(parts) >= 2 and parts[0] == "中国":
        province = parts[1]
        city = parts[2] if len(parts) >= 3 else parts[1]
        return province, city
    return REGION_OTHER, parts[0] if parts else "未知"


def _query_string(current, **changes):
    """Query string for a link that keeps the current filters except ``changes``."""
    params = {**current, **changes}
    pairs = [(key, params[key]) for key in ("days", "start", "end", "app", "region", "ip", "version", "page")
             if params.get(key) not in (None, "")]
    return "?" + urlencode(pairs) if pairs else "?"


def _session_rows(connection, window_start, window_end):
    """Every run in the window with its IP (exact per-run IP, else inferred from the installation)."""
    sessions = [dict(row) for row in connection.execute("""
        SELECT e.session_id, e.install_id, e.app_version AS version, MIN(e.occurred_at) AS opened_at,
               MAX(e.app) AS app,
               (SELECT s.ip_address FROM session_ips s WHERE s.session_id = e.session_id) AS exact_ip
        FROM events e WHERE e.session_id IN (
            SELECT session_id FROM events WHERE occurred_at BETWEEN ? AND ?
        )
        GROUP BY e.session_id ORDER BY opened_at DESC, e.session_id""", (window_start, window_end))]
    for session in sessions:
        session["ip_inferred"] = session["exact_ip"] is None
        ip = session.pop("exact_ip")
        if ip is None:
            # Earliest observation at or after the run started, else the latest before it.
            row = connection.execute("""SELECT ip_address FROM ip_observations
                WHERE install_id = ? AND app_version = ?
                ORDER BY last_seen_at < ?, CASE WHEN last_seen_at >= ? THEN last_seen_at ELSE -last_seen_at END
                LIMIT 1""", (session["install_id"], session["version"], session["opened_at"], session["opened_at"])).fetchone()
            ip = row[0] if row else None
        session["ip"] = ip
        session["app_label"] = APP_LABELS.get(session["app"], session["app"])
    return sessions


def _locate(sessions, ip_database_dir):
    """Location labels for the runs' distinct IPs (looked up once per address)."""
    unique = [{"ip": ip} for ip in dict.fromkeys(session["ip"] for session in sessions)]
    for start in range(0, len(unique), 200):
        enrich_ip_locations(unique[start:start + 200], ip_database_dir)
    labels = {row["ip"]: row["location"] for row in unique}
    for session in sessions:
        session["location"] = labels.get(session["ip"], "未知")
        session["province"], session["city_full"] = _region_parts(session["location"])
        session["city"] = _city_label(session["location"])


def _session_details(connection, session, now):
    sid, install, opened = session["session_id"], session["install_id"], session["opened_at"]
    session["opened_at_local"] = _local_text(opened)
    startup = connection.execute("""SELECT data_json FROM events WHERE session_id = ? AND name = 'startup'
        ORDER BY occurred_at LIMIT 1""", (sid,)).fetchone()
    environment = json.loads(startup[0]) if startup else {}
    session["environment"] = {
        "os": _os_label(environment.get("os"), environment.get("os_version")) if environment else "未知",
        "os_version": environment.get("os_version") or "未知",
        "architecture": environment.get("architecture") or "未知",
        "screen": (f"{environment['screen_width']}×{environment['screen_height']}"
                   if environment.get("screen_width") else "未知"),
        "scale": f"{round(float(environment['scale']) * 100)}%" if environment.get("scale") else "未知",
        "locale": environment.get("locale") or "未知",
    }
    end = connection.execute("""SELECT json_extract(data_json, '$.duration_s'), json_extract(data_json, '$.active_s')
        FROM events WHERE session_id = ? AND name = 'session_end' ORDER BY occurred_at DESC LIMIT 1""",
                             (sid,)).fetchone()
    # Open time: start to window closing. Active time: foreground with input in
    # the preceding 2 minutes (clients before this field send open time only).
    session["duration_s"] = end[0] if end else None
    session["duration_label"] = _duration_label(session["duration_s"])
    session["active_s"] = end[1] if end else None
    session["active_label"] = (_duration_label(session["active_s"]) if session["active_s"] is not None
                               else ("未记录" if end else "未知"))
    imports = [dict(row) for row in connection.execute("""
        SELECT extension, json_extract(data_json, '$.parser') AS parser,
               json_extract(data_json, '$.instrument') AS instrument, COUNT(*) AS total,
               SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
               SUM(CASE WHEN status='failure' THEN 1 ELSE 0 END) AS failure
        FROM events WHERE session_id = ? AND name = 'import_file'
        GROUP BY extension, parser, instrument ORDER BY total DESC, extension""", (sid,))]
    for item in imports:
        item["parser_label"] = PARSER_LABELS.get(item["parser"], item["parser"] or "未知")
        item["instrument_label"] = INSTRUMENT_LABELS.get(item["instrument"], "未知")
    session["imports"] = imports
    session["import_total"] = sum(item["total"] for item in imports)
    session["import_success"] = sum(item["success"] for item in imports)
    history = connection.execute("""SELECT COUNT(DISTINCT session_id),
            SUM(CASE WHEN name='import_file' THEN 1 ELSE 0 END), MIN(occurred_at)
        FROM events WHERE install_id = ? AND occurred_at <= ?""", (install, now)).fetchone()
    previous = connection.execute("""SELECT MAX(occurred_at) FROM events
        WHERE install_id = ? AND session_id != ? AND occurred_at < ?""", (install, sid, opened)).fetchone()[0]
    session["install"] = {
        "first_run": previous is None,
        "runs": history[0] or 0,
        "imports": history[1] or 0,
        "first_seen_local": _local_text(history[2]) if history[2] else "未知",
        "previous_local": _local_text(previous) if previous else None,
    }


def _matches(session, region, ip, version, app=""):
    if app and session["app"] != app:
        return False
    if version and session["version"] != version:
        return False
    if ip and not (session["ip"] or "").startswith(ip):
        return False
    if region:
        province, _, city = region.partition("/")
        if session["province"] != province or (city and session["city_full"] != city):
            return False
    return True


def _sessions(connection, window_start, window_end, now, page, ip_database_dir, *, region="", ip="", version="",
              app="", current=None):
    """One row per program run, newest first; filters apply to this table only."""
    current = dict(current or {})
    every = _session_rows(connection, window_start, window_end)
    _locate(every, ip_database_dir)
    apps = {}
    for session in every:
        apps[session["app"]] = apps.get(session["app"], 0) + 1
    app_options = [{"value": value, "label": APP_LABELS[value], "count": apps.get(value, 0),
                    "selected": value == app, "query": _query_string(current, app=value, version=None, page=None)}
                   for value in APP_IDS]
    # Region and version menus count the runs of the selected software only.
    scoped = [session for session in every if not app or session["app"] == app]
    provinces = {}
    for session in scoped:
        entry = provinces.setdefault(session["province"], {"count": 0, "cities": {}})
        entry["count"] += 1
        entry["cities"][session["city_full"]] = entry["cities"].get(session["city_full"], 0) + 1
    region_options = []
    for province, entry in sorted(provinces.items(), key=lambda item: (item[0] == REGION_OTHER, -item[1]["count"], item[0])):
        region_options.append({
            "value": province, "label": _short_region(province), "count": entry["count"],
            "query": _query_string(current, region=province, page=None), "selected": region.partition("/")[0] == province,
            "cities": [{"value": f"{province}/{city}", "label": _short_region(city), "count": count,
                        "query": _query_string(current, region=f"{province}/{city}", page=None),
                        "selected": region == f"{province}/{city}"}
                       for city, count in sorted(entry["cities"].items(), key=lambda item: (-item[1], item[0]))],
        })
    versions = {}
    for session in scoped:
        key = (session["app"], session["version"])
        versions[key] = versions.get(key, 0) + 1
    # BET and MIP version numbers overlap (both had 1.1.2), so a version option also selects its software.
    ordered = sorted(versions.items(), key=lambda item: item[0][1], reverse=True)
    ordered.sort(key=lambda item: APP_IDS.index(item[0][0]) if item[0][0] in APP_IDS else len(APP_IDS))
    version_options = [{"value": value, "app": owner, "count": count,
                        "label": value if app else f"{APP_LABELS.get(owner, owner)} {value}",
                        "selected": value == version and owner == (app or owner),
                        "query": _query_string(current, app=owner, version=value, page=None)}
                       for (owner, value), count in ordered]
    rows = [session for session in every if _matches(session, region, ip, version, app)]
    total = len(rows)
    pages = max(1, (total + SESSION_PAGE_SIZE - 1) // SESSION_PAGE_SIZE)
    page = min(page, pages)
    shown = rows[(page - 1) * SESSION_PAGE_SIZE: page * SESSION_PAGE_SIZE]
    for number, session in enumerate(shown, start=(page - 1) * SESSION_PAGE_SIZE + 1):
        session["number"] = number
        session["ip_query"] = _query_string(current, ip=session["ip"], page=None) if session["ip"] else None
        _session_details(connection, session, now)
    region_label = "全部地域"
    if region:
        province, _, city = region.partition("/")
        region_label = _short_region(city) if city else _short_region(province)
    filters = {
        "app": app, "app_label": APP_LABELS.get(app, "全部软件"), "app_options": app_options,
        "version_label": (version if not app else f"{APP_LABELS[app]} {version}") if version else "全部版本",
        "region": region, "region_label": region_label, "ip": ip, "version": version,
        "region_options": region_options, "version_options": version_options,
        "clear": {**{name: _query_string(current, **{name: None}, page=None) for name in ("region", "ip", "version")},
                  "app": _query_string(current, app=None, version=None, page=None)},
        "clear_all": _query_string({key: current.get(key) for key in ("days", "start", "end")}),
        "active": bool(app or region or ip or version),
    }
    return shown, {"page": page, "per_page": SESSION_PAGE_SIZE, "total": total, "total_pages": pages,
                   "has_previous": page > 1, "has_next": page < pages,
                   "previous_query": _query_string(current, page=page - 1) if page > 1 else None,
                   "next_query": _query_string(current, page=page + 1) if page < pages else None,
                   "window_total": len(every)}, filters


def _day_start(day):
    return int(datetime.combine(day, datetime.min.time(), DISPLAY_TIMEZONE).timestamp())


def _summary(path, now, ip_database_dir=None, *, window_days=30, ip_scope="window", ip_page=1, page=1,
             start_date=None, end_date=None, region="", ip="", version="", app=""):
    """``window_days`` is one of WINDOW_LABELS, or None for all recorded data.

    ``start_date``/``end_date`` (Beijing dates, inclusive) replace the rolling window.
    ``app`` (software), ``region`` ("省份" or "省份/城市" or "其他"), ``ip`` (prefix) and ``version``
    filter the run table only; the charts always draw one line per software.
    """
    if (window_days is not None and window_days not in WINDOW_LABELS) or ip_scope not in {"window", "all"}:
        raise ValueError("Unsupported statistics window or IP scope")
    if type(ip_page) is not int or ip_page < 1 or type(page) is not int or page < 1:
        raise ValueError("Invalid IP page")
    if app and app not in APP_IDS:
        raise ValueError("Unsupported software")
    today = datetime.fromtimestamp(now, DISPLAY_TIMEZONE).date()
    custom = start_date is not None
    matched_preset = None
    if custom:
        end_date = end_date or start_date
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        end_date = min(end_date, today)
        start_date = min(start_date, end_date)
        # Highlight a matching preset without discarding the explicitly requested
        # calendar boundaries: a rolling timestamp cutoff can omit the first day's
        # morning events even though the date field includes that whole day.
        for days in WINDOW_LABELS:
            if end_date == today and start_date == datetime.fromtimestamp(now - days * DAY, DISPLAY_TIMEZONE).date():
                matched_preset = window_days = days
                break
    if custom:
        cutoff = _day_start(start_date)
        window_end = min(now, _day_start(end_date + timedelta(days=1)) - 1)
    else:
        start_date = end_date = None
        cutoff = now - window_days * DAY if window_days is not None else 0
        window_end = now
    current = {"days": None if custom else ("all" if window_days is None else str(window_days)),
               "start": start_date.isoformat() if custom else None, "end": end_date.isoformat() if custom else None,
               "app": app or None, "region": region or None, "ip": ip or None, "version": version or None}
    with _database(path) as connection:
        connection.execute("BEGIN")  # One consistent read snapshot across tables.

        def rows(sql, parameters=()):
            return [dict(row) for row in connection.execute(sql, parameters)]

        active = {f"{days}d": connection.execute(
            "SELECT COUNT(DISTINCT install_id) FROM events WHERE occurred_at BETWEEN ? AND ?",
            (now - days * DAY, now)).fetchone()[0] for days in (7, 30)}
        window_active = (active["30d"] if window_days == 30 and not custom else connection.execute(
            "SELECT COUNT(DISTINCT install_id) FROM events WHERE occurred_at BETWEEN ? AND ?",
            (cutoff, window_end)).fetchone()[0])
        versions = rows("""WITH latest AS (
            SELECT install_id, app_version, app,
                   ROW_NUMBER() OVER (PARTITION BY install_id ORDER BY occurred_at DESC, received_at DESC, event_id DESC) AS rank
            FROM events WHERE occurred_at BETWEEN ? AND ?
        ) SELECT app, app_version AS version, COUNT(*) AS installs FROM latest WHERE rank = 1
          GROUP BY app, app_version ORDER BY installs DESC, app, app_version LIMIT 100""", (cutoff, window_end))
        for version_row in versions:
            version_row["app_label"] = APP_LABELS.get(version_row["app"], version_row["app"])
            version_row["share"] = version_row["installs"] / window_active if window_active else 0
        last_day = end_date if custom else today
        if custom:
            first_day = start_date
        elif window_days is None:
            earliest = connection.execute("SELECT MIN(occurred_at) FROM events WHERE occurred_at <= ?", (now,)).fetchone()[0]
            first_day = datetime.fromtimestamp(earliest, DISPLAY_TIMEZONE).date() if earliest is not None else today
        else:
            first_day = datetime.fromtimestamp(cutoff, DISPLAY_TIMEZONE).date()
        startup_counts = {(row["day"], row["app"]): row["count"] for row in rows("""
            SELECT strftime('%Y-%m-%d', occurred_at, 'unixepoch', '+8 hours') AS day, app, COUNT(*) AS count
            FROM events WHERE name = 'startup' AND occurred_at BETWEEN ? AND ? GROUP BY day, app""", (cutoff, window_end))}
        startups = []
        for index in range((last_day - first_day).days + 1):
            day = (first_day + timedelta(days=index)).isoformat()
            by_app = {owner: startup_counts.get((day, owner), 0) for owner in APP_IDS}
            startups.append({"date": day, "count": sum(by_app.values()), "by_app": by_app})
        # "当天" is today, or the last day of a custom range that ends before today.
        day_start = _day_start(last_day)
        day_end = min(window_end, day_start + DAY - 1)
        hourly_counts = {(row["hour"], row["app"]): row["count"] for row in rows("""
            SELECT CAST(strftime('%H', occurred_at, 'unixepoch', '+8 hours') AS INTEGER) AS hour, app, COUNT(*) AS count
            FROM events WHERE name = 'startup' AND occurred_at BETWEEN ? AND ? GROUP BY hour, app""", (day_start, day_end))}
        last_hour = datetime.fromtimestamp(day_end, DISPLAY_TIMEZONE).hour if last_day == today else 23
        hourly = []
        for hour in range(last_hour + 1):
            by_app = {owner: hourly_counts.get((hour, owner), 0) for owner in APP_IDS}
            hourly.append({"hour": hour, "count": sum(by_app.values()), "by_app": by_app})
        sessions, session_pagination, filters = _sessions(
            connection, cutoff, window_end, now, page, ip_database_dir,
            region=region, ip=ip, version=version, app=app, current=current)
        imports = rows("""SELECT app, extension, COUNT(*) AS total,
            SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
            SUM(CASE WHEN status='failure' THEN 1 ELSE 0 END) AS failure,
            ROUND(AVG(duration_ms), 1) AS average_duration_ms
            FROM events WHERE name='import_file' AND occurred_at BETWEEN ? AND ?
            GROUP BY app, extension ORDER BY app, total DESC, extension""", (cutoff, window_end))
        for item in imports:
            item["success_rate"] = item["success"] / item["total"]
            item["app_label"] = APP_LABELS.get(item["app"], item["app"])
        import_total = sum(item["total"] for item in imports)
        import_success = sum(item["success"] for item in imports)
        features = rows("""SELECT app, feature, surface, COUNT(*) AS views FROM events
            WHERE name='feature_view' AND occurred_at BETWEEN ? AND ?
            GROUP BY app, feature, surface ORDER BY views DESC, app, feature, surface""", (cutoff, window_end))
        exports = rows("""SELECT app, export_format AS format, COUNT(*) AS total,
            SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
            SUM(CASE WHEN status='failure' THEN 1 ELSE 0 END) AS failure,
            ROUND(AVG(duration_ms), 1) AS average_duration_ms
            FROM events WHERE name='export' AND occurred_at BETWEEN ? AND ?
            GROUP BY app, export_format ORDER BY total DESC, app, export_format""", (cutoff, window_end))
        errors = rows("""SELECT app, name AS event, error_code, COUNT(*) AS count FROM events
            WHERE status='failure' AND occurred_at BETWEEN ? AND ?
            GROUP BY app, name, error_code ORDER BY count DESC, app, name, error_code LIMIT 20""", (cutoff, window_end))
        environments = rows("""WITH latest AS (
            SELECT app, data_json, ROW_NUMBER() OVER (
                PARTITION BY install_id ORDER BY occurred_at DESC, received_at DESC, event_id DESC) AS rank
            FROM events WHERE name='startup' AND occurred_at BETWEEN ? AND ?
        ) SELECT app, json_extract(data_json, '$.os') AS os,
                 json_extract(data_json, '$.os_version') AS os_version,
                 json_extract(data_json, '$.architecture') AS architecture,
                 json_extract(data_json, '$.screen_width') AS screen_width,
                 json_extract(data_json, '$.screen_height') AS screen_height,
                 json_extract(data_json, '$.scale') AS scale, COUNT(*) AS installs
          FROM latest WHERE rank=1
          GROUP BY app, os, os_version, architecture, screen_width, screen_height, scale
          ORDER BY installs DESC, app, os, os_version, architecture, screen_width, screen_height, scale LIMIT 20""", (cutoff, window_end))
        for item in environments:
            item["app_label"] = APP_LABELS.get(item["app"], item["app"])
        ip_where = "last_seen_at BETWEEN ? AND ?" if ip_scope == "window" else "last_seen_at <= ?"
        ip_parameters = (cutoff, window_end) if ip_scope == "window" else (now,)
        total_ip_groups = connection.execute(f"""SELECT COUNT(*) FROM (
            SELECT 1 FROM ip_observations WHERE {ip_where} GROUP BY ip_address, app_version
        )""", ip_parameters).fetchone()[0]
        total_ip_pages = max(1, (total_ip_groups + IP_PAGE_SIZE - 1) // IP_PAGE_SIZE)
        ip_page = min(ip_page, total_ip_pages)
        ips = rows(f"""SELECT ip_address AS ip, app_version AS version,
            MAX(last_seen_at) AS last_seen_at, COUNT(DISTINCT install_id) AS installations
            FROM ip_observations WHERE {ip_where}
            GROUP BY ip_address, app_version ORDER BY last_seen_at DESC, ip_address, app_version
            LIMIT ? OFFSET ?""", (*ip_parameters, IP_PAGE_SIZE, (ip_page - 1) * IP_PAGE_SIZE))
        for item in ips:
            item["last_seen_at_local"] = _local_text(item["last_seen_at"])
            item["last_seen_at"] = _utc_text(item["last_seen_at"])
        used_bytes = connection.execute("PRAGMA page_count").fetchone()[0] * connection.execute("PRAGMA page_size").fetchone()[0]
        database_ratio = used_bytes / MAX_DATABASE_BYTES
    enrich_ip_locations(ips, ip_database_dir)
    if matched_preset is not None:
        window_label = WINDOW_LABELS[matched_preset]
    elif custom:
        window_label = (start_date.isoformat() if start_date == end_date
                        else f"{start_date.isoformat()} 至 {end_date.isoformat()}")
    else:
        window_label = WINDOW_LABELS[window_days] if window_days is not None else ALL_WINDOW_LABEL
    range_first = first_day
    day_series = [{"app": owner, "points": [{"label": row["date"], "count": row["by_app"][owner]} for row in startups]}
                  for owner in APP_IDS]
    hour_series = [{"app": owner, "points": [{"label": f"{row['hour']:02d}:00", "count": row["by_app"][owner]}
                                             for row in hourly]} for owner in APP_IDS]
    return {
        "generated_at": _utc_text(now), "generated_at_local": _local_text(now),
        "timezone": "Asia/Shanghai", "timezone_label": "北京时间（UTC+8）", "window_days": window_days,
        "window_query": (str(matched_preset) if matched_preset is not None
                         else "custom" if custom else ("all" if window_days is None else str(window_days))),
        "date_range": {"start": range_first.isoformat(), "end": last_day.isoformat(),
                       "custom": custom and matched_preset is None,
                       "today": today.isoformat(), "day_label": "当天" if last_day == today else last_day.isoformat()[5:]},
        "filters": filters,
        "window_label": window_label, "window_start_at": _utc_text(cutoff), "window_end_at": _utc_text(window_end),
        "window_start_at_local": _local_text(cutoff), "window_end_at_local": _local_text(window_end),
        "period_options": [{"days": days, "label": label,
                            "query": _query_string({**current, "start": None, "end": None}, days=str(days), page=None)}
                           for days, label in WINDOW_LABELS.items()]
                          + [{"days": "all", "label": ALL_WINDOW_LABEL,
                              "query": _query_string({**current, "start": None, "end": None}, days="all", page=None)}],
        "sessions": sessions, "session_pagination": session_pagination,
        "hourly_startups_today": hourly,
        "charts": {"today": _chart(hour_series, label_every=2),
                   "period": _chart(day_series, label_every=max(1, len(startups) // 10))},
        "apps": [{"value": owner, "label": APP_LABELS[owner], "color": APP_COLORS[owner]} for owner in APP_IDS],
        "window_active_installs": window_active,
        "retention": {"events_days": EVENT_RETENTION_DAYS, "ip_days": IP_RETENTION_DAYS},
        "database_usage": {"used_bytes": used_bytes, "max_bytes": MAX_DATABASE_BYTES,
                           "ratio": database_ratio, "warning": database_ratio >= 0.8},
        "ip_scope": ip_scope,
        "ip_pagination": {"page": ip_page, "per_page": IP_PAGE_SIZE, "total_groups": total_ip_groups,
                          "total_pages": total_ip_pages, "has_previous": ip_page > 1, "has_next": ip_page < total_ip_pages},
        "active_installs": active, "version_share": versions, "daily_startups": startups,
        "imports": {"total": import_total, "success": import_success, "failure": import_total - import_success,
                    "success_rate": import_success / import_total if import_total else None, "by_extension": imports},
        "feature_views": features,
        "exports": {"total": sum(item["total"] for item in exports), "success": sum(item["success"] for item in exports),
                    "failure": sum(item["failure"] for item in exports), "by_format": exports},
        "environments": environments, "errors": errors, "recent_ips": ips,
    }


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("BET_TELEMETRY_DB", str(Path(app.instance_path) / "telemetry.sqlite3")),
        IP_DATABASE_DIR=os.environ.get("BET_TELEMETRY_IP_DATABASE_DIR", str(Path(app.instance_path) / "ip-location")),
        ADMIN_USERNAME=os.environ.get("BET_TELEMETRY_ADMIN_USER", ""),
        ADMIN_PASSWORD=os.environ.get("BET_TELEMETRY_ADMIN_PASSWORD", ""),
        TRUST_PROXY=os.environ.get("BET_TELEMETRY_TRUST_PROXY", "0") == "1",
        MAX_CONTENT_LENGTH=MAX_BODY_BYTES,
        TIME_SOURCE=time.time,
        RATE_LIMIT_PER_MINUTE=60,
        RATE_LIMIT_GLOBAL_PER_MINUTE=600,
    )
    if config:
        app.config.update(config)
    app.config["MAX_CONTENT_LENGTH"] = min(int(app.config["MAX_CONTENT_LENGTH"]), MAX_BODY_BYTES)
    app.config["DATABASE"] = initialize_database(app.config["DATABASE"])
    events_guard = _RateGuard(app.config["RATE_LIMIT_PER_MINUTE"], app.config["RATE_LIMIT_GLOBAL_PER_MINUTE"])
    admin_guard = _RateGuard(20, 200)

    def now():
        return int(app.config["TIME_SOURCE"]())

    def rate_error():
        return jsonify(error="rate_limited"), 429, {"Retry-After": "60"}

    def summary_options():
        names = ("days", "ip_scope", "ip_page", "page", "start", "end", "app", "region", "ip", "version")
        for name in names:
            if len(request.args.getlist(name)) > 1:
                raise ValueError("Duplicate statistics option")
        raw_days = request.args.get("days", "30")
        scope = request.args.get("ip_scope", "window")
        pages = {}
        for name in ("ip_page", "page"):
            raw_page = request.args.get(name, "1")
            if not raw_page.isascii() or not raw_page.isdecimal() or len(raw_page) > 20 or int(raw_page) < 1:
                raise ValueError("Invalid page")
            pages[name] = int(raw_page)
        if raw_days not in {str(days) for days in WINDOW_LABELS} | {"all"} or scope not in {"window", "all"}:
            raise ValueError("Unsupported statistics option")
        dates = {}
        for name in ("start", "end"):
            raw = request.args.get(name, "").strip()
            if raw:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                    raise ValueError("Invalid date")
                value = datetime.strptime(raw, "%Y-%m-%d").date()
                if value.year < 2000:
                    raise ValueError("Invalid date")
                dates[name] = value
        if "end" in dates and "start" not in dates:
            dates["start"] = dates["end"]
        region = request.args.get("region", "").strip()
        ip = request.args.get("ip", "").strip()
        version = request.args.get("version", "").strip()
        software = request.args.get("app", "").strip()
        if software and software not in APP_IDS:
            raise ValueError("Invalid software filter")
        if len(region) > 80 or any(ord(character) < 32 for character in region):
            raise ValueError("Invalid region")
        if ip and not re.fullmatch(r"[0-9A-Fa-f:.]{1,45}", ip):
            raise ValueError("Invalid IP filter")
        if version and not re.fullmatch(r"[0-9A-Za-z.+_-]{1,32}", version):
            raise ValueError("Invalid version filter")
        return {"window_days": None if raw_days == "all" else int(raw_days), "ip_scope": scope, **pages,
                "start_date": dates.get("start"), "end_date": dates.get("end"),
                "region": region, "ip": ip, "version": version, "app": software}

    def admin_authorization():
        username, password = app.config["ADMIN_USERNAME"], app.config["ADMIN_PASSWORD"]
        if not username or not password:
            return jsonify(error="not_found"), 404
        peer = request.environ.get("REMOTE_ADDR", "")
        secure_proxy = (app.config["TRUST_PROXY"] and peer in {"127.0.0.1", "::1"}
                        and request.headers.get("X-Forwarded-Proto") == "https")
        local_development = not app.config["TRUST_PROXY"] and peer in {"127.0.0.1", "::1"}
        if not (request.is_secure or secure_proxy or local_development):
            return jsonify(error="https_required"), 403
        if not admin_guard.allow(_client_ip(app.config["TRUST_PROXY"]) or "unknown", now()):
            return rate_error()
        auth = request.authorization
        candidate_user = auth.username if auth and auth.type == "basic" and auth.username else ""
        candidate_password = auth.password if auth and auth.type == "basic" and auth.password else ""
        user_matches = secrets.compare_digest(candidate_user.encode("utf-8"), username.encode("utf-8"))
        password_matches = secrets.compare_digest(candidate_password.encode("utf-8"), password.encode("utf-8"))
        if not (user_matches and password_matches):
            return Response("需要管理员认证。", status=401,
                            headers={"WWW-Authenticate": 'Basic realm="BET telemetry", charset="UTF-8"'},
                            content_type="text/plain; charset=utf-8")
        return None

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="body_too_large"), 413

    @app.errorhandler(sqlite3.Error)
    def database_error(_error):
        # Do not echo SQL, stored values, request bodies, credentials or IPs.
        return jsonify(error="storage_unavailable"), 503

    @app.get("/healthz")
    def health():
        return jsonify(ok=True)

    @app.post("/api/v1/events")
    def receive_events():
        timestamp = now()
        address = _client_ip(app.config["TRUST_PROXY"])
        if not events_guard.allow(address or "unknown", timestamp):
            return rate_error()
        try:
            if not request.is_json:
                raise UnsupportedMediaType()
            decoded = json.loads(request.get_data(cache=False), object_pairs_hook=_json_object,
                                 parse_constant=_invalid_json_constant)
            batch = validate_batch(decoded)
        except UnsupportedMediaType:
            return jsonify(error="json_required"), 415
        except (BadRequest, ValueError, TypeError, RecursionError):
            return jsonify(error="invalid_batch"), 400
        try:
            future_event = any(_event_time(event["occurred_at"]) > timestamp + 300 for event in batch["events"])
        except (ValueError, OverflowError, OSError):
            return jsonify(error="invalid_timestamp"), 400
        if future_event:
            return jsonify(error="invalid_timestamp"), 400
        _save_batch(app.config["DATABASE"], batch, address, timestamp)
        return jsonify(accepted=[event["event_id"] for event in batch["events"]])

    @app.get("/admin")
    def admin_page():
        denied = admin_authorization()
        if denied is not None:
            return denied
        try:
            options = summary_options()
        except ValueError:
            return jsonify(error="invalid_query"), 400
        return render_template("admin.html", summary=_summary(app.config["DATABASE"], now(), app.config["IP_DATABASE_DIR"], **options))

    @app.get("/admin/summary")
    def admin_summary():
        denied = admin_authorization()
        if denied is not None:
            return denied
        try:
            options = summary_options()
        except ValueError:
            return jsonify(error="invalid_query"), 400
        return jsonify(_summary(app.config["DATABASE"], now(), app.config["IP_DATABASE_DIR"], **options))

    return app
