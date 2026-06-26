from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import numpy as np
import pyqtgraph as pg

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from tristar_bet.analysis import (
    FitResult,
    adsorption_points,
    automatic_bet_range,
    bet_analysis,
    bjh_pore_distribution,
    desorption_points,
    dft_pore_distribution,
    dh_pore_distribution,
    horvath_kawazoe_pore_distribution,
    langmuir_analysis,
    t_plot_analysis,
    t_plot_analysis_by_thickness,
)


pg.setConfigOptions(antialias=True, useOpenGL=False)

DEFAULT_COLORS = (
    "#2563eb",
    "#16a34a",
    "#9333ea",
    "#f97316",
    "#0891b2",
    "#4f46e5",
    "#65a30d",
    "#b45309",
    "#0f766e",
    "#db2777",
    "#7c3aed",
    "#ea580c",
    "#0284c7",
    "#84cc16",
    "#c026d3",
    "#ca8a04",
    "#14b8a6",
    "#0369a1",
    "#4d7c0f",
    "#a855f7",
    "#a16207",
    "#0d9488",
    "#6366f1",
)
ACTIVE_LINE_WIDTH = 4
ACTIVE_SYMBOL_SIZE = 11
ACTIVE_SYMBOL_PEN_WIDTH = 3
DEFAULT_LINE_WIDTH = 2
DEFAULT_SYMBOL_SIZE = 6
DEFAULT_SYMBOL_PEN_WIDTH = 1
SELECTED_SYMBOL_SIZE = 11
SELECTED_SYMBOL_PEN_WIDTH = 3
BJH_DIFFERENTIAL_LOG = "log"
BJH_DIFFERENTIAL_LINEAR = "linear"
BJH_CUMULATIVE_VOLUME = "cum_volume"
BJH_CUMULATIVE_AREA = "cum_area"
BJH_DIFFERENTIAL_AREA_LOG = "da_log"
BJH_DISPLAY_METRIC_ORDER = (
    BJH_DIFFERENTIAL_LOG,
    BJH_DIFFERENTIAL_LINEAR,
    BJH_CUMULATIVE_VOLUME,
    BJH_CUMULATIVE_AREA,
    BJH_DIFFERENTIAL_AREA_LOG,
)
BJH_DISPLAY_METRIC_LABELS = {
    BJH_DIFFERENTIAL_LOG: "dV/dlogD",
    BJH_DIFFERENTIAL_LINEAR: "dV/dD",
    BJH_CUMULATIVE_VOLUME: "Cumulative Pore Volume",
    BJH_CUMULATIVE_AREA: "Cumulative Pore Area",
    BJH_DIFFERENTIAL_AREA_LOG: "dA/dlogD",
}
BJH_DISPLAY_METRIC_AXIS_LABELS = {
    BJH_DIFFERENTIAL_LOG: "dV/dlogD (cm3/g)",
    BJH_DIFFERENTIAL_LINEAR: "dV/dD (cm3/g/nm)",
    BJH_CUMULATIVE_VOLUME: "Cumulative Pore Volume (cm3/g)",
    BJH_CUMULATIVE_AREA: "Cumulative Pore Area (m2/g)",
    BJH_DIFFERENTIAL_AREA_LOG: "dA/dlogD (m2/g)",
}
BJH_DISPLAY_METRIC_SYMBOLS = {
    BJH_DIFFERENTIAL_LOG: "o",
    BJH_DIFFERENTIAL_LINEAR: "s",
    BJH_CUMULATIVE_VOLUME: "t",
    BJH_CUMULATIVE_AREA: "d",
    BJH_DIFFERENTIAL_AREA_LOG: "+",
}
HK_CUMULATIVE_VOLUME = "cum_volume"
HK_DIFFERENTIAL_LINEAR = "linear"
HK_DIFFERENTIAL_LOG = "log"
HK_DISPLAY_METRIC_ORDER = (
    HK_DIFFERENTIAL_LINEAR,
    HK_DIFFERENTIAL_LOG,
    HK_CUMULATIVE_VOLUME,
)
HK_DISPLAY_METRIC_LABELS = {
    HK_DIFFERENTIAL_LINEAR: "dV/dW",
    HK_DIFFERENTIAL_LOG: "dV/dlogW",
    HK_CUMULATIVE_VOLUME: "Cumulative Pore Volume",
}
HK_DISPLAY_METRIC_AXIS_LABELS = {
    HK_DIFFERENTIAL_LINEAR: "dV/dW (cm3/g/nm)",
    HK_DIFFERENTIAL_LOG: "dV/dlogW (cm3/g)",
    HK_CUMULATIVE_VOLUME: "Cumulative Pore Volume (cm3/g)",
}
HK_DISPLAY_METRIC_SYMBOLS = {
    HK_DIFFERENTIAL_LINEAR: "o",
    HK_DIFFERENTIAL_LOG: "s",
    HK_CUMULATIVE_VOLUME: "t",
}


class _LegendToggleButton(QtWidgets.QToolButton):
    def __init__(self, plot: pg.PlotWidget) -> None:
        super().__init__(plot)
        self._plot = plot
        self.setCheckable(True)
        self.setChecked(True)
        self.setAutoRaise(True)
        self.setFixedSize(24, 22)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("隐藏图例")
        self._press_global_pos: QtCore.QPoint | None = None
        self._press_button_pos: QtCore.QPoint | None = None
        self._dragging_button = False
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool) -> None:
        _set_plot_legend_visible(self._plot, checked)

    def mousePressEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_global_pos = self._event_global_pos(event)
        self._press_button_pos = QtCore.QPoint(self.pos())
        self._dragging_button = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & QtCore.Qt.LeftButton) or self._press_global_pos is None or self._press_button_pos is None:
            super().mouseMoveEvent(event)
            return
        delta = self._event_global_pos(event) - self._press_global_pos
        if not self._dragging_button and delta.manhattanLength() < QtWidgets.QApplication.startDragDistance():
            event.accept()
            return
        self._dragging_button = True
        self._move_to(self._press_button_pos + delta)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._dragging_button:
            self._dragging_button = False
            self._press_global_pos = None
            self._press_button_pos = None
            event.accept()
            return
        self._press_global_pos = None
        self._press_button_pos = None
        self.setChecked(not self.isChecked())
        event.accept()

    def _move_to(self, position: QtCore.QPoint) -> None:
        margin = 4
        x = max(margin, min(int(position.x()), max(margin, self._plot.width() - self.width() - margin)))
        y = max(margin, min(int(position.y()), max(margin, self._plot.height() - self.height() - margin)))
        self.move(x, y)
        self.raise_()
        legend = getattr(self._plot.plotItem, "legend", None)
        if legend is not None and legend.isVisible():
            _move_legend_to_toggle_anchor(self._plot)
        else:
            setattr(self._plot, "_legend_hidden_toggle_anchor", QtCore.QPoint(self.pos()))

    @staticmethod
    def _event_global_pos(event) -> QtCore.QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        hovered = bool(self.underMouse())
        painter.setPen(QtGui.QPen(QtGui.QColor("#cbd5e1"), 1))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff" if not hovered else "#f8fafc")))
        painter.drawRoundedRect(rect, 5, 5)

        icon_rect = QtCore.QRectF(7, 7, 14, 10)
        pen = QtGui.QPen(QtGui.QColor("#334155"), 1.6)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        path = QtGui.QPainterPath()
        path.moveTo(icon_rect.left(), icon_rect.center().y())
        path.cubicTo(
            icon_rect.left() + 3,
            icon_rect.top(),
            icon_rect.right() - 3,
            icon_rect.top(),
            icon_rect.right(),
            icon_rect.center().y(),
        )
        path.cubicTo(
            icon_rect.right() - 3,
            icon_rect.bottom(),
            icon_rect.left() + 3,
            icon_rect.bottom(),
            icon_rect.left(),
            icon_rect.center().y(),
        )
        painter.drawPath(path)
        if self.isChecked():
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#334155")))
            painter.drawEllipse(QtCore.QPointF(icon_rect.center()), 2.3, 2.3)
        else:
            painter.drawLine(QtCore.QLineF(7, 18, 21, 6))


class _LegendToggleEventFilter(QtCore.QObject):
    def eventFilter(self, obj, event) -> bool:
        plot = self.parent()
        if event.type() in {
            QtCore.QEvent.Resize,
            QtCore.QEvent.Show,
            QtCore.QEvent.MouseMove,
            QtCore.QEvent.MouseButtonRelease,
        }:
            _position_legend_toggle_button(plot)
        return False


def _install_legend_toggle(plot: pg.PlotWidget) -> None:
    if getattr(plot, "_legend_toggle_button", None) is not None:
        return
    setattr(plot, "_legend_visible", True)
    original_clear = plot.clear

    def clear_with_legend_toggle(*args, **kwargs):
        result = original_clear(*args, **kwargs)
        _apply_plot_legend_visibility(plot)
        button = getattr(plot, "_legend_toggle_button", None)
        if button is not None:
            button.show()
            button.raise_()
        return result

    plot.clear = clear_with_legend_toggle
    button = _LegendToggleButton(plot)
    event_filter = _LegendToggleEventFilter(plot)
    plot.installEventFilter(event_filter)
    viewport = getattr(plot, "viewport", lambda: None)()
    if viewport is not None:
        viewport.installEventFilter(event_filter)
    setattr(plot, "_legend_toggle_button", button)
    setattr(plot, "_legend_toggle_event_filter", event_filter)
    _position_legend_toggle_button(plot)
    button.show()
    button.raise_()


def _apply_default_legend_position(plot: pg.PlotWidget) -> None:
    if getattr(plot, "_legend_user_offset", None) is not None:
        return
    if getattr(plot, "_legend_default_position", "left") != "right":
        return
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return
    rect = _legend_rect_in_plot(plot)
    if rect is None:
        return
    try:
        legend_pos = legend.pos()
        base_x = rect.left() - float(legend_pos.x())
        base_y = rect.top() - float(legend_pos.y())
    except Exception:
        return
    margin = 10.0
    desired_left = max(margin, float(plot.width()) - float(rect.width()) - margin)
    desired_top = margin
    offset_x = float(desired_left) - base_x
    offset_y = float(desired_top) - base_y
    _apply_legend_offset(plot, (offset_x, offset_y))


def _position_legend_toggle_button(plot) -> None:
    button = getattr(plot, "_legend_toggle_button", None)
    if button is None:
        return
    _refresh_legend_layout(plot)
    margin = 4
    position = _legend_toggle_position(plot, button)
    x = max(margin, min(int(position.x()), max(margin, plot.width() - button.width() - margin)))
    y = max(margin, min(int(position.y()), max(margin, plot.height() - button.height() - margin)))
    button.move(x, y)
    button.raise_()


def _legend_toggle_position(plot, button: QtWidgets.QToolButton) -> QtCore.QPoint:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is not None and legend.isVisible():
        try:
            rect = _legend_rect_in_plot(plot)
            if rect is None:
                raise RuntimeError("legend rect unavailable")
            position = QtCore.QPoint(
                int(rect.right() - button.width() - 4),
                int(rect.top() + 4),
            )
            return position
        except Exception:
            pass
    anchor = getattr(plot, "_legend_hidden_toggle_anchor", None)
    if isinstance(anchor, QtCore.QPoint):
        return QtCore.QPoint(anchor)
    return QtCore.QPoint(max(4, plot.width() - button.width() - 8), 8)


