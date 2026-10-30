"""Real loopback HTTP round trip; never contacts the production domain."""
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.serving import WSGIRequestHandler, make_server

from telemetry_server import create_app
from tristar_bet import telemetry


class QuietHandler(WSGIRequestHandler):
    def log(self, *_args, **_kwargs):
        pass


class TelemetryIntegrationTests(unittest.TestCase):
    def test_default_delivery_dashboard_dedup_and_opt_out_over_http(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app({"DATABASE": str(root / "server.sqlite3"),
                              "ADMIN_USERNAME": "test-admin", "ADMIN_PASSWORD": "test-only-secret"})
            server = make_server("127.0.0.1", 0, app, request_handler=QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = f"http://127.0.0.1:{server.server_port}/api/v1/events"
            client = telemetry.TelemetryClient(root / "client", "1.2.2", endpoint)
            sent = []
            real_send = telemetry._send_batch

            def capture(url, batch, **kwargs):
                sent.append(copy.deepcopy(batch))
                return real_send(url, batch, **kwargs)

            try:
                with patch.object(telemetry, "_send_batch", side_effect=capture):
                    self.assertTrue(client.enabled)
                    self.assertTrue(client.record("startup", os="Windows", os_version="10.0.26100",
                                                  architecture="x86_64", screen_width=1920, screen_height=1080, scale=1.5))
                    self.assertTrue(client.record("import_file", extension="smp", parser="micromeritics",
                                                  status="success", duration_ms=120, error_code="none"))
                    deadline = time.monotonic() + 5
                    while client.pending_count and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertEqual(client.pending_count, 0)
                    self.assertTrue(sent)
                    # A response lost after storage causes a retry of exactly the same IDs.
                    self.assertEqual(real_send(endpoint, sent[0], cancelled=threading.Event()),
                                     {event["event_id"] for event in sent[0]["events"]})
                    admin = app.test_client()
                    self.assertEqual(admin.get("/admin").status_code, 401)
                    summary = admin.get("/admin/summary", auth=("test-admin", "test-only-secret")).get_json()
                    self.assertEqual(summary["active_installs"]["7d"], 1)
                    self.assertEqual(summary["imports"]["total"], 1)
                    self.assertEqual(sum(day["count"] for day in summary["daily_startups"]), 1)
                    self.assertEqual(summary["version_share"][0]["version"], "1.2.2")
                    self.assertEqual(summary["recent_ips"][0]["ip"], "127.0.0.1")
                    page = admin.get("/admin", auth=("test-admin", "test-only-secret"))
                    self.assertEqual(page.status_code, 200)
                    self.assertIn("BET 使用统计", page.get_data(as_text=True))
                    requests_before = len(sent)
                    self.assertTrue(client.set_enabled(False))
                    self.assertFalse(client.record("feature_view", feature="dft", surface="plot"))
                    self.assertEqual(client.pending_count, 0)
                    self.assertEqual(len(sent), requests_before)
            finally:
                client.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
