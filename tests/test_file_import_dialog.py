import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtTest, QtWidgets

from tristar_bet.ui.main_window import FileImportDialog, SUPPORTED_DATA_SUFFIXES
from tristar_bet.ui.native_import_picker import ImportSelection


class FileImportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.first = self.root / "first"
        self.second = self.root / "second"
        self.first.mkdir()
        self.second.mkdir()
        self.old = self.first / "existing.SMP"
        self.other = self.first / "other.RAW"
        self.new = self.second / "new.XLSX"
        self.extra = self.second / "extra.DAT"
        for path in (self.old, self.other, self.new, self.extra):
            path.write_text("picker fixture", encoding="utf-8")
        self.dialog = FileImportDialog(initial_dir=self.first, existing_paths=[str(self.old)])
        self.accepted = QtTest.QSignalSpy(self.dialog.accepted)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.temp.cleanup()

    def choose(self, selection):
        with patch("tristar_bet.ui.main_window.sys.platform", "win32"), patch(
            "tristar_bet.ui.main_window.choose_import_files", return_value=selection
        ) as native, patch(
            "tristar_bet.ui.main_window.ImportFilePicker", side_effect=AssertionError("Unexpected Qt picker")
        ):
            self.dialog._browse_directory()
        return native

    @staticmethod
    def table_paths(table):
        return [table.item(row, 0).data(QtCore.Qt.UserRole) for row in range(table.rowCount())]

    def snapshot(self):
        return (
            self.dialog.current_directory,
            self.dialog.folder_edit.text(),
            self.dialog.search_edit.text(),
            self.table_paths(self.dialog.available_table),
            self.table_paths(self.dialog.selected_table),
            self.dialog.available_sort(),
            self.dialog.count_label.text(),
            self.dialog.result(),
        )

    def test_two_toolbar_buttons_open_native_picker(self):
        buttons = self.dialog.findChildren(QtWidgets.QToolButton)
        self.assertEqual([button.text() for button in buttons], ["选择文件夹", "刷新"])
        native = self.choose(None)
        owner, directory, suffixes = native.call_args.args
        self.assertNotEqual(owner, 0)
        self.assertEqual(directory, self.first)
        self.assertEqual(suffixes, SUPPORTED_DATA_SUFFIXES)

    def test_folder_only_updates_available_files_and_preserves_queue(self):
        self.dialog.search_edit.setText("other")
        self.choose(ImportSelection(self.second))
        self.assertEqual(self.dialog.current_directory, self.second)
        self.assertEqual(self.dialog.folder_edit.text(), str(self.second))
        self.assertEqual(self.dialog.search_edit.text(), "")
        self.assertEqual(set(self.table_paths(self.dialog.available_table)), {str(self.new), str(self.extra)})
        self.assertEqual(self.table_paths(self.dialog.selected_table), [str(self.old)])
        self.assertEqual(len(self.accepted), 0)

        self.dialog._remove_from_selected([self.old])
        self.choose(ImportSelection(self.first))
        self.assertEqual(self.dialog.selected_paths(), [])
        self.assertFalse(self.dialog.import_button.isEnabled())

    def test_add_appends_deduplicates_and_waits_for_final_import(self):
        self.choose(ImportSelection(self.second, (self.new, self.extra)))
        self.choose(ImportSelection(self.first, (self.old, self.other, self.new)))
        expected = [str(path) for path in (self.old, self.new, self.extra, self.other)]
        self.assertEqual(self.dialog.selected_paths(), expected)
        self.assertEqual(self.table_paths(self.dialog.selected_table), expected)
        self.assertEqual(self.table_paths(self.dialog.available_table), [])
        self.assertEqual(len(self.accepted), 0)
        self.dialog.import_button.click()
        self.assertEqual(len(self.accepted), 1)

    def test_cancel_preserves_both_lists_directory_filter_and_sort(self):
        self.dialog.search_edit.setText("other")
        self.dialog.available_table.sortItems(1, QtCore.Qt.DescendingOrder)
        before = self.snapshot()
        self.choose(None)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.accepted), 0)

    def test_native_failure_preserves_state_without_switching_style(self):
        before = self.snapshot()
        with patch("tristar_bet.ui.main_window.sys.platform", "win32"), patch(
            "tristar_bet.ui.main_window.choose_import_files", side_effect=OSError("native picker unavailable")
        ), patch("tristar_bet.ui.main_window.QtWidgets.QMessageBox.warning") as warning, patch(
            "tristar_bet.ui.main_window.ImportFilePicker", side_effect=AssertionError("Unexpected Qt fallback")
        ):
            self.dialog._browse_directory()
        warning.assert_called_once()
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