def _move_legend_to_toggle_anchor(plot) -> None:
    legend = getattr(plot.plotItem, "legend", None)
    button = getattr(plot, "_legend_toggle_button", None)
    if legend is None or button is None or not legend.isVisible():
        return
    rect = _legend_rect_in_plot(plot)
    if rect is None:
        return
    try:
        legend_pos = legend.pos()
        base_x = rect.left() - float(legend_pos.x())
        base_y = rect.top() - float(legend_pos.y())
        desired_rect_left = button.x() + button.width() + 4 - rect.width()
        desired_rect_top = button.y() - 4
        desired_offset = (
            float(desired_rect_left) - base_x,
            float(desired_rect_top) - base_y,
        )
        _apply_legend_offset(plot, desired_offset)
        setattr(plot, "_legend_user_offset", (float(desired_offset[0]), float(desired_offset[1])))
    except Exception:
        return
    _position_legend_toggle_button(plot)


def _apply_legend_offset(plot, offset: tuple[float, float]) -> None:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return
    legend.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(float(offset[0]), float(offset[1])))


def _legend_rect_in_plot(plot) -> QtCore.QRect | None:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return None
    try:
        _refresh_legend_layout(plot)
        scene_rect = legend.sceneBoundingRect()
        top_left = plot.mapFromScene(scene_rect.topLeft())
        bottom_right = plot.mapFromScene(scene_rect.bottomRight())
        return QtCore.QRect(top_left, bottom_right).normalized()
    except Exception:
        return None


def _refresh_legend_layout(plot) -> None:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return
    for method_name in ("updateSize", "adjustSize", "updateGeometry"):
        method = getattr(legend, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass


def _legend_is_inside_plot(plot) -> bool:
    rect = _legend_rect_in_plot(plot)
    if rect is None:
        return True
    safe_rect = plot.rect().adjusted(4, 4, -4, -4)
    intersection = safe_rect.intersected(rect)
    return intersection.width() >= 24 and intersection.height() >= 20


def _set_plot_legend_visible(plot: pg.PlotWidget, visible: bool) -> None:
    setattr(plot, "_legend_visible", bool(visible))
    _apply_plot_legend_visibility(plot)


def _apply_plot_legend_visibility(plot: pg.PlotWidget) -> None:
    visible = bool(getattr(plot, "_legend_visible", True))
    legend = getattr(plot.plotItem, "legend", None)
    button = getattr(plot, "_legend_toggle_button", None)
    if legend is not None and button is not None and not visible:
        setattr(plot, "_legend_hidden_toggle_anchor", QtCore.QPoint(button.pos()))
    if legend is not None:
        legend.setVisible(bool(visible))
        if visible:
            hidden_anchor = getattr(plot, "_legend_hidden_toggle_anchor", None)
            if isinstance(hidden_anchor, QtCore.QPoint):
                _move_legend_to_toggle_anchor(plot)
                try:
                    delattr(plot, "_legend_hidden_toggle_anchor")
                except AttributeError:
                    pass
            else:
                user_offset = getattr(plot, "_legend_user_offset", None)
                if user_offset is not None:
                    _apply_legend_offset(plot, user_offset)
    if button is not None:
        button.blockSignals(True)
        button.setChecked(bool(visible))
        button.setToolTip("隐藏图例" if visible else "显示图例")
        button.blockSignals(False)
        _position_legend_toggle_button(plot)
        button.update()


def _sync_plot_legend_visibility(plot: pg.PlotWidget) -> None:
    _apply_plot_legend_visibility(plot)
    QtCore.QTimer.singleShot(0, lambda plot=plot: _finalize_legend_layout(plot))


def _finalize_legend_layout(plot: pg.PlotWidget) -> None:
    _apply_default_legend_position(plot)
    _position_legend_toggle_button(plot)


def bjh_differential_axis_label(mode: str) -> str:
    return bjh_display_axis_label([mode])


def bjh_display_metric_label(metric: str) -> str:
    return BJH_DISPLAY_METRIC_LABELS.get(metric, BJH_DISPLAY_METRIC_LABELS[BJH_DIFFERENTIAL_LOG])


def normalize_bjh_display_metrics(metrics) -> list[str]:
    if metrics is None:
        items = []
    elif isinstance(metrics, str):
        text = metrics.replace(";", ",")
        items = [part.strip() for part in text.split(",")] if "," in text else [text.strip()]
    else:
        items = [str(item).strip() for item in metrics]
    normalized: list[str] = []
    for item in items:
        if item in BJH_DISPLAY_METRIC_ORDER and item not in normalized:
            normalized.append(item)
            break
    return normalized or [BJH_DIFFERENTIAL_LOG]


def bjh_display_axis_label(metrics) -> str:
    normalized = normalize_bjh_display_metrics(metrics)
    if len(normalized) == 1:
        return BJH_DISPLAY_METRIC_AXIS_LABELS.get(normalized[0], BJH_DISPLAY_METRIC_AXIS_LABELS[BJH_DIFFERENTIAL_LOG])
    return "BJH 显示值（见图例单位）"


def hk_display_metric_label(metric: str) -> str:
    return HK_DISPLAY_METRIC_LABELS.get(metric, HK_DISPLAY_METRIC_LABELS[HK_DIFFERENTIAL_LINEAR])


def normalize_hk_display_metric(metric) -> str:
    if isinstance(metric, (list, tuple)):
        metric = metric[0] if metric else HK_DIFFERENTIAL_LINEAR
    metric_key = str(metric or HK_DIFFERENTIAL_LINEAR)
    return metric_key if metric_key in HK_DISPLAY_METRIC_ORDER else HK_DIFFERENTIAL_LINEAR


def hk_display_axis_label(metric) -> str:
    metric_key = normalize_hk_display_metric(metric)
    return HK_DISPLAY_METRIC_AXIS_LABELS.get(metric_key, HK_DISPLAY_METRIC_AXIS_LABELS[HK_DIFFERENTIAL_LINEAR])


def _valid_nonnegative(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isfinite(number) and number >= 0.0:
        return number
    return None


def _bjh_differential_value(row: dict[str, float], mode: str) -> float:
    if mode != BJH_DIFFERENTIAL_LINEAR:
        value = _valid_nonnegative(row.get("differential_pore_volume_cm3_g"))
        if value is None:
            raise ValueError("missing BJH dV/dlogD value")
        return value

    official = _valid_nonnegative(row.get("differential_pore_volume_per_nm_cm3_g_nm"))
    if official is not None:
        return official

    log_value = _valid_nonnegative(row.get("differential_pore_volume_cm3_g"))
    diameter = _valid_nonnegative(row.get("pore_diameter_nm"))
    if log_value is not None and diameter is not None and diameter > 1e-12:
        return log_value / (math.log(10.0) * diameter)

    incremental = _valid_nonnegative(row.get("incremental_pore_volume_cm3_g"))
    high = _valid_nonnegative(row.get("pore_diameter_range_high_nm"))
    low = _valid_nonnegative(row.get("pore_diameter_range_low_nm"))
    if incremental is not None and high is not None and low is not None:
        width = abs(high - low)
        if width > 1e-12:
            return incremental / width
    raise ValueError("missing BJH dV/dD value")


def _bjh_metric_value(row: dict[str, float], metric: str) -> float:
    if metric in {BJH_DIFFERENTIAL_LOG, BJH_DIFFERENTIAL_LINEAR}:
        return _bjh_differential_value(row, metric)
    if metric == BJH_CUMULATIVE_VOLUME:
        value = _valid_nonnegative(row.get("cumulative_pore_volume_cm3_g"))
        if value is None:
            raise ValueError("missing BJH cumulative pore volume value")
        return value
    if metric == BJH_CUMULATIVE_AREA:
        value = _valid_nonnegative(row.get("cumulative_pore_area_m2_g"))
        if value is None:
            raise ValueError("missing BJH cumulative pore area value")
        return value
    if metric == BJH_DIFFERENTIAL_AREA_LOG:
        value = _valid_nonnegative(row.get("differential_pore_area_m2_g"))
        if value is not None:
            return value
        volume_value = _bjh_differential_value(row, BJH_DIFFERENTIAL_LOG)
        diameter = _valid_nonnegative(row.get("pore_diameter_nm"))
        if diameter is not None and diameter > 1e-12:
            return 4000.0 * volume_value / diameter
        raise ValueError("missing BJH dA/dlogD value")
    raise ValueError(f"unknown BJH display metric: {metric}")


def _bjh_metric_diameter(row: dict[str, float], metric: str) -> float:
    if metric in {BJH_CUMULATIVE_VOLUME, BJH_CUMULATIVE_AREA}:
        value = _valid_nonnegative(row.get("cumulative_pore_diameter_nm"))
        if value is not None and value > 0.0:
            return value
        value = _valid_nonnegative(row.get("pore_diameter_range_low_nm"))
        if value is not None and value > 0.0:
            return value
    value = _valid_nonnegative(row.get("pore_diameter_nm"))
    if value is None or value <= 0.0:
        raise ValueError("missing BJH pore diameter value")
    return value


def _hk_metric_value(row: dict[str, float], metric: str) -> float:
    metric_key = normalize_hk_display_metric(metric)
    if metric_key == HK_CUMULATIVE_VOLUME:
        value = _valid_nonnegative(row.get("cumulative_pore_volume_cm3_g"))
        if value is None:
            raise ValueError("missing HK cumulative pore volume value")
        return value
    if metric_key == HK_DIFFERENTIAL_LOG:
        value = _valid_nonnegative(row.get("differential_pore_volume_cm3_g"))
        if value is None:
            raise ValueError("missing HK dV/dlogW value")
        return value
    value = _valid_nonnegative(row.get("differential_pore_volume_per_nm_cm3_g_nm"))
    if value is not None:
        return value
    incremental = _valid_nonnegative(row.get("incremental_pore_volume_cm3_g"))
    width_delta = _valid_nonnegative(row.get("dwidth_nm"))
    if incremental is not None and width_delta is not None and width_delta > 1e-12:
        return incremental / width_delta
    raise ValueError("missing HK dV/dW value")


def _hk_metric_width(row: dict[str, float]) -> float:
    value = _valid_nonnegative(row.get("pore_width_nm"))
    if value is None:
        value = _valid_nonnegative(row.get("pore_diameter_nm"))
    if value is None or value <= 0.0:
        raise ValueError("missing HK pore width value")
    return value


class PlainNumberAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        labels = []
        axis_length = self.geometry().height() if self.orientation in {"left", "right"} else self.geometry().width()
        max_labels = max(3, int(max(1, axis_length) // 92))
        step = max(1, int(np.ceil(len(values) / max_labels))) if len(values) else 1
        for index, value in enumerate(values):
            if index % step:
                labels.append("")
                continue
            axis_value = float(value) * scale
            if getattr(self, "logMode", False):
                axis_value = 10.0**axis_value
            labels.append(_plain_number(axis_value))
        return labels


class ClickProjectionCursor:
    def __init__(self, plot: pg.PlotWidget) -> None:
        self.plot = plot
        self.plot_item = plot.getPlotItem()
        self.view_box = self.plot_item.getViewBox()
        self.point: tuple[float, float] | None = None
        pen = pg.mkPen("#2563eb", width=1, style=QtCore.Qt.DashLine)
        self.vertical_line = pg.PlotCurveItem(pen=pen)
        self.horizontal_line = pg.PlotCurveItem(pen=pen)
        self.x_label = pg.TextItem(
            text="",
            color="#111827",
            anchor=(0.5, 1.0),
            fill=pg.mkBrush(255, 255, 255, 235),
            border=pg.mkPen("#2563eb"),
        )
        self.y_label = pg.TextItem(
            text="",
            color="#111827",
            anchor=(0.0, 0.5),
            fill=pg.mkBrush(255, 255, 255, 235),
            border=pg.mkPen("#2563eb"),
        )
        for item in (self.vertical_line, self.horizontal_line, self.x_label, self.y_label):
            item.setZValue(10_000)
            self.view_box.addItem(item, ignoreBounds=True)
            item.hide()
        self.plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        self.view_box.sigRangeChanged.connect(lambda *_args: self.update())

    def reattach(self) -> None:
        added_items = getattr(self.view_box, "addedItems", [])
        for item in (self.vertical_line, self.horizontal_line, self.x_label, self.y_label):
            if item not in added_items:
                self.view_box.addItem(item, ignoreBounds=True)
        self.update()

    def _on_mouse_clicked(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        double_click = getattr(event, "double", False)
        is_double_click = double_click() if callable(double_click) else bool(double_click)
        if is_double_click:
            self.point = None
            self.hide()
            return
        scene_pos = event.scenePos()
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return
        view_pos = self.view_box.mapSceneToView(scene_pos)
        x = float(view_pos.x())
        y = float(view_pos.y())
        if not np.isfinite(x) or not np.isfinite(y):
            return
        self.point = (x, y)
        self.update()

    def update(self) -> None:
        if self.point is None:
            self.hide()
            return
        x, y = self.point
        view_range = self.view_box.viewRange()
        if not view_range or len(view_range) != 2:
            self.hide()
            return
        (x_min, x_max), (y_min, y_max) = view_range
        if not all(np.isfinite(value) for value in (x_min, x_max, y_min, y_max)):
            self.hide()
            return
        if (
            x < min(x_min, x_max)
            or x > max(x_min, x_max)
            or y < min(y_min, y_max)
            or y > max(y_min, y_max)
        ):
            self.hide()
            return

        left, right = min(x_min, x_max), max(x_min, x_max)
        bottom, top = min(y_min, y_max), max(y_min, y_max)
        self.vertical_line.setData([x, x], [bottom, top])
        self.horizontal_line.setData([left, right], [y, y])
        self.x_label.setText(self._label_text("bottom", x))
        self.y_label.setText(self._label_text("left", y))
        self.x_label.setPos(x, bottom)
        self.y_label.setPos(left, y)
        for item in (self.vertical_line, self.horizontal_line, self.x_label, self.y_label):
            item.show()

    def hide(self) -> None:
        for item in (self.vertical_line, self.horizontal_line, self.x_label, self.y_label):
            item.hide()

    def _label_text(self, axis_name: str, coordinate: float) -> str:
        axis = self.plot_item.getAxis(axis_name)
        value = coordinate
        if getattr(axis, "logMode", False):
            try:
                value = 10.0**coordinate
            except OverflowError:
                return ""
        return _plain_number(float(value))


def _enable_click_projection_cursor(plot: pg.PlotWidget) -> None:
    cursor = ClickProjectionCursor(plot)
    original_clear = plot.clear

    def clear_with_cursor(*args, **kwargs):
        result = original_clear(*args, **kwargs)
        cursor.point = None
        cursor.reattach()
        return result

    plot.clear = clear_with_cursor
    plot._click_projection_cursor = cursor


def make_plot(title: str, left_label: str, bottom_label: str, *, legend_position: str = "left") -> pg.PlotWidget:
    bottom_axis = PlainNumberAxis(orientation="bottom")
    left_axis = PlainNumberAxis(orientation="left")
    bottom_axis.setStyle(tickTextWidth=86, autoExpandTextSpace=True)
    left_axis.setStyle(tickTextWidth=96, autoExpandTextSpace=True)
    for axis in (bottom_axis, left_axis):
        axis.enableAutoSIPrefix(False)
    plot = pg.PlotWidget(axisItems={"bottom": bottom_axis, "left": left_axis})
    plot.setBackground("w")
    plot.showGrid(x=True, y=True, alpha=0.25)
    plot.setTitle(title)
    plot.setLabel("left", left_label)
    plot.setLabel("bottom", bottom_label)
    plot.setMenuEnabled(True)
    plot.addLegend(
        offset=(10, 10),
        labelTextColor="#111827",
        brush=pg.mkBrush(255, 255, 255, 220),
        pen=pg.mkPen("#d1d5db"),
    )
    setattr(plot, "_legend_default_position", "right" if legend_position == "right" else "left")
    _enable_click_projection_cursor(plot)
    _install_legend_toggle(plot)
    return plot


def plot_isotherm_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    active_index: int = -1,
    *,
    fade_inactive: bool = False,
    active_fit_rows: list[dict[str, float]] | None = None,
    x_log: bool = False,
) -> None:
    plot.clear()
    plot.setTitle("吸附/脱附等温线")
    plot.setLabel("left", "吸附量 (cm3/g STP)")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=bool(x_log), y=False)
    all_x = []
    all_y = []
    legend_entries = []

    def _collect_xy(pts):
        for p in pts:
            all_x.append(float(p.relative_pressure))
            all_y.append(float(p.quantity_adsorbed_cm3_g_stp or 0.0))

    # 先画非活跃样品，再画活跃样品，确保当前样品始终在最上层。
    draw_order = [i for i in range(len(results)) if i != active_index] + (
        [active_index] if 0 <= active_index < len(results) else []
    )
    for index in draw_order:
        if index >= len(visible) or not visible[index]:
            continue
        result = results[index]
        is_active = index == active_index
        base_color = _analysis_color(colors, index, active_index)
        color = _color_with_alpha(base_color, 46) if fade_inactive and not is_active else base_color
        width = ACTIVE_LINE_WIDTH if is_active else DEFAULT_LINE_WIDTH
        symbol_size = ACTIVE_SYMBOL_SIZE if is_active else DEFAULT_SYMBOL_SIZE
        symbol_pen_width = ACTIVE_SYMBOL_PEN_WIDTH if is_active else DEFAULT_SYMBOL_PEN_WIDTH
        name = _legend_name(result)
        adsorption = adsorption_points(result)
        desorption = desorption_points(result)
        item = _plot_points(
            plot,
            adsorption,
            color,
            None,
            solid=True,
            width=width,
            symbol_size=symbol_size,
            symbol_pen_width=symbol_pen_width,
            filled=not is_active,
        )
        if item is not None:
            legend_entries.append((index, item, name))
        _plot_adsorption_desorption_bridge(plot, adsorption, desorption, color, width=width)
        _plot_points(
            plot,
            desorption,
            color,
            None,
            solid=False,
            width=width,
            symbol_size=symbol_size,
            symbol_pen_width=symbol_pen_width,
            filled=False,
        )
        _collect_xy(adsorption)
        _collect_xy(desorption)

    fit_item, fit_x, fit_y = _plot_dft_isotherm_fit(plot, active_fit_rows)
    if fit_item is not None:
        legend_entries.append((len(results) + 1, fit_item, "DFT model fit"))
        all_x.extend(fit_x)
        all_y.extend(fit_y)

    _set_sample_legend_entries(plot, legend_entries)
    _fit_range(plot, all_x, all_y, x_log=bool(x_log))


def plot_isotherm_selection(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    selected_range: tuple[float, float] | list[float],
    active_index: int = -1,
) -> list:
    if selected_range is None:
        return []

    lo, hi = sorted((float(selected_range[0]), float(selected_range[1])))
    items = []
    draw_order = [i for i in range(len(results)) if i != active_index] + (
        [active_index] if 0 <= active_index < len(results) else []
    )
    for index in draw_order:
        if index >= len(visible) or not visible[index]:
            continue
        result = results[index]
        color = _analysis_color(colors, index, active_index)
        items.append(_plot_selected_isotherm_points(plot, adsorption_points(result), color, lo, hi))
        items.append(_plot_selected_isotherm_points(plot, desorption_points(result), color, lo, hi))
    return [item for item in items if item is not None]


def plot_bet(
    plot: pg.PlotWidget,
    result,
    p_min: float | None = None,
    p_max: float | None = None,
) -> np.ndarray:
    """仅绘制 BET 散点（不含拟合线），返回 x 坐标数组用于初始化选区边界。
    拟合线由 replace_bet_fit_line() 单独管理，以支持拖动时的快速更新。
    """
    if p_min is None or p_max is None:
        data_min, data_max = automatic_bet_range(result)
    else:
        data_min, data_max = p_min, p_max
    analysis = bet_analysis(result, data_min, data_max)

    plot.clear()
    plot.setTitle("BET 拟合")
    plot.setLabel("left", "P/[V(P0-P)]")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=False, y=False)

    if not analysis.rows:
        _plot_message(plot, f"BET {_range_text(analysis, '自动选点区间')} 内有效点不足")
        return np.array([])

    x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["bet_y"] for row in analysis.rows], dtype=float)
    plot.plot(
        x, y,
        pen=None, symbol="o", symbolSize=ACTIVE_SYMBOL_SIZE,
        symbolPen=pg.mkPen("#2563eb", width=ACTIVE_SYMBOL_PEN_WIDTH),
        symbolBrush=pg.mkBrush("#2563eb"),
        name="BET 点",
    )
    _fit_range(plot, x, y)
    return x


