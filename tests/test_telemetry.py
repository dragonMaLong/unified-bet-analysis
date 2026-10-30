import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from bet_telemetry.schema import validate_batch
from tristar_bet import telemetry as t


ENDPOINT = "https://telemetry.example.test/v1/events"
FEATURE = {"feature": "bet", "surface": "plot"}


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Telemetry worker did not reach the expected state")


class TelemetryClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        for client in self.clients:
            if client._worker is not None:
                client._worker.join(timeout=1)
        self.temp.cleanup()

    def client(self, version="1.2.2", endpoint=ENDPOINT):
        client = t.TelemetryClient(self.root, version, endpoint)
        self.clients.append(client)
        return client

    def queue(self, count=1, version="1.2.2"):
        with patch.object(t.TelemetryClient, "_start_worker_locked"):
            client = self.client(version)
            self.assertTrue(client.set_enabled(True))
            for _ in range(count):
                self.assertTrue(client.record("feature_view", **FEATURE))
        return client

    @staticmethod
    def start(client):
        with client._condition:
            client._start_worker_locked()

    def read_state(self):
        return json.loads((self.root / t._STATE_FILENAME).read_text(encoding="utf-8"))

    def test_new_installation_defaults_on_and_delivers_without_manual_enabling(self):
        delivered = []

        def send(endpoint, batch, **_kwargs):
            delivered.append(validate_batch(batch))
            self.assertEqual(endpoint, ENDPOINT)
            return {event["event_id"] for event in batch["events"]}

        with patch.object(t, "_send_batch", side_effect=send):
            client = self.client()
            self.assertTrue(client.available)
            self.assertTrue(client.enabled)
            self.assertTrue(client.consent_decided)
            self.assertTrue(self.read_state()["consent"])
            self.assertTrue(client.record("feature_view", **FEATURE))
            wait_for(lambda: client.pending_count == 0)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["events"][0]["data"], FEATURE)

    def test_first_launch_without_writable_preferences_stays_off(self):
        with patch.object(t.TelemetryClient, "_save_locked", return_value=False), patch.object(t, "_send_batch") as send:
            client = self.client()
            self.assertFalse(client.enabled)
            self.assertIsNone(client._install_id)
            self.assertIsNone(client._worker)
            self.assertFalse(client.record("feature_view", **FEATURE))
            send.assert_not_called()

    def test_opt_out_survives_failed_atomic_replace_and_restart(self):
        client = self.queue()
        with patch.object(t.os, "replace", side_effect=OSError("replace failed")):
            self.assertTrue(client.set_enabled(False))
        self.assertFalse(self.read_state()["consent"])
        self.assertEqual(self.read_state()["queue"], [])
        self.assertIsNone(self.read_state()["install_id"])
        client.close()
        with patch.object(t, "_send_batch") as send:
            restarted = self.client()
            self.assertFalse(restarted.enabled)
            self.assertFalse(restarted.record("feature_view", **FEATURE))
            send.assert_not_called()

    def test_no_endpoint_never_records_even_with_consent(self):
        client = self.client(endpoint="")
        with patch.object(t, "uuid4") as identifier:
            self.assertTrue(client.set_enabled(True))
            self.assertTrue(client.consent_decided)
            self.assertFalse(client.available)
            self.assertFalse(client.enabled)
            self.assertFalse(client.record("feature_view", **FEATURE))
            self.assertEqual(client.pending_count, 0)
            self.assertIsNone(client._worker)
            identifier.assert_not_called()
        self.assertIsNone(self.read_state()["install_id"])

    def test_strict_event_allowlist_drops_accidental_private_values(self):
        client = self.queue(0)
        with patch.object(client, "_start_worker_locked"):
            self.assertFalse(client.record("feature_view", **FEATURE, filename="private.SMP"))
            self.assertFalse(client.record("exception", traceback="private path"))
            self.assertFalse(client.record("import_file", extension="smp", parser="micromeritics", status="failure", duration_ms=4, error_code="private exception"))
        self.assertEqual(client.pending_count, 0)
        self.assertNotIn("private", (self.root / t._STATE_FILENAME).read_text())

    def test_queue_is_bounded_and_oldest_entries_are_evicted(self):
        client = self.queue(1)
        first = client._queue[0]["event"]["event_id"]
        with patch.object(client, "_start_worker_locked"):
            for _ in range(t.MAX_QUEUE_EVENTS + 2):
                client.record("feature_view", **FEATURE)
        self.assertEqual(client.pending_count, 500)
        state = self.read_state()
        self.assertEqual(len(state["queue"]), 500)
        self.assertNotIn(first, {item["event"]["event_id"] for item in state["queue"]})

    def test_expired_entries_removed_on_restart_and_old_envelope_preserved(self):
        old = self.queue(2, version="1.2.1")
        old.close()
        state = self.read_state()
        state["queue"][0]["event"]["occurred_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        retained = state["queue"][1]
        (self.root / t._STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")
        with patch.object(t.TelemetryClient, "_start_worker_locked"):
            new = self.client(version="1.2.2")
            self.assertEqual(new.pending_count, 1)
            new.record("feature_view", feature="dft", surface="results")
        self.assertEqual(new._install_id, old._install_id)
        self.assertNotEqual(new._session_id, old._session_id)
        delivered = []

        def send(_endpoint, batch, **_kwargs):
            delivered.append(validate_batch(batch))
            return {event["event_id"] for event in batch["events"]}

        with patch.object(t, "_send_batch", side_effect=send):
            self.start(new)
            wait_for(lambda: new.pending_count == 0)
        self.assertEqual(len(delivered), 2)
        self.assertEqual(delivered[0]["session_id"], retained["session_id"])
        self.assertEqual(delivered[0]["app_version"], "1.2.1")
        self.assertEqual(delivered[0]["events"], [retained["event"]])
        self.assertEqual(delivered[1]["app_version"], "1.2.2")

    def test_endpoint_change_requires_fresh_consent_and_discards_old_queue(self):
        old = self.queue(2)
        old.close()
        with patch.object(t, "_send_batch") as send:
            new = self.client(endpoint="https://another.example.test/v1/events")
            self.assertFalse(new.enabled)
            self.assertFalse(new.consent_decided)
            self.assertEqual(new.pending_count, 0)
            self.assertIsNone(new._install_id)
            self.assertIsNone(new._worker)
            send.assert_not_called()
        self.assertEqual(self.read_state()["queue"], [])
        self.assertIsNone(self.read_state()["install_id"])

    def test_retries_keep_event_ids_and_batches_at_most_thirty(self):
        client = self.queue(35)
        deliveries = []

        def send(_endpoint, batch, **_kwargs):
            deliveries.append(validate_batch(batch))
            if len(deliveries) == 1:
                raise OSError("offline")
            return {event["event_id"] for event in batch["events"]}

        with patch.object(t, "_send_batch", side_effect=send), patch.object(t, "RETRY_BASE_SECONDS", 0.01):
            self.start(client)
            wait_for(lambda: client.pending_count == 0)
        self.assertEqual([len(batch["events"]) for batch in deliveries], [30, 30, 5])
        self.assertEqual(deliveries[0], deliveries[1])
        self.assertEqual(self.read_state()["queue"], [])

    def test_partial_duplicate_or_foreign_ack_only_removes_acknowledged_sent_events(self):
        client = self.queue(3)
        ids = [entry["event"]["event_id"] for entry in client._queue]
        first_done = threading.Event()
        second_started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        calls = []

        def send(_endpoint, batch, **_kwargs):
            calls.append(batch)
            if len(calls) == 1:
                first_done.set()
                return [ids[0], ids[0], str(uuid4())]
            second_started.set()
            release.wait(1)
            return set()

        with patch.object(t, "_send_batch", side_effect=send):
            self.start(client)
            self.assertTrue(first_done.wait(1))
            self.assertTrue(second_started.wait(1))
            self.assertEqual(client.pending_count, 2)
            self.assertEqual([e["event_id"] for e in calls[1]["events"]], ids[1:])
            client.close()
            release.set()
            client._worker.join(1)
        self.assertEqual([entry["event"]["event_id"] for entry in self.read_state()["queue"]], ids[1:])

    def test_opt_out_during_delivery_clears_identity_and_prevents_subsequent_dispatch(self):
        client = self.queue(35)
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def send(_endpoint, batch, **_kwargs):
            entered.set()
            release.wait(1)
            return {event["event_id"] for event in batch["events"]}

        with patch.object(t, "_send_batch", side_effect=send) as sender:
            self.start(client)
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            self.assertTrue(client.set_enabled(False))
            self.assertLess(time.monotonic() - start, 0.25)
            self.assertFalse(client.enabled)
            self.assertEqual(client.pending_count, 0)
            self.assertIsNone(client._install_id)
            self.assertFalse(client.record("feature_view", **FEATURE))
            release.set()
            time.sleep(0.04)
            self.assertEqual(sender.call_count, 1)
        state = self.read_state()
        self.assertFalse(state["consent"])
        self.assertEqual(state["queue"], [])
        self.assertIsNone(state["install_id"])
        client.close()
        again = self.client()
        self.assertFalse(again.enabled)
        self.assertTrue(again.consent_decided)

    def test_old_inflight_ack_cannot_delete_new_consent_generations_events(self):
        client = self.queue(1)
        old_id = client._install_id
        entered = threading.Event()
        release = threading.Event()
        second = threading.Event()
        self.addCleanup(release.set)
        calls = []

        def send(_endpoint, batch, **_kwargs):
            calls.append(batch)
            if len(calls) == 1:
                entered.set()
                release.wait(1)
            else:
                second.set()
            return {event["event_id"] for event in batch["events"]}

        with patch.object(t, "_send_batch", side_effect=send):
            self.start(client)
            self.assertTrue(entered.wait(1))
            client.set_enabled(False)
            client.set_enabled(True)
            self.assertNotEqual(client._install_id, old_id)
            client.record("feature_view", feature="dft", surface="results")
            self.assertEqual(client.pending_count, 1)
            release.set()
            self.assertTrue(second.wait(1))
            wait_for(lambda: client.pending_count == 0)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]["install_id"], calls[1]["install_id"])
        self.assertEqual(calls[1]["events"][0]["data"]["feature"], "dft")

    def test_close_never_waits_for_inflight_transport_and_preserves_unsent_queue(self):
        client = self.queue()
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def send(*_args, **_kwargs):
            entered.set()
            release.wait(1)
            return set()

        with patch.object(t, "_send_batch", side_effect=send):
            self.start(client)
            self.assertTrue(entered.wait(1))
            self.assertTrue(client._worker.daemon)
            start = time.monotonic()
            client.close()
            self.assertLess(time.monotonic() - start, 0.1)
            self.assertEqual(len(self.read_state()["queue"]), 1)
            release.set()
            client._worker.join(1)

    def test_idle_worker_does_not_repeatedly_write_state(self):
        client = self.queue(0)
        with patch.object(client, "_save_locked", wraps=client._save_locked) as save:
            self.start(client)
            time.sleep(0.04)
            save.assert_not_called()

    def test_corrupt_unknown_and_read_only_settings_fail_closed(self):
        state_path = self.root / t._STATE_FILENAME
        for raw in ("not json", '{"state_version":99,"consent":true}', "[]"):
            state_path.write_text(raw, encoding="utf-8")
            client = self.client()
            self.assertFalse(client.enabled)
            self.assertFalse(client.consent_decided)
            self.assertIsNone(client._worker)
            client.close()
        state_path.unlink()
        client = self.queue()
        client.close()
        with patch.object(t.TelemetryClient, "_save_locked", return_value=False):
            readonly = self.client()
            self.assertFalse(readonly.enabled)
            self.assertIsNone(readonly._worker)
            self.assertEqual(readonly.pending_count, 0)
            self.assertFalse(readonly.set_enabled(True))

    def test_atomic_write_failure_keeps_previous_json_and_cleans_temporary_file(self):
        client = self.queue()
        before = (self.root / t._STATE_FILENAME).read_bytes()
        with patch.object(client, "_start_worker_locked"), patch.object(t.os, "replace", side_effect=OSError("read only")):
            self.assertTrue(client.record("feature_view", **FEATURE))
        self.assertEqual((self.root / t._STATE_FILENAME).read_bytes(), before)
        self.assertEqual(list(self.root.glob(".telemetry-*.tmp")), [])

    def test_second_client_cannot_read_send_or_overwrite_consent_in_same_process(self):
        owner = self.queue(2)
        before = (self.root / t._STATE_FILENAME).read_bytes()
        other = self.client()
        self.assertTrue(other.storage_busy)
        self.assertFalse(other.available)
        self.assertFalse(other.enabled)
        self.assertFalse(other.consent_decided)
        self.assertIsNone(other._install_id)
        self.assertIsNone(other._worker)
        self.assertFalse(other.record("feature_view", **FEATURE))
        self.assertFalse(other.set_enabled(True))
        self.assertFalse(other.set_enabled(False))
        self.assertEqual((self.root / t._STATE_FILENAME).read_bytes(), before)
        other.close()
        owner.close()
        with patch.object(t.TelemetryClient, "_start_worker_locked"):
            successor = self.client()
        self.assertFalse(successor.storage_busy)
        self.assertTrue(successor.enabled)
        self.assertEqual(successor.pending_count, 2)

    def test_storage_lock_also_excludes_another_process(self):
        owner = self.queue(1)
        script = (
            "import json,sys; from pathlib import Path; "
            "from tristar_bet.telemetry import TelemetryClient; "
            "c=TelemetryClient(Path(sys.argv[1]), '1.2.2', 'https://telemetry.example.test/v1/events'); "
            "print(json.dumps([c.storage_busy,c.available,c.enabled,c.set_enabled(False)])); c.close()"
        )
        result = subprocess.run([sys.executable, "-c", script, str(self.root)], capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), [True, False, False, False])
        self.assertTrue(owner.enabled)
        self.assertEqual(len(self.read_state()["queue"]), 1)

    def test_close_releases_lock_before_old_transport_returns_without_late_disk_write(self):
        owner = self.queue(1)
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def send(_endpoint, batch, **_kwargs):
            entered.set()
            release.wait(1)
            return {event["event_id"] for event in batch["events"]}

        with patch.object(t, "_send_batch", side_effect=send):
            self.start(owner)
            self.assertTrue(entered.wait(1))
            owner.close()
            with patch.object(t.TelemetryClient, "_start_worker_locked"):
                successor = self.client()
            self.assertFalse(successor.storage_busy)
            self.assertTrue(successor.set_enabled(False))
            before = (self.root / t._STATE_FILENAME).read_bytes()
            release.set()
            owner._worker.join(1)
            self.assertEqual((self.root / t._STATE_FILENAME).read_bytes(), before)
            self.assertFalse(self.read_state()["consent"])


