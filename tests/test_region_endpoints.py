import math
import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
import pyqtgraph as pg
from PyQt5 import QtCore, QtTest, QtWidgets

from tristar_bet.ui.region_endpoints import RegionEndpointControls, format_endpoint


class RegionEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.plot = pg.PlotWidget()
        self.plot.resize(650, 400)
        self.plot.setRange(xRange=(0, 3), yRange=(0, 10), padding=0)
        self.plot.show()
        self.app.processEvents()
        self.changed = Mock()
        self.status = Mock()
        self.controls = RegionEndpointControls(self.plot, green=True,
            changed_callback=self.changed, status_callback=self.status)
        self.region = pg.LinearRegionItem([math.log10(2), math.log10(20)], bounds=[0, 3], swapMode="block")
        self.plot.addItem(self.region)
        self.controls.attach(self.region)

    def tearDown(self):
        self.controls.detach()
        self.plot.close()
        self.plot.deleteLater()
        self.app.processEvents()

    def edit(self, index, text):
        self.controls.begin_edit(index)
        QtTest.QTest.keyClicks(self.controls.editor, text)
        QtTest.QTest.keyClick(self.controls.editor, QtCore.Qt.Key_Return)
        self.app.processEvents()

    def test_log_values_and_enter_edit(self):
        self.edit(0, "3.45678")
        self.assertAlmostEqual(self.controls.values()[0], 3.45678)
        self.assertIsNone(self.controls.editing_index)
        self.assertEqual(self.controls.labels[0].toPlainText(), "3.46")
        self.assertGreater(self.changed.call_count, 1)

    def test_linear_pressure_and_small_numbers(self):
        self.controls.is_log = False
        self.region.setBounds([0, 1])
        self.region.setRegion([.000012, .2])
        self.controls.show_labels()
        self.assertEqual(self.controls.labels[0].toPlainText(), "0.000012")
        self.edit(1, ".123456789")
        self.assertAlmostEqual(self.region.getRegion()[1], .123456789)

    def test_escape_and_unmodified_blur_do_not_round_value(self):
        self.region.setRegion([math.log10(2.123456789), math.log10(20)])
        original = self.region.getRegion()
        self.controls.begin_edit(0)
        self.controls.commit()
        self.assertEqual(self.region.getRegion(), original)
        self.controls.begin_edit(0)
        QtTest.QTest.keyClicks(self.controls.editor, "9")
        QtTest.QTest.keyClick(self.controls.editor, QtCore.Qt.Key_Escape)
        self.assertEqual(self.region.getRegion(), original)

    def test_blur_commits(self):
        self.controls.begin_edit(1)
        QtTest.QTest.keyClicks(self.controls.editor, "27.12")
        self.controls.editor.clearFocus()
        self.app.processEvents()
        self.assertAlmostEqual(self.controls.values()[1], 27.12)

    def test_invalid_input_keeps_editing_and_region(self):
        original = self.region.getRegion()
        for text in ("nan", "inf", "abc"):
            self.edit(0, text)
            self.assertEqual(self.region.getRegion(), original)
            self.assertEqual(self.controls.editing_index, 0)
            self.assertTrue(self.controls.editor.hasFocus())
        self.assertTrue(self.status.called)

    def test_live_bounds_and_crossing_are_clamped(self):
        self.region.setBounds([math.log10(1.5), math.log10(30)])
        self.edit(0, "-100")
        self.assertAlmostEqual(self.controls.values()[0], 1.5)
        self.edit(1, "1000")
        self.assertAlmostEqual(self.controls.values()[1], 30)
        self.edit(0, "1000")
        self.assertLess(self.controls.values()[0], self.controls.values()[1])

    def test_hide_show_and_edit_timer(self):
        self.controls.hide_labels()
        self.assertFalse(any(label.isVisible() for label in self.controls.labels))
        self.region.setRegion([.4, 1.4])
        self.assertTrue(all(label.isVisible() for label in self.controls.labels))
        self.controls.begin_edit(0)
        self.assertFalse(self.controls.timer.isActive())
        self.controls.hide_labels()
        self.assertTrue(self.controls.editor.isVisible())
        self.controls.cancel()
        self.controls.timer.setInterval(10)
        self.controls.show_labels()
        QtTest.QTest.qWait(30)
        self.assertFalse(any(label.isVisible() for label in self.controls.labels))

    def test_click_label_and_ignore_plot_projection(self):
        from tristar_bet.ui.plots import ClickProjectionCursor
        cursor = ClickProjectionCursor(self.plot)
        label = self.controls.labels[0]
        scene_pos = label.sceneBoundingRect().center()
        self.assertTrue(self.controls.ignore_coordinate_click(scene_pos))
        pos = self.plot.mapFromScene(scene_pos)
        QtTest.QTest.mouseClick(self.plot.viewport(), QtCore.Qt.LeftButton, pos=pos)
        self.app.processEvents()
        self.assertEqual(self.controls.editing_index, 0)
        self.assertTrue(self.controls.editor.isVisible())
        self.assertIsNone(cursor.point)

    def test_edge_labels_flip_inward_and_follow_resize(self):
        self.region.setRegion([0, 3])
        self.controls.update_positions()
        self.assertEqual(self.controls.labels[0].anchor.x(), 0)
        self.assertEqual(self.controls.labels[1].anchor.x(), 1)
        self.controls.begin_edit(1)
        self.plot.resize(400, 300)
        self.app.processEvents()
        self.assertLessEqual(self.controls.editor.geometry().right(), self.plot.width())
        self.assertGreaterEqual(self.controls.editor.x(), 0)

    def test_detach_reattach_after_plot_clear(self):
        old_labels = list(self.controls.labels)
        self.controls.begin_edit(0)
        self.controls.detach()
        self.plot.clear()
        self.assertFalse(self.controls.timer.isActive())
        self.assertFalse(self.controls.editor.isVisible())
        self.assertIsNone(self.controls.editing_index)
        self.plot.addItem(self.region)
        self.controls.attach(self.region)
        self.assertFalse(any(label in self.plot.getViewBox().addedItems for label in old_labels))
        self.assertEqual(len(self.controls.labels), 2)

    def test_mercury_numeric_format(self):
        for value, expected in [(1000, "1,000"), (10.25, "10.2"), (2.345, "2.35"), (.23456, "0.235"), (0, "0")]:
            self.assertEqual(format_endpoint(value), expected)