def plot_bet_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    active_index: int = -1,
    p_min: float | None = None,
    p_max: float | None = None,
    analysis_provider: Callable[..., object] | None = None,
) -> dict[int, np.ndarray]:
    """绘制所有勾选样品的 BET 散点，返回每个样品的 x 坐标数组。"""
    data_min = p_min if p_min is not None else 0.05
    data_max = p_max if p_max is not None else 0.30

    plot.clear()
    _clear_manual_legend_entries(plot)
    plot.setTitle("BET 拟合")
    plot.setLabel("left", "P/[V(P0-P)]")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=False, y=False)

    x_by_index: dict[int, np.ndarray] = {}
    all_x = []
    all_y = []
    for index in _analysis_draw_order(results, visible, active_index):
        if analysis_provider is None:
            analysis = bet_analysis(results[index], data_min, data_max)
        else:
            analysis = analysis_provider(results[index], data_min, data_max)
        if not analysis.rows:
            continue
        x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
        y = np.asarray([row["bet_y"] for row in analysis.rows], dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        if not np.any(mask):
            continue
        x = x[mask]
        y = y[mask]
        color = _analysis_color(colors, index, active_index)
        item = _plot_analysis_xy(plot, x, y, color, _legend_name(results[index]), index == active_index)
        _append_sample_legend_entry(plot, index, item, _legend_name(results[index]))
        x_by_index[index] = x
        all_x.extend(x.tolist())
        all_y.extend(y.tolist())

    if all_x:
        _fit_range(plot, all_x, all_y)
    else:
        _plot_message(plot, f"BET 当前区间 {_plain_number(data_min)}-{_plain_number(data_max)} 内有效点不足")
    return x_by_index


def plot_bet_selection(
    plot: pg.PlotWidget,
    result,
    fit_p_min: float,
    fit_p_max: float,
    data_p_min: float | None = None,
    data_p_max: float | None = None,
    color: str = "#2563eb",
    analysis_provider: Callable[..., object] | None = None,
):
    data_min = data_p_min if data_p_min is not None else 0.05
    data_max = data_p_max if data_p_max is not None else 0.30
    if analysis_provider is None:
        analysis = bet_analysis(result, data_min, data_max)
    else:
        analysis = analysis_provider(result, data_min, data_max)
    if not analysis.rows:
        return None

    lo, hi = sorted((float(fit_p_min), float(fit_p_max)))
    x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["bet_y"] for row in analysis.rows], dtype=float)
    mask = np.isfinite(x) & np.isfinite(y) & (x >= lo) & (x <= hi)
    if not np.any(mask):
        return None
    return _plot_selected_xy(plot, x[mask], y[mask], color)


