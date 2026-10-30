from __future__ import annotations

import ipaddress
import calendar
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from bet_telemetry.schema import MAX_BODY_BYTES, validate_batch


EVENT_RETENTION_DAYS = 90
IP_RETENTION_DAYS = 7
DAY = 86400
MAX_DATABASE_BYTES = 512 * 1024 * 1024

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
    duration_ms INTEGER
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
    if os.name != "nt":
        path.chmod(0o600)
    return str(path)


def purge_database(path, *, now=None):
    """Run from the scheduled CLI even when the service receives no traffic."""
    now = int(time.time() if now is None else now)
    with _database(path) as connection:
        with connection:
            events = connection.execute("DELETE FROM events WHERE occurred_at < ?", (now - EVENT_RETENTION_DAYS * DAY,)).rowcount
            ips = connection.execute("DELETE FROM ip_observations WHERE last_seen_at < ?", (now - IP_RETENTION_DAYS * DAY,)).rowcount
        # secure_delete zeros removed records; truncate old WAL frames too.
        checkpoint = tuple(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
    return {"events_deleted": events, "ip_observations_deleted": ips, "checkpoint_busy": bool(checkpoint[0])}


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
    rows = []
    for event in batch["events"]:
        occurred_at = _event_time(event["occurred_at"])
        # Acknowledge already-expired queued events without retaining them.
        if occurred_at < now - EVENT_RETENTION_DAYS * DAY:
            continue
        data = event["data"]
        rows.append((event["event_id"], batch["install_id"], batch["session_id"],
                     batch["app_version"], event["name"], occurred_at, now,
                     json.dumps(data, ensure_ascii=True, separators=(",", ":")),
                     data.get("status"), data.get("extension"), data.get("feature"),
                     data.get("surface"), data.get("format"), data.get("error_code"), data.get("duration_ms")))
    with _database(path) as connection:
        with connection:
            connection.executemany("INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            if ip_address is not None:
                connection.execute("""INSERT INTO ip_observations VALUES (?, ?, ?, ?)
                    ON CONFLICT(install_id, ip_address, app_version)
                    DO UPDATE SET last_seen_at = excluded.last_seen_at""",
                    (batch["install_id"], ip_address, batch["app_version"], now))


def _summary(path, now):
    cutoff = now - 30 * DAY
    with _database(path) as connection:
        connection.execute("BEGIN")  # One consistent read snapshot across tables.

        def rows(sql, parameters=()):
            return [dict(row) for row in connection.execute(sql, parameters)]

        active = {f"{days}d": connection.execute(
            "SELECT COUNT(DISTINCT install_id) FROM events WHERE occurred_at BETWEEN ? AND ?",
            (now - days * DAY, now)).fetchone()[0] for days in (7, 30)}
        versions = rows("""WITH latest AS (
            SELECT install_id, app_version,
                   ROW_NUMBER() OVER (PARTITION BY install_id ORDER BY occurred_at DESC, received_at DESC, event_id DESC) AS rank
            FROM events WHERE occurred_at BETWEEN ? AND ?
        ) SELECT app_version AS version, COUNT(*) AS installs FROM latest WHERE rank = 1
          GROUP BY app_version ORDER BY installs DESC, app_version LIMIT 100""", (cutoff, now))
        for version in versions:
            version["share"] = version["installs"] / active["30d"] if active["30d"] else 0
        today = datetime.fromtimestamp(now, timezone.utc).date()
        first_day = today - timedelta(days=29)
        midnight = int(datetime.combine(first_day, datetime.min.time(), timezone.utc).timestamp())
        startup_counts = {row["day"]: row["count"] for row in rows("""
            SELECT strftime('%Y-%m-%d', occurred_at, 'unixepoch') AS day, COUNT(*) AS count
            FROM events WHERE name = 'startup' AND occurred_at BETWEEN ? AND ? GROUP BY day""", (midnight, now))}
        startups = [{"date": (first_day + timedelta(days=index)).isoformat(),
                     "count": startup_counts.get((first_day + timedelta(days=index)).isoformat(), 0)} for index in range(30)]
        imports = rows("""SELECT extension, COUNT(*) AS total,
            SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
            SUM(CASE WHEN status='failure' THEN 1 ELSE 0 END) AS failure,
            ROUND(AVG(duration_ms), 1) AS average_duration_ms
            FROM events WHERE name='import_file' AND occurred_at BETWEEN ? AND ?
            GROUP BY extension ORDER BY total DESC, extension""", (cutoff, now))
        for item in imports:
            item["success_rate"] = item["success"] / item["total"]
        import_total = sum(item["total"] for item in imports)
        import_success = sum(item["success"] for item in imports)
        features = rows("""SELECT feature, surface, COUNT(*) AS views FROM events
            WHERE name='feature_view' AND occurred_at BETWEEN ? AND ?
            GROUP BY feature, surface ORDER BY views DESC, feature, surface""", (cutoff, now))
        exports = rows("""SELECT export_format AS format, COUNT(*) AS total,
            SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
            SUM(CASE WHEN status='failure' THEN 1 ELSE 0 END) AS failure,
            ROUND(AVG(duration_ms), 1) AS average_duration_ms
            FROM events WHERE name='export' AND occurred_at BETWEEN ? AND ?
            GROUP BY export_format ORDER BY total DESC, export_format""", (cutoff, now))
        errors = rows("""SELECT name AS event, error_code, COUNT(*) AS count FROM events
            WHERE status='failure' AND occurred_at BETWEEN ? AND ?
            GROUP BY name, error_code ORDER BY count DESC, name, error_code LIMIT 20""", (cutoff, now))
        environments = rows("""WITH latest AS (
            SELECT data_json, ROW_NUMBER() OVER (
                PARTITION BY install_id ORDER BY occurred_at DESC, received_at DESC, event_id DESC) AS rank
            FROM events WHERE name='startup' AND occurred_at BETWEEN ? AND ?
        ) SELECT json_extract(data_json, '$.os') AS os,
                 json_extract(data_json, '$.os_version') AS os_version,
                 json_extract(data_json, '$.architecture') AS architecture,
                 json_extract(data_json, '$.screen_width') AS screen_width,
                 json_extract(data_json, '$.screen_height') AS screen_height,
                 json_extract(data_json, '$.scale') AS scale, COUNT(*) AS installs
          FROM latest WHERE rank=1
          GROUP BY os, os_version, architecture, screen_width, screen_height, scale
          ORDER BY installs DESC, os, os_version, architecture, screen_width, screen_height, scale LIMIT 20""", (cutoff, now))
        ips = rows("""SELECT ip_address AS ip, app_version AS version,
            MAX(last_seen_at) AS last_seen_at, COUNT(DISTINCT install_id) AS installations
            FROM ip_observations WHERE last_seen_at BETWEEN ? AND ?
            GROUP BY ip_address, app_version ORDER BY last_seen_at DESC, ip_address LIMIT 200""", (now - IP_RETENTION_DAYS * DAY, now))
        for item in ips:
            item["last_seen_at"] = _utc_text(item["last_seen_at"])
    return {
        "generated_at": _utc_text(now), "timezone": "UTC", "window_days": 30,
        "retention": {"events_days": EVENT_RETENTION_DAYS, "ip_days": IP_RETENTION_DAYS},
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
        return render_template("admin.html", summary=_summary(app.config["DATABASE"], now()))

    @app.get("/admin/summary")
    def admin_summary():
        denied = admin_authorization()
        if denied is not None:
            return denied
        return jsonify(_summary(app.config["DATABASE"], now()))

    return app
