import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from tristar_bet.ui.main_window import MainWindow


class DftRegularizationRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.window._save_dft_settings_for_active = Mock()
        self.window._apply_dft_regularization_to_all = Mock()
        self.window._refresh_dft_regularization_dependents = Mock()

    def tearDown(self):
        self.window._dft_regularization_refresh_timer.stop()
        self.window.close()

    def test_drag_coalesces_to_latest_value_for_single_and_all_samples(self):
        w = self.window
        for apply_all in (False, True):
            with self.subTest(apply_all=apply_all):
                w.dft_regularization_apply_all = apply_all
                w._save_dft_settings_for_active.reset_mock()
                w._apply_dft_regularization_to_all.reset_mock()
                w._refresh_dft_regularization_dependents.reset_mock()
                w.dft_regularization_slider._dragging = True
                for value in (0.011, 0.022, 0.033):
                    w.dft_regularization_slider.setValue(value, emit=True)
                w._refresh_dft_regularization_dependents.assert_not_called()
                self.assertTrue(w._dft_regularization_refresh_timer.isActive())
                w._dft_regularization_refresh_timer.stop()
                w._preview_deferred_dft_regularization_refresh()
                self.assertEqual(w.dft_regularization, 0.033)
                w._refresh_dft_regularization_dependents.assert_called_once_with(preview=True)
                if apply_all:
                    w._apply_dft_regularization_to_all.assert_called_once_with(0.033)
                    w._save_dft_settings_for_active.assert_not_called()
                else:
                    w._save_dft_settings_for_active.assert_called_once_with()
                    w._apply_dft_regularization_to_all.assert_not_called()
                # No new mouse movement before release: finish still updates diagnostics.
                w.dft_regularization_slider._dragging = False
                w.dft_regularization_slider.valueChangeFinished.emit(0.033)
                self.assertEqual(w._refresh_dft_regularization_dependents.call_count, 2)
                w._refresh_dft_regularization_dependents.assert_called_with(preview=False)
                w._preview_deferred_dft_regularization_refresh()
                w._finish_deferred_dft_regularization_refresh()
                self.assertEqual(w._refresh_dft_regularization_dependents.call_count, 2)

    def test_release_before_preview_commits_final_value(self):
        w = self.window
        w.dft_regularization_apply_all = False
        w.dft_regularization_slider._dragging = True
        w.dft_regularization_slider.setValue(0.027, emit=True)
        w.dft_regularization_slider._dragging = False
        w.dft_regularization_slider.valueChangeFinished.emit(0.027)
        w._save_dft_settings_for_active.assert_called_once_with()
        w._refresh_dft_regularization_dependents.assert_called_once_with(preview=False)
        self.assertEqual(w.dft_regularization, 0.027)
        self.assertFalse(w._dft_regularization_refresh_timer.isActive())

    def test_changed_release_position_refreshes_only_once(self):
        w = self.window
        w.dft_regularization_apply_all = False
        w.dft_regularization_slider._dragging = True
        w.dft_regularization_slider.setValue(0.02, emit=True)
        w.dft_regularization_slider._dragging = False
        w.dft_regularization_slider.setValue(0.03, emit=True)
        w.dft_regularization_slider.valueChangeFinished.emit(0.03)
        w._save_dft_settings_for_active.assert_called_once_with()
        w._refresh_dft_regularization_dependents.assert_called_once_with()
        self.assertEqual(w.dft_regularization, 0.03)
        self.assertFalse(w._dft_regularization_refresh_timer.isActive())

    def test_non_drag_change_updates_immediately(self):
        w = self.window
        w.dft_regularization_apply_all = False
        w.dft_regularization_slider.setValue(0.047, emit=True)
        w.dft_regularization_slider.valueChangeFinished.emit(0.047)
        w._save_dft_settings_for_active.assert_called_once_with()
        w._refresh_dft_regularization_dependents.assert_called_once_with()

    def test_scope_change_commits_pending_value_to_original_scope(self):
        w = self.window
        w.dft_regularization_apply_all = False
        w.dft_regularization_slider._dragging = True
        w.dft_regularization_slider.setValue(0.061, emit=True)
        w._on_dft_regularization_apply_all_toggled(True)
        w._save_dft_settings_for_active.assert_called_once_with()
        w._apply_dft_regularization_to_all.assert_not_called()
        self.assertTrue(w.dft_regularization_apply_all)


if __name__ == "__main__":
    unittest.main()
