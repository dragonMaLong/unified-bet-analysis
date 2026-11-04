"""Software updates leave optional usage-statistics preferences unchanged."""
import os
import unittest
from dataclasses import replace
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets

from tristar_bet.ui.main_window import MainWindow
from tristar_bet.update_checker import UpdateInfo


UPDATE = UpdateInfo(
    current_version="1.2.2", latest_version="1.2.3", update_available=True,
    release_url="https://example.test/release", download_url="https://example.test/BET.exe",
    asset_name="BET.exe", release_name="1.2.3", release_notes="修复兼容性", published_at="",
)


class ConsentClient:
    def __init__(self, *, enabled=False, available=True, decided=False, busy=False, save_succeeds=True):
        self.enabled = enabled
        self.available = available
        self.consent_decided = decided
        self.storage_busy = busy
        self.endpoint = "https://stats.example/api/v1/events" if available or busy else ""
        self.save_succeeds = save_succeeds
        self.set_enabled = Mock(side_effect=self._save)

    def _save(self, enabled):
        self.enabled = enabled if self.save_succeeds else False
        self.consent_decided = True
        return self.save_succeeds


class TelemetryUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.receiver = QtWidgets.QWidget()
        self.receiver._telemetry = ConsentClient()
        self.receiver._telemetry_startup_sent = False
        self.receiver._initialize_telemetry = Mock()
        self.receiver._apply_telemetry_consent = MethodType(MainWindow._apply_telemetry_consent, self.receiver)
        self.receiver._record_telemetry_startup = MethodType(MainWindow._record_telemetry_startup, self.receiver)
        self.receiver._record_telemetry = Mock()
        self.receiver._download_and_install_update = Mock()

    def tearDown(self):
        self.receiver.close()
        self.receiver.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_every_telemetry_state_uses_update_or_later_without_preference_changes(self):
        scenarios = (
            ConsentClient(enabled=True, decided=True),
            ConsentClient(enabled=False, decided=True),
            ConsentClient(enabled=False, decided=False),
            ConsentClient(available=False),
            ConsentClient(available=False, busy=True),
            None,
        )
        for client in scenarios:
            for action in ("update", "later", "close"):
                with self.subTest(client=client, action=action):
                    self.receiver._telemetry = client
                    self.receiver._download_and_install_update.reset_mock()
                    before = (client.enabled, client.consent_decided) if client else None
                    update_button, later_button = object(), object()
                    box = Mock()
                    box.addButton.side_effect = [update_button, later_button]
                    box.clickedButton.return_value = {
                        "update": update_button, "later": later_button, "close": None,
                    }[action]
                    with patch("tristar_bet.ui.main_window.QtWidgets.QMessageBox", return_value=box):
                        MainWindow._show_update_available_dialog(self.receiver, UPDATE)
                    self.assertEqual([call.args[0] for call in box.addButton.call_args_list], ["更新", "稍后"])
                    text = box.setText.call_args.args[0] + box.setInformativeText.call_args.args[0]
                    self.assertIn("修复兼容性", text)
                    self.assertNotIn("帮助 DragonScience", text)
                    self.assertNotIn("统计", text)
                    if action == "update":
                        self.receiver._download_and_install_update.assert_called_once_with(UPDATE)
                    else:
                        self.receiver._download_and_install_update.assert_not_called()
                    if client:
                        client.set_enabled.assert_not_called()
                        self.assertEqual((client.enabled, client.consent_decided), before)
                    self.receiver._initialize_telemetry.assert_not_called()
                    self.receiver._record_telemetry.assert_not_called()

    def test_download_failure_keeps_existing_telemetry_preference(self):
        for enabled, decided in ((True, True), (False, True), (False, False)):
            with self.subTest(enabled=enabled, decided=decided):
                client = ConsentClient(enabled=enabled, decided=decided)
                self.receiver._telemetry = client
                self.receiver._download_and_install_update.side_effect = OSError("download failed")
                update_button, later_button = object(), object()
                box = Mock()
                box.addButton.side_effect = [update_button, later_button]
                box.clickedButton.return_value = update_button
                with patch("tristar_bet.ui.main_window.QtWidgets.QMessageBox", return_value=box):
                    with self.assertRaises(OSError):
                        MainWindow._show_update_available_dialog(self.receiver, UPDATE)
                self.assertEqual((client.enabled, client.consent_decided), (enabled, decided))
                client.set_enabled.assert_not_called()

    def test_startup_initializer_never_prompts_and_restores_only_existing_consent(self):
        for enabled, decided, available in ((False, False, True), (False, True, True), (True, True, True), (False, False, False)):
            with self.subTest(enabled=enabled, decided=decided, available=available):
                client = ConsentClient(enabled=enabled, decided=decided, available=available)
                receiver = SimpleNamespace(
                    _telemetry_closed=False, _telemetry_initialized=False, _telemetry=None,
                    _telemetry_startup_sent=False, _record_telemetry=Mock(), screen=self.app.primaryScreen,
                )
                receiver._record_telemetry_startup = MethodType(MainWindow._record_telemetry_startup, receiver)
                with patch("tristar_bet.ui.main_window.TelemetryClient", return_value=client) as create, patch(
                    "tristar_bet.ui.main_window.configured_endpoint", return_value=client.endpoint
                ), patch("tristar_bet.ui.main_window.QtCore.QStandardPaths.writableLocation", return_value="C:/telemetry-test"), patch(
                    "tristar_bet.ui.main_window.TelemetrySettingsDialog", side_effect=AssertionError("Startup must not prompt")
                ):
                    MainWindow._initialize_telemetry(receiver)
                    MainWindow._initialize_telemetry(receiver)
                create.assert_called_once()
                client.set_enabled.assert_not_called()
                self.assertEqual(receiver._record_telemetry.call_count, 1 if enabled else 0)

    def test_closed_window_does_not_initialize_from_delayed_startup_timer(self):
        receiver = SimpleNamespace(_telemetry_closed=True, _telemetry_initialized=False)
        with patch("tristar_bet.ui.main_window.TelemetryClient") as create:
            MainWindow._initialize_telemetry(receiver)
        create.assert_not_called()

    def test_automatic_update_check_only_shows_indicator_and_no_update_does_not_ask_consent(self):
        receiver = SimpleNamespace(
            _finish_update_check=Mock(), _set_update_available_indicator=Mock(),
            _show_update_available_dialog=Mock(), statusBar=Mock(return_value=Mock()),
        )
        MainWindow._on_update_check_finished(receiver, UPDATE, False)
        receiver._set_update_available_indicator.assert_called_once_with(UPDATE)
        receiver._show_update_available_dialog.assert_not_called()
        with patch("tristar_bet.ui.main_window.QtWidgets.QMessageBox.information") as up_to_date:
            MainWindow._on_update_check_finished(receiver, replace(UPDATE, update_available=False), True)
        up_to_date.assert_called_once()
        receiver._show_update_available_dialog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