class TelemetryEndpointTests(unittest.TestCase):
    def test_only_https_or_exact_loopback_http_is_allowed(self):
        for endpoint in ("http://127.0.0.1:8123/events", "http://localhost/events", "http://[::1]:8123/events", ENDPOINT):
            self.assertEqual(t._endpoint(endpoint), endpoint)
        for endpoint in ("http://example.com/events", "ftp://localhost/events", "https://user:secret@example.com/events", "https://example.com/#fragment", "http://localhost.example.com/events", "http://127.0.0.2/events", "https://example.com:99999/events", "https://example.com/with space", "file:///tmp/test"):
            self.assertEqual(t._endpoint(endpoint), "", endpoint)

    def test_source_configuration_and_environment_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "telemetry_config.json"
            with patch.dict(os.environ, {}, clear=True), patch.object(t, "__file__", str(root / "tristar_bet" / "telemetry.py")), patch.object(t.sys, "frozen", False, create=True):
                self.assertEqual(t.configured_endpoint(), "")
                config.write_text(json.dumps({"endpoint": ENDPOINT}), encoding="utf-8")
                self.assertEqual(t.configured_endpoint(), ENDPOINT)
                with patch.dict(os.environ, {"BET_TELEMETRY_URL": "http://127.0.0.1/events"}):
                    self.assertEqual(t.configured_endpoint(), "http://127.0.0.1/events")
                with patch.dict(os.environ, {"BET_TELEMETRY_URL": ""}):
                    self.assertEqual(t.configured_endpoint(), "")
                config.write_text("invalid", encoding="utf-8")
                self.assertEqual(t.configured_endpoint(), "")

    def test_frozen_bundled_config_is_overridden_by_adjacent_even_if_empty_or_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable, bundled = root / "app", root / "bundle"
            executable.mkdir()
            bundled.mkdir()
            adjacent = executable / "telemetry_config.json"
            (bundled / "telemetry_config.json").write_text(json.dumps({"endpoint": ENDPOINT}), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch.object(t.sys, "frozen", True, create=True), patch.object(t.sys, "_MEIPASS", str(bundled), create=True), patch.object(t.sys, "executable", str(executable / "BET.exe")):
                self.assertEqual(t.configured_endpoint(), ENDPOINT)
                for content, expected in ((json.dumps({"endpoint": "https://override.example.test/events"}), "https://override.example.test/events"), ('{"endpoint":""}', ""), ("invalid", "")):
                    adjacent.write_text(content, encoding="utf-8")
                    self.assertEqual(t.configured_endpoint(), expected)
                with patch.dict(os.environ, {"BET_TELEMETRY_URL": ENDPOINT}):
                    self.assertEqual(t.configured_endpoint(), ENDPOINT)


class TelemetryTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                batch = json.loads(body)
                cls.received.append((self.path, batch))
                if self.path == "/redirect":
                    self.send_response(307)
                    self.send_header("Location", "/collector")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/large":
                    response = b"x" * (t.MAX_ACK_BYTES + 100)
                else:
                    identifier = batch["events"][0]["event_id"]
                    response = json.dumps({"accepted": [identifier, identifier, str(uuid4())]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(1)

    def batch(self):
        return {"schema_version": 1, "install_id": str(uuid4()), "session_id": str(uuid4()), "app_version": "1.2.2", "events": [{"event_id": str(uuid4()), "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "name": "feature_view", "data": FEATURE}]}

    def url(self, path):
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"

    def test_real_http_ack_is_deduplicated_and_limited_to_this_batch(self):
        batch = self.batch()
        result = t._send_batch(self.url("/collector"), batch, cancelled=threading.Event())
        self.assertEqual(result, {batch["events"][0]["event_id"]})

    def test_redirect_is_not_followed_and_body_is_not_forwarded(self):
        before = len(self.received)
        with self.assertRaises(Exception):
            t._send_batch(self.url("/redirect"), self.batch(), cancelled=threading.Event())
        self.assertEqual([path for path, _batch in self.received[before:]], ["/redirect"])

    def test_oversized_ack_is_rejected_and_revoked_transport_does_not_send(self):
        with self.assertRaises(ValueError):
            t._send_batch(self.url("/large"), self.batch(), cancelled=threading.Event())
        before = len(self.received)
        cancelled = threading.Event()
        cancelled.set()
        self.assertEqual(t._send_batch(self.url("/collector"), self.batch(), cancelled=cancelled), set())
        self.assertEqual(len(self.received), before)


if __name__ == "__main__":
    unittest.main()
