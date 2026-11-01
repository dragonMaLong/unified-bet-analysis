import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets
from tristar_bet.ui import plots


class PlotLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_queued_legend_layout_ignores_deleted_widget_or_item(self):
        for target in ('widget', 'item'):
            with self.subTest(target=target):
                plot = pg.PlotWidget()
                with patch.object(plots, '_apply_plot_legend_visibility'), \
                     patch.object(plots, '_apply_default_legend_position') as position, \
                     patch.object(plots, '_position_legend_toggle_button') as button:
                    plots._sync_plot_legend_visibility(plot)
                    (plot if target == 'widget' else plot.getPlotItem()).deleteLater()
                    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                    self.app.processEvents()
                    position.assert_not_called()
                    button.assert_not_called()
                if target == 'item':
                    plot.deleteLater()
                    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_live_plot_still_updates_legend(self):
        plot = pg.PlotWidget()
        try:
            with patch.object(plots, '_apply_default_legend_position') as position, \
                 patch.object(plots, '_position_legend_toggle_button') as button:
                plots._finalize_legend_layout(plot)
                position.assert_called_once_with(plot)
                button.assert_called_once_with(plot)
        finally:
            plot.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
