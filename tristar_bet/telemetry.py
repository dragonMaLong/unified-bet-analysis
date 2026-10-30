"""Optional, bounded product telemetry with no dependency on Qt.

New installations default to enabled for the configured endpoint once the
preference is saved. Existing preferences are preserved. Disabling drops queued events and the installation ID.
A request already handed to the transport may finish after consent is revoked.
"""

from __future__ import annotations

import json
import errno
import os
import re
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from bet_telemetry.schema import MAX_BATCH_EVENTS, MAX_BODY_BYTES, SCHEMA_VERSION, validate_batch, validate_event


DEFAULT_ENDPOINT = ""
MAX_QUEUE_EVENTS = 500
MAX_QUEUE_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_STATE_BYTES = 1_000_000
MAX_ACK_BYTES = 16_384
NETWORK_TIMEOUT_SECONDS = 3.0
RETRY_BASE_SECONDS = 5.0
RETRY_MAX_SECONDS = 300.0
_STATE_VERSION = 1
_STATE_FILENAME = "telemetry_state.json"


def _endpoint(value: object) -> str:
    """Reject insecure/non-HTTP destinations and URL credentials."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > 2048 or any(ord(char) < 33 for char in value):
        return ""
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port  # Also validates malformed and out-of-range ports.
        if not host or parts.username is not None or parts.password is not None or parts.fragment:
            return ""
        if parts.scheme != "https" and not (
            parts.scheme == "http" and host.lower() in {"localhost", "127.0.0.1", "::1"}
        ):
            return ""
        host = host.encode("idna").decode("ascii").lower()
        authority = f"[{host}]" if ":" in host else host
        if port is not None:
            authority += f":{port}"
        return urlunsplit((parts.scheme, authority, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        return ""


def configured_endpoint() -> str:
    """Read env, then EXE/source-root config, then the disabled default.

    An explicitly empty or invalid configuration disables delivery rather than
    falling through to a different recipient. Configuration contains no secret.
    """
    try:
        if "BET_TELEMETRY_URL" in os.environ:
            return _endpoint(os.environ["BET_TELEMETRY_URL"])
        if getattr(sys, "frozen", False):
            directories = [Path(sys.executable).resolve().parent]
            if getattr(sys, "_MEIPASS", None):
                directories.append(Path(sys._MEIPASS))
        else:
            directories = [Path(__file__).resolve().parents[1]]
        for directory in directories:
            config = directory / "telemetry_config.json"
            if config.exists():
                with config.open("rb") as stream:
                    raw = stream.read(4097)
                if len(raw) > 4096:
                    return ""
                value = json.loads(raw)
                if type(value) is not dict or set(value) != {"endpoint"}:
                    return ""
                return _endpoint(value["endpoint"])
        return _endpoint(DEFAULT_ENDPOINT)
    except Exception:
        return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _send_batch(endpoint: str, batch: dict, *, cancelled: threading.Event) -> set[str]:
    """Send once; follow no redirects and accept only bounded, explicit ACKs."""
    destination = _endpoint(endpoint)
    if not destination or cancelled.is_set():
        return set()
    safe = validate_batch(batch)
    body = json.dumps(safe, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("Telemetry batch too large")
    request = urllib.request.Request(
        destination,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": f"BET-Telemetry/{safe['app_version']}"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    if cancelled.is_set():
        return set()
    with opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
        if not 200 <= response.status < 300:
            raise ValueError("Telemetry request was not accepted")
        raw = response.read(MAX_ACK_BYTES + 1)
    if len(raw) > MAX_ACK_BYTES:
        raise ValueError("Telemetry acknowledgement too large")
    acknowledgement = json.loads(raw)
    if type(acknowledgement) is not dict or set(acknowledgement) != {"accepted"}:
        raise ValueError("Invalid telemetry acknowledgement")
    accepted = acknowledgement["accepted"]
    if type(accepted) is not list or len(accepted) > MAX_BATCH_EVENTS * 2 or any(type(item) is not str for item in accepted):
        raise ValueError("Invalid telemetry acknowledgement")
    sent_ids = {event["event_id"] for event in safe["events"]}
    return set(accepted) & sent_ids


def _random_uuid(value: object) -> bool:
    try:
        return type(value) is str and UUID(value).version == 4 and str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _event_time(event: dict) -> float:
    return datetime.strptime(event["occurred_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


class TelemetryClient:
    """A best-effort client; public operations do not raise telemetry errors.

    ``set_enabled`` returns whether the preference was saved. ``record`` returns
    whether an allowlisted event was queued. No background thread is created
    until an enabled preference and a valid endpoint are both available. Network operations
    run exclusively on a daemon thread, and ``close`` never waits for them.
    """

    def __init__(self, storage_dir: Path, app_version: str, endpoint: str = "") -> None:
        self._condition = threading.Condition(threading.RLock())
        self._state_path = Path(storage_dir) / _STATE_FILENAME
        self._app_version = app_version
        self._endpoint = _endpoint(endpoint)
        self._version_valid = type(app_version) is str and re.fullmatch(r"[0-9][0-9A-Za-z.+_-]{0,31}", app_version) is not None
        self._storage_lock = None
        self._storage_busy = False
        self._consent: bool | None = None
        self._install_id: str | None = None
        self._session_id: str | None = None
        self._queue: list[dict] = []
        self._closed = False
        self._generation = 0
        self._cancelled = threading.Event()
        self._worker: threading.Thread | None = None
        self._next_attempt = 0.0
        self._failures = 0
        with self._condition:
            self._acquire_storage_locked()
            if self._storage_lock is None:
                return
            self._load_locked()
            if self._active_locked():
                # A readable but unwritable state must not silently authorize
                # delivery that the application could not subsequently revoke.
                if self._save_locked():
                    self._session_id = str(uuid4())
                    self._start_worker_locked()
                else:
                    self._consent = False
                    self._install_id = None
                    self._queue.clear()

    @property
    def enabled(self) -> bool:
        with self._condition:
            return self._active_locked()

    @property
    def consent_decided(self) -> bool:
        with self._condition:
            return self._consent is not None

    @property
    def available(self) -> bool:
        return bool(self._endpoint and self._version_valid and self._storage_lock is not None)

    @property
    def storage_busy(self) -> bool:
        """Another client owns this storage; this client neither reads nor sends."""
        return self._storage_busy

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def pending_count(self) -> int:
        with self._condition:
            cutoff = time.time() - MAX_QUEUE_AGE_SECONDS
            return sum(_event_time(entry["event"]) >= cutoff for entry in self._queue)

    def set_enabled(self, enabled: bool) -> bool:
        with self._condition:
            if self._closed or self._storage_lock is None or type(enabled) is not bool:
                return False
            if enabled and self._active_locked():
                return True
            self._generation += 1
            self._cancelled.set()
            self._cancelled = threading.Event()
            self._next_attempt = 0.0
            self._failures = 0
            self._queue.clear()
            self._session_id = None
            self._install_id = None
            self._consent = enabled
            if enabled and self.available:
                self._install_id = str(uuid4())
                self._session_id = str(uuid4())
            saved = self._save_locked()
            if enabled and not saved:
                self._consent = False
                self._install_id = None
                self._session_id = None
                self._cancelled.set()
            if not enabled and not saved:
                # Do not delete the state: a missing file now means a fresh
                # installation with statistics enabled. Try writing the disabled
                # state in place if atomic replacement failed. A partial write
                # is invalid and will also remain disabled on the next launch.
                try:
                    state = {"state_version": _STATE_VERSION, "consent": False,
                             "install_id": None, "endpoint": self._endpoint, "queue": []}
                    self._state_path.write_text(json.dumps(state), encoding="utf-8")
                    saved = True
                except Exception:
                    pass
            if self._active_locked():
                self._start_worker_locked()
            self._condition.notify_all()
            return saved

    def record(self, name: str, **data) -> bool:
        with self._condition:
            if not self._active_locked():
                return False
            try:
                event = validate_event({
                    "event_id": str(uuid4()),
                    "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "name": name,
                    "data": data,
                })
                self._prune_locked()
                self._queue.append({"session_id": self._session_id, "app_version": self._app_version, "event": event})
                if len(self._queue) > MAX_QUEUE_EVENTS:
                    del self._queue[:-MAX_QUEUE_EVENTS]
                self._save_locked()
                self._start_worker_locked()
                self._condition.notify_all()
                return True
            except Exception:
                return False

    def close(self) -> None:
        # All queued changes were saved when made; do not join a blocked socket.
        with self._condition:
            self._closed = True
            self._generation += 1
            self._cancelled.set()
            self._condition.notify_all()
            self._release_storage_locked()

    def _acquire_storage_locked(self) -> None:
        """Hold a nonblocking OS lock for this client's entire lifetime.

        Separate windows/processes must not overwrite consent or each other's
        queue. The lock file contains no identifier and is never deleted, which
        avoids replacing the inode/handle while another process owns its lock.
        """
        handle = None
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            handle = (self._state_path.parent / "telemetry.lock").open("a+b")
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                self._storage_busy = error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                raise
            self._storage_lock = handle
            handle = None
        except Exception:
            # Unwritable storage and lock contention both fail closed. The UI
            # can distinguish contention through storage_busy.
            pass
        finally:
            if handle is not None:
                handle.close()

    def _release_storage_locked(self) -> None:
        handle, self._storage_lock = self._storage_lock, None
        if handle is not None:
            try:
                # Closing releases either platform's OS lock, including when
                # no explicit unlock could run (e.g. process termination).
                handle.close()
            except Exception:
                pass

    def _active_locked(self) -> bool:
        return self._consent is True and self.available and not self._closed

    def _load_locked(self) -> None:
        try:
            with self._state_path.open("rb") as stream:
                raw = stream.read(MAX_STATE_BYTES + 1)
        except FileNotFoundError:
            # Apply the default only to a new installation. The constructor
            # persists it before starting the worker; failed storage stays off.
            if self.available:
                self._consent = True
                self._install_id = str(uuid4())
            return
        except Exception:
            return
        try:
            if len(raw) > MAX_STATE_BYTES:
                return
            state = json.loads(raw)
            if type(state) is not dict or set(state) != {"state_version", "consent", "install_id", "endpoint", "queue"}:
                return
            if type(state["state_version"]) is not int or state["state_version"] != _STATE_VERSION:
                return
            consent = state["consent"]
            if consent is not None and type(consent) is not bool:
                return
            if type(state["endpoint"]) is not str or _endpoint(state["endpoint"]) != state["endpoint"]:
                return
            if type(state["queue"]) is not list or len(state["queue"]) > MAX_QUEUE_EVENTS:
                return
            if state["endpoint"] != self._endpoint:
                # Consent identifies a recipient, not just a global on switch.
                self._save_locked()
                return
            if consent is not True or not self.available:
                if state["install_id"] is not None or state["queue"]:
                    return
                self._consent = consent
                return
            if not _random_uuid(state["install_id"]):
                return
            entries = []
            seen = set()
            for entry in state["queue"]:
                if type(entry) is not dict or set(entry) != {"session_id", "app_version", "event"}:
                    return
                safe = validate_batch({
                    "schema_version": SCHEMA_VERSION,
                    "install_id": state["install_id"],
                    "session_id": entry["session_id"],
                    "app_version": entry["app_version"],
                    "events": [entry["event"]],
                })
                event = safe["events"][0]
                if event["event_id"] not in seen:
                    entries.append({"session_id": safe["session_id"], "app_version": safe["app_version"], "event": event})
                    seen.add(event["event_id"])
            self._consent = True
            self._install_id = state["install_id"]
            self._queue = entries
            self._prune_locked()
        except Exception:
            # Corrupt, unknown, or unreadable consent is never an opt-in.
            self._consent = None
            self._install_id = None
            self._queue.clear()

    def _save_locked(self) -> bool:
        temporary = None
        try:
            state = {"state_version": _STATE_VERSION, "consent": self._consent,
                     "install_id": self._install_id, "endpoint": self._endpoint, "queue": self._queue}
            raw = json.dumps(state, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
            if len(raw) > MAX_STATE_BYTES:
                return False
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".telemetry-", suffix=".tmp", dir=self._state_path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
            os.replace(temporary, self._state_path)
            return True
        except Exception:
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except Exception:
                    pass

    def _prune_locked(self) -> bool:
        cutoff = time.time() - MAX_QUEUE_AGE_SECONDS
        kept = [entry for entry in self._queue if _event_time(entry["event"]) >= cutoff]
        changed = len(kept) != len(self._queue)
        self._queue = kept[-MAX_QUEUE_EVENTS:]
        return changed

    def _start_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        try:
            self._worker = threading.Thread(target=self._run, name="bet-telemetry", daemon=True)
            self._worker.start()
        except Exception:
            self._worker = None

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                if not self._active_locked():
                    self._condition.wait()
                    continue
                if self._prune_locked():
                    self._save_locked()
                if not self._queue:
                    self._condition.wait()
                    continue
                delay = self._next_attempt - time.monotonic()
                if delay > 0:
                    self._condition.wait(timeout=delay)
                    continue
                first = self._queue[0]
                entries = [entry for entry in self._queue
                           if (entry["session_id"], entry["app_version"]) == (first["session_id"], first["app_version"])][:MAX_BATCH_EVENTS]
                batch = {"schema_version": SCHEMA_VERSION, "install_id": self._install_id,
                         "session_id": first["session_id"], "app_version": first["app_version"],
                         "events": [entry["event"] for entry in entries]}
                generation, cancelled = self._generation, self._cancelled
            try:
                acknowledged = _send_batch(self._endpoint, batch, cancelled=cancelled)
                sent = {event["event_id"] for event in batch["events"]}
                acknowledged = set(acknowledged) & sent
            except Exception:
                acknowledged = set()
            with self._condition:
                if self._closed:
                    return
                if generation != self._generation or not self._active_locked():
                    continue
                if acknowledged:
                    self._queue = [entry for entry in self._queue if entry["event"]["event_id"] not in acknowledged]
                    self._save_locked()
                    self._failures = 0
                    self._next_attempt = 0.0
                else:
                    self._failures += 1
                    backoff = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2 ** min(self._failures - 1, 16))
                    self._next_attempt = time.monotonic() + backoff
