"""Open time and active time of one program run (for the session_end statistic).

Open time: seconds from start to the main window closing.
Active time: seconds during which the application was in the foreground and
the user pressed a key, clicked, scrolled or moved the mouse within the
preceding IDLE_LIMIT_S seconds (reading results right after an action
counts; a window left open in the background or untouched does not).
Only these two integers leave the program; no input content is recorded.
"""
from __future__ import annotations

import time

from pyqtgraph.Qt import QtCore, QtWidgets

IDLE_LIMIT_S = 120.0
TICK_MS = 5000
MAX_SESSION_SECONDS = 30 * 86400

_INPUT_EVENTS = frozenset({
    QtCore.QEvent.KeyPress, QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick,
    QtCore.QEvent.MouseMove, QtCore.QEvent.Wheel, QtCore.QEvent.TouchBegin,
})


class UsageTimer(QtCore.QObject):
    def __init__(self, parent: QtCore.QObject | None = None, clock=time.monotonic) -> None:
        super().__init__(parent)
        self._clock = clock
        self._started = clock()
        self._last_tick = self._started
        self._last_input = self._started  # launching the program is an action
        self._active = 0.0
        self._finished: tuple[int, int] | None = None
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)
        self._timer.start()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        if event.type() in _INPUT_EVENTS:
            self._last_input = self._clock()
        return False

    @staticmethod
    def _foreground() -> bool:
        app = QtWidgets.QApplication.instance()
        return app is not None and app.applicationState() == QtCore.Qt.ApplicationActive

    def tick(self, foreground: bool | None = None) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_tick)
        self._last_tick = now
        if foreground is None:
            foreground = self._foreground()
        # A long gap means the computer slept or the loop was blocked: count at
        # most two ticks so a suspended machine does not add active time.
        elapsed = min(elapsed, 2 * TICK_MS / 1000.0)
        if foreground and now - self._last_input <= IDLE_LIMIT_S:
            self._active += elapsed

    def finish(self) -> tuple[int, int]:
        """(open_s, active_s) — computed once; later calls return the same values."""
        if self._finished is None:
            self.tick()
            self._timer.stop()
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            open_s = int(min(max(0.0, self._clock() - self._started), MAX_SESSION_SECONDS))
            self._finished = (open_s, int(min(self._active, open_s)))
        return self._finished
