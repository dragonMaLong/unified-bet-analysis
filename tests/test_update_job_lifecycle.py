"""Regression tests for the v1.3.3 single-process test-suite exit.

Root cause: the automatic update check ran on a ``QThread`` parented to ``MainWindow``.
When a window was destroyed (closed + deleted, or garbage-collected) while the request
to the update server was still in flight, Qt printed
"QThread: Destroyed while thread is still running" and called ``abort()``, which killed
the whole process with no Python traceback.

The destruction scenarios run in a child interpreter so that a regression is reported as
an ordinary test failure (with the child's output) instead of taking the suite down.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import QtCore, QtWidgets

import tristar_bet.ui.main_window as main_window
from tristar_bet.ui.main_window import BackgroundNetworkJob, MainWindow

ROOT = Path(__file__).resolve().parents[1]


def make_info(update_available: bool) -> main_window.UpdateInfo:
    return main_window.UpdateInfo(
        current_version='1.3.3', latest_version='1.3.4' if update_available else '1.3.3',
        update_available=update_available, release_url='', download_url='https://example.invalid/BET.exe',
        asset_name='BET.exe', release_name='', release_notes='', published_at='',
    )

CHILD_PRELUDE = textwrap.dedent(
    '''
    import gc, os, sys, tempfile, threading, time
    from pathlib import Path
    from unittest.mock import patch
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path.insert(0, sys.argv[1])
    from PyQt5 import QtCore, QtWidgets
    import tristar_bet.ui.main_window as mw

    release = threading.Event()
    started = threading.Event()

    def blocked_network(*_args, **_kwargs):
        started.set()
        release.wait(10)
        raise OSError('simulated network timeout')

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    temp = tempfile.TemporaryDirectory()
    settings = QtCore.QSettings(str(Path(temp.name) / 'settings.ini'), QtCore.QSettings.IniFormat)

    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)
    '''
)


def run_child(body: str) -> subprocess.CompletedProcess:
    code = CHILD_PRELUDE + textwrap.dedent(body)
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONFAULTHANDLER='1')
    return subprocess.run(
        [sys.executable, '-c', code, str(ROOT)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


class WindowDestroyedDuringUpdateCheckTests(unittest.TestCase):
    def assert_child_ok(self, result):
        output = f'exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}'
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('CHILD OK', result.stdout, output)
        self.assertNotIn('Destroyed while thread is still running', result.stderr, output)

    def test_close_and_delete_window_while_auto_update_request_is_blocked(self):
        result = run_child(
            '''
            with patch.object(mw, 'check_for_update', blocked_network), \\
                 patch.object(mw, 'AUTO_UPDATE_CHECK_DELAY_MS', 0), \\
                 patch.object(mw.MainWindow, '_initialize_telemetry'), \\
                 patch.object(mw.QtCore, 'QSettings', return_value=settings):
                window = mw.MainWindow()
                pump(0.2)
                assert started.wait(5), 'update request never started'
                window.close()
                window.deleteLater()
                app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                del window
                gc.collect()
                release.set()
                pump(0.5)
                assert not mw.BackgroundNetworkJob.active_jobs(), mw.BackgroundNetworkJob.active_jobs()
            print('CHILD OK', flush=True)
            '''
        )
        self.assert_child_ok(result)

    def test_garbage_collected_window_while_update_request_is_blocked(self):
        # Tests (and dialogs) that only drop their last reference never call deleteLater;
        # the C++ window is then destroyed by the garbage collector at an arbitrary point.
        result = run_child(
            '''
            with patch.object(mw, 'check_for_update', blocked_network), \\
                 patch.object(mw, 'AUTO_UPDATE_CHECK_DELAY_MS', 0), \\
                 patch.object(mw.MainWindow, '_initialize_telemetry'), \\
                 patch.object(mw.QtCore, 'QSettings', return_value=settings):
                window = mw.MainWindow()
                pump(0.2)
                assert started.wait(5), 'update request never started'
                window.close()
                del window
                gc.collect()
                pump(0.1)
                release.set()
                pump(0.5)
                assert not mw.BackgroundNetworkJob.active_jobs()
            print('CHILD OK', flush=True)
            '''
        )
        self.assert_child_ok(result)

    def test_application_exit_while_update_request_is_blocked(self):
        # Real app: the user closes the program within seconds of starting it while the
        # update server is slow.  The interpreter must exit normally, not abort.
        result = run_child(
            '''
            with patch.object(mw, 'check_for_update', blocked_network), \\
                 patch.object(mw, 'AUTO_UPDATE_CHECK_DELAY_MS', 0), \\
                 patch.object(mw.MainWindow, '_initialize_telemetry'), \\
                 patch.object(mw.QtCore, 'QSettings', return_value=settings):
                window = mw.MainWindow()
                window.show()
                pump(0.2)
                assert started.wait(5), 'update request never started'
                window.close()
            print('CHILD OK', flush=True)
            '''
        )
        self.assert_child_ok(result)


class UpdateJobDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QtCore.QSettings(str(Path(self.temp.name) / 'settings.ini'), QtCore.QSettings.IniFormat)
        with patch.object(MainWindow, '_auto_check_for_updates'), patch.object(MainWindow, '_initialize_telemetry'), \
                patch('tristar_bet.ui.main_window.QtCore.QSettings', return_value=self.settings):
            self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.temp.cleanup()

    def pump_until(self, predicate, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def test_update_check_result_reaches_window_on_gui_thread(self):
        seen = []
        gui_thread = threading.get_ident()
        info = make_info(update_available=False)

        def finished(received, manual):
            seen.append((received, manual, threading.get_ident()))

        with patch.object(main_window, 'check_for_update', return_value=info), \
                patch.object(self.window, '_on_update_check_finished', side_effect=finished):
            self.window.check_for_updates(manual=False)
            self.assertTrue(self.pump_until(lambda: self.window._update_job is None))
        self.assertEqual(seen, [(info, False, gui_thread)])
        self.assertFalse(BackgroundNetworkJob.active_jobs())

    def test_update_check_failure_resets_state(self):
        with patch.object(main_window, 'check_for_update', side_effect=OSError('offline')):
            self.window.check_for_updates(manual=False)
            self.assertTrue(self.window._checking_for_updates)
            self.assertTrue(self.pump_until(lambda: self.window._update_job is None))
        self.assertFalse(self.window._checking_for_updates)
        self.assertIn('offline', self.window.statusBar().currentMessage())
        self.assertFalse(BackgroundNetworkJob.active_jobs())

    def test_download_progress_and_result_are_delivered(self):
        info = make_info(update_available=True)
        delivered = []

        def fake_download(_info, progress_callback):
            progress_callback(5, 10)
            progress_callback(10, 10)
            return Path(self.temp.name) / 'BET.exe'

        with patch.object(main_window, 'download_update', side_effect=fake_download), \
                patch.object(self.window, '_on_update_download_finished',
                             side_effect=lambda i, p: delivered.append((i, p))):
            self.window._download_and_install_update(info)
            self.assertTrue(self.pump_until(lambda: self.window._update_download_job is None))
        self.assertEqual(delivered, [(info, str(Path(self.temp.name) / 'BET.exe'))])
        self.assertEqual(self.window._update_progress_dialog.value(), 100)
        self.window._update_progress_dialog.close()
        self.assertFalse(BackgroundNetworkJob.active_jobs())


if __name__ == '__main__':
    unittest.main()
