import base64
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from telemetry_server import create_app
from telemetry_server.app import DAY, MAX_DATABASE_BYTES, _RateGuard, purge_database


class _ServerFixture(unittest.TestCase):
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


class TelemetryServerTests(_ServerFixture):
    def test_valid_batch_is_idempotent_and_acknowledges_retries(self):
        batch = self.batch([self.event(), self.event("import_file")])
        for _ in range(2):
            response = self.client.post("/api/v1/events", json=batch)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {"accepted": [event["event_id"] for event in batch["events"]]})
        self.assertEqual(self.query("SELECT COUNT(*) FROM events")[0][0], 2)
        self.assertEqual(self.query("SELECT COUNT(*) FROM ip_observations")[0][0], 1)

    def test_ip_location_is_available_only_in_authenticated_admin_and_escaped(self):
        def add_locations(rows, directory):
            self.assertEqual(directory, self.app.config["IP_DATABASE_DIR"])
            for row in rows:
                row["location"] = "中国 · <script>test</script>"

        with patch("telemetry_server.app.enrich_ip_locations", side_effect=add_locations) as enrich:
            self.assertEqual(self.client.post("/api/v1/events", json=self.batch()).status_code, 200)
            self.assertEqual(self.client.get("/healthz").status_code, 200)
            self.assertEqual(self.client.get("/admin/summary").status_code, 401)
            enrich.assert_not_called()
            summary = self.client.get("/admin/summary", headers=self.auth)
            self.assertEqual(summary.status_code, 200)
            self.assertEqual(summary.json["recent_ips"][0]["location"], "中国 · <script>test</script>")
            page = self.client.get("/admin", headers=self.auth)
            self.assertEqual(page.status_code, 200)
            self.assertIn("归属地".encode(), page.data)
            self.assertNotIn(b"<script>", page.data)
            self.assertIn(b"&lt;script&gt;", page.data)
            # Each summary looks up the IP groups and the per-run rows.
            self.assertEqual(enrich.call_count, 4)

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

    def test_historical_queue_event_is_acknowledged_and_retained(self):
        event = self.event(when=self.now - 400 * DAY)
        response = self.client.post("/api/v1/events", json=self.batch([event]))
        self.assertEqual(response.json, {"accepted": [event["event_id"]]})
        self.assertEqual(self.query("SELECT COUNT(*) FROM events")[0][0], 1)
        self.assertEqual(self.query("SELECT occurred_at FROM events")[0][0], self.now - 400 * DAY)

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
        self.assertEqual(summary["retention"], {"events_days": None, "ip_days": None})
        self.assertEqual(summary["window_active_installs"], 2)

    def test_maintenance_retains_400_day_events_and_ip_history(self):
        self.client.post("/api/v1/events", json=self.batch())
        self.query("UPDATE events SET occurred_at=?", (self.now - 400 * DAY,))
        self.query("UPDATE ip_observations SET last_seen_at=?", (self.now - 400 * DAY,))
        summary = self.client.get("/admin/summary", headers=self.auth).json
        self.assertEqual(summary["recent_ips"], [])
        for maintenance_time in (self.now, self.now + 5 * 365 * DAY):
            result = purge_database(self.path, now=maintenance_time)
            self.assertEqual(result, {"events_deleted": 0, "ip_observations_deleted": 0, "checkpoint_busy": False})
            self.assertEqual(self.query("SELECT COUNT(*) FROM events")[0][0], 1)
            self.assertEqual(self.query("SELECT COUNT(*) FROM ip_observations")[0][0], 1)
        history = self.client.get("/admin/summary?days=365&ip_scope=all", headers=self.auth).json
        self.assertEqual(history["window_active_installs"], 0)
        self.assertEqual(history["ip_pagination"]["total_groups"], 1)
        self.assertEqual(history["recent_ips"][0]["ip"], "127.0.0.1")

    def test_database_usage_reports_page_budget_and_high_usage(self):
        self.client.post("/api/v1/events", json=self.batch())
        used = self.query("PRAGMA page_count")[0][0] * self.query("PRAGMA page_size")[0][0]
        summary = self.client.get("/admin/summary", headers=self.auth).json
        self.assertEqual(summary["database_usage"]["used_bytes"], used)
        self.assertEqual(summary["database_usage"]["max_bytes"], MAX_DATABASE_BYTES)
        self.assertAlmostEqual(summary["database_usage"]["ratio"], used / MAX_DATABASE_BYTES)
        self.assertFalse(summary["database_usage"]["warning"])
        with patch("telemetry_server.app.MAX_DATABASE_BYTES", used * 5 // 4):
            summary = self.client.get("/admin/summary", headers=self.auth).json
        self.assertTrue(summary["database_usage"]["warning"])
        self.assertAlmostEqual(summary["database_usage"]["ratio"], 0.8)

    def test_selected_windows_apply_consistent_inclusive_boundaries_to_every_metric(self):
        for days in (30, 90, 180, 365):
            with self.subTest(days=days):
                self.query("DELETE FROM events")
                self.query("DELETE FROM ip_observations")
                cutoff = self.now - days * DAY
                for when, version, width in ((cutoff - 1, "0.9", 1600), (cutoff, "1.0", 1920),
                                             (self.now, "1.1", 2560), (self.now + 1, "2.0", 3000)):
                    install = str(uuid4())
                    events = [self.event(when=when, screen_width=width),
                              self.event("import_file", when=when),
                              self.event("feature_view", when=when),
                              self.event("export", when=when, status="failure", error_code="write_error")]
                    response = self.client.post("/api/v1/events", json=self.batch(events, install_id=install, version=version))
                    self.assertEqual(response.status_code, 200)
                    self.query("UPDATE ip_observations SET last_seen_at=? WHERE install_id=?", (when, install))
                response = self.client.get(f"/admin/summary?days={days}", headers=self.auth)
                self.assertEqual(response.status_code, 200)
                summary = response.json
                self.assertEqual(summary["window_days"], days)
                self.assertEqual(summary["window_active_installs"], 2)
                self.assertEqual({row["version"] for row in summary["version_share"]}, {"1.0", "1.1"})
                self.assertTrue(all(row["share"] == 0.5 for row in summary["version_share"]))
                self.assertEqual(sum(row["count"] for row in summary["daily_startups"]), 2)
                self.assertEqual(len(summary["daily_startups"]), days + 1)
                self.assertEqual(summary["daily_startups"][0]["count"], 1)
                self.assertEqual(summary["daily_startups"][-1]["count"], 1)
                self.assertEqual(summary["imports"]["total"], 2)
                self.assertEqual(summary["exports"]["total"], 2)
                self.assertEqual(sum(row["views"] for row in summary["feature_views"]), 2)
                self.assertEqual(sum(row["count"] for row in summary["errors"]), 2)
                self.assertEqual({row["screen_width"] for row in summary["environments"]}, {1920, 2560})
                self.assertEqual({row["version"] for row in summary["recent_ips"]}, {"1.0", "1.1"})
                self.assertEqual(summary["ip_pagination"]["total_groups"], 2)
                self.assertEqual(summary["window_start_at"], datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def test_period_query_defaults_and_authentication_precede_validation(self):
        default = self.client.get("/admin/summary", headers=self.auth).json
        self.assertEqual(default["window_days"], 30)
        self.assertEqual(default["window_label"], "近30天")
        self.assertEqual(default["timezone"], "Asia/Shanghai")
        self.assertEqual(default["timezone_label"], "北京时间（UTC+8）")
        self.assertEqual(default["generated_at"], "2026-10-29T12:00:00Z")
        self.assertEqual(default["generated_at_local"], "2026-10-29 20:00:00")
        self.assertEqual(default["ip_scope"], "window")
        self.assertEqual(default["ip_pagination"], {"page": 1, "per_page": 200, "total_groups": 0,
                                                   "total_pages": 1, "has_previous": False, "has_next": False})
        self.assertEqual([(row["days"], row["label"], row["query"]) for row in default["period_options"]],
                         [(30, "近30天", "?days=30"), (90, "近90天", "?days=90"), (180, "近半年（180天）", "?days=180"),
                          (365, "近1年（365天）", "?days=365"), ("all", "全部", "?days=all")])
        invalid = ("days=7", "days=0", "days=ALL", "days=", "days=030", "days=30&days=90", "days=all&days=all",
                   "page=0", "page=x", "page=1&page=2", "start=2026-13-01", "start=26-10-01", "start=1999-01-01",
                   "end=2026-10-01x", "ip=1.2.3.4;x", "ip=" + "1" * 46, "version=1.3 3", "region=%01",
                   "ip=1.1.1.1&ip=2.2.2.2",
                   "ip_scope=private", "ip_scope=", "ip_scope=all&ip_scope=window", "ip_page=0",
                   "ip_page=-1", "ip_page=1.5", "ip_page=abc", "ip_page=", "ip_page=1&ip_page=2",
                   "ip_page=" + "9" * 30)
        for endpoint in ("/admin", "/admin/summary"):
            for query in invalid:
                with self.subTest(endpoint=endpoint, query=query):
                    self.now += 60  # Each validation is independent of the admin rate limiter.
                    self.assertEqual(self.client.get(endpoint + "?" + query).status_code, 401)
                    response = self.client.get(endpoint + "?" + query, headers=self.auth)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json, {"error": "invalid_query"})

    def test_ip_history_pagination_is_bounded_stable_and_independent_of_period(self):
        # Direct local fixtures avoid exercising unrelated ingestion rate limits.
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany("INSERT INTO ip_observations VALUES (?, ?, ?, ?)", [
                (str(uuid4()), f"198.51.{index // 256}.{index % 256}", "1.3.0", self.now - 400 * DAY)
                for index in range(405)])
            connection.execute("INSERT INTO ip_observations VALUES (?, ?, ?, ?)",
                               (str(uuid4()), "203.0.113.1", "1.3.0", self.now))
            # A second installation with the same IP/version belongs to the same group.
            connection.execute("INSERT INTO ip_observations VALUES (?, ?, ?, ?)",
                               (str(uuid4()), "203.0.113.1", "1.3.0", self.now))
        current = self.client.get("/admin/summary?days=365", headers=self.auth).json
        self.assertEqual(current["ip_pagination"]["total_groups"], 1)
        self.assertEqual(current["recent_ips"][0]["installations"], 2)
        seen = set()
        for page, expected_size in ((1, 200), (2, 200), (3, 6)):
            response = self.client.get(f"/admin/summary?days=30&ip_scope=all&ip_page={page}", headers=self.auth)
            self.assertEqual(response.status_code, 200)
            summary = response.json
            self.assertEqual(summary["ip_pagination"]["total_groups"], 406)
            self.assertEqual(summary["ip_pagination"]["total_pages"], 3)
            self.assertEqual(summary["ip_pagination"]["page"], page)
            self.assertEqual(len(summary["recent_ips"]), expected_size)
            groups = {(row["ip"], row["version"]) for row in summary["recent_ips"]}
            self.assertFalse(seen & groups)
            seen.update(groups)
        clamped = self.client.get("/admin/summary?days=180&ip_scope=all&ip_page=999999999999", headers=self.auth).json
        self.assertEqual(clamped["ip_pagination"]["page"], 3)
        self.assertEqual({(row["ip"], row["version"]) for row in clamped["recent_ips"]}, groups)
        self.assertEqual(clamped["window_days"], 180)
        self.assertTrue(clamped["ip_pagination"]["has_previous"])
        self.assertFalse(clamped["ip_pagination"]["has_next"])

    def test_daily_startups_use_beijing_midnight_and_local_times_keep_utc_fields(self):
        self.now = int(datetime(2026, 10, 30, 2, tzinfo=timezone.utc).timestamp())
        before = int(datetime(2026, 10, 29, 15, 59, tzinfo=timezone.utc).timestamp())
        after = int(datetime(2026, 10, 29, 16, 1, tzinfo=timezone.utc).timestamp())
        response = self.client.post("/api/v1/events", json=self.batch([self.event(when=before), self.event(when=after)]))
        self.assertEqual(response.status_code, 200)
        summary = self.client.get("/admin/summary", headers=self.auth).json
        counts = {row["date"]: row["count"] for row in summary["daily_startups"] if row["count"]}
        self.assertEqual(counts, {"2026-10-29": 1, "2026-10-30": 1})
        self.assertEqual(summary["generated_at"], "2026-10-30T02:00:00Z")
        self.assertEqual(summary["generated_at_local"], "2026-10-30 10:00:00")
        self.assertEqual(summary["window_end_at_local"], "2026-10-30 10:00:00")
        self.assertEqual(summary["window_start_at"], "2026-09-30T02:00:00Z")
        self.assertEqual(summary["window_start_at_local"], "2026-09-30 10:00:00")
        self.assertEqual(summary["recent_ips"][0]["last_seen_at"], "2026-10-30T02:00:00Z")
        self.assertEqual(summary["recent_ips"][0]["last_seen_at_local"], "2026-10-30 10:00:00")
        self.assertEqual(self.query("SELECT occurred_at FROM events ORDER BY occurred_at"), [(before,), (after,)])

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


class SessionStatisticsTests(_ServerFixture):
    """The admin page lists one row per program run, newest first, with expandable details."""

    def post(self, events, *, session_id, install_id, version="1.3.3", ip="203.0.113.7"):
        batch = dict(schema_version=1, install_id=install_id, session_id=session_id, app_version=version, events=events)
        response = self.client.post("/api/v1/events", json=batch, environ_base={"REMOTE_ADDR": ip})
        self.assertEqual(response.status_code, 200, response.json)

    def test_runs_are_rows_with_ip_environment_imports_and_duration(self):
        install = str(uuid4())
        first, second = str(uuid4()), str(uuid4())
        self.post([self.event(when=self.now - 2 * DAY, os_version="10.0.19045")], session_id=first, install_id=install, ip="198.51.100.1")
        self.post([
            self.event(when=self.now - 600, os_version="10.0.22631", locale="zh-CN"),
            self.event("import_file", when=self.now - 500, instrument="tristar_ii_plus"),
            self.event("import_file", when=self.now - 400, instrument="tristar_ii_plus"),
            self.event("import_file", when=self.now - 300, status="failure", error_code="parse_error",
                       extension="raw", parser="jwgb"),
            {"event_id": str(uuid4()), "occurred_at": datetime.fromtimestamp(self.now - 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "name": "session_end", "data": {"duration_s": 540, "active_s": 300}},
        ], session_id=second, install_id=install, ip="198.51.100.2")
        with patch("telemetry_server.app.enrich_ip_locations",
                   side_effect=lambda rows, _directory: [row.__setitem__("location", "中国 · 江苏省 · 苏州市") for row in rows]):
            summary = self.client.get("/admin/summary", headers=self.auth).json
        newest, older = summary["sessions"]
        self.assertEqual([newest["number"], older["number"]], [1, 2])
        self.assertEqual((newest["ip"], older["ip"]), ("198.51.100.2", "198.51.100.1"))
        self.assertFalse(newest["ip_inferred"])
        self.assertEqual(newest["city"], "苏州")
        self.assertEqual(newest["version"], "1.3.3")
        self.assertEqual(newest["opened_at_local"], "2026-10-29 19:50:00")
        self.assertEqual(newest["duration_s"], 540)
        self.assertEqual(newest["duration_label"], "9'00\"")
        self.assertEqual(older["duration_label"], "未知")
        self.assertEqual((newest["active_s"], newest["active_label"]), (300, "5'00\""))
        self.assertEqual(older["active_label"], "未知")
        self.assertEqual(newest["environment"]["os"], "Windows 11")
        self.assertEqual(older["environment"]["os"], "Windows 10")
        self.assertEqual(newest["environment"]["locale"], "zh-CN")
        self.assertEqual(older["environment"]["locale"], "未知")
        self.assertEqual(newest["environment"]["screen"], "1920×1080")
        self.assertEqual(newest["environment"]["scale"], "125%")
        imports = {(row["extension"], row["instrument_label"]): (row["total"], row["success"], row["failure"]) for row in newest["imports"]}
        self.assertEqual(imports, {("smp", "TriStar II Plus"): (2, 2, 0), ("raw", "未知"): (1, 0, 1)})
        self.assertEqual((newest["import_total"], newest["import_success"]), (3, 2))
        self.assertFalse(newest["install"]["first_run"])
        self.assertTrue(older["install"]["first_run"])
        self.assertEqual(newest["install"]["runs"], 2)
        self.assertEqual(newest["install"]["previous_local"], "2026-10-27 20:00:00")
        self.assertEqual(summary["session_pagination"]["total"], 2)

    def test_runs_recorded_before_per_run_ip_infer_ip_from_installation(self):
        install, session = str(uuid4()), str(uuid4())
        self.post([self.event(when=self.now - 100)], session_id=session, install_id=install, ip="198.51.100.9")
        self.query("DELETE FROM session_ips")
        row = self.client.get("/admin/summary", headers=self.auth).json["sessions"][0]
        self.assertEqual(row["ip"], "198.51.100.9")
        self.assertTrue(row["ip_inferred"])

    def test_first_ip_of_a_run_is_kept_when_queued_events_arrive_from_another_network(self):
        install, session = str(uuid4()), str(uuid4())
        self.post([self.event(when=self.now - 100)], session_id=session, install_id=install, ip="198.51.100.1")
        self.post([self.event("import_file", when=self.now - 50)], session_id=session, install_id=install, ip="198.51.100.2")
        self.assertEqual(self.client.get("/admin/summary", headers=self.auth).json["sessions"][0]["ip"], "198.51.100.1")

    def test_date_filter_keeps_true_open_time_for_a_run_crossing_midnight(self):
        install, session = str(uuid4()), str(uuid4())
        opened = int(datetime(2026, 10, 27, 15, 50, tzinfo=timezone.utc).timestamp())
        self.post([self.event(when=opened), self.event("import_file", when=opened + 20 * 60)],
                  session_id=session, install_id=install)
        response = self.client.get("/admin/summary?start=2026-10-28&end=2026-10-28", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        summary = response.json
        self.assertEqual(len(summary["sessions"]), 1)
        self.assertEqual(summary["sessions"][0]["opened_at_local"], "2026-10-27 23:50:00")
        self.assertEqual(summary["sessions"][0]["import_total"], 1)
        self.assertEqual(summary["window_end_at"], "2026-10-28T15:59:59Z")
        self.assertEqual(summary["window_end_at_local"], "2026-10-28 23:59:59")

    def test_run_pages_are_bounded(self):
        install = str(uuid4())
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany("INSERT INTO events (event_id, install_id, session_id, app_version, name, occurred_at, received_at, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                   [(str(uuid4()), install, str(uuid4()), "1.3.3", "startup", self.now - index, self.now, "{}") for index in range(120)])
        first = self.client.get("/admin/summary?page=1", headers=self.auth).json
        last = self.client.get("/admin/summary?page=3", headers=self.auth).json
        self.assertEqual(first["session_pagination"]["total_pages"], 3)
        self.assertEqual(len(first["sessions"]), 50)
        self.assertEqual([row["number"] for row in last["sessions"]][:2], [101, 102])
        self.assertEqual(len(last["sessions"]), 20)
        beyond = self.client.get("/admin/summary?page=99", headers=self.auth).json
        self.assertEqual(beyond["session_pagination"]["page"], 3)

    def test_all_window_covers_every_recorded_day(self):
        install = str(uuid4())
        self.post([self.event(when=self.now - 500 * DAY)], session_id=str(uuid4()), install_id=install)
        self.post([self.event(when=self.now)], session_id=str(uuid4()), install_id=install)
        summary = self.client.get("/admin/summary?days=all", headers=self.auth).json
        self.assertIsNone(summary["window_days"])
        self.assertEqual(summary["window_label"], "全部")
        self.assertEqual(summary["daily_startups"][0]["count"], 1)
        self.assertEqual(sum(row["count"] for row in summary["daily_startups"]), 2)
        self.assertEqual(len(summary["daily_startups"]), 501)
        self.assertEqual(summary["session_pagination"]["total"], 2)
        year = self.client.get("/admin/summary?days=365", headers=self.auth).json
        self.assertEqual(year["session_pagination"]["total"], 1)

    def test_today_chart_counts_startups_per_beijing_hour(self):
        install = str(uuid4())
        # self.now is 20:00 Beijing time; 07:30 and 19:10 today, 23:00 yesterday.
        for minutes_before in (750, 50, 21 * 60):
            self.post([self.event(when=self.now - minutes_before * 60)], session_id=str(uuid4()), install_id=install)
        summary = self.client.get("/admin/summary", headers=self.auth).json
        hourly = summary["hourly_startups_today"]
        self.assertEqual(len(hourly), 21)
        self.assertEqual({row["hour"]: row["count"] for row in hourly if row["count"]}, {7: 1, 19: 1})
        self.assertEqual(summary["charts"]["today"]["total"], 2)
        self.assertEqual(summary["charts"]["period"]["total"], 3)

    def test_page_shows_run_table_and_charts_without_removed_sections(self):
        install = str(uuid4())
        self.post([self.event(when=self.now - 100), self.event("import_file", when=self.now - 50)],
                  session_id=str(uuid4()), install_id=install)
        page = self.client.get("/admin?days=all", headers=self.auth).data.decode("utf-8")
        # Layout as deployed: the daily-startups chart sits above the IP statistics.
        self.assertLess(page.index('id="chart-title"'), page.index('id="ip-title"'))
        for expected in ("IP 统计", "打开时间", "地域", "打开时长", "活跃时长", "运行环境", "本次使用", "全部", "每日启动次数",
                         'aria-current="page">全部', "<polygon", "文件导入"):
            self.assertIn(expected, page)
        for removed in ("当前统计", "所选周期活跃安装", "所选周期启动次数", "文件导入成功率", "版本占比",
                        "所选周期内出现的 IP", "全部历史 IP", "最后收到", "功能页面浏览", "错误分类排行", "<h2>导出", "<script"):
            self.assertNotIn(removed, page)

    def test_session_end_locale_and_instrument_are_allowlisted(self):
        install, session = str(uuid4()), str(uuid4())
        good = [self.event(locale="en-US"), self.event("import_file", instrument="asap_2460"),
                self.event("import_file", instrument="asap_2010"), self.event("import_file", instrument="bsd"),
                {"event_id": str(uuid4()), "occurred_at": datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "name": "session_end", "data": {"duration_s": 3600}}]
        self.post(good, session_id=session, install_id=install)
        for bad in (self.event(locale="C:\\Users\\name"), self.event("import_file", instrument="My Lab TriStar"),
                    {"event_id": str(uuid4()), "occurred_at": datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "name": "session_end", "data": {"duration_s": -1}},
                    {"event_id": str(uuid4()), "occurred_at": datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "name": "session_end", "data": {"duration_s": 5, "sample": "x"}},
                    {"event_id": str(uuid4()), "occurred_at": datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "name": "session_end", "data": {"duration_s": 60, "active_s": 61}},
                    self.event("import_file", instrument="asap_2025")):
            with self.subTest(bad=bad["data"]):
                batch = dict(schema_version=1, install_id=install, session_id=session, app_version="1.3.3", events=[bad])
                self.assertEqual(self.client.post("/api/v1/events", json=batch).status_code, 400)


class SessionFilterTests(_ServerFixture):
    LOCATIONS = {"198.51.100.1": "中国 · 江苏省 · 苏州市", "198.51.100.2": "中国 · 江苏省 · 南京市",
                 "198.51.100.3": "中国 · 上海 · 上海市", "198.51.100.4": "美国 · 加利福尼亚"}

    def setUp(self):
        super().setUp()
        def locate(rows, _directory):
            for row in rows:
                row["location"] = self.LOCATIONS.get(row.get("ip"), "未知")
        patcher = patch("telemetry_server.app.enrich_ip_locations", side_effect=locate)
        patcher.start()
        self.addCleanup(patcher.stop)
        # now = 2026-10-29 20:00 Beijing.  Runs: (ip, version, minutes before now)
        self.runs = [("198.51.100.1", "1.3.3", 10), ("198.51.100.2", "1.3.3", 60 * 24 + 10),
                     ("198.51.100.3", "1.3.1", 60 * 24 * 3), ("198.51.100.4", "1.3.1", 60 * 24 * 40),
                     ("198.51.100.1", "1.3.1", 60 * 24 * 2)]
        for ip, version, minutes in self.runs:
            batch = dict(schema_version=1, install_id=str(uuid4()), session_id=str(uuid4()), app_version=version,
                         events=[self.event(when=self.now - minutes * 60)])
            response = self.client.post("/api/v1/events", json=batch, environ_base={"REMOTE_ADDR": ip})
            self.assertEqual(response.status_code, 200)

    def summary(self, query=""):
        response = self.client.get("/admin/summary" + query, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def ips(self, summary):
        return [row["ip"] for row in summary["sessions"]]

    def test_region_options_cascade_province_to_city_with_counts(self):
        summary = self.summary("?days=90")
        options = {row["value"]: row for row in summary["filters"]["region_options"]}
        self.assertEqual(options["江苏省"]["count"], 3)
        self.assertEqual(options["江苏省"]["label"], "江苏")
        self.assertEqual({city["label"]: city["count"] for city in options["江苏省"]["cities"]}, {"苏州": 2, "南京": 1})
        self.assertEqual(options["其他"]["count"], 1)
        self.assertEqual(list(options)[-1], "其他")

    def test_region_ip_and_version_filters_narrow_the_table(self):
        self.assertEqual(self.ips(self.summary("?days=90&region=江苏省")), ["198.51.100.1", "198.51.100.2", "198.51.100.1"])
        self.assertEqual(self.ips(self.summary("?days=90&region=江苏省/南京市")), ["198.51.100.2"])
        self.assertEqual(self.ips(self.summary("?days=90&region=其他")), ["198.51.100.4"])
        self.assertEqual(self.ips(self.summary("?days=90&ip=198.51.100.1")), ["198.51.100.1", "198.51.100.1"])
        self.assertEqual(len(self.summary("?days=90&ip=198.51.100")["sessions"]), 5)  # prefix
        self.assertEqual(self.ips(self.summary("?days=90&version=1.3.1&region=江苏省")), ["198.51.100.1"])
        filtered = self.summary("?days=90&version=1.3.1")
        self.assertEqual(filtered["session_pagination"]["total"], 3)
        self.assertEqual(filtered["session_pagination"]["window_total"], 5)
        # Charts and other sections keep describing the whole window.
        self.assertEqual(filtered["charts"]["period"]["total"], 5)

    def test_clicking_an_ip_keeps_other_filters_and_clear_links_remove_one(self):
        summary = self.summary("?days=90&version=1.3.3")
        self.assertEqual(summary["sessions"][0]["ip_query"], "?days=90&ip=198.51.100.1&version=1.3.3")
        filtered = self.summary("?days=90&version=1.3.3&ip=198.51.100.1")
        self.assertEqual(filtered["filters"]["clear"]["ip"], "?days=90&version=1.3.3")
        self.assertEqual(filtered["filters"]["clear"]["version"], "?days=90&ip=198.51.100.1")
        self.assertEqual(filtered["filters"]["clear_all"], "?days=90")

    def test_custom_date_range_replaces_the_period_and_its_highlight(self):
        summary = self.summary("?start=2026-10-26&end=2026-10-28")
        self.assertEqual(summary["window_query"], "custom")
        self.assertEqual(summary["window_label"], "2026-10-26 至 2026-10-28")
        self.assertEqual(self.ips(summary), ["198.51.100.2", "198.51.100.1", "198.51.100.3"])
        self.assertEqual([row["date"] for row in summary["daily_startups"]], ["2026-10-26", "2026-10-27", "2026-10-28"])
        self.assertEqual(summary["date_range"], {"start": "2026-10-26", "end": "2026-10-28", "custom": True,
                                                 "today": "2026-10-29", "day_label": "10-28"})
        self.assertEqual(len(summary["hourly_startups_today"]), 24)
        # Period links drop the custom dates but keep the other filters.
        filtered = self.summary("?start=2026-10-26&end=2026-10-28&version=1.3.1")
        self.assertEqual(filtered["period_options"][0]["query"], "?days=30&version=1.3.1")
        single = self.summary("?start=2026-10-29")
        self.assertEqual(single["window_label"], "2026-10-29")
        self.assertEqual(self.ips(single), ["198.51.100.1"])
        swapped = self.summary("?start=2026-10-28&end=2026-10-26")
        self.assertEqual(swapped["date_range"]["start"], "2026-10-26")
        future = self.summary("?start=2026-10-29&end=2027-01-01")
        self.assertEqual(future["date_range"]["end"], "2026-10-29")
        rolling = self.summary("?days=30")
        self.assertEqual(rolling["window_query"], "30")
        self.assertEqual(rolling["date_range"]["start"], "2026-09-29")
        self.assertFalse(rolling["date_range"]["custom"])

    def test_page_renders_filter_controls_without_script(self):
        page = self.client.get("/admin?start=2026-10-26&end=2026-10-28&ip=198.51.100.1", headers=self.auth).data.decode()
        self.assertNotIn("<script", page)
        periods = page.split('<nav class="periods"')[1].split("</nav>")[0]
        self.assertNotIn('aria-current="page"', periods)  # no preset highlighted for a custom range
        for expected in ('type="date"', 'name="start" value="2026-10-26"', 'name="end" value="2026-10-28"',
                         'name="ip" value="198.51.100.1"', "全部地域", "全部版本", 'class="ip-link"', "清除"):
            self.assertIn(expected, page)
        preset = self.client.get("/admin?days=90", headers=self.auth).data.decode()
        self.assertIn('aria-current="page">近90天', preset)

    def test_calendar_range_equal_to_a_preset_highlights_that_preset(self):
        summary = self.summary("?start=2026-09-29&end=2026-10-29")
        self.assertEqual(summary["window_query"], "30")
        self.assertFalse(summary["date_range"]["custom"])
        page = self.client.get("/admin?start=2026-07-31&end=2026-10-29", headers=self.auth).data.decode()
        self.assertIn('aria-current="page">近90天', page)

    def test_calendar_range_matching_preset_includes_the_whole_first_day(self):
        # At 20:00 Beijing, a rolling cutoff would discard the first day's
        # morning record even though the explicit date range includes it.
        morning = int(datetime(2026, 9, 29, 0, tzinfo=timezone.utc).timestamp())
        batch = dict(schema_version=1, install_id=str(uuid4()), session_id=str(uuid4()), app_version="1.3.3",
                     events=[self.event(when=morning)])
        self.assertEqual(self.client.post("/api/v1/events", json=batch).status_code, 200)
        calendar_range = self.summary("?start=2026-09-29&end=2026-10-29&version=1.3.3")
        rolling = self.summary("?days=30&version=1.3.3")
        self.assertEqual(calendar_range["window_query"], "30")
        self.assertEqual(calendar_range["window_start_at_local"], "2026-09-29 00:00:00")
        self.assertEqual(calendar_range["daily_startups"][0]["count"], 1)
        self.assertEqual(rolling["daily_startups"][0]["count"], 0)
        self.assertEqual(calendar_range["session_pagination"]["total"], rolling["session_pagination"]["total"] + 1)
        self.assertEqual(calendar_range["filters"]["clear"]["version"],
                         "?start=2026-09-29&end=2026-10-29")
