"""Consent is an optional update choice, never a prerequisite or revocation."""
import os
import unittest
from dataclasses import replace
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets

from tristar_bet.ui.main_window import MainWindow
from tristar_bet.ui.telemetry_settings import TelemetryUpdateDialog
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

    def choose(self, action):
        prompt = Mock()

        def execute(dialog):
            prompt()
            if action == "agree":
                dialog.agree_button.click()
            elif action == "update":
                dialog.update_only_button.click()
            elif action == "invalid_accept":
                dialog.accept()
            else:
                dialog.reject()
            return dialog.result()

        with patch.object(TelemetryUpdateDialog, "exec_", new=execute):
            MainWindow._show_update_available_dialog(self.receiver, UPDATE)
        prompt.assert_called_once()

    def test_explicit_consent_is_saved_before_update_and_startup_is_recorded_once(self):
        order = []
        client = self.receiver._telemetry
        original_save = client._save

        def save(enabled):
            order.append(("consent", enabled))
            return original_save(enabled)

        client.set_enabled.side_effect = save
        self.receiver._download_and_install_update.side_effect = lambda _info: order.append(("download", client.enabled))
        self.choose("agree")
        self.assertEqual(order, [("consent", True), ("download", True)])
        self.assertTrue(client.enabled)
        self.receiver._record_telemetry.assert_called_once()
        self.assertEqual(self.receiver._record_telemetry.call_args.args, ("startup",))
        self.receiver._record_telemetry_startup()
        self.receiver._record_telemetry.assert_called_once()

    def test_update_only_saves_decline_for_undecided_user_and_downloads(self):
        self.choose("update")
        client = self.receiver._telemetry
        client.set_enabled.assert_called_once_with(False)
        self.assertFalse(client.enabled)
        self.assertTrue(client.consent_decided)
        self.receiver._record_telemetry.assert_not_called()
        self.receiver._download_and_install_update.assert_called_once_with(UPDATE)

    def test_close_or_invalid_accept_does_not_change_consent_or_download(self):
        for action in ("close", "invalid_accept"):
            with self.subTest(action=action):
                self.choose(action)
                self.receiver._telemetry.set_enabled.assert_not_called()
                self.assertFalse(self.receiver._telemetry.consent_decided)
                self.receiver._record_telemetry.assert_not_called()
                self.receiver._download_and_install_update.assert_not_called()

    def test_saving_consent_failure_warns_but_update_still_proceeds_without_statistics(self):
        self.receiver._telemetry = ConsentClient(save_succeeds=False)
        with patch("tristar_bet.ui.main_window.QtWidgets.QMessageBox.information") as warning:
            self.choose("agree")
        warning.assert_called_once()
        self.assertFalse(self.receiver._telemetry.enabled)
        self.receiver._record_telemetry.assert_not_called()
        self.receiver._download_and_install_update.assert_called_once_with(UPDATE)

    def test_download_failure_does_not_revoke_explicitly_saved_consent(self):
        self.receiver._download_and_install_update.side_effect = OSError("download failed")
        with self.assertRaises(OSError):
            self.choose("agree")
        self.assertTrue(self.receiver._telemetry.enabled)
        self.receiver._telemetry.set_enabled.assert_called_once_with(True)

    def test_known_preferences_and_unavailable_clients_use_regular_update_without_consent_changes(self):
        scenarios = (
            ConsentClient(enabled=True, decided=True),
            ConsentClient(enabled=False, decided=True),
            ConsentClient(available=False),
            ConsentClient(available=False, busy=True),
            None,
        )
        for client in scenarios:
            for update in (False, True):
                with self.subTest(client=client, update=update):
                    self.receiver._telemetry = client
                    self.receiver._download_and_install_update.reset_mock()
                    before = client.enabled if client else None
                    update_button, later_button = object(), object()
                    box = Mock()
                    box.addButton.side_effect = [update_button, later_button]
                    box.clickedButton.return_value = update_button if update else later_button
                    with patch("tristar_bet.ui.main_window.QtWidgets.QMessageBox", return_value=box), patch(
                        "tristar_bet.ui.main_window.TelemetryUpdateDialog", side_effect=AssertionError("Unexpected consent prompt")
                    ):
                        MainWindow._show_update_available_dialog(self.receiver, UPDATE)
                    if update:
                        self.receiver._download_and_install_update.assert_called_once_with(UPDATE)
                    else:
                        self.receiver._download_and_install_update.assert_not_called()
                    if client:
                        client.set_enabled.assert_not_called()
                        self.assertEqual(client.enabled, before)

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
                ), patch("tristar_bet.ui.main_window.TelemetryUpdateDialog", side_effect=AssertionError("Startup must not prompt")):
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