def plot_langmuir_selection(
    plot: pg.PlotWidget,
    result,
    fit_p_min: float,
    fit_p_max: float,
    data_p_min: float | None = None,
    data_p_max: float | None = None,
    color: str = "#2563eb",
    analysis_provider: Callable[..., object] | None = None,
):
    data_min = data_p_min if data_p_min is not None else 0.05
    data_max = data_p_max if data_p_max is not None else 0.30
    if analysis_provider is None:
        analysis = langmuir_analysis(result, data_min, data_max)
    else:
        analysis = analysis_provider(result, data_min, data_max)
    if not analysis.rows:
        return None

    lo, hi = sorted((float(fit_p_min), float(fit_p_max)))
    x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["langmuir_y"] for row in analysis.rows], dtype=float)
    mask = np.isfinite(x) & np.isfinite(y) & (x >= lo) & (x <= hi)
    if not np.any(mask):
        return None
    return _plot_selected_xy(plot, x[mask], y[mask], color)


def replace_bet_fit_line(
    plot: pg.PlotWidget,
    old_item,
    result,
    fit_p_min: float,
    fit_p_max: float,
    line_x_min: float | None = None,
    line_x_max: float | None = None,
    color: str = "#2563eb",
    name: str | None = "线性拟合",
    width: int = 2,
    analysis_provider: Callable[..., object] | None = None,
):
    """移除旧拟合线 item，根据新的拟合区间重新绘制，返回 (new_item, FitResult)。
    line_x_min/line_x_max 控制线的显示范围，默认与拟合区间相同。
    不调用 plot.clear()，仅操作单个 item，因此拖动时无需重绘点和选区。
    """
    if old_item is not None:
        try:
            plot.removeItem(old_item)
        except RuntimeError:
            pass

    if analysis_provider is None:
        analysis = bet_analysis(result, fit_p_min, fit_p_max)
    else:
        analysis = analysis_provider(result, fit_p_min, fit_p_max)
    if not analysis.ok or analysis.slope is None or analysis.intercept is None:
        return None, analysis

    x_start = line_x_min if line_x_min is not None else fit_p_min
    x_end = line_x_max if line_x_max is not None else fit_p_max
    line_x = np.linspace(x_start, x_end, 120)
    line_y = analysis.slope * line_x + analysis.intercept
    item = plot.plot(line_x, line_y, pen=pg.mkPen(color, width=width), name=name)
    return item, analysis


def plot_langmuir_points(
    plot: pg.PlotWidget,
    result,
    p_min: float | None = None,
    p_max: float | None = None,
) -> np.ndarray:
    """仅绘制 Langmuir 散点（不含拟合线），返回 x 坐标数组（P/P0）。"""
    data_min = p_min if p_min is not None else 0.05
    data_max = p_max if p_max is not None else 0.30
    analysis = langmuir_analysis(result, data_min, data_max)

    plot.clear()
    plot.setTitle("Langmuir 拟合")
    plot.setLabel("left", "(P/P0) / V")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=False, y=False)

    if not analysis.rows:
        _plot_message(plot, f"Langmuir {_range_text(analysis, '默认区间 0.05-0.30')} 内有效点不足")
        return np.array([])

    x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["langmuir_y"] for row in analysis.rows], dtype=float)
    plot.plot(
        x, y,
        pen=None, symbol="o", symbolSize=ACTIVE_SYMBOL_SIZE,
        symbolPen=pg.mkPen("#2563eb", width=ACTIVE_SYMBOL_PEN_WIDTH),
        symbolBrush=pg.mkBrush("#2563eb"),
        name="Langmuir 点",
    )
    _fit_range(plot, x, y)
    return x


def plot_langmuir_points_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    active_index: int = -1,
    p_min: float | None = None,
    p_max: float | None = None,
    analysis_provider: Callable[..., object] | None = None,
) -> dict[int, np.ndarray]:
    """绘制所有勾选样品的 Langmuir 散点，返回每个样品的 x 坐标数组。"""
    data_min = p_min if p_min is not None else 0.05
    data_max = p_max if p_max is not None else 0.30

    plot.clear()
    _clear_manual_legend_entries(plot)
    plot.setTitle("Langmuir 拟合")
    plot.setLabel("left", "(P/P0) / V")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=False, y=False)

    x_by_index: dict[int, np.ndarray] = {}
    all_x = []
    all_y = []
    for index in _analysis_draw_order(results, visible, active_index):
        if analysis_provider is None:
            analysis = langmuir_analysis(results[index], data_min, data_max)
        else:
            analysis = analysis_provider(results[index], data_min, data_max)
        if not analysis.rows:
            continue
        x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
        y = np.asarray([row["langmuir_y"] for row in analysis.rows], dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        if not np.any(mask):
            continue
        x = x[mask]
        y = y[mask]
        color = _analysis_color(colors, index, active_index)
        item = _plot_analysis_xy(plot, x, y, color, _legend_name(results[index]), index == active_index)
        _append_sample_legend_entry(plot, index, item, _legend_name(results[index]))
        x_by_index[index] = x
        all_x.extend(x.tolist())
        all_y.extend(y.tolist())

    if all_x:
        _fit_range(plot, all_x, all_y)
    else:
        _plot_message(plot, f"Langmuir 当前区间 {_plain_number(data_min)}-{_plain_number(data_max)} 内有效点不足")
    return x_by_index


def replace_langmuir_fit_line(
    plot: pg.PlotWidget,
    old_item,
    result,
    fit_p_min: float,
    fit_p_max: float,
    line_x_min: float | None = None,
    line_x_max: float | None = None,
    color: str = "#2563eb",
    name: str | None = "线性拟合",
    width: int = 2,
    analysis_provider: Callable[..., object] | None = None,
):
    """移除旧 Langmuir 拟合线并重绘，返回 (new_item, FitResult)。"""
    if old_item is not None:
        try:
            plot.removeItem(old_item)
        except RuntimeError:
            pass

    if analysis_provider is None:
        analysis = langmuir_analysis(result, fit_p_min, fit_p_max)
    else:
        analysis = analysis_provider(result, fit_p_min, fit_p_max)
    if not analysis.ok or analysis.slope is None or analysis.intercept is None:
        return None, analysis

    x_start = line_x_min if line_x_min is not None else fit_p_min
    x_end = line_x_max if line_x_max is not None else fit_p_max
    line_x = np.linspace(x_start, x_end, 120)
    line_y = analysis.slope * line_x + analysis.intercept
    item = plot.plot(line_x, line_y, pen=pg.mkPen(color, width=width), name=name)
    return item, analysis


def _t_plot_y_values(rows) -> np.ndarray:
    return np.asarray(
        [row.get("t_plot_y_value", row["liquid_volume_cm3_g"]) for row in rows],
        dtype=float,
    )


def _t_plot_y_axis_label(rows) -> str:
    units = {str(row.get("t_plot_y_unit", "cm3/g liquid")) for row in rows}
    if units == {"cm3/g STP"}:
        return "吸附量 (cm3/g STP)"
    if units == {"cm3/g liquid"}:
        return "液体体积 (cm3/g)"
    return "t-Plot Y"


def plot_t_plot_points(
    plot: pg.PlotWidget,
    result,
    p_min: float | None = None,
    p_max: float | None = None,
    thickness_params: dict[str, float] | None = None,
    thickness_method: str = "harkins_jura",
) -> np.ndarray:
    """仅绘制 t-Plot 散点（不含拟合线），返回 x 坐标数组（厚度，nm）。"""
    data_min = p_min if p_min is not None else 0.20
    data_max = p_max if p_max is not None else 0.50
    analysis = t_plot_analysis(result, data_min, data_max, thickness_params, thickness_method)

    plot.clear()
    plot.setTitle("t-Plot")
    plot.setLabel("left", _t_plot_y_axis_label(analysis.rows))
    plot.setLabel("bottom", "统计膜厚 t (nm)")
    plot.setLogMode(x=False, y=False)

    if not analysis.rows:
        _plot_message(plot, f"t-Plot {_range_text(analysis, '默认区间 0.20-0.50')} 内有效点不足")
        return np.array([])

    x = np.asarray([row["thickness_nm"] for row in analysis.rows], dtype=float)
    y = _t_plot_y_values(analysis.rows)
    plot.plot(
        x, y,
        pen=None, symbol="o", symbolSize=ACTIVE_SYMBOL_SIZE,
        symbolPen=pg.mkPen("#2563eb", width=ACTIVE_SYMBOL_PEN_WIDTH),
        symbolBrush=pg.mkBrush("#2563eb"),
        name="t-Plot 点",
    )
    _fit_range(plot, x, y)
    return x


def plot_t_plot_points_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    active_index: int = -1,
    p_min: float | None = None,
    p_max: float | None = None,
    thickness_params_by_index: dict[int, dict[str, float]] | None = None,
    thickness_method_by_index: dict[int, str] | None = None,
    analysis_provider: Callable[..., object] | None = None,
) -> dict[int, np.ndarray]:
    """绘制所有勾选样品的 t-Plot 散点，返回每个样品的厚度 x 坐标数组。"""
    data_min = p_min if p_min is not None else 0.20
    data_max = p_max if p_max is not None else 0.50

    plot.clear()
    _clear_manual_legend_entries(plot)
    plot.setTitle("t-Plot")
    plot.setLabel("left", "t-Plot Y")
    plot.setLabel("bottom", "统计膜厚 t (nm)")
    plot.setLogMode(x=False, y=False)

    x_by_index: dict[int, np.ndarray] = {}
    all_x = []
    all_y = []
    plotted_rows = []
    for index in _analysis_draw_order(results, visible, active_index):
        thickness_params = None
        if thickness_params_by_index is not None:
            thickness_params = thickness_params_by_index.get(index)
        thickness_method = "harkins_jura"
        if thickness_method_by_index is not None:
            thickness_method = thickness_method_by_index.get(index, thickness_method)
        if analysis_provider is None:
            analysis = t_plot_analysis(results[index], data_min, data_max, thickness_params, thickness_method)
        else:
            analysis = analysis_provider(results[index], data_min, data_max, thickness_params, thickness_method)
        if not analysis.rows:
            continue
        x = np.asarray([row["thickness_nm"] for row in analysis.rows], dtype=float)
        y = _t_plot_y_values(analysis.rows)
        mask = np.isfinite(x) & np.isfinite(y)
        if not np.any(mask):
            continue
        x = x[mask]
        y = y[mask]
        color = _analysis_color(colors, index, active_index)
        item = _plot_analysis_xy(plot, x, y, color, _legend_name(results[index]), index == active_index)
        _append_sample_legend_entry(plot, index, item, _legend_name(results[index]))
        x_by_index[index] = x
        all_x.extend(x.tolist())
        all_y.extend(y.tolist())
        plotted_rows.extend(analysis.rows)

    if all_x:
        plot.setLabel("left", _t_plot_y_axis_label(plotted_rows))
        _fit_range(plot, all_x, all_y)
    else:
        _plot_message(plot, f"t-Plot 当前区间 {_plain_number(data_min)}-{_plain_number(data_max)} 内有效点不足")
    return x_by_index


