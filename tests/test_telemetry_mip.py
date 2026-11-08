"""BET and MIP (mercury_smp) share one statistics server, page and database."""
import sqlite3
import unittest
from contextlib import closing
from uuid import uuid4

from bet_telemetry.schema import validate_batch, validate_event
from telemetry_server.app import initialize_database
from tests.test_telemetry_server import _ServerFixture


def _mip_event(fixture, name="startup", **data):
    event = fixture.event(name, **data)
    if name == "feature_view" and "feature" not in data:
        event["data"]["feature"] = "pore_distribution"
    return event


class SchemaTests(_ServerFixture):
    def mip_batch(self, events):
        return dict(schema_version=1, app_id="mercury_smp", install_id=str(uuid4()), session_id=str(uuid4()),
                    app_version="1.1.2", events=events)

    def test_bet_batches_without_app_id_stay_valid_and_unchanged(self):
        batch = self.batch()
        self.assertNotIn("app_id", validate_batch(batch))
        self.assertEqual(validate_batch({**batch, "app_id": "bet"})["app_id"], "bet")

    def test_mip_batch_uses_mip_categories(self):
        events = [_mip_event(self), _mip_event(self, "feature_view", feature="mayer_stowe", surface="plot"),
                  _mip_event(self, "import_file", instrument="autopore_v"),
                  _mip_event(self, "export", format="pdf")]
        self.assertEqual(len(validate_batch(self.mip_batch(events))["events"]), 4)

    def test_categories_of_the_other_software_are_rejected(self):
        for app_id, event in (("mercury_smp", self.event("feature_view", feature="bet")),
                              ("mercury_smp", self.event("import_file", instrument="3flex")),
                              ("bet", self.event("export", format="pdf")),
                              ("bet", self.event("feature_view", feature="mayer_stowe"))):
            with self.subTest(app_id=app_id, event=event["data"]), self.assertRaises(ValueError):
                validate_event(event, app_id)

    def test_unknown_application_is_rejected(self):
        response = self.client.post("/api/v1/events", json={**self.batch(), "app_id": "other_app"})
        self.assertEqual(response.status_code, 400)


class SharedDashboardTests(_ServerFixture):
    def post(self, events, *, app_id=None, version="1.3.7", install_id=None, ip="203.0.113.7"):
        batch = dict(schema_version=1, install_id=install_id or str(uuid4()), session_id=str(uuid4()),
                     app_version=version, events=events)
        if app_id:
            batch["app_id"] = app_id
        response = self.client.post("/api/v1/events", json=batch, environ_base={"REMOTE_ADDR": ip})
        self.assertEqual(response.status_code, 200, response.json)

    def summary(self, query=""):
        response = self.client.get("/admin/summary" + query, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def setUp(self):
        super().setUp()
        for _ in range(2):
            self.post([self.event(when=self.now - 600)])
        self.post([self.event(when=self.now - 300), self.event("import_file", when=self.now - 200)], version="1.1.2")
        self.post([_mip_event(self, when=self.now - 100), _mip_event(self, "import_file", when=self.now - 50,
                                                                     instrument="autopore_v")],
                  app_id="mercury_smp", version="1.1.2")

    def test_rows_are_stored_per_software(self):
        rows = dict(self.query("SELECT app, COUNT(*) FROM events WHERE name = 'startup' GROUP BY app"))
        self.assertEqual(rows, {"bet": 3, "mercury_smp": 1})

    def test_charts_draw_one_line_per_software_on_one_scale(self):
        summary = self.summary()
        for key in ("today", "period"):
            chart = summary["charts"][key]
            self.assertEqual([line["app_label"] for line in chart["series"]], ["BET", "MIP"])
            self.assertEqual([line["total"] for line in chart["series"]], [3, 1])
            self.assertEqual(chart["total"], 4)
        self.assertEqual(summary["daily_startups"][-1]["by_app"], {"bet": 3, "mercury_smp": 1})
        self.assertEqual(summary["charts"]["period"]["series"][1]["color"], "#e0a100")

    def test_software_filter_and_version_options(self):
        everything = self.summary()
        self.assertEqual(everything["session_pagination"]["total"], 4)
        self.assertEqual({row["app_label"] for row in everything["sessions"]}, {"BET", "MIP"})
        labels = [option["label"] for option in everything["filters"]["version_options"]]
        # BET 1.1.2 and MIP 1.1.2 are different versions of different programs.
        self.assertEqual(labels, ["BET 1.3.7", "BET 1.1.2", "MIP 1.1.2"])
        mip_option = everything["filters"]["version_options"][2]
        self.assertIn("app=mercury_smp", mip_option["query"])
        mip = self.summary("?app=mercury_smp")
        self.assertEqual(mip["session_pagination"]["total"], 1)
        self.assertEqual(mip["sessions"][0]["imports"][0]["instrument_label"], "AutoPore V")
        self.assertEqual([option["label"] for option in mip["filters"]["version_options"]], ["1.1.2"])
        self.assertEqual(mip["filters"]["app_label"], "MIP")
        self.assertEqual(mip["charts"]["period"]["total"], 4)  # charts are not filtered
        bet_112 = self.summary("?app=bet&version=1.1.2")
        self.assertEqual(bet_112["session_pagination"]["total"], 1)
        self.assertEqual(bet_112["sessions"][0]["app"], "bet")
        self.assertEqual(self.client.get("/admin/summary?app=other", headers=self.auth).status_code, 400)

    def test_page_shows_software_column_between_ip_and_version(self):
        page = self.client.get("/admin", headers=self.auth).data.decode("utf-8")
        self.assertIn("<title>DragonScience 软件使用统计</title>", page)
        self.assertIn("<th>IP</th><th>软件</th><th>版本</th>", page)
        self.assertIn('<span class="field-label">软件</span>', page)
        self.assertIn('stroke="#e0a100"', page)
        self.assertNotIn("<script", page)
        filtered = self.client.get("/admin?app=mercury_smp", headers=self.auth).data.decode("utf-8")
        self.assertIn('<input type="hidden" name="app" value="mercury_smp">', filtered)
        self.assertIn("<summary>MIP</summary>", filtered)


class MigrationTests(unittest.TestCase):
    OLD_EVENTS = """CREATE TABLE events (
        event_id TEXT PRIMARY KEY, install_id TEXT NOT NULL, session_id TEXT NOT NULL, app_version TEXT NOT NULL,
        name TEXT NOT NULL, occurred_at INTEGER NOT NULL, received_at INTEGER NOT NULL, data_json TEXT NOT NULL,
        status TEXT, extension TEXT, feature TEXT, surface TEXT, export_format TEXT, error_code TEXT,
        duration_ms INTEGER)"""

    def test_existing_rows_become_bet_and_migration_is_repeatable(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(self.OLD_EVENTS)
                connection.execute("INSERT INTO events VALUES ('e1','i','s','1.3.0','startup',1,1,'{}',"
                                   "NULL,NULL,NULL,NULL,NULL,NULL,NULL)")
            for _ in range(2):
                initialize_database(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT event_id, app FROM events").fetchall(), [("e1", "bet")])
                indexes = {row[1] for row in connection.execute("PRAGMA index_list(events)")}
                self.assertIn("events_app_time", indexes)


if __name__ == "__main__":
    unittest.main()
