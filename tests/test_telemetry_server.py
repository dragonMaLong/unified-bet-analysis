import base64
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from telemetry_server import create_app
from telemetry_server.app import DAY, _RateGuard, purge_database


class TelemetryServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "stats.sqlite3"
        self.now = int(datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp())
        self.app = create_app({"TESTING": True, "DATABASE": self.path,
                               "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "test-secret",
                               "TIME_SOURCE": lambda: self.now})
        self.client = self.app.test_client()
        self.install_id = str(uuid4())
        self.session_id = str(uuid4())
        self.auth = {"Authorization": "Basic " + base64.b64encode(b"admin:test-secret").decode("ascii")}

    def event(self, name="startup", *, when=None, **data):
        values = {
            "startup": dict(os="Windows", os_version="10.0", architecture="x86_64",
                            screen_width=1920, screen_height=1080, scale=1.25),
            "import_file": dict(status="success", duration_ms=100, error_code="none", extension="smp", parser="micromeritics"),
            "feature_view": dict(feature="bet", surface="plot"),
            "export": dict(status="success", duration_ms=400, error_code="none", format="xlsx"),
        }[name]
        values.update(data)
        return {"event_id": str(uuid4()), "occurred_at": datetime.fromtimestamp(
            self.now if when is None else when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "name": name, "data": values}

    def batch(self, events=None, *, install_id=None, version="1.2.3"):
        return dict(schema_version=1, install_id=install_id or self.install_id,
                    session_id=self.session_id, app_version=version, events=events or [self.event()])

    def query(self, sql, parameters=()):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                return connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()

    def test_valid_batch_is_idempotent_and_acknowledges_retries(self):
        batch = self.batch([self.event(), self.event("import_file")])
        for _ in range(2):
            response = self.client.post("/api/v1/events", json=batch)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {"accepted": [event["event_id"] for event in batch["events"]]})
        self.assertEqual(self.query("SELECT COUNT(*) FROM events")[0][0], 2)
        self.assertEqual(self.query("SELECT COUNT(*) FROM ip_observations")[0][0], 1)

    def test_unknown_sensitive_field_rejects_entire_batch_without_echo(self):
        batch = self.batch([self.event(), self.event("import_file")])
        batch["events"][1]["data"]["filename"] = "PRIVATE-SAMPLE-DO-NOT-STORE.smp"
        response = self.client.post("/api/v1/events", json=batch)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b"PRIVATE-SAMPLE", response.data)
        self.assertEqual(self.query("SELECT COUNT(*) FROM events")[0][0], 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM ip_observations")[0][0], 0)
        self.assertNotIn(b"PRIVATE-SAMPLE", self.path.read_bytes())

    def test_json_malformed_duplicate_nonfinite_and_media_type_are_rejected(self):
        for body in ('{"schema_version":1,"schema_version":1}', '{broken', '{"value":NaN}', '[' * 1200):
            with self.subTest(body=body[:40]):
                self.assertEqual(self.client.post("/api/v1/events", data=body, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/events", data="text", content_type="text/plain").status_code, 415)

    def test_payload_over_64k_and_batch_over_30_are_rejected(self):
        response = self.client.post("/api/v1/events", data=b" " * 65537, content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.post("/api/v1/events", json=self.batch([self.event() for _ in range(31)])).status_code, 400)

    def test_extreme_and_far_future_timestamps_return_400_not_500(self):
        for timestamp in ("0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z"):
            event = self.event()
            event["occurred_at"] = timestamp
            self.assertEqual(self.client.post("/api/v1/events", json=self.batch([event])).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/events", json=self.batch([self.event(when=self.now + 301)])).status_code, 400)

    def test_expired_queue_event_is_acknowledged_but_not_retained(self):
        event = self.event(when=self.now - 91 * DAY)
        response = self.client.post("/api/v1/events", json=self.batch([event]))
        self.assertEqual(response.json, {"accepted": [event["event_id"]]})
        self.assertEqual(self.query("SELECT COUNT(*) FROM events")[0][0], 0)

    def test_forwarded_ip_is_ignored_by_default_and_for_untrusted_peers(self):
        for trust in (False, True):
            self.app.config["TRUST_PROXY"] = trust
            response = self.client.post("/api/v1/events", json=self.batch(),
                                        headers={"X-Forwarded-For": "10.20.30.40", "X-Real-IP": "10.20.30.41"},
                                        environ_overrides={"REMOTE_ADDR": "203.0.113.20"})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.query("SELECT DISTINCT ip_address FROM ip_observations"), [("203.0.113.20",)])

    def test_exact_loopback_single_hop_is_trusted_but_chains_are_not(self):
        self.app.config["TRUST_PROXY"] = True
        for forwarded in ("198.51.100.42", "198.51.100.99, 10.0.0.1", "invalid", "2001:db8::42"):
            response = self.client.post("/api/v1/events", json=self.batch(), headers={"X-Forwarded-For": forwarded},
                                        environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(set(row[0] for row in self.query("SELECT DISTINCT ip_address FROM ip_observations")),
                         {"198.51.100.42", "127.0.0.1", "2001:db8::42"})
        self.assertEqual(self.query("SELECT COUNT(*) FROM events WHERE data_json LIKE '%198.51.100%'")[0][0], 0)

    def test_authentication_required_and_unconfigured_admin_is_disabled(self):
        for endpoint in ("/admin", "/admin/summary"):
            response = self.client.get(endpoint)
            self.assertEqual(response.status_code, 401)
            self.assertIn("Basic", response.headers["WWW-Authenticate"])
            self.assertEqual(self.client.get(endpoint, headers=self.auth).status_code, 200)
        self.app.config["ADMIN_PASSWORD"] = ""
        self.assertEqual(self.client.get("/admin", headers=self.auth).status_code, 404)

    def test_admin_requires_https_outside_local_development(self):
        response = self.client.get("/admin", headers=self.auth, environ_overrides={"REMOTE_ADDR": "203.0.113.1"})
        self.assertEqual(response.status_code, 403)
        self.app.config["TRUST_PROXY"] = True
        self.assertEqual(self.client.get("/admin", headers=self.auth).status_code, 403)
        headers = {**self.auth, "X-Forwarded-Proto": "https"}
        self.assertEqual(self.client.get("/admin", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/admin", headers=headers, environ_overrides={"REMOTE_ADDR": "203.0.113.1"}).status_code, 403)

    def test_admin_escapes_database_values_and_disables_caching(self):
        self.client.post("/api/v1/events", json=self.batch())
        self.query("UPDATE events SET app_version = ?", ("<script>alert(1)</script>",))
        response = self.client.get("/admin", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"<script>", response.data)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_summary_uses_unique_installs_latest_version_and_latest_environment(self):
        second, old = str(uuid4()), str(uuid4())
        batches = [
            self.batch([self.event(when=self.now - 20 * DAY)], version="1.0"),
            self.batch([self.event(when=self.now - 2 * DAY, os_version="11.0", screen_width=2560)], version="1.1"),
            self.batch([self.event(when=self.now - 10 * DAY)], install_id=second, version="1.0"),
            self.batch([self.event(when=self.now - 40 * DAY)], install_id=old, version="0.9"),
            self.batch([self.event("import_file"),
                        self.event("import_file", status="failure", error_code="parse_error", extension="dat", duration_ms=200),
                        self.event("feature_view"), self.event("feature_view", feature="dft", surface="results"),
                        self.event("export"), self.event("export", status="failure", error_code="write_error", duration_ms=500)], version="1.1"),
        ]
        for batch in batches:
            self.assertEqual(self.client.post("/api/v1/events", json=batch).status_code, 200)
        summary = self.client.get("/admin/summary", headers=self.auth).json
        self.assertEqual(summary["active_installs"], {"7d": 1, "30d": 2})
        self.assertEqual({row["version"]: row["installs"] for row in summary["version_share"]}, {"1.0": 1, "1.1": 1})
        self.assertEqual(sum(row["count"] for row in summary["daily_startups"]), 3)
        self.assertEqual(summary["imports"]["success_rate"], 0.5)
        self.assertEqual(summary["imports"]["total"], 2)
        self.assertEqual(summary["exports"]["by_format"][0]["average_duration_ms"], 450.0)
        self.assertEqual(sum(row["views"] for row in summary["feature_views"]), 2)
        self.assertEqual({row["error_code"] for row in summary["errors"]}, {"parse_error", "write_error"})
        self.assertEqual({row["screen_width"]: row["installs"] for row in summary["environments"]}, {1920: 1, 2560: 1})
        self.assertEqual(summary["retention"], {"events_days": 90, "ip_days": 7})

    def test_retention_cleans_events_and_ips_independently_without_ingestion(self):
        self.client.post("/api/v1/events", json=self.batch())
        self.query("UPDATE events SET occurred_at=?", (self.now - 91 * DAY,))
        self.query("UPDATE ip_observations SET last_seen_at=?", (self.now - 8 * DAY,))
        summary = self.client.get("/admin/summary", headers=self.auth).json
        self.assertEqual(summary["recent_ips"], [])
        result = purge_database(self.path, now=self.now)
        self.assertEqual(result["events_deleted"], 1)
        self.assertEqual(result["ip_observations_deleted"], 1)
        self.assertEqual(self.query("SELECT COUNT(*) FROM events")[0][0], 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM ip_observations")[0][0], 0)

    def test_retention_keeps_recent_events_when_ip_window_expires(self):
        self.client.post("/api/v1/events", json=self.batch([self.event(when=self.now - 20 * DAY)]))
        self.query("UPDATE ip_observations SET last_seen_at=?", (self.now - 8 * DAY,))
        result = purge_database(self.path, now=self.now)
        self.assertEqual(result["events_deleted"], 0)
        self.assertEqual(result["ip_observations_deleted"], 1)

    def test_rate_limit_returns_retry_after_and_resets(self):
        app = create_app({"TESTING": True, "DATABASE": self.path, "TIME_SOURCE": lambda: self.now,
                          "RATE_LIMIT_PER_MINUTE": 2, "RATE_LIMIT_GLOBAL_PER_MINUTE": 10})
        client = app.test_client()
        self.assertEqual(client.post("/api/v1/events", json=self.batch()).status_code, 200)
        self.assertEqual(client.post("/api/v1/events", json=self.batch()).status_code, 200)
        response = client.post("/api/v1/events", json=self.batch())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.now += 60
        self.assertEqual(client.post("/api/v1/events", json=self.batch()).status_code, 200)

    def test_rate_guard_memory_is_bounded_and_does_not_keep_raw_ips(self):
        guard = _RateGuard(60, 1000, max_keys=4)
        for index in range(10):
            self.assertTrue(guard.allow(f"192.0.2.{index}", self.now))
        self.assertEqual(len(guard.clients), 4)
        self.assertTrue(all(isinstance(key, bytes) and len(key) == 16 for key in guard.clients))

    def test_health_does_not_disclose_configuration(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.json, {"ok": True})
        self.assertNotIn(str(self.path).encode(), response.data)
        self.assertNotIn(b"secret", response.data)

    def test_database_capacity_returns_503_without_acknowledging_unstored_events(self):
        limited_path = Path(self.temporary.name) / "limited.sqlite3"
        with patch("telemetry_server.app.MAX_DATABASE_BYTES", 65536):
            app = create_app({"TESTING": True, "DATABASE": limited_path, "TIME_SOURCE": lambda: self.now})
            client = app.test_client()
            statuses = []
            for _ in range(20):
                response = client.post("/api/v1/events", json=self.batch([self.event() for _ in range(30)]))
                statuses.append(response.status_code)
                if response.status_code == 503:
                    self.assertEqual(response.json, {"error": "storage_unavailable"})
                    self.assertNotIn("accepted", response.json)
                    break
            self.assertIn(503, statuses)
        self.assertLessEqual(limited_path.stat().st_size, 65536)

    def test_version_list_is_bounded_and_share_uses_all_active_installs(self):
        for index in range(105):
            response = self.client.post("/api/v1/events", json=self.batch(install_id=str(uuid4()), version=f"1.v{index:03d}"),
                                        environ_overrides={"REMOTE_ADDR": f"192.0.2.{index // 20 + 1}"})
            self.assertEqual(response.status_code, 200)
        summary = self.client.get("/admin/summary", headers=self.auth).json
        self.assertEqual(summary["active_installs"]["30d"], 105)
        self.assertEqual(len(summary["version_share"]), 100)
        self.assertAlmostEqual(summary["version_share"][0]["share"], 1 / 105)


if __name__ == "__main__":
    unittest.main()