def plot_t_plot_selection(
    plot: pg.PlotWidget,
    result,
    fit_t_min: float,
    fit_t_max: float,
    data_p_min: float | None = None,
    data_p_max: float | None = None,
    thickness_params: dict[str, float] | None = None,
    thickness_method: str = "harkins_jura",
    color: str = "#2563eb",
    analysis_provider: Callable[..., object] | None = None,
):
    if analysis_provider is None:
        analysis = t_plot_analysis_by_thickness(
            result,
            fit_t_min,
            fit_t_max,
            data_p_min,
            data_p_max,
            thickness_params,
            thickness_method,
        )
    else:
        analysis = analysis_provider(
            result,
            fit_t_min,
            fit_t_max,
            data_p_min,
            data_p_max,
            thickness_params,
            thickness_method,
        )
    if not analysis.rows:
        return None

    x = np.asarray([row["thickness_nm"] for row in analysis.rows], dtype=float)
    y = _t_plot_y_values(analysis.rows)
    mask = np.isfinite(x) & np.isfinite(y)
    if not np.any(mask):
        return None
    return _plot_selected_xy(plot, x[mask], y[mask], color)


def replace_t_plot_fit_line(
    plot: pg.PlotWidget,
    old_item,
    result,
    fit_t_min: float,
    fit_t_max: float,
    line_x_min: float | None = None,
    line_x_max: float | None = None,
    data_p_min: float | None = None,
    data_p_max: float | None = None,
    thickness_params: dict[str, float] | None = None,
    thickness_method: str = "harkins_jura",
    color: str = "#2563eb",
    name: str | None = "线性拟合",
    width: int = 2,
    analysis_provider: Callable[..., object] | None = None,
):
    """移除旧 t-Plot 拟合线并重绘（按厚度范围选点），返回 (new_item, FitResult)。"""
    if old_item is not None:
        try:
            plot.removeItem(old_item)
        except RuntimeError:
            pass

    if analysis_provider is None:
        analysis = t_plot_analysis_by_thickness(
            result,
            fit_t_min,
            fit_t_max,
            data_p_min,
            data_p_max,
            thickness_params,
            thickness_method,
        )
    else:
        analysis = analysis_provider(
            result,
            fit_t_min,
            fit_t_max,
            data_p_min,
            data_p_max,
            thickness_params,
            thickness_method,
        )
    if not analysis.ok or analysis.slope is None or analysis.intercept is None:
        return None, analysis

    x_start = line_x_min if line_x_min is not None else fit_t_min
    x_end = line_x_max if line_x_max is not None else fit_t_max
    line_x = np.linspace(x_start, x_end, 120)
    line_y = analysis.slope * line_x + analysis.intercept
    item = plot.plot(line_x, line_y, pen=pg.mkPen(color, width=width), name=name)
    return item, analysis


def plot_langmuir(plot: pg.PlotWidget, result, p_min: float | None = None, p_max: float | None = None) -> None:
    analysis = (
        langmuir_analysis(result)
        if p_min is None or p_max is None
        else langmuir_analysis(result, p_min, p_max)
    )
    plot.clear()
    plot.setTitle("Langmuir 拟合")
    plot.setLabel("left", "(P/P0) / V")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=False, y=False)
    if not analysis.rows:
        _plot_message(plot, f"Langmuir {_range_text(analysis, '默认区间 0.05-0.30')} 内有效点不足")
        return
    x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["langmuir_y"] for row in analysis.rows], dtype=float)
    plot.plot(
        x,
        y,
        pen=None,
        symbol="o",
        symbolSize=ACTIVE_SYMBOL_SIZE,
        symbolPen=pg.mkPen("#2563eb", width=ACTIVE_SYMBOL_PEN_WIDTH),
        symbolBrush=pg.mkBrush("#2563eb"),
        name="Langmuir 点",
    )
    _plot_fit_line(plot, analysis, x, "#2563eb")
    _fit_range(plot, x, y)


def plot_t_plot(plot: pg.PlotWidget, result, p_min: float | None = None, p_max: float | None = None) -> None:
    analysis = t_plot_analysis(result) if p_min is None or p_max is None else t_plot_analysis(result, p_min, p_max)
    plot.clear()
    plot.setTitle("t-Plot")
    plot.setLabel("left", _t_plot_y_axis_label(analysis.rows))
    plot.setLabel("bottom", "统计膜厚 t (nm)")
    plot.setLogMode(x=False, y=False)
    if not analysis.rows:
        _plot_message(plot, f"t-Plot {_range_text(analysis, '默认区间 0.20-0.50')} 内有效点不足")
        return
    x = np.asarray([row["thickness_nm"] for row in analysis.rows], dtype=float)
    y = _t_plot_y_values(analysis.rows)
    plot.plot(
        x,
        y,
        pen=None,
        symbol="o",
        symbolSize=ACTIVE_SYMBOL_SIZE,
        symbolPen=pg.mkPen("#2563eb", width=ACTIVE_SYMBOL_PEN_WIDTH),
        symbolBrush=pg.mkBrush("#2563eb"),
        name="t-Plot 点",
    )
    _plot_fit_line(plot, analysis, x, "#2563eb")
    _fit_range(plot, x, y)


def plot_bjh_distribution_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    active_index: int = -1,
    thickness_method: str = "harkins_jura",
    thickness_params: dict[str, float] | None = None,
    correction: str = "standard",
    open_pore_fraction: float = 0.0,
    show_adsorption: bool = True,
    show_desorption: bool = True,
    smooth: bool = True,
    pressure_range: tuple[float, float] | None = None,
    bjh_settings_by_index: dict[int, dict] | None = None,
    distribution_provider: Callable[..., list[dict[str, float]]] | None = None,
    differential_mode: str = BJH_DIFFERENTIAL_LOG,
    display_metrics=None,
) -> dict[tuple[int, str], list[dict[str, float]]]:
    metrics = normalize_bjh_display_metrics(display_metrics if display_metrics is not None else [differential_mode])
    plot.clear()
    _clear_manual_legend_entries(plot)
    plot.setTitle("BJH 孔径分布")
    plot.setLabel("left", bjh_display_axis_label(metrics))
    plot.setLabel("bottom", "孔径 (nm)")
    plot.setLogMode(x=True, y=False)
    all_x = []
    all_y = []
    legend_entries = []
    rows_by_key: dict[tuple[int, str], list[dict[str, float]]] = {}
    if not bjh_settings_by_index and not show_adsorption and not show_desorption:
        _plot_message(plot, "请选择 BJH 吸附或 BJH 脱附")
        return rows_by_key

    for index in _analysis_draw_order(results, visible, active_index):
        result = results[index]
        is_active = index == active_index
        color = _analysis_color(colors, index, active_index)
        width = ACTIVE_LINE_WIDTH if is_active else DEFAULT_LINE_WIDTH
        settings = (bjh_settings_by_index or {}).get(index, {})
        sample_thickness_method = str(settings.get("thickness_method", thickness_method))
        sample_thickness_params = dict(settings.get("thickness_params", thickness_params or {}))
        sample_correction = str(settings.get("correction", correction))
        sample_open_pore_fraction = float(settings.get("open_pore_fraction", open_pore_fraction))
        sample_show_adsorption = bool(settings.get("show_adsorption", show_adsorption))
        sample_show_desorption = bool(settings.get("show_desorption", show_desorption))
        sample_smooth = bool(settings.get("smooth_derivative", smooth))
        phases: list[tuple[str, bool, QtCore.Qt.PenStyle]] = [
            ("adsorption", sample_show_adsorption, QtCore.Qt.SolidLine),
            ("desorption", sample_show_desorption, QtCore.Qt.DashLine),
        ]
        for phase, enabled, line_style in phases:
            if not enabled:
                continue
            if distribution_provider is None:
                distribution = bjh_pore_distribution(
                    result,
                    phase=phase,
                    thickness_method=sample_thickness_method,
                    thickness_params=sample_thickness_params,
                    correction=sample_correction,
                    open_pore_fraction=sample_open_pore_fraction,
                    smooth=sample_smooth,
                )
                all_rows = list(distribution.rows)
            else:
                all_rows = list(
                    distribution_provider(
                        result,
                        phase=phase,
                        thickness_method=sample_thickness_method,
                        thickness_params=sample_thickness_params,
                        correction=sample_correction,
                        open_pore_fraction=sample_open_pore_fraction,
                        smooth=sample_smooth,
                    )
                )
            rows = _bjh_rows_in_pressure_range(all_rows, pressure_range)
            rows_by_key[(index, phase)] = list(rows)
            if not rows:
                continue
            phase_label = "Ads" if phase == "adsorption" else "Des"
            for metric in metrics:
                x_values = []
                y_values = []
                for row in rows:
                    try:
                        diameter = _bjh_metric_diameter(row, metric)
                        metric_value = _bjh_metric_value(row, metric)
                    except (KeyError, TypeError, ValueError):
                        continue
                    x_values.append(diameter)
                    y_values.append(metric_value)
                x = np.asarray(x_values, dtype=float)
                y = np.asarray(y_values, dtype=float)
                mask = np.isfinite(x) & np.isfinite(y) & (x > 0.0) & (y >= 0.0)
                if not np.any(mask):
                    continue
                x = x[mask]
                y = y[mask]
                order = np.argsort(x)
                x = x[order]
                y = y[order]
                pen = pg.mkPen(color, width=width)
                pen.setStyle(line_style)
                item = plot.plot(
                    x,
                    y,
                    pen=pen,
                    symbol=BJH_DISPLAY_METRIC_SYMBOLS.get(metric, "o"),
                    symbolSize=ACTIVE_SYMBOL_SIZE if is_active else DEFAULT_SYMBOL_SIZE,
                    symbolPen=pg.mkPen(color, width=ACTIVE_SYMBOL_PEN_WIDTH if is_active else DEFAULT_SYMBOL_PEN_WIDTH),
                    symbolBrush=pg.mkBrush("#ffffff"),
                    name=None,
                )
                legend_entries.append(
                    (index, item, f"{_legend_name(result)} BJH{phase_label} {bjh_display_metric_label(metric)}")
                )
                all_x.extend(x.tolist())
                all_y.extend(y.tolist())

    _set_sample_legend_entries(plot, legend_entries)
    if all_x:
        _fit_range(plot, all_x, all_y, x_log=True)
    else:
        _plot_message(plot, "当前样品没有足够的 BJH 孔径分布点")
    return rows_by_key


