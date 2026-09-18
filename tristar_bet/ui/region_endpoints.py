"""Editable selection boundaries, matching the mercury application's controls."""

import math

from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg


def format_endpoint(value):
    if not math.isfinite(value):
        return ""
    magnitude = abs(value)
    if magnitude >= 100:
        return f"{value:,.0f}"
    if magnitude >= 10:
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    if magnitude >= 1:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if magnitude >= .01:
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return f"{value:.6f}".rstrip("0").rstrip(".") if value else "0"


class RegionEndpointLabel(pg.TextItem):
    def __init__(self, index, edit_callback, line_color, text_color):
        super().__init__(text="", color=text_color,
                         anchor=(1 if index == 0 else 0, 1),
                         fill=pg.mkBrush(255, 255, 255, 245), border=pg.mkPen(line_color))
        self.index = index
        self.edit_callback = edit_callback
        self.setZValue(20_000)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def mouseClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            event.accept()
            self.edit_callback(self.index)


class RegionEndpointLineEdit(QtWidgets.QLineEdit):
    def __init__(self, parent, line_color, text_color, cancel):
        super().__init__(parent)
        self.cancel_requested = cancel
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.setFrame(False)
        self.setFixedSize(48, 22)
        self.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {line_color};
                border-radius: 0px;
                background: rgba(255, 255, 255, 245);
                color: {text_color};
                padding: 0px 2px;
                selection-background-color: #0078d7;
                selection-color: #ffffff;
            }}
        """)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.cancel_requested()
            event.accept()
        else:
            super().keyPressEvent(event)


class RegionEndpointControls(QtCore.QObject):
    """One controller per plot; safely reattach when a calculation redraws it."""
    def __init__(self, plot, *, green=False, status_callback=None, changed_callback=None):
        super().__init__(plot)
        self.plot = plot
        self.region = None
        self.labels = []
        self.is_log = green
        self.line_color = "#16a34a" if green else "#2563eb"
        self.text_color = "#064e3b" if green else "#1e3a8a"
        self.status_callback = status_callback
        self.changed_callback = changed_callback
        self.editing_index = None
        self.dirty = False
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(1800)
        self.timer.timeout.connect(self.hide_labels)
        self.editor = RegionEndpointLineEdit(plot, self.line_color, self.text_color, self.cancel)
        self.editor.hide()
        self.editor.textEdited.connect(self._mark_dirty)
        self.editor.editingFinished.connect(self.commit)
        plot.getViewBox().sigRangeChanged.connect(self.update_positions)
        plot.getViewBox().sigResized.connect(self.update_positions)
        plot._click_projection_ignore_callback = self.ignore_coordinate_click

    def attach(self, region):
        self.detach()
        self.region = region
        for index in range(2):
            label = RegionEndpointLabel(index, self.begin_edit, self.line_color, self.text_color)
            self.labels.append(label)
            self.plot.addItem(label, ignoreBounds=True)
        region.sigRegionChanged.connect(self._region_changed)
        region.sigRegionChangeFinished.connect(self.show_labels)
        self._region_changed()

    def detach(self):
        self.timer.stop()
        self._close_editor()
        if self.region is not None:
            for signal, slot in ((self.region.sigRegionChanged, self._region_changed),
                                 (self.region.sigRegionChangeFinished, self.show_labels)):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.region = None
        for label in self.labels:
            self.plot.removeItem(label)
        self.labels = []

    def values(self):
        values = sorted(self.region.getRegion())
        return [10 ** value for value in values] if self.is_log else values

    def _mark_dirty(self, _text):
        self.dirty = True

    def _region_changed(self, *_args):
        if self.editing_index is not None and not self.dirty:
            self._close_editor()
        self.show_labels()
        if self.changed_callback is not None:
            self.changed_callback()

    def show_labels(self, *_args):
        if self.region is None:
            return
        self.update_positions()
        for index, label in enumerate(self.labels):
            label.setVisible(index != self.editing_index)
        if self.editing_index is None:
            self.timer.start()

    def hide_labels(self):
        if self.editing_index is None:
            for label in self.labels:
                label.hide()

    def update_positions(self, *_args):
        if self.region is None:
            return
        view = self.plot.getViewBox()
        bottom = min(view.viewRange()[1])
        rect = view.sceneBoundingRect()
        for index, (label, x, value) in enumerate(zip(self.labels, sorted(self.region.getRegion()), self.values())):
            label.setText(format_endpoint(value))
            scene = view.mapViewToScene(QtCore.QPointF(x, bottom))
            width = max(28, label.boundingRect().width())
            anchor, offset = (1, -5) if index == 0 else (0, 5)
            if index == 0 and scene.x() - width - 6 < rect.left():
                anchor, offset = 0, 5
            elif index == 1 and scene.x() + width + 6 > rect.right():
                anchor, offset = 1, -5
            label.setAnchor((anchor, 1))
            scene.setX(scene.x() + offset)
            label.setPos(view.mapSceneToView(scene).x(), bottom)
            if index == self.editing_index:
                self._position_editor(label)

    def _position_editor(self, label):
        rect = label.sceneBoundingRect()
        start = self.plot.mapFromScene(rect.topLeft())
        end = self.plot.mapFromScene(rect.bottomRight())
        width, height = max(28, abs(end.x() - start.x())), max(20, abs(end.y() - start.y()))
        self.editor.setFixedSize(width, height)
        left = max(4, min(start.x(), self.plot.width() - width - 4))
        top = max(4, min(start.y(), self.plot.height() - height - 4))
        self.editor.move(left, top)

    def begin_edit(self, index):
        if self.region is None:
            return
        self.timer.stop()
        self.editing_index = index
        self.dirty = False
        self.editor.setText(format_endpoint(self.values()[index]))
        self.editor.setFont(self.labels[index].textItem.font())
        self.editor.show()
        self.editor.raise_()
        self.show_labels()
        self.editor.setFocus(QtCore.Qt.MouseFocusReason)
        self.editor.selectAll()

    def _close_editor(self):
        # Clear state first: hide/clearFocus can synchronously emit editingFinished.
        self.editing_index = None
        self.dirty = False
        self.editor.hide()
        self.editor.clearFocus()

    def cancel(self):
        self._close_editor()
        self.show_labels()

    def commit(self):
        if self.editing_index is None or self.region is None:
            return
        if not self.dirty or not self.editor.text().strip():
            self.cancel()
            return
        try:
            value = float(self.editor.text().strip().replace(",", ""))
            if not math.isfinite(value):
                raise ValueError
        except ValueError:
            if self.status_callback is not None:
                self.status_callback("边界必须是有效数字。", 4000)
            self.editor.selectAll()
            QtCore.QTimer.singleShot(0, self._refocus_editor)
            return
        left, right = self.values()
        # Read live line bounds: setBounds can change without recreating the region.
        lower, upper = self.region.lines[0].bounds()
        if self.is_log:
            lower, upper = 10 ** lower, 10 ** upper
        epsilon = max(upper - lower, abs(right), 1) * 1e-9
        if self.editing_index == 0:
            left = max(lower, min(value, right - epsilon))
        else:
            right = min(upper, max(value, left + epsilon))
        self._close_editor()
        self.region.setRegion([math.log10(left), math.log10(right)] if self.is_log else [left, right])
        self.show_labels()

    def _refocus_editor(self):
        if self.editing_index is not None:
            self.editor.setFocus(QtCore.Qt.OtherFocusReason)

    def ignore_coordinate_click(self, scene_pos):
        return self.editing_index is not None or any(
            label.isVisible() and label.sceneBoundingRect().adjusted(-4, -4, 4, 4).contains(scene_pos)
            for label in self.labels
        )
