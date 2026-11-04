"""Locale, instrument category and open/active time in the usage statistics."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import QtCore, QtWidgets

from bet_telemetry.schema import INSTRUMENTS, validate_event
from tristar_bet.ui.main_window import MainWindow, _telemetry_instrument
from tristar_bet.ui.telemetry_settings import startup_environment, system_locale
from tristar_bet.ui.usage_timer import IDLE_LIMIT_S, UsageTimer


def _event(name, **data):
    return {"event_id": "6f1c1a4e-8d4b-4a8e-9b2a-3c2d1e0f9a7b", "occurred_at": "2026-11-04T08:00:00Z",
            "name": name, "data": data}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class UsageTimerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_active_time_needs_foreground_and_recent_input(self):
        clock = FakeClock()
        timer = UsageTimer(clock=clock)
        for _ in range(12):  # 60 s in the foreground right after starting
            clock.now += 5
            timer.tick(foreground=True)
        for _ in range(12):  # 60 s in the background
            clock.now += 5
            timer.tick(foreground=False)
        clock.now += IDLE_LIMIT_S  # foreground but untouched for longer than the idle limit
        timer.tick(foreground=True)
        for _ in range(12):
            clock.now += 5
            timer.tick(foreground=True)
        timer._last_input = clock.now  # input again: next minute counts
        for _ in range(12):
            clock.now += 5
            timer.tick(foreground=True)
        with patch.object(UsageTimer, "_foreground", return_value=False):
            open_s, active_s = timer.finish()
        self.assertEqual(open_s, int(60 + 60 + IDLE_LIMIT_S + 60 + 60))
        self.assertEqual(active_s, 120)
        self.assertEqual(timer.finish(), (open_s, active_s))  # stable after closing

    def test_sleep_gap_is_not_counted_as_active(self):
        clock = FakeClock()
        timer = UsageTimer(clock=clock)
        timer._last_input = clock.now + 3600
        clock.now += 3600  # machine asleep for an hour
        timer.tick(foreground=True)
        self.assertLessEqual(timer._active, 10.0)

    def test_input_events_update_last_input(self):
        clock = FakeClock()
        timer = UsageTimer(clock=clock)
        clock.now += 500
        timer.eventFilter(None, QtCore.QEvent(QtCore.QEvent.KeyPress))
        self.assertEqual(timer._last_input, clock.now)
        clock.now += 10
        timer.eventFilter(None, QtCore.QEvent(QtCore.QEvent.Paint))
        self.assertNotEqual(timer._last_input, clock.now)


class TelemetryFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_schema_accepts_new_fields_and_bounds_active_time(self):
        validate_event(_event("session_end", duration_s=600, active_s=300))
        validate_event(_event("session_end", duration_s=600))  # older clients
        with self.assertRaises(ValueError):
            validate_event(_event("session_end", duration_s=60, active_s=61))
        with self.assertRaises(ValueError):
            validate_event(_event("session_end", duration_s=60, active_s=1.5))
        for instrument in ("asap_2010", "bsd"):
            validate_event(_event("import_file", status="success", duration_ms=5, error_code="none",
                                  extension="smp", parser="micromeritics", instrument=instrument))

    def test_locale_is_bounded_code(self):
        locale = system_locale()
        if locale:
            validate_event(_event("startup", os="Windows", os_version="10.0", architecture="x86_64",
                                  screen_width=1, screen_height=1, scale=1.0, locale=locale))
        with patch("tristar_bet.ui.telemetry_settings.QtCore.QLocale.system") as system:
            system.return_value.name.return_value = "C"
            self.assertEqual(system_locale(), "")
            system.return_value.name.return_value = "zh_CN"
            self.assertEqual(system_locale(), "zh_CN")
            self.assertEqual(startup_environment(None)["locale"], "zh_CN")

    def test_instrument_category_comes_from_parsed_metadata(self):
        def result(model="", manufacturer=""):
            return SimpleNamespace(method_options={"instrument_model": model, "instrument_manufacturer": manufacturer})
        cases = [
            (result("TriStar II Plus", "Micromeritics"), "micromeritics", "tristar_ii_plus"),
            (result("TriStar II 3020", "Micromeritics"), "micromeritics", "tristar_ii_3020"),
            (result("ASAP 2020 Plus", "Micromeritics"), "micromeritics", "asap_2020_plus"),
            (result("ASAP 2020", "Micromeritics"), "micromeritics", "asap_2020"),
            (result("ASAP 2010", "Micromeritics"), "micromeritics", "asap_2010"),
            (result("3Flex 3500", "Micromeritics"), "excel", "3flex"),
            (result("", "Micromeritics"), "micromeritics", "micromeritics_other"),
            (result("BELMaster", "MicrotracBEL"), "excel", "belsorp"),
            (result("Autosorb", "Quantachrome"), "quantachrome", "quantachrome"),
            (result("BSD-660MC", "BSD"), "excel", "bsd"),
            (result("APAS1000_ZQ", "JWGB"), "jwgb", "jwgb"),
            (result(), "excel", "excel"),
            (result(), "unknown", "other"),
        ]
        for value, parser, expected in cases:
            with self.subTest(expected=expected):
                category = _telemetry_instrument(value, parser)
                self.assertEqual(category, expected)
                self.assertIn(category, INSTRUMENTS)


class SessionEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_closing_records_open_and_active_time_once(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = QtCore.QSettings(str(Path(temp.name) / 's.ini'), QtCore.QSettings.IniFormat)
        with patch.object(MainWindow, '_auto_check_for_updates'), patch.object(MainWindow, '_initialize_telemetry'), \
                patch('tristar_bet.ui.main_window.QtCore.QSettings', return_value=settings):
            window = MainWindow()
        recorded = []
        window._record_telemetry = lambda name, **data: recorded.append((name, data))
        window._usage_timer.finish = lambda: (125, 40)
        window.close()
        window.close()
        self.assertEqual(recorded, [("session_end", {"duration_s": 125, "active_s": 40})])
        validate_event(_event("session_end", **recorded[0][1]))
        window.deleteLater()

    def test_import_event_carries_instrument_only_when_parsed(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = QtCore.QSettings(str(Path(temp.name) / 's.ini'), QtCore.QSettings.IniFormat)
        with patch.object(MainWindow, '_auto_check_for_updates'), patch.object(MainWindow, '_initialize_telemetry'), \
                patch('tristar_bet.ui.main_window.QtCore.QSettings', return_value=settings):
            window = MainWindow()
        self.addCleanup(window.deleteLater)
        recorded = []
        window._record_telemetry = lambda name, **data: recorded.append((name, data))
        parsed = SimpleNamespace(point_count=10, method_options={"instrument_model": "ASAP 2460",
                                                                  "instrument_manufacturer": "Micromeritics"})
        with patch('tristar_bet.ui.main_window.load_file', return_value=parsed):
            window._load_file_with_telemetry(str(Path(temp.name) / 'a.smp'))
        with patch('tristar_bet.ui.main_window.load_file', side_effect=ValueError("bad")):
            with self.assertRaises(ValueError):
                window._load_file_with_telemetry(str(Path(temp.name) / 'b.smp'))
        self.assertEqual(recorded[0][1]["instrument"], "asap_2460")
        self.assertNotIn("instrument", recorded[1][1])
        for _name, data in recorded:
            validate_event(_event("import_file", **data))


if __name__ == '__main__':
    unittest.main()