def plot_dh_distribution_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    active_index: int = -1,
    thickness_method: str | None = None,
    thickness_params: dict[str, float] | None = None,
    show_adsorption: bool = True,
    show_desorption: bool = False,
    smooth: bool = False,
    pressure_range: tuple[float, float] | None = None,
    dh_settings_by_index: dict[int, dict] | None = None,
    distribution_provider: Callable[..., list[dict[str, float]]] | None = None,
    display_metrics=None,
) -> dict[tuple[int, str], list[dict[str, float]]]:
    metrics = normalize_bjh_display_metrics(display_metrics)
    plot.clear()
    _clear_manual_legend_entries(plot)
    plot.setTitle("Dollimore-Heal 孔径分布")
    plot.setLabel("left", bjh_display_axis_label(metrics))
    plot.setLabel("bottom", "孔径 (nm)")
    plot.setLogMode(x=True, y=False)
    all_x = []
    all_y = []
    legend_entries = []
    rows_by_key: dict[tuple[int, str], list[dict[str, float]]] = {}
    if not dh_settings_by_index and not show_adsorption and not show_desorption:
        _plot_message(plot, "请选择 DH 吸附或 DH 脱附")
        return rows_by_key

    for index in _analysis_draw_order(results, visible, active_index):
        result = results[index]
        is_active = index == active_index
        color = _analysis_color(colors, index, active_index)
        width = ACTIVE_LINE_WIDTH if is_active else DEFAULT_LINE_WIDTH
        settings = (dh_settings_by_index or {}).get(index, {})
        sample_thickness_method = str(settings.get("thickness_method", thickness_method))
        sample_thickness_params = dict(settings.get("thickness_params", thickness_params or {}))
        sample_show_adsorption = bool(settings.get("show_adsorption", show_adsorption))
        sample_show_desorption = bool(settings.get("show_desorption", show_desorption))
        sample_smooth = bool(settings.get("smooth_derivative", smooth))
        phases: list[tuple[str, bool, QtCore.Qt.PenStyle]] = [
            ("adsorption", sample_show_adsorption, QtCore.Qt.SolidLine),
            ("desorption", sample_show_desorption, QtCore.Qt.DashLine),
        ]
        for phase, enabled, line_style in phases:
            if not enabled:
                continue
            if distribution_provider is None:
                distribution = dh_pore_distribution(
                    result,
                    phase=phase,
                    thickness_method=sample_thickness_method,
                    thickness_params=sample_thickness_params,
                    smooth=sample_smooth,
                )
                rows = list(distribution.rows)
            else:
                rows = list(
                    distribution_provider(
                        result,
                        phase=phase,
                        thickness_method=sample_thickness_method,
                        thickness_params=sample_thickness_params,
                        smooth=sample_smooth,
                    )
                )
            rows = _bjh_rows_in_pressure_range(rows, pressure_range)
            rows_by_key[(index, phase)] = list(rows)
            if not rows:
                continue
            phase_label = "Ads" if phase == "adsorption" else "Des"
            for metric in metrics:
                x_values = []
                y_values = []
                for row in rows:
                    try:
                        diameter = _bjh_metric_diameter(row, metric)
                        metric_value = _bjh_metric_value(row, metric)
                    except (KeyError, TypeError, ValueError):
                        continue
                    x_values.append(diameter)
                    y_values.append(metric_value)
                x = np.asarray(x_values, dtype=float)
                y = np.asarray(y_values, dtype=float)
                mask = np.isfinite(x) & np.isfinite(y) & (x > 0.0) & (y >= 0.0)
                if not np.any(mask):
                    continue
                x = x[mask]
                y = y[mask]
                order = np.argsort(x)
                x = x[order]
                y = y[order]
                pen = pg.mkPen(color, width=width)
                pen.setStyle(line_style)
                item = plot.plot(
                    x,
                    y,
                    pen=pen,
                    symbol=BJH_DISPLAY_METRIC_SYMBOLS.get(metric, "o"),
                    symbolSize=ACTIVE_SYMBOL_SIZE if is_active else DEFAULT_SYMBOL_SIZE,
                    symbolPen=pg.mkPen(color, width=ACTIVE_SYMBOL_PEN_WIDTH if is_active else DEFAULT_SYMBOL_PEN_WIDTH),
                    symbolBrush=pg.mkBrush("#ffffff"),
                    name=None,
                )
                legend_entries.append(
                    (index, item, f"{_legend_name(result)} DH{phase_label} {bjh_display_metric_label(metric)}")
                )
                all_x.extend(x.tolist())
                all_y.extend(y.tolist())

    _set_sample_legend_entries(plot, legend_entries)
    if all_x:
        _fit_range(plot, all_x, all_y, x_log=True)
    else:
        _plot_message(plot, "当前样品没有足够的 DH 孔径分布点")
    return rows_by_key


def plot_hk_distribution_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    active_index: int = -1,
    geometry: str = "slit",
    adsorbent_key: str = "zeolite",
    adsorptive_key: str = "N2",
    adsorbent_properties: dict[str, object] | None = None,
    adsorptive_properties: dict[str, object] | None = None,
    interaction_parameter_erg_cm4: float = 3.49e-43,
    interaction_parameter_mode: str = "input",
    cheng_yang_correction: bool = False,
    smooth: bool = False,
    pressure_range: tuple[float, float] | None = None,
    hk_settings_by_index: dict[int, dict] | None = None,
    distribution_provider: Callable[..., list[dict[str, float]]] | None = None,
    display_metric: str = HK_DIFFERENTIAL_LINEAR,
) -> dict[tuple[int, str], list[dict[str, float]]]:
    metric = normalize_hk_display_metric(display_metric)
    plot.clear()
    _clear_manual_legend_entries(plot)
    plot.setTitle("Horvath-Kawazoe 孔径分布")
    plot.setLabel("left", hk_display_axis_label(metric))
    plot.setLabel("bottom", "孔宽 W (nm)")
    plot.setLogMode(x=True, y=False)
    all_x = []
    all_y = []
    legend_entries = []
    rows_by_key: dict[tuple[int, str], list[dict[str, float]]] = {}

    for index in _analysis_draw_order(results, visible, active_index):
        result = results[index]
        is_active = index == active_index
        color = _analysis_color(colors, index, active_index)
        width = ACTIVE_LINE_WIDTH if is_active else DEFAULT_LINE_WIDTH
        settings = (hk_settings_by_index or {}).get(index, {})
        sample_geometry = str(settings.get("geometry", geometry))
        sample_adsorbent_key = str(settings.get("adsorbent_key", adsorbent_key))
        sample_adsorptive_key = str(settings.get("adsorptive_key", adsorptive_key))
        sample_adsorbent_properties = dict(settings.get("adsorbent_properties", adsorbent_properties or {}))
        sample_adsorptive_properties = dict(settings.get("adsorptive_properties", adsorptive_properties or {}))
        sample_interaction_parameter = float(
            settings.get("interaction_parameter", interaction_parameter_erg_cm4)
        )
        sample_interaction_mode = str(settings.get("interaction_parameter_mode", interaction_parameter_mode))
        sample_cheng_yang = bool(settings.get("cheng_yang_correction", cheng_yang_correction))
        sample_smooth = bool(settings.get("smooth_derivative", smooth))
        if distribution_provider is None:
            distribution = horvath_kawazoe_pore_distribution(
                result,
                geometry=sample_geometry,
                adsorbent_key=sample_adsorbent_key,
                adsorptive_key=sample_adsorptive_key,
                adsorbent_properties=sample_adsorbent_properties,
                adsorptive_properties=sample_adsorptive_properties,
                interaction_parameter_erg_cm4=sample_interaction_parameter,
                interaction_parameter_mode=sample_interaction_mode,
                cheng_yang_correction=sample_cheng_yang,
                smooth=sample_smooth,
            )
            rows = list(distribution.rows)
        else:
            rows = list(
                distribution_provider(
                    result,
                    geometry=sample_geometry,
                    adsorbent_key=sample_adsorbent_key,
                    adsorptive_key=sample_adsorptive_key,
                    adsorbent_properties=sample_adsorbent_properties,
                    adsorptive_properties=sample_adsorptive_properties,
                    interaction_parameter_erg_cm4=sample_interaction_parameter,
                    interaction_parameter_mode=sample_interaction_mode,
                    cheng_yang_correction=sample_cheng_yang,
                    smooth=sample_smooth,
                )
            )
        rows = _bjh_rows_in_pressure_range(rows, pressure_range)
        rows_by_key[(index, "adsorption")] = list(rows)
        if not rows:
            continue
        x_values = []
        y_values = []
        for row in rows:
            try:
                x_values.append(_hk_metric_width(row))
                y_values.append(_hk_metric_value(row, metric))
            except (KeyError, TypeError, ValueError):
                continue
        x = np.asarray(x_values, dtype=float)
        y = np.asarray(y_values, dtype=float)
        mask = np.isfinite(x) & np.isfinite(y) & (x > 0.0) & (y >= 0.0)
        if not np.any(mask):
            continue
        x = x[mask]
        y = y[mask]
        order = np.argsort(x)
        x = x[order]
        y = y[order]
        item = plot.plot(
            x,
            y,
            pen=pg.mkPen(color, width=width),
            symbol=HK_DISPLAY_METRIC_SYMBOLS.get(metric, "o"),
            symbolSize=ACTIVE_SYMBOL_SIZE if is_active else DEFAULT_SYMBOL_SIZE,
            symbolPen=pg.mkPen(color, width=ACTIVE_SYMBOL_PEN_WIDTH if is_active else DEFAULT_SYMBOL_PEN_WIDTH),
            symbolBrush=pg.mkBrush("#ffffff"),
            name=None,
        )
        legend_entries.append((index, item, f"{_legend_name(result)} HK {hk_display_metric_label(metric)}"))
        all_x.extend(x.tolist())
        all_y.extend(y.tolist())

    _set_sample_legend_entries(plot, legend_entries)
    if all_x:
        _fit_range(plot, all_x, all_y, x_log=True)
    else:
        _plot_message(plot, "当前样品没有足够的 HK 孔径分布点")
    return rows_by_key


