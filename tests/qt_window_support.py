"""Shared helper for tests that need a real ``MainWindow``.

The window is built with a private ``QSettings`` file and without the automatic update
check / telemetry start-up timers, so tests neither touch the user's real settings nor
reach the update server.  It is deleted deterministically when the test finishes instead
of being left to the garbage collector at an arbitrary later point in the run.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt5 import QtCore, QtWidgets


def isolated_main_window(test: unittest.TestCase, window_class=None):
    if window_class is None:
        from tristar_bet.ui.main_window import MainWindow as window_class
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    temp = tempfile.TemporaryDirectory()
    settings = QtCore.QSettings(str(Path(temp.name) / 'settings.ini'), QtCore.QSettings.IniFormat)
    with patch.object(window_class, '_auto_check_for_updates'), \
            patch.object(window_class, '_initialize_telemetry'), \
            patch('tristar_bet.ui.main_window.QtCore.QSettings', return_value=settings):
        window = window_class()

    def dispose():
        window.close()
        window.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        temp.cleanup()

    test.addCleanup(dispose)
    return window
