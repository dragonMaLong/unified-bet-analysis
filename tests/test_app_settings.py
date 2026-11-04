import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import QtCore, QtWidgets

from tristar_bet.analysis import single_point_bet_analysis
from tristar_bet.ui.app_settings import CONTACT_EMAIL, AppSettings, AppSettingsDialog
from tristar_bet.ui.main_window import BET_DISPLAY_LABELS, IMPORT_FAILURE_CONTACT, MainWindow, _with_import_contact
from test_detail_result_tables import sample


class AppSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QtCore.QSettings(str(Path(self.temp.name) / 's.ini'), QtCore.QSettings.IniFormat)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.temp.cleanup()

    def make_window(self):
        with patch.object(MainWindow, '_auto_check_for_updates'), patch.object(MainWindow, '_initialize_telemetry'), \
                patch('tristar_bet.ui.main_window.QtCore.QSettings', return_value=self.settings):
            window = MainWindow()
        self.windows.append(window)
        return window

    def test_dialog_returns_edited_values(self):
        current = AppSettings('xlsx', Path(self.temp.name), 'default', True, 0.2)
        dialog = AppSettingsDialog(None, current, BET_DISPLAY_LABELS, '9.9.9', None)
        self.assertEqual(dialog.values(), current)
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData('pdf'))
        dialog.bet_combo.setCurrentIndex(dialog.bet_combo.findData('multi'))
        dialog.orientation_combo.setCurrentIndex(1)
        dialog.single_point_spin.setValue(0.3)
        self.assertEqual(dialog.values(), AppSettings('pdf', Path(self.temp.name), 'multi', False, 0.3))
        dialog.single_point_spin.setValue(0.9)  # clamped to the allowed range
        self.assertEqual(dialog.values().single_point_pressure, 0.35)
        dialog.deleteLater()

    def test_applied_settings_persist_and_drive_single_point(self):
        window = self.make_window()
        window.results = [sample('A', 1)]
        window.visible_results = [True]
        window.active_index = 0
        window.refresh_all()
        window._apply_app_settings(AppSettings('pdf', Path(self.temp.name), 'single', False, 0.3))
        self.assertEqual(window._default_export_format(), 'pdf')
        self.assertEqual(window.export_directory, Path(self.temp.name))
        self.assertFalse(window._analysis_tables_transposed)
        expected = single_point_bet_analysis(window.results[0], 0.3).surface_area_m2_g
        cell = window.sample_table.item(0, 3)
        self.assertAlmostEqual(float(cell.text()), expected, places=2)
        reopened = self.make_window()
        self.assertEqual(reopened.single_point_default_pressure, 0.3)
        self.assertEqual(reopened.bet_display_mode, 'single')
        self.assertEqual(reopened._default_export_format(), 'pdf')

    def test_supported_instruments_dialog_lists_and_filters(self):
        from tristar_bet.supported_instruments import SUPPORTED_INSTRUMENTS
        from tristar_bet.ui.app_settings import SupportedInstrumentsDialog

        dialog = SupportedInstrumentsDialog()
        table = dialog.table
        self.assertEqual(table.rowCount(), len(SUPPORTED_INSTRUMENTS))
        models = [table.item(row, 1).text() for row in range(table.rowCount())]
        self.assertTrue(any('ASAP 2020 Plus' in model for model in models))
        dialog.filter_edit.setText('qps')
        shown = [row for row in range(table.rowCount()) if not table.isRowHidden(row)]
        self.assertEqual([table.item(row, 0).text() for row in shown], ['Quantachrome 康塔'])
        dialog.filter_edit.clear()
        self.assertFalse(any(table.isRowHidden(row) for row in range(table.rowCount())))
        dialog.deleteLater()

    def test_settings_dialog_has_supported_models_button(self):
        current = AppSettings('xlsx', Path(self.temp.name), 'default', True, 0.2)
        dialog = AppSettingsDialog(None, current, BET_DISPLAY_LABELS, '9.9.9', None)
        texts = [button.text() for button in dialog.findChildren(QtWidgets.QPushButton)]
        self.assertIn('查看支持的设备型号', texts)
        dialog.deleteLater()

    def test_import_failure_text_has_contact(self):
        self.assertIn(CONTACT_EMAIL, IMPORT_FAILURE_CONTACT)
        self.assertTrue(_with_import_contact('a.smp: 失败').endswith(IMPORT_FAILURE_CONTACT))

    def test_gear_button_is_next_to_export(self):
        window = self.make_window()
        self.assertEqual(window.settings_button.toolTip(), '设置')
        row = next(
            layout for layout in window.settings_button.parentWidget().findChildren(QtWidgets.QHBoxLayout)
            if layout.indexOf(window.settings_button) >= 0
        )
        widgets = [row.itemAt(i).widget() for i in range(row.count())]
        texts = [w.text() if isinstance(w, QtWidgets.QPushButton) else w for w in widgets]
        self.assertEqual(texts.index('导出文件') + 1, widgets.index(window.settings_button))


if __name__ == '__main__':
    unittest.main()