def plot_dft_distribution_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    active_index: int = -1,
    analysis_type: str = "dft_pore",
    geometry: str = "slit",
    model: str = "n2_dft_model",
    regularization: float = 0.316,
    dft_settings_by_index: dict[int, dict] | None = None,
    result_provider: Callable[..., object] | None = None,
) -> dict[int, list[dict[str, float]]]:
    plot.clear()
    _clear_manual_legend_entries(plot)
    plot.setTitle("DFT pore distribution")
    plot.setLabel("left", "dV/dlogW (cm3/g)")
    plot.setLabel("bottom", "Pore width W (nm)")
    plot.setLogMode(x=True, y=False)
    all_x = []
    all_y = []
    legend_entries = []
    rows_by_index: dict[int, list[dict[str, float]]] = {}

    for index in _analysis_draw_order(results, visible, active_index):
        result = results[index]
        is_active = index == active_index
        color = _analysis_color(colors, index, active_index)
        width = ACTIVE_LINE_WIDTH if is_active else DEFAULT_LINE_WIDTH
        settings = (dft_settings_by_index or {}).get(index, {})
        sample_analysis_type = str(settings.get("analysis_type", analysis_type))
        sample_geometry = str(settings.get("geometry", geometry))
        sample_model = str(settings.get("model", model))
        sample_regularization = float(settings.get("regularization", regularization))
        if result_provider is None:
            dft_result = dft_pore_distribution(
                result,
                analysis_type=sample_analysis_type,
                geometry=sample_geometry,
                model=sample_model,
                regularization=sample_regularization,
                include_diagnostics=False,
            )
        else:
            dft_result = result_provider(
                result,
                analysis_type=sample_analysis_type,
                geometry=sample_geometry,
                model=sample_model,
                regularization=sample_regularization,
                include_diagnostics=False,
            )
        rows = list(getattr(dft_result, "rows", []))
        rows_by_index[index] = rows
        if not rows:
            continue
        x_values = []
        y_values = []
        for row in rows:
            try:
                x_values.append(float(row.get("pore_width_nm", row.get("pore_diameter_nm"))))
                y_values.append(float(row.get("differential_pore_volume_cm3_g", 0.0)))
            except (TypeError, ValueError):
                continue
        x = np.asarray(x_values, dtype=float)
        y = np.asarray(y_values, dtype=float)
        mask = np.isfinite(x) & np.isfinite(y) & (x > 0.0) & (y >= 0.0)
        if not np.any(mask):
            continue
        x = x[mask]
        y = y[mask]
        order = np.argsort(x)
        x = x[order]
        y = y[order]
        item = plot.plot(
            x,
            y,
            pen=pg.mkPen(color, width=width),
            symbol="o",
            symbolSize=ACTIVE_SYMBOL_SIZE if is_active else DEFAULT_SYMBOL_SIZE,
            symbolPen=pg.mkPen(color, width=ACTIVE_SYMBOL_PEN_WIDTH if is_active else DEFAULT_SYMBOL_PEN_WIDTH),
            symbolBrush=pg.mkBrush("#ffffff"),
            name=None,
        )
        legend_entries.append((index, item, f"{_legend_name(result)} DFT"))
        all_x.extend(x.tolist())
        all_y.extend(y.tolist())

    _set_sample_legend_entries(plot, legend_entries)
    if all_x:
        _fit_range(plot, all_x, all_y, x_log=True)
    else:
        _plot_message(plot, "No DFT pore distribution points")
    return rows_by_index


def plot_dft_selection(
    plot: pg.PlotWidget,
    rows_by_index: dict[int, list[dict[str, float]]],
    colors: list[str],
    width_range: tuple[float, float] | None,
    active_index: int = -1,
) -> list:
    if width_range is None:
        return []
    lo, hi = sorted((float(width_range[0]), float(width_range[1])))
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return []
    items = []
    keys = sorted(rows_by_index, key=lambda index: (index == active_index, index))
    for index in keys:
        rows = rows_by_index.get(index, [])
        color = _analysis_color(colors, index, active_index)
        is_active = index == active_index
        selected_x = []
        selected_y = []
        for row in rows:
            try:
                width = float(row.get("pore_width_nm", row.get("pore_diameter_nm")))
                value = float(row.get("differential_pore_volume_cm3_g", 0.0))
            except (TypeError, ValueError):
                continue
            if not (np.isfinite(width) and np.isfinite(value)):
                continue
            if width <= 0.0 or value < 0.0 or width < lo or width > hi:
                continue
            selected_x.append(width)
            selected_y.append(value)
        if not selected_x:
            continue
        items.append(
            _plot_selected_xy(
                plot,
                np.asarray(selected_x, dtype=float),
                np.asarray(selected_y, dtype=float),
                color,
                symbol_size=SELECTED_SYMBOL_SIZE if is_active else DEFAULT_SYMBOL_SIZE,
                symbol_pen_width=SELECTED_SYMBOL_PEN_WIDTH if is_active else DEFAULT_SYMBOL_PEN_WIDTH,
            )
        )
    return [item for item in items if item is not None]


def plot_dft_diagnostics(
    plot: pg.PlotWidget,
    diagnostic_rows: list[dict[str, float]],
    regularization: float,
) -> pg.InfiniteLine | None:
    plot.clear()
    _clear_manual_legend_entries(plot)
    right_view = getattr(plot, "_dft_roughness_view", None)
    if right_view is not None:
        for item in list(getattr(right_view, "addedItems", [])):
            try:
                right_view.removeItem(item)
            except Exception:
                pass
    plot_item = plot.getPlotItem()
    plot.setTitle("拟合误差 / 分布粗糙度 vs. 正则化")
    plot.setLabel("bottom", "正则化")
    plot.setLabel("left", "RMS 拟合误差 (mmol/g)", color="#2563eb")
    plot_item.showAxis("right")
    plot_item.getAxis("right").setLabel("分布粗糙度", color="#f97316")
    plot_item.getAxis("left").setTextPen(pg.mkPen("#2563eb"))
    plot_item.getAxis("right").setTextPen(pg.mkPen("#f97316"))
    plot.setLogMode(x=True, y=False)
    if not diagnostic_rows:
        _plot_message(plot, "No DFT diagnostic data")
        return None
    reg = np.asarray([max(float(row["regularization"]), 1e-6) for row in diagnostic_rows], dtype=float)
    rms = np.asarray([float(row["rms_error_mmol_g"]) for row in diagnostic_rows], dtype=float)
    rough = np.asarray([float(row["distribution_roughness"]) for row in diagnostic_rows], dtype=float)
    mask = np.isfinite(reg) & np.isfinite(rms) & np.isfinite(rough) & (reg > 0.0)
    if not np.any(mask):
        _plot_message(plot, "No DFT diagnostic data")
        return None
    reg = reg[mask]
    rms = rms[mask]
    rough = rough[mask]
    if right_view is None:
        right_view = pg.ViewBox()
        setattr(plot, "_dft_roughness_view", right_view)
        plot_item.scene().addItem(right_view)
        plot_item.getAxis("right").linkToView(right_view)
        right_view.setXLink(plot_item.vb)

        def update_views():
            right_view.setGeometry(plot_item.vb.sceneBoundingRect())
            right_view.linkedViewChanged(plot_item.vb, right_view.XAxis)

        setattr(plot, "_dft_roughness_update", update_views)
        plot_item.vb.sigResized.connect(update_views)
    update_views = getattr(plot, "_dft_roughness_update", None)
    if callable(update_views):
        update_views()
    rms_item = plot.plot(
        reg,
        rms,
        pen=pg.mkPen("#2563eb", width=2),
        symbol="o",
        symbolSize=6,
        symbolPen=pg.mkPen("#2563eb"),
        symbolBrush=pg.mkBrush("#2563eb"),
        name=None,
    )
    rough_item = pg.PlotDataItem(
        reg,
        rough,
        pen=pg.mkPen("#f97316", width=2),
        symbol="o",
        symbolSize=6,
        symbolPen=pg.mkPen("#f97316"),
        symbolBrush=pg.mkBrush("#f97316"),
        name=None,
    )
    rough_item.setLogMode(True, False)
    right_view.addItem(rough_item)
    _set_sample_legend_entries(
        plot,
        [
            (0, rms_item, "RMS error"),
            (1, rough_item, "Distribution roughness"),
        ],
    )
    line = pg.InfiniteLine(
        pos=math.log10(max(float(regularization), 1e-6)),
        angle=90,
        movable=True,
        pen=pg.mkPen("#1d4ed8", width=2),
        hoverPen=pg.mkPen("#2563eb", width=3),
    )
    line.setCursor(QtCore.Qt.SizeHorCursor)
    plot.addItem(line, ignoreBounds=True)
    _fit_range(plot, reg.tolist(), rms.tolist(), x_log=True)
    rough_min = float(np.nanmin(rough))
    rough_max = float(np.nanmax(rough))
    if np.isfinite(rough_min) and np.isfinite(rough_max):
        if rough_min == rough_max:
            rough_min -= 1.0
            rough_max += 1.0
        margin = (rough_max - rough_min) * 0.08
        right_view.setYRange(rough_min - margin, rough_max + margin, padding=0.0)
    return line


def plot_bjh_selection(
    plot: pg.PlotWidget,
    rows_by_key: dict[tuple[int, str], list[dict[str, float]]],
    colors: list[str],
    diameter_range: tuple[float, float] | None,
    active_index: int = -1,
    differential_mode: str = BJH_DIFFERENTIAL_LOG,
    display_metrics=None,
) -> list:
    metrics = normalize_bjh_display_metrics(display_metrics if display_metrics is not None else [differential_mode])
    if diameter_range is None:
        return []
    lo, hi = sorted((float(diameter_range[0]), float(diameter_range[1])))
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return []
    items = []
    keys = sorted(rows_by_key, key=lambda key: (key[0] == active_index, key[0], key[1]))
    for index, phase in keys:
        rows = rows_by_key.get((index, phase), [])
        color = _analysis_color(colors, index, active_index)
        is_active = index == active_index
        for metric in metrics:
            selected_x = []
            selected_y = []
            for row in rows:
                try:
                    diameter = _bjh_metric_diameter(row, metric)
                    metric_value = _bjh_metric_value(row, metric)
                except (KeyError, TypeError, ValueError):
                    continue
                if not (np.isfinite(diameter) and np.isfinite(metric_value)):
                    continue
                if diameter <= 0.0 or metric_value < 0.0 or diameter < lo or diameter > hi:
                    continue
                selected_x.append(diameter)
                selected_y.append(metric_value)
            if not selected_x:
                continue
            items.append(
                _plot_selected_xy(
                    plot,
                    np.asarray(selected_x, dtype=float),
                    np.asarray(selected_y, dtype=float),
                    color,
                    symbol_size=SELECTED_SYMBOL_SIZE if is_active else DEFAULT_SYMBOL_SIZE,
                    symbol_pen_width=SELECTED_SYMBOL_PEN_WIDTH if is_active else DEFAULT_SYMBOL_PEN_WIDTH,
                )
            )
    return [item for item in items if item is not None]


def plot_hk_selection(
    plot: pg.PlotWidget,
    rows_by_key: dict[tuple[int, str], list[dict[str, float]]],
    colors: list[str],
    width_range: tuple[float, float] | None,
    active_index: int = -1,
    display_metric: str = HK_DIFFERENTIAL_LINEAR,
) -> list:
    metric = normalize_hk_display_metric(display_metric)
    if width_range is None:
        return []
    lo, hi = sorted((float(width_range[0]), float(width_range[1])))
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return []
    items = []
    keys = sorted(rows_by_key, key=lambda key: (key[0] == active_index, key[0], key[1]))
    for index, _phase in keys:
        rows = rows_by_key.get((index, "adsorption"), [])
        color = _analysis_color(colors, index, active_index)
        is_active = index == active_index
        selected_x = []
        selected_y = []
        for row in rows:
            try:
                width = _hk_metric_width(row)
                metric_value = _hk_metric_value(row, metric)
            except (KeyError, TypeError, ValueError):
                continue
            if not (np.isfinite(width) and np.isfinite(metric_value)):
                continue
            if width <= 0.0 or metric_value < 0.0 or width < lo or width > hi:
                continue
            selected_x.append(width)
            selected_y.append(metric_value)
        if not selected_x:
            continue
        items.append(
            _plot_selected_xy(
                plot,
                np.asarray(selected_x, dtype=float),
                np.asarray(selected_y, dtype=float),
                color,
                symbol_size=SELECTED_SYMBOL_SIZE if is_active else DEFAULT_SYMBOL_SIZE,
                symbol_pen_width=SELECTED_SYMBOL_PEN_WIDTH if is_active else DEFAULT_SYMBOL_PEN_WIDTH,
            )
        )
    return [item for item in items if item is not None]


