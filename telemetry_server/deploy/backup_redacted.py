"""Bounded-memory event backup; never read or copy the IP observation table."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from uuid import uuid4


EVENT_WINDOW_DAYS = 80
BACKUP_LIFETIME_DAYS = 7
COLUMNS = (
    "event_id", "install_id", "session_id", "app_version", "name",
    "occurred_at", "received_at", "data_json", "status", "extension",
    "feature", "surface", "export_format", "error_code", "duration_ms",
)
CREATE_EVENTS = """
CREATE TABLE events (
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
)
"""
BACKUP_NAME = re.compile(r"telemetry-redacted-(\d{8}T\d{6}Z)-[0-9a-f]{8}\.sqlite3\Z")
PENDING_NAME = re.compile(r"\.telemetry-redacted-[A-Za-z0-9_-]+\.pending(?:-journal|-wal|-shm)?\Z")


def create_backup(database: Path, directory: Path) -> dict:
    database = database.resolve(strict=True)
    if not database.is_file():
        raise ValueError("Source database must be a file")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = int(time.time())
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = directory / f"telemetry-redacted-{stamp}-{uuid4().hex[:8]}.sqlite3"
    with tempfile.NamedTemporaryFile(prefix=".telemetry-redacted-", suffix=".pending", dir=directory, delete=False) as stream:
        pending = Path(stream.name)
    pending.chmod(0o600)
    source = None
    target = None
    count = 0
    try:
        source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        target = sqlite3.connect(pending, timeout=30)
        target.execute(CREATE_EVENTS)
        # This fixed SELECT deliberately cannot touch ip_observations.
        rows = source.execute(
            f"SELECT {','.join(COLUMNS)} FROM events WHERE occurred_at >= ?",
            (now - EVENT_WINDOW_DAYS * 86400,),
        )
        insert = f"INSERT INTO events ({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})"
        while batch := rows.fetchmany(1000):
            target.executemany(insert, batch)
            count += len(batch)
        target.commit()
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed")
        target.close()
        target = None
        source.close()
        source = None
        pending.replace(output)
        return {"path": str(output), "events": count, "ip_observations": 0, "event_window_days": EVENT_WINDOW_DAYS}
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        pending.unlink(missing_ok=True)
        for suffix in ("-journal", "-wal", "-shm"):
            Path(str(pending) + suffix).unlink(missing_ok=True)


def prune_backups(directory: Path) -> dict:
    if not directory.exists():
        return {"deleted": 0}
    cutoff = time.time() - BACKUP_LIFETIME_DAYS * 86400
    count = 0
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        match = BACKUP_NAME.fullmatch(path.name)
        if match:
            try:
                created = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
        elif PENDING_NAME.fullmatch(path.name):
            # A process crash can leave a partial, already IP-free snapshot.
            created = path.stat().st_mtime
        else:
            continue
        if created < cutoff:
            path.unlink()
            count += 1
    return {"deleted": count}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--db", type=Path, required=True)
    create.add_argument("--directory", type=Path, required=True)
    prune = commands.add_parser("prune")
    prune.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    result = create_backup(args.db, args.directory) if args.command == "create" else prune_backups(args.directory)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
