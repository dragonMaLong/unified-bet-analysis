import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtTest, QtWidgets

from bet_telemetry.schema import validate_event
from tristar_bet.ui.main_window import MainWindow
from tristar_bet.ui.telemetry_settings import (
    TelemetryDetailsDialog, TelemetrySettingsDialog, TelemetryUpdateDialog, startup_environment,
)


class TelemetryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_settings_show_compact_summary_and_switch_matches_enabled(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                client = SimpleNamespace(enabled=enabled, available=True, endpoint="https://stats.example/api/v1/events")
                dialog = TelemetrySettingsDialog(client)
                self.assertEqual(dialog.enable_check.isChecked(), enabled)
                buttons = {b.text(): b for b in dialog.findChildren(QtWidgets.QPushButton)}
                self.assertEqual(set(buttons), {"详情", "保存", "取消"})
                labels = [label.text() for label in dialog.findChildren(QtWidgets.QLabel)]
                self.assertEqual(labels, ["帮助 DragonScience 改进兼容性和使用体验"])
                self.assertNotIn(client.endpoint, " ".join(labels))
                dialog.close()

    def test_update_has_two_explicit_actions_and_enter_defaults_to_update_only(self):
        client = SimpleNamespace(enabled=False, available=True, endpoint="https://stats.example/api/v1/events")
        dialog = TelemetryUpdateDialog("发现新版本", "更新说明", client)
        self.assertEqual(dialog.findChildren(QtWidgets.QCheckBox), [])
        self.assertEqual({button.text() for button in dialog.findChildren(QtWidgets.QPushButton)},
                         {"详情", "同意统计并更新", "仅更新"})
        self.assertTrue(dialog.update_only_button.isDefault())
        self.assertFalse(dialog.agree_button.autoDefault())
        self.assertIsNone(dialog.choice)
        dialog.show()
        self.app.processEvents()
        dialog.setFocus()
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Return)
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(dialog.choice, TelemetryUpdateDialog.UPDATE_ONLY)
        self.assertFalse(client.enabled)
        dialog.close()

    def test_update_agreement_is_explicit_and_unavailable_agreement_is_disabled(self):
        client = SimpleNamespace(enabled=False, available=True, endpoint="https://stats.example/api/v1/events")
        dialog = TelemetryUpdateDialog("发现新版本", "更新说明", client)
        dialog.agree_button.click()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(dialog.choice, TelemetryUpdateDialog.CONSENT_AND_UPDATE)
        self.assertFalse(client.enabled, "The main window applies consent only after the dialog returns")
        dialog.close()
        client.available = False
        dialog = TelemetryUpdateDialog("发现新版本", "更新说明", client)
        self.assertFalse(dialog.agree_button.isEnabled())
        self.assertTrue(dialog.update_only_button.isEnabled())
        dialog.agree_button.click()
        self.assertIsNone(dialog.choice)
        dialog.close()

    def test_update_escape_and_window_close_leave_choice_undecided(self):
        client = SimpleNamespace(enabled=False, available=True, endpoint="https://stats.example/api/v1/events")
        for action in ("escape", "close"):
            dialog = TelemetryUpdateDialog("发现新版本", "更新说明", client)
            dialog.show()
            self.app.processEvents()
            if action == "escape":
                QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
            else:
                dialog.close()
            self.assertEqual(dialog.result(), QtWidgets.QDialog.Rejected)
            self.assertIsNone(dialog.choice)
            self.assertFalse(client.enabled)

    def test_details_can_open_without_accepting_update_or_changing_settings(self):
        client = SimpleNamespace(enabled=False, available=True, endpoint="https://stats.example/api/v1/events", set_enabled=Mock())
        for dialog in (TelemetrySettingsDialog(client), TelemetryUpdateDialog("发现新版本", "更新说明", client)):
            details = next(button for button in dialog.findChildren(QtWidgets.QPushButton) if button.text() == "详情")
            with patch.object(TelemetryDetailsDialog, "exec_", return_value=QtWidgets.QDialog.Rejected) as show:
                details.click()
            show.assert_called_once()
            self.assertEqual(dialog.result(), QtWidgets.QDialog.Rejected)
            if isinstance(dialog, TelemetryUpdateDialog):
                self.assertIsNone(dialog.choice)
            else:
                self.assertFalse(dialog.enable_check.isChecked())
            client.set_enabled.assert_not_called()
            dialog.close()

    def test_details_contain_scope_purpose_retention_recipient_and_revocation(self):
        client = SimpleNamespace(enabled=False, available=True, endpoint="https://stats.example/api/v1/events")
        dialog = TelemetryDetailsDialog(client)
        text = "\n".join(label.text() for label in dialog.findChildren(QtWidgets.QLabel))
        for required in ("版本覆盖", "文件兼容性", "包含", "不包含", "7 天", "90 天", "隐私设置", "清空", "无法撤回", client.endpoint):
            self.assertIn(required, text)
        self.assertEqual(dialog.findChildren(QtWidgets.QCheckBox), [])
        self.assertEqual([button.text() for button in dialog.findChildren(QtWidgets.QPushButton)], ["关闭"])
        dialog.close()

    def test_missing_endpoint_cannot_be_enabled(self):
        dialog = TelemetrySettingsDialog(SimpleNamespace(enabled=False, available=False, endpoint=""))
        self.assertFalse(dialog.enable_check.isEnabled())
        self.assertFalse(dialog.enable_check.isChecked())
        dialog.close()

    def test_system_environment_matches_allowlist(self):
        data = startup_environment(self.app.primaryScreen())
        validate_event({"event_id": str(uuid4()), "occurred_at": "2026-10-29T12:00:00Z",
                        "name": "startup", "data": data})
        self.assertNotIn("hostname", data)
        self.assertNotIn("username", data)

    def test_import_success_failure_empty_and_no_private_text(self):
        receiver = SimpleNamespace(_record_telemetry=Mock())
        for result, exception, code in ((SimpleNamespace(point_count=10), None, "none"),
                                        (SimpleNamespace(point_count=0), None, "no_points"),
                                        (None, ValueError("PRIVATE sample C:/PRIVATE/path.SMP"), "parse_error"),
                                        (None, OSError("PRIVATE"), "io_error")):
            with patch("tristar_bet.ui.main_window.load_file", return_value=result, side_effect=exception):
                if exception:
                    with self.assertRaises(type(exception)):
                        MainWindow._load_file_with_telemetry(receiver, "C:/PRIVATE/sample.SMP")
                else:
                    self.assertIs(MainWindow._load_file_with_telemetry(receiver, "C:/PRIVATE/sample.SMP"), result)
            args, data = receiver._record_telemetry.call_args
            self.assertEqual(args, ("import_file",))
            self.assertEqual(data["error_code"], code)
            self.assertNotIn("PRIVATE", json.dumps(data))
            validate_event({"event_id": str(uuid4()), "occurred_at": "2026-10-29T12:00:00Z",
                            "name": "import_file", "data": data})

    def test_disabled_or_broken_telemetry_does_not_break_analysis(self):
        receiver = SimpleNamespace(_telemetry=Mock())
        receiver._telemetry.record.side_effect = OSError("disk failure")
        MainWindow._record_telemetry(receiver, "feature_view", feature="bet", surface="plot")
        receiver._telemetry = None
        MainWindow._record_telemetry(receiver, "feature_view", feature="bet", surface="plot")

    def test_only_known_analysis_views_with_data_are_recorded(self):
        receiver = SimpleNamespace(results=[], _record_telemetry=Mock())
        MainWindow._record_feature_view(receiver, 0, "plot")
        receiver._record_telemetry.assert_not_called()
        receiver.results = [object()]
        MainWindow._record_feature_view(receiver, 6, "plot")
        receiver._record_telemetry.assert_called_once_with("feature_view", feature="dft", surface="plot")
        receiver._record_telemetry.reset_mock()
        MainWindow._record_feature_view(receiver, 4, "results")
        receiver._record_telemetry.assert_not_called()

    def test_export_cancel_does_not_record_and_export_failure_is_sanitized(self):
        receiver = SimpleNamespace(results=[object()], visible_results=[True], export_directory=Path("."),
                                   _selected_pore_volume_range=lambda: (2, 50),
                                   _pore_volume_method_label=lambda: "BJH",
                                   _selected_pore_volume_for_result=lambda _: 0.2,
                                   _write_directory_setting=Mock(), _record_telemetry=Mock())
        with patch("tristar_bet.ui.main_window.QtWidgets.QFileDialog.getSaveFileName", return_value=("", "")):
            MainWindow.export_xlsx(receiver)
        receiver._record_telemetry.assert_not_called()
        with patch("tristar_bet.ui.main_window.QtWidgets.QFileDialog.getSaveFileName", return_value=("PRIVATE.xlsx", "")), \
                patch("tristar_bet.ui.main_window.export_results_xlsx", side_effect=OSError("PRIVATE")), \
                patch("tristar_bet.ui.main_window.QtWidgets.QMessageBox.critical"):
            MainWindow.export_xlsx(receiver)
        args, data = receiver._record_telemetry.call_args
        self.assertEqual(args, ("export",))
        self.assertEqual(data["status"], "failure")
        self.assertNotIn("PRIVATE", json.dumps(data))


if __name__ == "__main__":
    unittest.main()
