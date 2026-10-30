import copy
import unittest
from uuid import uuid4

from bet_telemetry.schema import validate_batch, validate_event


def event(name="import_file", **data):
    return {"event_id": str(uuid4()), "occurred_at": "2026-10-29T12:00:00Z", "name": name,
            "data": data or {"extension": "smp", "parser": "micromeritics", "status": "success",
                             "duration_ms": 12, "error_code": "none"}}


class TelemetrySchemaTests(unittest.TestCase):
    def test_valid_events_and_batch(self):
        events = [event(), event("feature_view", feature="dft", surface="plot"),
                  event("export", format="xlsx", status="failure", duration_ms=4, error_code="write_error"),
                  event("startup", os="Windows", os_version="10.0.26100", architecture="x86_64",
                        screen_width=1920, screen_height=1080, scale=1.5)]
        batch = {"schema_version": 1, "install_id": str(uuid4()), "session_id": str(uuid4()),
                 "app_version": "1.2.2", "events": events}
        result = validate_batch(batch)
        self.assertEqual(result, batch)
        result["events"][0]["data"]["duration_ms"] = 99
        self.assertEqual(batch["events"][0]["data"]["duration_ms"], 12)

    def test_private_or_arbitrary_fields_are_rejected(self):
        for key in ("path", "sample", "ip", "username", "error_message", "traceback"):
            value = event()
            value["data"][key] = "PRIVATE"
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_event(value)
        value = event()
        value["data"]["parser"] = "PRIVATE instrument description"
        with self.assertRaises(ValueError):
            validate_event(value)

    def test_types_bounds_and_semantics_are_strict(self):
        for change in ({"duration_ms": True}, {"duration_ms": -1}, {"status": "failure"},
                       {"extension": "smp; private text"}, {"error_code": "file not found PRIVATE"}):
            value = event()
            value["data"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_event(value)
        for invalid in ("2026-02-30T12:00:00Z", "2026-10-29T12:00:00+08:00", "0001-01-01T00:00:00Z", "PRIVATE"):
            value = event()
            value["occurred_at"] = invalid
            with self.assertRaises(ValueError):
                validate_event(value)

    def test_identifiers_version_batch_size(self):
        batch = {"schema_version": 1, "install_id": str(uuid4()), "session_id": str(uuid4()),
                 "app_version": "1.2.2", "events": [event()]}
        for change in ({"install_id": "computer-name"}, {"app_version": "bad/version"},
                       {"schema_version": True}, {"events": []}, {"events": [event() for _ in range(31)]},
                       {"events": batch["events"] * 2}, {"hostname": "PRIVATE"}):
            value = copy.deepcopy(batch)
            value.update(change)
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                validate_batch(value)


if __name__ == "__main__":
    unittest.main()