def plot_pore_distribution_placeholder(
    plot: pg.PlotWidget,
    differential_mode: str = BJH_DIFFERENTIAL_LOG,
    display_metrics=None,
) -> None:
    metrics = normalize_bjh_display_metrics(display_metrics if display_metrics is not None else [differential_mode])
    plot.clear()
    plot.setTitle("BJH 孔径分布")
    plot.setLabel("left", bjh_display_axis_label(metrics))
    plot.setLabel("bottom", "孔径 (nm)")
    plot.setLogMode(x=True, y=False)
    _plot_message(plot, "当前没有可显示的 BJH 孔径分布")


def plot_dh_distribution_placeholder(
    plot: pg.PlotWidget,
    display_metrics=None,
) -> None:
    metrics = normalize_bjh_display_metrics(display_metrics)
    plot.clear()
    plot.setTitle("Dollimore-Heal 孔径分布")
    plot.setLabel("left", bjh_display_axis_label(metrics))
    plot.setLabel("bottom", "孔径 (nm)")
    plot.setLogMode(x=True, y=False)
    _plot_message(plot, "当前没有可显示的 DH 孔径分布")


def plot_hk_distribution_placeholder(
    plot: pg.PlotWidget,
    display_metric: str = HK_DIFFERENTIAL_LINEAR,
) -> None:
    metric = normalize_hk_display_metric(display_metric)
    plot.clear()
    plot.setTitle("Horvath-Kawazoe 孔径分布")
    plot.setLabel("left", hk_display_axis_label(metric))
    plot.setLabel("bottom", "孔宽 W (nm)")
    plot.setLogMode(x=True, y=False)
    _plot_message(plot, "当前没有可显示的 HK 孔径分布")


def _bjh_rows_in_pressure_range(rows, pressure_range: tuple[float, float] | None):
    if pressure_range is None:
        return rows
    pressure_min, pressure_max = sorted((float(pressure_range[0]), float(pressure_range[1])))
    filtered = []
    for row in rows:
        if "relative_pressure" in row:
            try:
                pressure = float(row["relative_pressure"])
            except (TypeError, ValueError):
                continue
            if pressure_min <= pressure <= pressure_max:
                filtered.append(row)
            continue
        if "relative_pressure_low" not in row or "relative_pressure_high" not in row:
            filtered.append(row)
            continue
        interval_min = min(float(row["relative_pressure_low"]), float(row["relative_pressure_high"]))
        interval_max = max(float(row["relative_pressure_low"]), float(row["relative_pressure_high"]))
        if interval_max >= pressure_min and interval_min <= pressure_max:
            filtered.append(row)
    return filtered


def _color_with_alpha(color, alpha: int):
    faded = pg.mkColor(color)
    faded.setAlpha(max(0, min(255, int(alpha))))
    return faded


def _plot_dft_isotherm_fit(plot: pg.PlotWidget, fit_rows: list[dict[str, float]] | None):
    if not fit_rows:
        return None, [], []
    x_values = []
    y_values = []
    for row in fit_rows:
        try:
            pressure = float(row["relative_pressure"])
            quantity = float(row["model_quantity_adsorbed_cm3_g_stp"])
        except (KeyError, TypeError, ValueError):
            continue
        if np.isfinite(pressure) and np.isfinite(quantity) and pressure > 0.0 and quantity >= 0.0:
            x_values.append(pressure)
            y_values.append(quantity)
    if not x_values:
        return None, [], []
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    order = np.argsort(x)
    x = x[order]
    y = y[order]
    item = plot.plot(
        x,
        y,
        pen=pg.mkPen("#111827", width=3),
        name=None,
    )
    return item, x.tolist(), y.tolist()


def _plot_points(
    plot: pg.PlotWidget,
    points,
    color,
    name: str | None,
    *,
    solid: bool,
    width: int = DEFAULT_LINE_WIDTH,
    symbol_size: int = DEFAULT_SYMBOL_SIZE,
    symbol_pen_width: int = DEFAULT_SYMBOL_PEN_WIDTH,
    filled: bool | None = None,
):
    if not points:
        return None
    if filled is None:
        filled = solid
    x = np.asarray([float(point.relative_pressure) for point in points], dtype=float)
    y = np.asarray([float(point.quantity_adsorbed_cm3_g_stp or 0.0) for point in points], dtype=float)
    pen = pg.mkPen(color, width=width)
    if not solid:
        pen.setStyle(QtCore.Qt.DashLine)
    return plot.plot(
        x,
        y,
        pen=pen,
        symbol="o",
        symbolSize=symbol_size,
        symbolPen=pg.mkPen(color, width=symbol_pen_width),
        symbolBrush=pg.mkBrush(color if filled else "#ffffff"),
        name=name,
    )


def _plot_adsorption_desorption_bridge(
    plot: pg.PlotWidget,
    adsorption,
    desorption,
    color: str,
    *,
    width: int = 2,
) -> None:
    if not adsorption or not desorption:
        return
    adsorption_end = adsorption[-1]
    desorption_start = max(desorption, key=lambda point: float(point.relative_pressure))
    x = np.asarray(
        [float(adsorption_end.relative_pressure), float(desorption_start.relative_pressure)],
        dtype=float,
    )
    y = np.asarray(
        [
            float(adsorption_end.quantity_adsorbed_cm3_g_stp or 0.0),
            float(desorption_start.quantity_adsorbed_cm3_g_stp or 0.0),
        ],
        dtype=float,
    )
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        return
    pen = pg.mkPen(color, width=width)
    pen.setStyle(QtCore.Qt.DashLine)
    plot.plot(x, y, pen=pen, name=None)


def _analysis_draw_order(results, visible: list[bool], active_index: int) -> list[int]:
    draw_order = [i for i in range(len(results)) if i != active_index]
    if 0 <= active_index < len(results):
        draw_order.append(active_index)
    return [i for i in draw_order if i < len(visible) and visible[i]]


def _analysis_color(colors: list[str], index: int, active_index: int) -> str:
    return colors[index % len(colors)] if colors else "#2563eb"


def _clear_manual_legend_entries(plot: pg.PlotWidget) -> None:
    setattr(plot, "_manual_sample_legend_entries", [])
    legend = getattr(plot.plotItem, "legend", None)
    if legend is not None:
        legend.clear()
    _sync_plot_legend_visibility(plot)


def _append_sample_legend_entry(plot: pg.PlotWidget, index: int, item, name: str | None) -> None:
    if item is None or not name:
        return
    entries = list(getattr(plot, "_manual_sample_legend_entries", []))
    entries.append((index, item, name))
    setattr(plot, "_manual_sample_legend_entries", entries)
    _set_sample_legend_entries(plot, entries)


def _set_sample_legend_entries(plot: pg.PlotWidget, entries) -> None:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return
    legend.clear()
    for _index, item, name in sorted(entries, key=lambda entry: entry[0]):
        legend.addItem(item, name)
    _apply_default_legend_position(plot)
    _sync_plot_legend_visibility(plot)


def _plot_analysis_xy(
    plot: pg.PlotWidget,
    x: np.ndarray,
    y: np.ndarray,
    color: str,
    name: str | None,
    is_active: bool,
):
    return plot.plot(
        x,
        y,
        pen=None,
        symbol="o",
        symbolSize=ACTIVE_SYMBOL_SIZE if is_active else DEFAULT_SYMBOL_SIZE,
        symbolPen=pg.mkPen(color, width=ACTIVE_SYMBOL_PEN_WIDTH if is_active else DEFAULT_SYMBOL_PEN_WIDTH),
        symbolBrush=pg.mkBrush("#ffffff"),
        name=None,
    )


def _plot_selected_isotherm_points(
    plot: pg.PlotWidget,
    points,
    color: str,
    pressure_min: float,
    pressure_max: float,
):
    selected_x = []
    selected_y = []
    for point in points:
        try:
            pressure = float(point.relative_pressure)
            quantity = float(point.quantity_adsorbed_cm3_g_stp or 0.0)
        except (TypeError, ValueError):
            continue
        if np.isfinite(pressure) and np.isfinite(quantity) and pressure_min <= pressure <= pressure_max:
            selected_x.append(pressure)
            selected_y.append(quantity)
    if not selected_x:
        return None
    return _plot_selected_xy(
        plot,
        np.asarray(selected_x, dtype=float),
        np.asarray(selected_y, dtype=float),
        color,
    )


def _plot_selected_xy(
    plot: pg.PlotWidget,
    x: np.ndarray,
    y: np.ndarray,
    color: str,
    *,
    symbol_size: int = SELECTED_SYMBOL_SIZE,
    symbol_pen_width: int = SELECTED_SYMBOL_PEN_WIDTH,
):
    return plot.plot(
        x,
        y,
        pen=None,
        symbol="o",
        symbolSize=symbol_size,
        symbolPen=pg.mkPen(color, width=symbol_pen_width),
        symbolBrush=pg.mkBrush(color),
    )


def _legend_name(result) -> str:
    return Path(str(result.file_name or result.sample_name or "样品")).stem


def _plot_fit_line(plot: pg.PlotWidget, analysis: FitResult, x_values: np.ndarray, color: str) -> None:
    if not analysis.ok or analysis.slope is None or analysis.intercept is None or x_values.size == 0:
        return
    x_min = float(np.nanmin(x_values))
    x_max = float(np.nanmax(x_values))
    line_x = np.linspace(x_min, x_max, 120)
    line_y = analysis.slope * line_x + analysis.intercept
    plot.plot(line_x, line_y, pen=pg.mkPen(color, width=ACTIVE_LINE_WIDTH), name="线性拟合")


def _plot_message(plot: pg.PlotWidget, text: str) -> None:
    item = pg.TextItem(text=text, color="#374151", anchor=(0.5, 0.5))
    item.setPos(0.5, 0.5)
    plot.addItem(item)
    plot.setXRange(0.0, 1.0)
    plot.setYRange(0.0, 1.0)


def _range_text(analysis: FitResult, fallback: str) -> str:
    if analysis.pressure_min is None or analysis.pressure_max is None:
        return fallback
    return f"当前区间 {_plain_number(analysis.pressure_min)}-{_plain_number(analysis.pressure_max)}"


def _fit_range(plot: pg.PlotWidget, x_values, y_values, *, x_log: bool = False) -> None:
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if x_log:
        mask &= x > 0.0
    if not np.any(mask):
        return
    x = x[mask]
    y = y[mask]
    if x_log:
        x = np.log10(x)
    x_min = float(np.nanmin(x))
    x_max = float(np.nanmax(x))
    y_min = float(np.nanmin(y))
    y_max = float(np.nanmax(y))
    if x_min == x_max:
        x_min -= 0.01
        x_max += 0.01
    if y_min == y_max:
        y_min -= 0.01
        y_max += 0.01
    plot.setXRange(x_min, x_max, padding=0.06)
    plot.setYRange(y_min, y_max, padding=0.10)


def _plain_number(value: float) -> str:
    if not np.isfinite(value):
        return ""
    abs_value = abs(value)
    if abs_value >= 100:
        return f"{value:,.0f}"
    if abs_value >= 10:
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    if abs_value >= 1:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if abs_value >= 0.01:
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if abs_value == 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".")
