from __future__ import annotations

import os
import math
import sys
from pathlib import Path
from typing import Iterable

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from tristar_bet import TriStarParseError, load_smp
from tristar_bet.analysis import (
    DEFAULT_THICKNESS_METHOD,
    THICKNESS_METHOD_DEFAULT_PARAMS,
    analysis_bundle,
    bet_analysis,
    bjh_pore_volume_cm3_g,
    density_conversion_factor,
    langmuir_analysis,
    t_plot_analysis_by_thickness,
    thickness_nm,
)
from tristar_bet.ui.plots import (
    DEFAULT_COLORS,
    make_plot,
    plot_bet_multi,
    plot_bet_selection,
    plot_isotherm_multi,
    plot_isotherm_selection,
    plot_bjh_distribution_multi,
    plot_langmuir_points_multi,
    plot_langmuir_selection,
    plot_pore_distribution_placeholder,
    plot_t_plot_points_multi,
    plot_t_plot_selection,
    replace_bet_fit_line,
    replace_langmuir_fit_line,
    replace_t_plot_fit_line,
)


APP_NAME = "TriStar II 3020 BET 综合分析"
Signal = getattr(QtCore, "Signal", None) or getattr(QtCore, "pyqtSignal")


class SelectAllCheckBox(QtWidgets.QCheckBox):
    def nextCheckState(self) -> None:
        if self.checkState() == QtCore.Qt.Checked:
            self.setCheckState(QtCore.Qt.Unchecked)
        else:
            self.setCheckState(QtCore.Qt.Checked)


class SampleTableWidget(QtWidgets.QTableWidget):
    rowMoveRequested = Signal(int, int)
    smpFilesDropped = Signal(list)
    LONG_PRESS_MS = 220
    FROZEN_COLUMN_COUNT = 2

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self._syncing_frozen_columns = False
        self._drag_source_row = -1
        self._drag_start_pos = QtCore.QPoint()
        self._drag_timer = QtCore.QElapsedTimer()
        self._dragging_row = False
        self._drop_indicator = QtWidgets.QFrame(self.viewport())
        self._drop_indicator.setFixedHeight(2)
        self._drop_indicator.setStyleSheet("background: #2563eb;")
        self._drop_indicator.hide()
        self._init_frozen_columns()

    def frozen_header(self):
        return self._frozen_table.horizontalHeader()

    def _init_frozen_columns(self) -> None:
        self._frozen_table = QtWidgets.QTableView(self)
        self._frozen_table.setModel(self.model())
        self._frozen_table.setSelectionModel(self.selectionModel())
        self._frozen_table.setFocusPolicy(QtCore.Qt.NoFocus)
        self._frozen_table.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._frozen_table.setShowGrid(False)
        self._frozen_table.setAlternatingRowColors(False)
        self._frozen_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._frozen_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._frozen_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._frozen_table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self._frozen_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._frozen_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._frozen_table.setStyleSheet(
            """
            QTableView {
                border: 0;
                background: #ffffff;
                alternate-background-color: #ffffff;
            }
            QTableView::item:selected {
                background: #e0ecff;
            }
            QTableView::item:focus {
                outline: none;
            }
            QTableView::indicator {
                width: 11px;
                height: 11px;
                border-radius: 6px;
                border: 1px solid #6b7280;
                background: white;
            }
            QTableView::indicator:checked {
                border: 1px solid #2563eb;
                background: #2563eb;
            }
            QHeaderView::section {
                background: #f9fafb;
                border: 0;
                border-right: 1px solid #d1d5db;
                border-bottom: 1px solid #d1d5db;
                color: #374151;
                font-weight: 600;
                padding: 4px 8px 4px 6px;
            }
            """
        )
        self._frozen_table.verticalHeader().hide()
        self._frozen_table.verticalHeader().setDefaultSectionSize(self.verticalHeader().defaultSectionSize())
        frozen_header = self._frozen_table.horizontalHeader()
        frozen_header.setSectionsMovable(False)
        frozen_header.setHighlightSections(False)
        frozen_header.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        frozen_header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self._frozen_table.viewport().installEventFilter(self)

        for column in range(self.model().columnCount()):
            self._frozen_table.setColumnHidden(column, column >= self.FROZEN_COLUMN_COUNT)

        self.horizontalHeader().sectionResized.connect(self._on_main_section_resized)
        frozen_header.sectionResized.connect(self._on_frozen_section_resized)
        self.verticalHeader().sectionResized.connect(self._on_main_row_resized)
        self.verticalScrollBar().valueChanged.connect(self._frozen_table.verticalScrollBar().setValue)
        self._frozen_table.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        self._frozen_table.show()
        self.sync_frozen_row_heights()
        self._update_frozen_geometry()

    def setRowCount(self, rows: int) -> None:
        super().setRowCount(rows)
        self.sync_frozen_row_heights()

    def sync_frozen_row_heights(self) -> None:
        if not hasattr(self, "_frozen_table"):
            return
        self._frozen_table.verticalHeader().setDefaultSectionSize(self.verticalHeader().defaultSectionSize())
        for row in range(self.rowCount()):
            self._frozen_table.setRowHeight(row, self.rowHeight(row))

    def eventFilter(self, obj, event) -> bool:
        frozen_table = getattr(self, "_frozen_table", None)
        if frozen_table is not None and obj is frozen_table.viewport():
            if event.type() == QtCore.QEvent.ContextMenu:
                row = self._frozen_table.rowAt(event.pos().y())
                if row >= 0:
                    self.selectRow(row)
                    self.customContextMenuRequested.emit(QtCore.QPoint(0, event.pos().y()))
                    return True
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                self._begin_row_drag(self._frozen_to_main_viewport_pos(event.pos()))
                return False
            if event.type() == QtCore.QEvent.MouseMove:
                if self._update_row_drag(self._frozen_to_main_viewport_pos(event.pos()), event.buttons()):
                    return True
            if event.type() == QtCore.QEvent.MouseButtonRelease:
                if self._finish_row_drag(self._frozen_to_main_viewport_pos(event.pos()), event.button()):
                    return True
                if event.button() == QtCore.Qt.LeftButton:
                    self._reset_row_drag()
                return False
        return super().eventFilter(obj, event)

    def _frozen_to_main_viewport_pos(self, position: QtCore.QPoint) -> QtCore.QPoint:
        return self.viewport().mapFromGlobal(self._frozen_table.viewport().mapToGlobal(position))

    def scrollTo(self, index, hint=QtWidgets.QAbstractItemView.EnsureVisible) -> None:
        if index.isValid():
            horizontal_value = self.horizontalScrollBar().value()
            super().scrollTo(index, hint)
            if index.column() < self.FROZEN_COLUMN_COUNT:
                self.horizontalScrollBar().setValue(horizontal_value)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_frozen_geometry()

    def setColumnWidth(self, column: int, width: int) -> None:
        super().setColumnWidth(column, width)
        if hasattr(self, "_frozen_table") and column < self.FROZEN_COLUMN_COUNT:
            self._frozen_table.setColumnWidth(column, width)
            self._update_frozen_geometry()

    def setRowHeight(self, row: int, height: int) -> None:
        super().setRowHeight(row, height)
        if hasattr(self, "_frozen_table"):
            self._frozen_table.setRowHeight(row, height)

    def _on_main_section_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        if logical_index >= self.FROZEN_COLUMN_COUNT or self._syncing_frozen_columns:
            self._update_frozen_geometry()
            return
        self._syncing_frozen_columns = True
        try:
            self._frozen_table.setColumnWidth(logical_index, new_size)
        finally:
            self._syncing_frozen_columns = False
        self._update_frozen_geometry()

    def _on_frozen_section_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        if logical_index >= self.FROZEN_COLUMN_COUNT or self._syncing_frozen_columns:
            return
        self._syncing_frozen_columns = True
        try:
            super().setColumnWidth(logical_index, new_size)
        finally:
            self._syncing_frozen_columns = False
        self._update_frozen_geometry()

    def _on_main_row_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        self._frozen_table.setRowHeight(logical_index, new_size)

    def _frozen_width(self) -> int:
        return sum(self.columnWidth(column) for column in range(self.FROZEN_COLUMN_COUNT))

    def _update_frozen_geometry(self) -> None:
        if not hasattr(self, "_frozen_table"):
            return
        width = self._frozen_width()
        self._frozen_table.setGeometry(
            self.frameWidth(),
            self.frameWidth(),
            width,
            self.viewport().height() + self.horizontalHeader().height(),
        )
        self._frozen_table.raise_()

    def mousePressEvent(self, event) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._begin_row_drag(event.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._update_row_drag(event.pos(), event.buttons()):
            super().mouseMoveEvent(event)
            return
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._finish_row_drag(event.pos(), event.button()):
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            self._reset_row_drag()
        super().mouseReleaseEvent(event)

    def _begin_row_drag(self, position: QtCore.QPoint) -> None:
        row = self.rowAt(position.y())
        if row >= 0:
            self._drag_source_row = row
            self._drag_start_pos = position
            self._drag_timer.start()
            self._dragging_row = False
        else:
            self._reset_row_drag()

    def _update_row_drag(self, position: QtCore.QPoint, buttons) -> bool:
        if not (buttons & QtCore.Qt.LeftButton) or self._drag_source_row < 0:
            return False
        distance = (position - self._drag_start_pos).manhattanLength()
        if not self._dragging_row:
            if self._drag_timer.elapsed() < self.LONG_PRESS_MS or distance < QtWidgets.QApplication.startDragDistance():
                return True
            self._dragging_row = True
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            self._frozen_table.setCursor(QtCore.Qt.ClosedHandCursor)

        insert_row = self._drop_insert_row(position)
        self._show_drop_indicator(insert_row)
        self._auto_scroll(position)
        return True

    def _finish_row_drag(self, position: QtCore.QPoint, button) -> bool:
        if button != QtCore.Qt.LeftButton or not self._dragging_row:
            return False
        source_row = self._drag_source_row
        insert_row = self._drop_insert_row(position)
        self._reset_row_drag()
        if source_row >= 0:
            self.rowMoveRequested.emit(source_row, insert_row)
        return True

    def leaveEvent(self, event) -> None:
        if not self._dragging_row:
            self._drop_indicator.hide()
        super().leaveEvent(event)

    def _reset_row_drag(self) -> None:
        if self._dragging_row:
            self.unsetCursor()
            self._frozen_table.unsetCursor()
        self._drag_source_row = -1
        self._dragging_row = False
        self._drop_indicator.hide()

    def _drop_insert_row(self, position: QtCore.QPoint) -> int:
        row_count = self.rowCount()
        if row_count == 0:
            return 0
        row = self.rowAt(position.y())
        if row < 0:
            return 0 if position.y() < 0 else row_count
        midpoint = self.rowViewportPosition(row) + self.rowHeight(row) / 2
        return row if position.y() < midpoint else row + 1

    def _show_drop_indicator(self, insert_row: int) -> None:
        row_count = self.rowCount()
        if row_count == 0:
            self._drop_indicator.hide()
            return
        if insert_row <= 0:
            y = self.rowViewportPosition(0)
        elif insert_row >= row_count:
            last_row = row_count - 1
            y = self.rowViewportPosition(last_row) + self.rowHeight(last_row)
        else:
            y = self.rowViewportPosition(insert_row)
        self._drop_indicator.setGeometry(0, max(0, int(y) - 1), self.viewport().width(), 2)
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    def _auto_scroll(self, position: QtCore.QPoint) -> None:
        margin = 24
        step = 18
        scroll_bar = self.verticalScrollBar()
        if position.y() < margin:
            scroll_bar.setValue(scroll_bar.value() - step)
        elif position.y() > self.viewport().height() - margin:
            scroll_bar.setValue(scroll_bar.value() + step)

    def dragEnterEvent(self, event) -> None:
        paths = self._smp_paths_from_mime_data(event.mimeData())
        if paths:
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        paths = self._smp_paths_from_mime_data(event.mimeData())
        if paths:
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._smp_paths_from_mime_data(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.smpFilesDropped.emit(paths)
            return
        super().dropEvent(event)

    @staticmethod
    def _smp_paths_from_mime_data(mime_data) -> list[str]:
        if not mime_data.hasUrls():
            return []
        paths = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() == ".smp":
                paths.append(str(path))
        return paths


class _NoFocusDelegate(QtWidgets.QStyledItemDelegate):
    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.state &= ~QtWidgets.QStyle.State_HasFocus
        foreground = index.data(QtCore.Qt.ForegroundRole)
        color = foreground.color() if isinstance(foreground, QtGui.QBrush) else QtGui.QColor("#111827")
        option.palette.setColor(QtGui.QPalette.HighlightedText, color)


class _FrozenColumnsDelegate(_NoFocusDelegate):
    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)


def _check_state_value(state) -> int:
    return int(getattr(state, "value", state))


VISIBLE_COLUMN = 0
FILE_COLUMN = 1
TEST_TIME_COLUMN = 2
BET_COLUMN = 3
LANGMUIR_COLUMN = 4
T_PLOT_COLUMN = 5
BJH_PORE_VOLUME_COLUMN = 6
BET_DEFAULT_RANGE = (0.05, 0.30)
BET_PLOT_RANGE = (0.0, 1.0)
LANGMUIR_DEFAULT_RANGE = (0.05, 0.30)
LANGMUIR_PLOT_RANGE = (0.0, 1.0)
T_PLOT_DEFAULT_PRESSURE_RANGE = (0.20, 0.50)
T_PLOT_PLOT_RANGE = (0.0, 1.0)
CM3_STP_PER_MMOL = 22.414
SURFACE_AREA_CORRECTION_FACTOR = 1.0
CUSTOM_BET_COLOR = "#2563eb"
T_PLOT_THICKNESS_METHOD_LABELS = {
    "kjs": "Kruk-Jaroniec-Sayari",
    "halsey": "Halsey",
    "harkins_jura": "Harkins-Jura",
    "broekhoff_de_boer": "Broekhoff-De Boer",
    "carbon_black_stsa": "碳黑STSA",
}
T_PLOT_THICKNESS_PARAM_DEFAULTS = {
    method: {key: value for key, value in params.items() if key != "scale"}
    for method, params in THICKNESS_METHOD_DEFAULT_PARAMS.items()
}
DEFAULT_T_PLOT_THICKNESS_METHOD = DEFAULT_THICKNESS_METHOD
DEFAULT_T_PLOT_THICKNESS_PARAMS = dict(T_PLOT_THICKNESS_PARAM_DEFAULTS[DEFAULT_T_PLOT_THICKNESS_METHOD])
DEFAULT_T_PLOT_SURFACE_AREA_MODE = "BET"
DEFAULT_T_PLOT_SURFACE_AREA_INPUT = 1.0
DEFAULT_T_PLOT_SURFACE_AREA_CORRECTION = 1.0
DEFAULT_BJH_CORRECTION = "standard"
DEFAULT_BJH_OPEN_PORE_FRACTION = 0.0
DEFAULT_BJH_SMOOTH_DERIVATIVE = True
DEFAULT_BJH_SHOW_ADSORPTION = True
DEFAULT_BJH_SHOW_DESORPTION = False
T_PLOT_PANEL_COLLAPSED_WIDTH = 360
T_PLOT_PANEL_EXPANDED_WIDTH = 660
BJH_PANEL_COLLAPSED_WIDTH = 380
BJH_PANEL_EXPANDED_WIDTH = 660
REGION_LINE_COLOR = "#2563eb"
REGION_LINE_HOVER_COLOR = "#dc2626"
REGION_FILL_COLOR = (37, 99, 235, 34)
REGION_FILL_HOVER_COLOR = (37, 99, 235, 48)


def _region_pen(color: str) -> QtGui.QPen:
    pen = pg.mkPen(color, width=3)
    pen.setStyle(QtCore.Qt.DashLine)
    return pen


def _default_t_plot_thickness_params_by_method() -> dict[str, dict[str, float]]:
    return {method: dict(params) for method, params in T_PLOT_THICKNESS_PARAM_DEFAULTS.items()}


def _t_plot_thickness_label(method: str) -> str:
    return T_PLOT_THICKNESS_METHOD_LABELS.get(method, T_PLOT_THICKNESS_METHOD_LABELS[DEFAULT_T_PLOT_THICKNESS_METHOD])


def _float_equal(left: object, right: object, *, tol: float = 1e-9) -> bool:
    try:
        return abs(float(left) - float(right)) <= tol
    except (TypeError, ValueError):
        return False


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.results = []
        self.visible_results: list[bool] = []
        self.active_index = -1
        self.sample_colors = list(DEFAULT_COLORS)
        self.sample_items = []
        self.custom_bet_fit_ranges: dict[int, tuple[float, float]] = {}
        self.custom_langmuir_fit_ranges: dict[int, tuple[float, float]] = {}
        self.custom_t_plot_fit_ranges: dict[int, tuple[float, float]] = {}
        self.custom_t_plot_settings: dict[int, dict[str, object]] = {}
        self.custom_bjh_settings: dict[int, dict[str, object]] = {}
        self._updating_table = False
        self._updating_sample_checks = False
        self._updating_sample_column_widths = False
        self._sample_column_widths_initialized = False
        self.sample_column_widths: dict[int, int] = {}
        self.test_time_sort_ascending = False
        self.bet_sort_ascending = False
        self.langmuir_sort_ascending = False
        self.t_plot_sort_ascending = False
        self.bjh_pore_sort_ascending = False
        self.region = None
        self._isotherm_selection_items = []
        self.bet_region = None
        self._bet_fit_line = None
        self._bet_selection_item = None
        self._bet_x_range = None
        self._bet_plot_p_range = None
        self.langmuir_region = None
        self._langmuir_fit_line = None
        self._langmuir_selection_item = None
        self._langmuir_x_range = None
        self._langmuir_plot_p_range = None
        self.t_plot_region = None
        self._t_plot_fit_line = None
        self._t_plot_selection_item = None
        self._t_plot_x_range = None
        self._t_plot_p_range = None
        self._syncing_t_plot_controls = False
        self.t_plot_thickness_method = DEFAULT_T_PLOT_THICKNESS_METHOD
        self.t_plot_thickness_params_by_method = _default_t_plot_thickness_params_by_method()
        self.t_plot_thickness_params = dict(DEFAULT_T_PLOT_THICKNESS_PARAMS)
        self.t_plot_surface_area_mode = DEFAULT_T_PLOT_SURFACE_AREA_MODE
        self.t_plot_surface_area_input = DEFAULT_T_PLOT_SURFACE_AREA_INPUT
        self.t_plot_surface_area_correction = DEFAULT_T_PLOT_SURFACE_AREA_CORRECTION
        self._syncing_bjh_controls = False
        self.bjh_thickness_method = DEFAULT_T_PLOT_THICKNESS_METHOD
        self.bjh_thickness_params_by_method = _default_t_plot_thickness_params_by_method()
        self.bjh_thickness_params = dict(DEFAULT_T_PLOT_THICKNESS_PARAMS)
        self.bjh_correction = DEFAULT_BJH_CORRECTION
        self.bjh_open_pore_fraction = DEFAULT_BJH_OPEN_PORE_FRACTION
        self.bjh_smooth_derivative = DEFAULT_BJH_SMOOTH_DERIVATIVE
        self.bjh_show_adsorption = DEFAULT_BJH_SHOW_ADSORPTION
        self.bjh_show_desorption = DEFAULT_BJH_SHOW_DESORPTION
        self.region_is_log = False
        self._isotherm_region_custom = False
        self._setting_isotherm_region = False
        self._metrics_pending = False
        self._bet_region_pending = False
        self._langmuir_region_pending = False
        self._t_plot_region_pending = False
        self._syncing_region_changes = False
        self._setting_bet_region = False
        self._setting_langmuir_region = False
        self._setting_t_plot_region = False

        self.setWindowTitle(APP_NAME)
        self.resize(1280, 780)

        open_button = QtWidgets.QPushButton("打开 SMP")
        open_button.clicked.connect(self.open_files)
        add_button = QtWidgets.QPushButton("添加 SMP")
        add_button.clicked.connect(self.add_files)
        export_button = QtWidgets.QPushButton("导出 XLSX")
        export_button.clicked.connect(self.export_xlsx)
        for button in (open_button, add_button, export_button):
            button.setMinimumHeight(32)

        self.select_all_check = SelectAllCheckBox()
        self.select_all_check.setTristate(True)
        self.select_all_check.setCheckState(QtCore.Qt.Checked)
        self.select_all_check.setCursor(QtCore.Qt.PointingHandCursor)
        self.select_all_check.setToolTip("显示或隐藏全部样品")
        self.select_all_check.stateChanged.connect(self.on_select_all_changed)
        self.select_all_check.setStyleSheet(
            """
            QCheckBox::indicator {
                width: 12px;
                height: 12px;
                border-radius: 7px;
                border: 1px solid #6b7280;
                background: white;
            }
            QCheckBox::indicator:checked {
                border: 1px solid #2563eb;
                background: #2563eb;
            }
            QCheckBox::indicator:indeterminate {
                border: 1px solid #2563eb;
                background: #93c5fd;
            }
            """
        )

        self.sample_table = SampleTableWidget(0, 7)
        self.sample_table.setHorizontalHeaderLabels(
            ["", "文件名", "测试时间", "BET(m2/g)", "Langmuir(m2/g)", "t-Plot外比(m2/g)", "2-10nm孔容量(cm3/g)"]
        )
        sample_header = self.sample_table.horizontalHeader()
        sample_header.setVisible(True)
        sample_header.setSectionsMovable(False)
        sample_header.setHighlightSections(False)
        sample_header.setStretchLastSection(False)
        sample_header.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        sample_header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        sample_header.sectionClicked.connect(self.on_sample_header_clicked)
        sample_header.sectionResized.connect(self.on_sample_header_resized)
        self.sample_table.horizontalHeaderItem(TEST_TIME_COLUMN).setToolTip("点击按测试时间排序")
        self.sample_table.horizontalHeaderItem(BET_COLUMN).setToolTip("点击按BET比表面积排序")
        self.sample_table.horizontalHeaderItem(BET_COLUMN).setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.sample_table.horizontalHeaderItem(LANGMUIR_COLUMN).setToolTip("点击按Langmuir比表面积排序")
        self.sample_table.horizontalHeaderItem(LANGMUIR_COLUMN).setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.sample_table.horizontalHeaderItem(T_PLOT_COLUMN).setToolTip("点击按t-Plot外比表面积排序")
        self.sample_table.horizontalHeaderItem(T_PLOT_COLUMN).setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.sample_table.horizontalHeaderItem(BJH_PORE_VOLUME_COLUMN).setToolTip("点击按 BJH 2-10 nm 孔容量排序")
        self.sample_table.horizontalHeaderItem(BJH_PORE_VOLUME_COLUMN).setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.sample_table.verticalHeader().setVisible(False)
        self.sample_table.verticalHeader().setDefaultSectionSize(28)
        self.sample_table.sync_frozen_row_heights()
        self.sample_table.setShowGrid(False)
        self.sample_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.sample_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.sample_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.sample_table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.sample_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.sample_table.setMinimumHeight(70)
        self.sample_table.setColumnWidth(VISIBLE_COLUMN, 30)
        self.sample_table.setColumnWidth(FILE_COLUMN, 170)
        self.sample_table.setColumnWidth(TEST_TIME_COLUMN, 250)
        self.sample_table.setColumnWidth(BET_COLUMN, 120)
        self.sample_table.setColumnWidth(LANGMUIR_COLUMN, 200)
        self.sample_table.setColumnWidth(T_PLOT_COLUMN, 200)
        self.sample_table.setColumnWidth(BJH_PORE_VOLUME_COLUMN, 190)
        self.sample_table.setItemDelegate(_NoFocusDelegate(self.sample_table))
        self.sample_table._frozen_table.setItemDelegate(_FrozenColumnsDelegate(self.sample_table._frozen_table))
        self.sample_table.itemChanged.connect(self.on_sample_item_changed)
        self.sample_table.currentCellChanged.connect(self.on_active_cell_changed)
        self.sample_table.rowMoveRequested.connect(self.move_sample_row)
        self.sample_table.smpFilesDropped.connect(self.append_files)
        self.sample_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.sample_table.customContextMenuRequested.connect(self.show_sample_context_menu)
        self.sample_table.horizontalScrollBar().valueChanged.connect(self._position_header_controls)
        self.sample_table.setStyleSheet(
            """
            QTableWidget {
                border: 1px solid #d1d5db;
                background: #ffffff;
            }
            QTableWidget::item:selected {
                background: #e0ecff;
            }
            QTableWidget::item:focus {
                outline: none;
            }
            QTableWidget::indicator {
                width: 11px;
                height: 11px;
                border-radius: 6px;
                border: 1px solid #6b7280;
                background: white;
            }
            QTableWidget::indicator:checked {
                border: 1px solid #2563eb;
                background: #2563eb;
            }
            QHeaderView::section {
                background: #f9fafb;
                border: 0;
                border-right: 1px solid #d1d5db;
                border-bottom: 1px solid #d1d5db;
                color: #374151;
                font-weight: 600;
                padding: 4px 8px 4px 6px;
            }
            """
        )
        self.select_all_check.setParent(self.sample_table.frozen_header())
        self.select_all_check.show()

        self.metrics_table = self._make_table(["参数", "值"])

        self.isotherm_plot = make_plot("吸附/脱附等温线", "吸附量 (cm3/g STP)", "相对压力 (P/P0)")
        self.bet_plot = make_plot("BET 拟合", "P/[V(P0-P)]", "相对压力 (P/P0)")
        self.langmuir_plot = make_plot("Langmuir 拟合", "(P/P0) / V", "相对压力 (P/P0)")
        self.t_plot = make_plot("t-Plot", "液体体积 (cm3/g)", "统计膜厚 t (nm)")
        self.pore_plot = make_plot("BJH 孔径分布", "dV/dlogD (cm3/g)", "孔径 (nm)")

        self.bet_default_button = QtWidgets.QPushButton("默认")
        self.bet_default_button.setToolTip("按默认 BET 区间 0.05-0.30 重新计算")
        self.bet_default_button.setMinimumWidth(76)
        self.bet_default_button.clicked.connect(self.reset_bet_fit_to_default)
        bet_controls = QtWidgets.QHBoxLayout()
        bet_controls.setContentsMargins(6, 2, 6, 6)
        bet_controls.addWidget(self.bet_default_button)
        bet_controls.addStretch(1)
        self.bet_tab = QtWidgets.QWidget()
        bet_tab_layout = QtWidgets.QVBoxLayout(self.bet_tab)
        bet_tab_layout.setContentsMargins(0, 0, 0, 0)
        bet_tab_layout.setSpacing(0)
        bet_tab_layout.addWidget(self.bet_plot, 1)
        bet_tab_layout.addLayout(bet_controls)

        self.langmuir_default_button = QtWidgets.QPushButton("默认")
        self.langmuir_default_button.setToolTip("按默认 Langmuir 区间 0.05-0.30 重新计算")
        self.langmuir_default_button.setMinimumWidth(76)
        self.langmuir_default_button.clicked.connect(self.reset_langmuir_fit_to_default)
        langmuir_controls = QtWidgets.QHBoxLayout()
        langmuir_controls.setContentsMargins(6, 2, 6, 6)
        langmuir_controls.addWidget(self.langmuir_default_button)
        langmuir_controls.addStretch(1)
        self.langmuir_tab = QtWidgets.QWidget()
        langmuir_tab_layout = QtWidgets.QVBoxLayout(self.langmuir_tab)
        langmuir_tab_layout.setContentsMargins(0, 0, 0, 0)
        langmuir_tab_layout.setSpacing(0)
        langmuir_tab_layout.addWidget(self.langmuir_plot, 1)
        langmuir_tab_layout.addLayout(langmuir_controls)

        t_plot_plot_panel = QtWidgets.QWidget()
        t_plot_plot_layout = QtWidgets.QVBoxLayout(t_plot_plot_panel)
        t_plot_plot_layout.setContentsMargins(0, 0, 0, 0)
        t_plot_plot_layout.setSpacing(0)
        t_plot_plot_layout.addWidget(self.t_plot, 1)
        self.t_plot_options_panel = self._make_t_plot_options_panel()
        self.t_plot_tab = QtWidgets.QWidget()
        t_plot_tab_layout = QtWidgets.QHBoxLayout(self.t_plot_tab)
        t_plot_tab_layout.setContentsMargins(0, 0, 0, 0)
        t_plot_tab_layout.setSpacing(0)
        t_plot_tab_layout.addWidget(self.t_plot_options_panel)
        t_plot_tab_layout.addWidget(t_plot_plot_panel, 1)

        bjh_plot_panel = QtWidgets.QWidget()
        bjh_plot_layout = QtWidgets.QVBoxLayout(bjh_plot_panel)
        bjh_plot_layout.setContentsMargins(0, 0, 0, 0)
        bjh_plot_layout.setSpacing(0)
        bjh_plot_layout.addWidget(self.pore_plot, 1)
        self.bjh_options_panel = self._make_bjh_options_panel()
        self.bjh_tab = QtWidgets.QWidget()
        bjh_tab_layout = QtWidgets.QHBoxLayout(self.bjh_tab)
        bjh_tab_layout.setContentsMargins(0, 0, 0, 0)
        bjh_tab_layout.setSpacing(0)
        bjh_tab_layout.addWidget(self.bjh_options_panel)
        bjh_tab_layout.addWidget(bjh_plot_panel, 1)

        self.plot_tabs = QtWidgets.QTabWidget()
        self.plot_tabs.addTab(self.bet_tab, "BET")
        self.plot_tabs.addTab(self.langmuir_tab, "Langmuir")
        self.plot_tabs.addTab(self.t_plot_tab, "t-Plot")
        self.plot_tabs.addTab(self.bjh_tab, "BJH")
        self.plot_tabs.currentChanged.connect(self.on_plot_tab_changed)

        self.isotherm_table = self._make_table(
            [
                "#",
                "阶段",
                "P/P0",
                "压力(mmHg)",
                "吸附量(cm3/g STP)",
                "吸附量(mmol/g)",
                "Po(mmHg)",
                "Elapsed",
            ]
        )
        self.target_table = self._make_table(["行", "阶段", "起始P/P0", "终止P/P0", "步长P/P0", "偏移"])
        self.condition_table = self._make_table(["字段", "值"])
        self.log_table = self._make_table(["来源", "偏移", "文本"])
        self.report_module_table = self._make_table(["SUBSET", "偏移", "文本"])

        self.detail_tabs = QtWidgets.QTabWidget()
        self.detail_tabs.addTab(self.metrics_table, "结果参数")
        self.detail_tabs.addTab(self.condition_table, "样品/条件")
        self.detail_tabs.addTab(self.isotherm_table, "实际等温线")
        self.detail_tabs.addTab(self.target_table, "目标压力表")
        self.detail_tabs.addTab(self.report_module_table, "报告模块")
        self.detail_tabs.addTab(self.log_table, "日志/样品管")

        sample_panel = QtWidgets.QWidget()
        sample_panel_layout = QtWidgets.QVBoxLayout(sample_panel)
        sample_panel_layout.setContentsMargins(0, 0, 0, 0)
        sample_panel_layout.setSpacing(0)
        sample_panel_layout.addWidget(self.sample_table, 1)

        self.left_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.left_splitter.addWidget(sample_panel)
        self.left_splitter.addWidget(self.detail_tabs)
        self.left_splitter.setChildrenCollapsible(False)
        self.left_splitter.setHandleWidth(8)
        self.left_splitter.setSizes([190, 520])
        self.left_splitter.setStyleSheet(
            """
            QSplitter::handle:vertical {
                background: #e5e7eb;
                margin: 2px 0;
            }
            QSplitter::handle:vertical:hover {
                background: #93c5fd;
            }
            """
        )

        side_panel = QtWidgets.QWidget()
        side_layout = QtWidgets.QVBoxLayout(side_panel)
        side_layout.setContentsMargins(6, 6, 6, 6)
        side_layout.setSpacing(6)
        side_layout.addWidget(open_button)
        side_layout.addWidget(add_button)
        side_layout.addWidget(export_button)
        side_layout.addWidget(self.left_splitter, 1)

        right_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_splitter.addWidget(self.plot_tabs)
        right_splitter.addWidget(self.isotherm_plot)
        right_splitter.setChildrenCollapsible(False)
        right_splitter.setHandleWidth(8)
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 2)
        right_splitter.setSizes([480, 300])
        right_splitter.setStyleSheet(
            """
            QSplitter::handle:vertical {
                background: #e5e7eb;
                margin: 2px 0;
            }
            QSplitter::handle:vertical:hover {
                background: #93c5fd;
            }
            """
        )

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(side_panel)
        splitter.addWidget(right_splitter)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([390, 890])
        self.setCentralWidget(splitter)

        self.statusBar().showMessage("打开或拖入 TriStar II 3020 2.0 SMP 文件")
        self.refresh_all()
        self._sync_select_all_state()
        QtCore.QTimer.singleShot(0, self._position_header_controls)

    def _make_t_plot_options_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(T_PLOT_PANEL_COLLAPSED_WIDTH)
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(6, 6, 6, 6)
        panel_layout.setSpacing(8)

        thickness_group = QtWidgets.QGroupBox("厚度曲线")
        thickness_layout = QtWidgets.QVBoxLayout(thickness_group)
        thickness_layout.setContentsMargins(8, 8, 8, 8)
        thickness_layout.setSpacing(4)
        self.t_plot_method_radios = {}
        self.t_plot_method_group = QtWidgets.QButtonGroup(panel)
        self.t_plot_method_group.setExclusive(True)
        self.t_plot_param_spins = {}
        self.t_plot_formula_buttons = {}
        self.t_plot_formula_widgets = {}
        self.t_plot_formula_expanded = {}
        thickness_methods = [
            ("reference", "参比", False),
            ("kjs", "Kruk-Jaroniec-Sayari E", True),
            ("halsey", "Halsey", True),
            ("harkins_jura", "Harkins and Jura 厚度", True),
            ("broekhoff_de_boer", "Broekhoff-De Boer 厚度", True),
            ("carbon_black_stsa", "碳黑STSA", True),
        ]
        for key, label, enabled in thickness_methods:
            row = self._make_thickness_method_row(key, label, enabled, context="t_plot")
            thickness_layout.addWidget(row)

        surface_group = QtWidgets.QGroupBox("表面积")
        surface_layout = QtWidgets.QVBoxLayout(surface_group)
        surface_layout.setContentsMargins(8, 8, 8, 8)
        surface_layout.setSpacing(6)
        self.surface_area_bet_radio = QtWidgets.QRadioButton("BET")
        self.surface_area_langmuir_radio = QtWidgets.QRadioButton("Langmuir")
        self.surface_area_input_radio = QtWidgets.QRadioButton("输入")
        self.surface_area_bet_radio.setChecked(True)
        self.surface_area_bet_radio.toggled.connect(self._on_t_plot_surface_area_mode_changed)
        self.surface_area_langmuir_radio.toggled.connect(self._on_t_plot_surface_area_mode_changed)
        self.surface_area_input_radio.toggled.connect(self._on_t_plot_surface_area_mode_changed)
        surface_layout.addWidget(self.surface_area_bet_radio)
        surface_layout.addWidget(self.surface_area_langmuir_radio)
        input_row = QtWidgets.QHBoxLayout()
        input_row.addWidget(self.surface_area_input_radio)
        self.surface_area_input_spin = self._make_param_spin(1.0, 0.0, 1000000.0, 3)
        self.surface_area_input_spin.setEnabled(False)
        self.surface_area_input_spin.valueChanged.connect(self._on_t_plot_surface_area_input_changed)
        input_row.addWidget(self.surface_area_input_spin)
        input_row.addWidget(QtWidgets.QLabel("m²/g"))
        input_row.addStretch(1)
        surface_layout.addLayout(input_row)

        correction_group = QtWidgets.QGroupBox("表面积校正因子")
        correction_layout = QtWidgets.QVBoxLayout(correction_group)
        correction_layout.setContentsMargins(8, 8, 8, 8)
        self.surface_area_correction_spin = self._make_param_spin(SURFACE_AREA_CORRECTION_FACTOR, 0.0, 1000.0, 3)
        self.surface_area_correction_spin.valueChanged.connect(self._on_t_plot_surface_area_correction_changed)
        correction_layout.addWidget(self.surface_area_correction_spin)

        self.t_plot_default_button = QtWidgets.QPushButton("默认")
        self.t_plot_default_button.setToolTip("重置当前样品的 t-Plot 厚度曲线、表面积选项和拟合区间")
        self.t_plot_default_button.clicked.connect(self.reset_t_plot_fit_to_default)
        default_button_row = QtWidgets.QHBoxLayout()
        default_button_row.setContentsMargins(0, 0, 0, 0)
        default_button_row.addWidget(self.t_plot_default_button)
        default_button_row.addStretch(1)

        panel_layout.addWidget(thickness_group)
        panel_layout.addWidget(surface_group)
        panel_layout.addWidget(correction_group)
        panel_layout.addLayout(default_button_row)
        panel_layout.addStretch(1)
        self._update_t_plot_options_panel_width(panel)
        return panel

    def _make_bjh_options_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(BJH_PANEL_COLLAPSED_WIDTH)
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(6, 6, 6, 6)
        panel_layout.setSpacing(8)

        thickness_group = QtWidgets.QGroupBox("厚度曲线")
        thickness_layout = QtWidgets.QVBoxLayout(thickness_group)
        thickness_layout.setContentsMargins(8, 8, 8, 8)
        thickness_layout.setSpacing(4)
        self.bjh_method_radios = {}
        self.bjh_method_group = QtWidgets.QButtonGroup(panel)
        self.bjh_method_group.setExclusive(True)
        self.bjh_param_spins = {}
        self.bjh_formula_buttons = {}
        self.bjh_formula_widgets = {}
        self.bjh_formula_expanded = {}
        thickness_methods = [
            ("reference", "参比", False),
            ("kjs", "Kruk-Jaroniec-Sayari E", True),
            ("halsey", "Halsey", True),
            ("harkins_jura", "Harkins and Jura 厚度", True),
            ("broekhoff_de_boer", "Broekhoff-De Boer 厚度", True),
            ("carbon_black_stsa", "碳黑STSA", True),
        ]
        for key, label, enabled in thickness_methods:
            thickness_layout.addWidget(self._make_thickness_method_row(key, label, enabled, context="bjh"))

        correction_group = QtWidgets.QGroupBox("BJH 校正")
        correction_layout = QtWidgets.QVBoxLayout(correction_group)
        correction_layout.setContentsMargins(8, 8, 8, 8)
        correction_layout.setSpacing(6)
        self.bjh_standard_radio = QtWidgets.QRadioButton("标准的")
        self.bjh_kjs_correction_radio = QtWidgets.QRadioButton("Kruk-Jaroniec-Sayari E")
        self.bjh_faas_correction_radio = QtWidgets.QRadioButton("Faas 校正")
        self.bjh_standard_radio.setChecked(True)
        for radio in (self.bjh_standard_radio, self.bjh_kjs_correction_radio, self.bjh_faas_correction_radio):
            radio.toggled.connect(self._on_bjh_option_changed)
            correction_layout.addWidget(radio)

        open_fraction_label = QtWidgets.QLabel("两端开口孔的分数")
        self.bjh_open_fraction_spin = self._make_param_spin(0.0, 0.0, 1.0, 2, width=112)
        self.bjh_open_fraction_spin.setToolTip("暂作为 BJH 参数保留；当前标准 BJH 计算中不改变结果")
        self.bjh_open_fraction_spin.valueChanged.connect(self._on_bjh_option_changed)
        open_fraction_row = QtWidgets.QHBoxLayout()
        open_fraction_row.setContentsMargins(0, 0, 0, 0)
        open_fraction_row.addWidget(self.bjh_open_fraction_spin)
        open_fraction_row.addStretch(1)

        self.bjh_smooth_checkbox = QtWidgets.QCheckBox("平滑的微分")
        self.bjh_smooth_checkbox.setChecked(True)
        self.bjh_smooth_checkbox.stateChanged.connect(self._on_bjh_option_changed)
        self.bjh_adsorption_checkbox = QtWidgets.QCheckBox("BJH 吸附")
        self.bjh_desorption_checkbox = QtWidgets.QCheckBox("BJH 脱附")
        self.bjh_adsorption_checkbox.setChecked(True)
        self.bjh_desorption_checkbox.setChecked(False)
        self.bjh_adsorption_checkbox.stateChanged.connect(self._on_bjh_option_changed)
        self.bjh_desorption_checkbox.stateChanged.connect(self._on_bjh_option_changed)

        self.bjh_default_button = QtWidgets.QPushButton("默认")
        self.bjh_default_button.setToolTip("重置 BJH 厚度曲线、校正参数和显示分支")
        self.bjh_default_button.clicked.connect(self.reset_bjh_to_default)
        default_button_row = QtWidgets.QHBoxLayout()
        default_button_row.setContentsMargins(0, 0, 0, 0)
        default_button_row.addWidget(self.bjh_default_button)
        default_button_row.addStretch(1)

        panel_layout.addWidget(thickness_group)
        panel_layout.addWidget(correction_group)
        panel_layout.addWidget(open_fraction_label)
        panel_layout.addLayout(open_fraction_row)
        panel_layout.addWidget(self.bjh_smooth_checkbox)
        panel_layout.addWidget(self.bjh_adsorption_checkbox)
        panel_layout.addWidget(self.bjh_desorption_checkbox)
        panel_layout.addLayout(default_button_row)
        panel_layout.addStretch(1)
        self._update_bjh_options_panel_width(panel)
        return panel

    def _make_thickness_method_row(self, key: str, label: str, enabled: bool, *, context: str = "t_plot") -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        row_layout = QtWidgets.QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        radio = QtWidgets.QRadioButton(label)
        radio.setEnabled(enabled)
        radio.setChecked(key == self._thickness_method_for_context(context))
        if context == "bjh":
            radio.toggled.connect(lambda checked, method_key=key: self._on_bjh_thickness_method_changed(method_key, checked))
            self.bjh_method_radios[key] = radio
            self.bjh_method_group.addButton(radio)
        else:
            radio.toggled.connect(lambda checked, method_key=key: self._on_t_plot_thickness_method_changed(method_key, checked))
            self.t_plot_method_radios[key] = radio
            self.t_plot_method_group.addButton(radio)
        arrow = QtWidgets.QToolButton()
        arrow.setArrowType(QtCore.Qt.DownArrow)
        arrow.setAutoRaise(True)
        arrow.setFixedSize(22, 22)
        row_layout.addWidget(radio, 1)
        row_layout.addWidget(arrow)

        formula = self._make_t_plot_formula_widget(key, context=context) if enabled else self._make_pending_formula_widget()
        formula.setVisible(False)
        if context == "bjh":
            arrow.clicked.connect(lambda _checked=False, method_key=key: self._toggle_bjh_formula(method_key))
            self.bjh_formula_buttons[key] = arrow
            self.bjh_formula_widgets[key] = formula
            self.bjh_formula_expanded[key] = False
        else:
            arrow.clicked.connect(lambda _checked=False, method_key=key: self._toggle_t_plot_formula(method_key))
            self.t_plot_formula_buttons[key] = arrow
            self.t_plot_formula_widgets[key] = formula
            self.t_plot_formula_expanded[key] = False

        layout.addLayout(row_layout)
        layout.addWidget(formula)
        return container

    def _make_t_plot_formula_widget(self, method_key: str, *, context: str = "t_plot") -> QtWidgets.QWidget:
        if method_key in {"kjs", "harkins_jura"}:
            return self._make_power_log_formula_widget(method_key, context=context)
        if method_key == "halsey":
            return self._make_halsey_formula_widget(context=context)
        if method_key == "broekhoff_de_boer":
            return self._make_broekhoff_de_boer_formula_widget(context=context)
        if method_key == "carbon_black_stsa":
            return self._make_carbon_black_stsa_formula_widget(context=context)
        return self._make_pending_formula_widget()

    def _make_param_spin_for_method(
        self,
        method_key: str,
        param_key: str,
        minimum: float,
        maximum: float,
        decimals: int,
        *,
        width: int | None = None,
        context: str = "t_plot",
    ) -> QtWidgets.QDoubleSpinBox:
        params_by_method = (
            self.bjh_thickness_params_by_method if context == "bjh" else self.t_plot_thickness_params_by_method
        )
        params = params_by_method.get(
            method_key,
            T_PLOT_THICKNESS_PARAM_DEFAULTS.get(method_key, DEFAULT_T_PLOT_THICKNESS_PARAMS),
        )
        spin = self._make_param_spin(params[param_key], minimum, maximum, decimals, width=width)
        if context == "bjh":
            spin.valueChanged.connect(
                lambda _value, changed_method=method_key: self._on_bjh_thickness_param_changed(changed_method)
            )
            self.bjh_param_spins.setdefault(method_key, {})[param_key] = spin
        else:
            spin.valueChanged.connect(
                lambda _value, changed_method=method_key: self._on_t_plot_thickness_param_changed(changed_method)
            )
            self.t_plot_param_spins.setdefault(method_key, {})[param_key] = spin
        return spin

    def _make_power_log_formula_widget(self, method_key: str, *, context: str = "t_plot") -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        outer_layout = QtWidgets.QHBoxLayout(frame)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(5)

        outer_layout.addWidget(QtWidgets.QLabel("t = ("))
        fraction_layout = QtWidgets.QVBoxLayout()
        fraction_layout.setContentsMargins(0, 0, 0, 0)
        fraction_layout.setSpacing(3)

        numerator_spin = self._make_param_spin_for_method(
            method_key, "numerator", -1000000.0, 1000000.0, 4, width=126, context=context
        )
        offset_spin = self._make_param_spin_for_method(
            method_key, "offset", -1000.0, 1000.0, 5, width=126, context=context
        )
        exponent_spin = self._make_param_spin_for_method(
            method_key, "exponent", -10.0, 10.0, 4, width=96, context=context
        )
        if method_key == "harkins_jura" and context == "t_plot":
            self.hj_numerator_spin = numerator_spin
            self.hj_offset_spin = offset_spin
            self.hj_exponent_spin = exponent_spin

        numerator_row = QtWidgets.QHBoxLayout()
        numerator_row.setContentsMargins(0, 0, 0, 0)
        numerator_row.addStretch(1)
        numerator_row.addWidget(numerator_spin)
        numerator_row.addStretch(1)

        fraction_line = QtWidgets.QFrame()
        fraction_line.setFrameShape(QtWidgets.QFrame.HLine)
        fraction_line.setFrameShadow(QtWidgets.QFrame.Plain)
        fraction_line.setLineWidth(2)

        denominator_row = QtWidgets.QHBoxLayout()
        denominator_row.setContentsMargins(0, 0, 0, 0)
        denominator_row.setSpacing(4)
        denominator_row.addStretch(1)
        denominator_row.addWidget(offset_spin)
        denominator_row.addWidget(QtWidgets.QLabel("- log(p/p°)"))
        denominator_row.addStretch(1)

        fraction_layout.addLayout(numerator_row)
        fraction_layout.addWidget(fraction_line)
        fraction_layout.addLayout(denominator_row)
        outer_layout.addLayout(fraction_layout)
        outer_layout.addWidget(QtWidgets.QLabel(")^"))
        outer_layout.addWidget(exponent_spin)
        outer_layout.addStretch(1)
        return frame

    def _make_halsey_formula_widget(self, *, context: str = "t_plot") -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        outer_layout = QtWidgets.QHBoxLayout(frame)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(5)

        outer_layout.addWidget(QtWidgets.QLabel("t ="))
        prefactor_spin = self._make_param_spin_for_method(
            "halsey", "prefactor", -1000000.0, 1000000.0, 4, width=112, context=context
        )
        numerator_spin = self._make_param_spin_for_method(
            "halsey", "numerator", -1000000.0, 1000000.0, 4, width=112, context=context
        )
        exponent_spin = self._make_param_spin_for_method(
            "halsey", "exponent", -10.0, 10.0, 4, width=96, context=context
        )
        outer_layout.addWidget(prefactor_spin)
        outer_layout.addWidget(QtWidgets.QLabel("("))

        fraction_layout = QtWidgets.QVBoxLayout()
        fraction_layout.setContentsMargins(0, 0, 0, 0)
        fraction_layout.setSpacing(3)
        numerator_row = QtWidgets.QHBoxLayout()
        numerator_row.setContentsMargins(0, 0, 0, 0)
        numerator_row.addWidget(numerator_spin)
        fraction_line = QtWidgets.QFrame()
        fraction_line.setFrameShape(QtWidgets.QFrame.HLine)
        fraction_line.setFrameShadow(QtWidgets.QFrame.Plain)
        fraction_line.setLineWidth(2)
        denominator_row = QtWidgets.QHBoxLayout()
        denominator_row.setContentsMargins(0, 0, 0, 0)
        denominator_row.addWidget(QtWidgets.QLabel("ln(p/p°)"))
        fraction_layout.addLayout(numerator_row)
        fraction_layout.addWidget(fraction_line)
        fraction_layout.addLayout(denominator_row)

        outer_layout.addLayout(fraction_layout)
        outer_layout.addWidget(QtWidgets.QLabel(")^"))
        outer_layout.addWidget(exponent_spin)
        outer_layout.addStretch(1)
        return frame

    def _make_broekhoff_de_boer_formula_widget(self, *, context: str = "t_plot") -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        outer_layout = QtWidgets.QHBoxLayout(frame)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(4)

        outer_layout.addWidget(QtWidgets.QLabel("log(p/p°) ="))
        inverse_spin = self._make_param_spin_for_method(
            "broekhoff_de_boer", "inverse_square", -1000000.0, 1000000.0, 4, width=116, context=context
        )
        factor_spin = self._make_param_spin_for_method(
            "broekhoff_de_boer", "exponential_factor", -1000000.0, 1000000.0, 4, width=112, context=context
        )
        rate_spin = self._make_param_spin_for_method(
            "broekhoff_de_boer", "exponential_rate", -1000000.0, 1000000.0, 4, width=112, context=context
        )

        fraction_layout = QtWidgets.QVBoxLayout()
        fraction_layout.setContentsMargins(0, 0, 0, 0)
        fraction_layout.setSpacing(3)
        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addWidget(inverse_spin)
        fraction_line = QtWidgets.QFrame()
        fraction_line.setFrameShape(QtWidgets.QFrame.HLine)
        fraction_line.setFrameShadow(QtWidgets.QFrame.Plain)
        fraction_line.setLineWidth(2)
        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.addWidget(QtWidgets.QLabel("t²"))
        fraction_layout.addLayout(top_row)
        fraction_layout.addWidget(fraction_line)
        fraction_layout.addLayout(bottom_row)

        outer_layout.addLayout(fraction_layout)
        outer_layout.addWidget(QtWidgets.QLabel("+"))
        outer_layout.addWidget(factor_spin)
        outer_layout.addWidget(QtWidgets.QLabel("e^"))
        outer_layout.addWidget(rate_spin)
        outer_layout.addWidget(QtWidgets.QLabel("t"))
        outer_layout.addStretch(1)
        return frame

    def _make_carbon_black_stsa_formula_widget(self, *, context: str = "t_plot") -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        outer_layout = QtWidgets.QHBoxLayout(frame)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(4)

        constant_spin = self._make_param_spin_for_method(
            "carbon_black_stsa", "constant", -1000000.0, 1000000.0, 4, width=108, context=context
        )
        linear_spin = self._make_param_spin_for_method(
            "carbon_black_stsa", "linear", -1000000.0, 1000000.0, 4, width=108, context=context
        )
        quadratic_spin = self._make_param_spin_for_method(
            "carbon_black_stsa", "quadratic", -1000000.0, 1000000.0, 4, width=108, context=context
        )
        outer_layout.addWidget(QtWidgets.QLabel("t ="))
        outer_layout.addWidget(constant_spin)
        outer_layout.addWidget(QtWidgets.QLabel("+"))
        outer_layout.addWidget(linear_spin)
        outer_layout.addWidget(QtWidgets.QLabel("(p/p°) +"))
        outer_layout.addWidget(quadratic_spin)
        outer_layout.addWidget(QtWidgets.QLabel("(p/p°)²"))
        outer_layout.addStretch(1)
        return frame

    def _make_pending_formula_widget(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        label = QtWidgets.QLabel("公式参数待补充")
        label.setStyleSheet("color: #6b7280;")
        layout.addWidget(label)
        return frame

    def _make_param_spin(
        self,
        value: float,
        minimum: float,
        maximum: float,
        decimals: int,
        *,
        width: int | None = None,
    ) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(float(value))
        spin.setSingleStep(10 ** -decimals)
        spin.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if width is None:
            width = 112
        spin.setMinimumWidth(width)
        spin.setMaximumWidth(width)
        return spin

    def _toggle_t_plot_formula(self, key: str) -> None:
        widget = self.t_plot_formula_widgets.get(key)
        button = self.t_plot_formula_buttons.get(key)
        if widget is None or button is None:
            return
        is_visible = not self.t_plot_formula_expanded.get(key, False)
        widget.setVisible(is_visible)
        self.t_plot_formula_expanded[key] = is_visible
        button.setArrowType(QtCore.Qt.UpArrow if is_visible else QtCore.Qt.DownArrow)
        self._update_t_plot_options_panel_width()

    def _update_t_plot_options_panel_width(self, panel: QtWidgets.QWidget | None = None) -> None:
        panel = panel or getattr(self, "t_plot_options_panel", None)
        if panel is None:
            return
        expanded = any(self.t_plot_formula_expanded.values())
        panel.setFixedWidth(T_PLOT_PANEL_EXPANDED_WIDTH if expanded else T_PLOT_PANEL_COLLAPSED_WIDTH)

    def _toggle_bjh_formula(self, key: str) -> None:
        widget = self.bjh_formula_widgets.get(key)
        button = self.bjh_formula_buttons.get(key)
        if widget is None or button is None:
            return
        is_visible = not self.bjh_formula_expanded.get(key, False)
        widget.setVisible(is_visible)
        self.bjh_formula_expanded[key] = is_visible
        button.setArrowType(QtCore.Qt.UpArrow if is_visible else QtCore.Qt.DownArrow)
        self._update_bjh_options_panel_width()

    def _update_bjh_options_panel_width(self, panel: QtWidgets.QWidget | None = None) -> None:
        panel = panel or getattr(self, "bjh_options_panel", None)
        if panel is None:
            return
        expanded = any(self.bjh_formula_expanded.values())
        panel.setFixedWidth(BJH_PANEL_EXPANDED_WIDTH if expanded else BJH_PANEL_COLLAPSED_WIDTH)

    def _thickness_method_for_context(self, context: str) -> str:
        if context == "bjh":
            return self.bjh_thickness_method
        return self.t_plot_thickness_method

    def _on_t_plot_thickness_method_changed(self, method_key: str, checked: bool) -> None:
        if self._syncing_t_plot_controls:
            return
        if not checked:
            return
        self.t_plot_thickness_method = method_key
        self.t_plot_thickness_params = dict(
            self.t_plot_thickness_params_by_method.get(
                method_key,
                T_PLOT_THICKNESS_PARAM_DEFAULTS.get(method_key, DEFAULT_T_PLOT_THICKNESS_PARAMS),
            )
        )
        self._syncing_t_plot_controls = True
        try:
            self._set_t_plot_formula_spins_for_method(method_key, self.t_plot_thickness_params)
        finally:
            self._syncing_t_plot_controls = False
        self._save_t_plot_settings_for_active()
        self._refresh_t_plot_for_option_change(refresh_table=True)

    def _on_t_plot_thickness_param_changed(self, method_key: str | None = None) -> None:
        if self._syncing_t_plot_controls:
            return
        method_key = method_key or self.t_plot_thickness_method
        params = self._read_t_plot_thickness_params(method_key)
        self.t_plot_thickness_params_by_method[method_key] = dict(params)
        if method_key == self.t_plot_thickness_method:
            self.t_plot_thickness_params = dict(params)
        self._save_t_plot_settings_for_active()
        if method_key == self.t_plot_thickness_method:
            self._refresh_t_plot_for_option_change(refresh_table=True)
        else:
            self._refresh_sample_t_plot_cell(self.active_index)

    def _read_t_plot_thickness_params(self, method_key: str) -> dict[str, float]:
        params = dict(T_PLOT_THICKNESS_PARAM_DEFAULTS.get(method_key, DEFAULT_T_PLOT_THICKNESS_PARAMS))
        for param_key, spin in self.t_plot_param_spins.get(method_key, {}).items():
            params[param_key] = float(spin.value())
        return params

    def _set_t_plot_formula_spins_for_method(self, method_key: str, params: dict[str, float]) -> None:
        for param_key, spin in self.t_plot_param_spins.get(method_key, {}).items():
            if param_key in params:
                spin.setValue(float(params[param_key]))

    def _set_all_t_plot_formula_spins(self) -> None:
        for method_key, params in self.t_plot_thickness_params_by_method.items():
            self._set_t_plot_formula_spins_for_method(method_key, params)

    def _on_bjh_thickness_method_changed(self, method_key: str, checked: bool) -> None:
        if self._syncing_bjh_controls or not checked:
            return
        self.bjh_thickness_method = method_key
        self.bjh_thickness_params = dict(
            self.bjh_thickness_params_by_method.get(
                method_key,
                T_PLOT_THICKNESS_PARAM_DEFAULTS.get(method_key, DEFAULT_T_PLOT_THICKNESS_PARAMS),
            )
        )
        self._syncing_bjh_controls = True
        try:
            self._set_bjh_formula_spins_for_method(method_key, self.bjh_thickness_params)
        finally:
            self._syncing_bjh_controls = False
        self._save_bjh_settings_for_active()
        self.refresh_bjh_plot()
        self._refresh_sample_bjh_pore_cell(self.active_index)

    def _on_bjh_thickness_param_changed(self, method_key: str | None = None) -> None:
        if self._syncing_bjh_controls:
            return
        method_key = method_key or self.bjh_thickness_method
        params = self._read_bjh_thickness_params(method_key)
        self.bjh_thickness_params_by_method[method_key] = dict(params)
        if method_key == self.bjh_thickness_method:
            self.bjh_thickness_params = dict(params)
        self._save_bjh_settings_for_active()
        if method_key == self.bjh_thickness_method:
            self.refresh_bjh_plot()
            self._refresh_sample_bjh_pore_cell(self.active_index)

    def _read_bjh_thickness_params(self, method_key: str) -> dict[str, float]:
        params = dict(T_PLOT_THICKNESS_PARAM_DEFAULTS.get(method_key, DEFAULT_T_PLOT_THICKNESS_PARAMS))
        for param_key, spin in self.bjh_param_spins.get(method_key, {}).items():
            params[param_key] = float(spin.value())
        return params

    def _set_bjh_formula_spins_for_method(self, method_key: str, params: dict[str, float]) -> None:
        for param_key, spin in self.bjh_param_spins.get(method_key, {}).items():
            if param_key in params:
                spin.setValue(float(params[param_key]))

    def _set_all_bjh_formula_spins(self) -> None:
        for method_key, params in self.bjh_thickness_params_by_method.items():
            self._set_bjh_formula_spins_for_method(method_key, params)

    def _on_bjh_option_changed(self, *_args) -> None:
        if self._syncing_bjh_controls:
            return
        if self.bjh_kjs_correction_radio.isChecked():
            self.bjh_correction = "kjs"
        elif self.bjh_faas_correction_radio.isChecked():
            self.bjh_correction = "faas"
        else:
            self.bjh_correction = "standard"
        self.bjh_open_pore_fraction = float(self.bjh_open_fraction_spin.value())
        self.bjh_smooth_derivative = self.bjh_smooth_checkbox.isChecked()
        self.bjh_show_adsorption = self.bjh_adsorption_checkbox.isChecked()
        self.bjh_show_desorption = self.bjh_desorption_checkbox.isChecked()
        self._save_bjh_settings_for_active()
        self.refresh_bjh_plot()
        self._refresh_sample_bjh_pore_cell(self.active_index)

    def reset_bjh_to_default(self) -> None:
        default_params_by_method = _default_t_plot_thickness_params_by_method()
        self._syncing_bjh_controls = True
        try:
            self.bjh_thickness_method = DEFAULT_T_PLOT_THICKNESS_METHOD
            self.bjh_thickness_params_by_method = default_params_by_method
            self.bjh_thickness_params = dict(default_params_by_method[DEFAULT_T_PLOT_THICKNESS_METHOD])
            self.bjh_correction = DEFAULT_BJH_CORRECTION
            self.bjh_open_pore_fraction = DEFAULT_BJH_OPEN_PORE_FRACTION
            self.bjh_smooth_derivative = DEFAULT_BJH_SMOOTH_DERIVATIVE
            self.bjh_show_adsorption = DEFAULT_BJH_SHOW_ADSORPTION
            self.bjh_show_desorption = DEFAULT_BJH_SHOW_DESORPTION

            for key, radio in self.bjh_method_radios.items():
                radio.setChecked(key == self.bjh_thickness_method)
            self._set_all_bjh_formula_spins()
            self.bjh_standard_radio.setChecked(True)
            self.bjh_open_fraction_spin.setValue(DEFAULT_BJH_OPEN_PORE_FRACTION)
            self.bjh_smooth_checkbox.setChecked(DEFAULT_BJH_SMOOTH_DERIVATIVE)
            self.bjh_adsorption_checkbox.setChecked(DEFAULT_BJH_SHOW_ADSORPTION)
            self.bjh_desorption_checkbox.setChecked(DEFAULT_BJH_SHOW_DESORPTION)
        finally:
            self._syncing_bjh_controls = False
        active = self.active_result()
        if active is not None:
            self.custom_bjh_settings.pop(id(active), None)
        self.refresh_bjh_plot()
        self._refresh_sample_bjh_pore_cell(self.active_index)

    def _on_t_plot_surface_area_mode_changed(self) -> None:
        if self._syncing_t_plot_controls:
            return
        if self.surface_area_bet_radio.isChecked():
            self.t_plot_surface_area_mode = "BET"
        elif self.surface_area_langmuir_radio.isChecked():
            self.t_plot_surface_area_mode = "Langmuir"
        elif self.surface_area_input_radio.isChecked():
            self.t_plot_surface_area_mode = "Input"
        self.surface_area_input_spin.setEnabled(self.t_plot_surface_area_mode == "Input")
        self._save_t_plot_settings_for_active()
        self._refresh_sample_t_plot_cell(self.active_index)
        self.refresh_metrics()

    def _on_t_plot_surface_area_input_changed(self) -> None:
        if self._syncing_t_plot_controls:
            return
        self.t_plot_surface_area_input = float(self.surface_area_input_spin.value())
        self._save_t_plot_settings_for_active()
        if self.t_plot_surface_area_mode == "Input":
            self._refresh_sample_t_plot_cell(self.active_index)
            self.refresh_metrics()

    def _on_t_plot_surface_area_correction_changed(self) -> None:
        if self._syncing_t_plot_controls:
            return
        self.t_plot_surface_area_correction = float(self.surface_area_correction_spin.value())
        self._save_t_plot_settings_for_active()
        self._refresh_sample_t_plot_cell(self.active_index)
        self.refresh_metrics()

    def _refresh_t_plot_for_option_change(self, *, refresh_table: bool = False) -> None:
        active = self.active_result()
        if active is None:
            return
        pressure_range = self._current_pressure_region()
        p_min, p_max = pressure_range if pressure_range else (None, None)
        self._refresh_t_plot_plot(active, p_min, p_max, reset_region=False)
        if refresh_table:
            self._refresh_sample_t_plot_cell(self.active_index)
        self.refresh_metrics()

    def _default_t_plot_settings(self) -> dict[str, object]:
        params_by_method = _default_t_plot_thickness_params_by_method()
        return {
            "thickness_method": DEFAULT_T_PLOT_THICKNESS_METHOD,
            "thickness_params_by_method": params_by_method,
            "thickness_params": dict(params_by_method[DEFAULT_T_PLOT_THICKNESS_METHOD]),
            "surface_area_mode": DEFAULT_T_PLOT_SURFACE_AREA_MODE,
            "surface_area_input": DEFAULT_T_PLOT_SURFACE_AREA_INPUT,
            "surface_area_correction": DEFAULT_T_PLOT_SURFACE_AREA_CORRECTION,
        }

    def _t_plot_settings_for_result(self, result) -> dict[str, object]:
        settings = self._default_t_plot_settings()
        custom = self.custom_t_plot_settings.get(id(result))
        if custom:
            settings.update(custom)
            params_by_method = _default_t_plot_thickness_params_by_method()
            if "thickness_params_by_method" in custom:
                for method_key, params in dict(custom["thickness_params_by_method"]).items():
                    if method_key in params_by_method:
                        params_by_method[method_key] = {
                            **params_by_method[method_key],
                            **dict(params),
                        }
            elif "thickness_params" in custom:
                method_key = str(settings["thickness_method"])
                if method_key in params_by_method:
                    params_by_method[method_key] = {
                        **params_by_method[method_key],
                        **dict(custom["thickness_params"]),
                    }
            settings["thickness_params_by_method"] = params_by_method
            method_key = str(settings["thickness_method"])
            settings["thickness_params"] = dict(
                params_by_method.get(method_key, params_by_method[DEFAULT_T_PLOT_THICKNESS_METHOD])
            )
        return settings

    def _save_t_plot_settings_for_active(self) -> None:
        active = self.active_result()
        if active is None:
            return
        self.custom_t_plot_settings[id(active)] = {
            "thickness_method": self.t_plot_thickness_method,
            "thickness_params_by_method": {
                method_key: dict(params)
                for method_key, params in self.t_plot_thickness_params_by_method.items()
            },
            "thickness_params": dict(self.t_plot_thickness_params),
            "surface_area_mode": self.t_plot_surface_area_mode,
            "surface_area_input": self.t_plot_surface_area_input,
            "surface_area_correction": self.t_plot_surface_area_correction,
        }

    def _load_t_plot_settings_for_active(self) -> None:
        active = self.active_result()
        settings = self._default_t_plot_settings() if active is None else self._t_plot_settings_for_result(active)
        self._syncing_t_plot_controls = True
        try:
            self.t_plot_thickness_method = str(settings["thickness_method"])
            self.t_plot_thickness_params_by_method = {
                method_key: dict(params)
                for method_key, params in dict(settings["thickness_params_by_method"]).items()
            }
            self.t_plot_thickness_params = dict(settings["thickness_params"])
            self.t_plot_surface_area_mode = str(settings["surface_area_mode"])
            self.t_plot_surface_area_input = float(settings["surface_area_input"])
            self.t_plot_surface_area_correction = float(settings["surface_area_correction"])

            for key, radio in self.t_plot_method_radios.items():
                radio.setChecked(key == self.t_plot_thickness_method)
            self._set_all_t_plot_formula_spins()
            self.surface_area_bet_radio.setChecked(self.t_plot_surface_area_mode == "BET")
            self.surface_area_langmuir_radio.setChecked(self.t_plot_surface_area_mode == "Langmuir")
            self.surface_area_input_radio.setChecked(self.t_plot_surface_area_mode == "Input")
            self.surface_area_input_spin.setValue(self.t_plot_surface_area_input)
            self.surface_area_input_spin.setEnabled(self.t_plot_surface_area_mode == "Input")
            self.surface_area_correction_spin.setValue(self.t_plot_surface_area_correction)
        finally:
            self._syncing_t_plot_controls = False

    def _default_bjh_settings(self) -> dict[str, object]:
        params_by_method = _default_t_plot_thickness_params_by_method()
        return {
            "thickness_method": DEFAULT_T_PLOT_THICKNESS_METHOD,
            "thickness_params_by_method": params_by_method,
            "thickness_params": dict(params_by_method[DEFAULT_T_PLOT_THICKNESS_METHOD]),
            "correction": DEFAULT_BJH_CORRECTION,
            "open_pore_fraction": DEFAULT_BJH_OPEN_PORE_FRACTION,
            "smooth_derivative": DEFAULT_BJH_SMOOTH_DERIVATIVE,
            "show_adsorption": DEFAULT_BJH_SHOW_ADSORPTION,
            "show_desorption": DEFAULT_BJH_SHOW_DESORPTION,
        }

    def _bjh_settings_for_result(self, result) -> dict[str, object]:
        settings = self._default_bjh_settings()
        custom = self.custom_bjh_settings.get(id(result))
        if custom:
            settings.update(custom)
            params_by_method = _default_t_plot_thickness_params_by_method()
            if "thickness_params_by_method" in custom:
                for method_key, params in dict(custom["thickness_params_by_method"]).items():
                    if method_key in params_by_method:
                        params_by_method[method_key] = {
                            **params_by_method[method_key],
                            **dict(params),
                        }
            elif "thickness_params" in custom:
                method_key = str(settings["thickness_method"])
                if method_key in params_by_method:
                    params_by_method[method_key] = {
                        **params_by_method[method_key],
                        **dict(custom["thickness_params"]),
                    }
            settings["thickness_params_by_method"] = params_by_method
            method_key = str(settings["thickness_method"])
            settings["thickness_params"] = dict(
                params_by_method.get(method_key, params_by_method[DEFAULT_T_PLOT_THICKNESS_METHOD])
            )
        return settings

    def _save_bjh_settings_for_active(self) -> None:
        active = self.active_result()
        if active is None:
            return
        self.custom_bjh_settings[id(active)] = {
            "thickness_method": self.bjh_thickness_method,
            "thickness_params_by_method": {
                method_key: dict(params)
                for method_key, params in self.bjh_thickness_params_by_method.items()
            },
            "thickness_params": dict(self.bjh_thickness_params),
            "correction": self.bjh_correction,
            "open_pore_fraction": self.bjh_open_pore_fraction,
            "smooth_derivative": self.bjh_smooth_derivative,
            "show_adsorption": self.bjh_show_adsorption,
            "show_desorption": self.bjh_show_desorption,
        }

    def _load_bjh_settings_for_active(self) -> None:
        active = self.active_result()
        settings = self._default_bjh_settings() if active is None else self._bjh_settings_for_result(active)
        self._syncing_bjh_controls = True
        try:
            self.bjh_thickness_method = str(settings["thickness_method"])
            self.bjh_thickness_params_by_method = {
                method_key: dict(params)
                for method_key, params in dict(settings["thickness_params_by_method"]).items()
            }
            self.bjh_thickness_params = dict(settings["thickness_params"])
            self.bjh_correction = str(settings["correction"])
            self.bjh_open_pore_fraction = float(settings["open_pore_fraction"])
            self.bjh_smooth_derivative = bool(settings["smooth_derivative"])
            self.bjh_show_adsorption = bool(settings["show_adsorption"])
            self.bjh_show_desorption = bool(settings["show_desorption"])

            for key, radio in self.bjh_method_radios.items():
                radio.setChecked(key == self.bjh_thickness_method)
            self._set_all_bjh_formula_spins()
            self.bjh_standard_radio.setChecked(self.bjh_correction == "standard")
            self.bjh_kjs_correction_radio.setChecked(self.bjh_correction == "kjs")
            self.bjh_faas_correction_radio.setChecked(self.bjh_correction == "faas")
            self.bjh_open_fraction_spin.setValue(self.bjh_open_pore_fraction)
            self.bjh_smooth_checkbox.setChecked(self.bjh_smooth_derivative)
            self.bjh_adsorption_checkbox.setChecked(self.bjh_show_adsorption)
            self.bjh_desorption_checkbox.setChecked(self.bjh_show_desorption)
        finally:
            self._syncing_bjh_controls = False

    def open_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "打开 SMP",
            str(Path.cwd()),
            "SMP 文件 (*.SMP *.smp)",
        )
        if paths:
            self.load_files(paths, replace=True)

    def add_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "添加 SMP",
            str(Path.cwd()),
            "SMP 文件 (*.SMP *.smp)",
        )
        if paths:
            self.load_files(paths, replace=False)

    def append_files(self, paths: list[str]) -> None:
        self.load_files(paths, replace=False)

    def load_files(self, paths: Iterable[str], *, replace: bool) -> None:
        parsed = []
        errors = []
        for path in paths:
            try:
                result = load_smp(path)
            except (OSError, TriStarParseError, ValueError) as exc:
                errors.append(f"{Path(path).name}: {exc}")
                continue
            if result.point_count <= 0:
                errors.append(f"{Path(path).name}: 没有解析到 2.0 实际等温线，已跳过")
                continue
            parsed.append(result)

        if errors:
            QtWidgets.QMessageBox.warning(self, "部分文件未加载", "\n".join(errors))
        if not parsed:
            return

        if replace:
            self.results = parsed
            self.visible_results = [True] * len(parsed)
            self.active_index = 0
            self.custom_bet_fit_ranges.clear()
            self.custom_langmuir_fit_ranges.clear()
            self.custom_t_plot_fit_ranges.clear()
            self.custom_t_plot_settings.clear()
            self.custom_bjh_settings.clear()
            self._isotherm_region_custom = False
        else:
            self.results.extend(parsed)
            self.visible_results.extend([True] * len(parsed))
            if self.active_index < 0:
                self.active_index = 0
        self.refresh_all()

    def export_xlsx(self) -> None:
        selected = [result for result, visible in zip(self.results, self.visible_results) if visible]
        if not selected:
            QtWidgets.QMessageBox.information(self, "导出 XLSX", "没有勾选可导出的样品。")
            return

        default_name = f"BET解析导出_{len(selected)}个样品.xlsx"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出 XLSX",
            str(Path.cwd() / default_name),
            "Excel 工作簿 (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_results_xlsx(selected, path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.statusBar().showMessage(f"已导出: {path}", 6000)

    def on_sample_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._updating_table or self._updating_sample_checks or item.column() != VISIBLE_COLUMN:
            return
        row = item.row()
        if row >= len(self.visible_results):
            return
        self.visible_results[row] = _check_state_value(item.checkState()) == _check_state_value(QtCore.Qt.Checked)
        self._sync_select_all_state()
        self._refresh_visibility_dependent_ui()

    def on_active_cell_changed(self, current_row: int, current_column: int, previous_row: int, previous_column: int) -> None:
        if self._updating_table:
            return
        if current_row < 0 or current_row >= len(self.results):
            return
        if current_row == self.active_index:
            return
        self.active_index = current_row
        self._reset_all_fit_regions()
        self._load_t_plot_settings_for_active()
        self._load_bjh_settings_for_active()
        self.refresh_active_views()
        self.refresh_analysis_plots()

    def on_sample_header_clicked(self, section: int) -> None:
        if len(self.results) < 2:
            return
        if section == TEST_TIME_COLUMN:
            self.test_time_sort_ascending = not self.test_time_sort_ascending
            self.sort_samples_by_test_time(self.test_time_sort_ascending)
        elif section == BET_COLUMN:
            self.bet_sort_ascending = not self.bet_sort_ascending
            self.sort_samples_by_bet(self.bet_sort_ascending)
        elif section == LANGMUIR_COLUMN:
            self.langmuir_sort_ascending = not self.langmuir_sort_ascending
            self.sort_samples_by_langmuir(self.langmuir_sort_ascending)
        elif section == T_PLOT_COLUMN:
            self.t_plot_sort_ascending = not self.t_plot_sort_ascending
            self.sort_samples_by_t_plot(self.t_plot_sort_ascending)
        elif section == BJH_PORE_VOLUME_COLUMN:
            self.bjh_pore_sort_ascending = not self.bjh_pore_sort_ascending
            self.sort_samples_by_bjh_pore_volume(self.bjh_pore_sort_ascending)

    def on_sample_header_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        self._position_header_controls()
        if self._updating_sample_column_widths or not self._sample_column_widths_initialized:
            return
        self.sample_column_widths[int(logical_index)] = int(new_size)

    def show_sample_context_menu(self, position: QtCore.QPoint) -> None:
        row = self.sample_table.rowAt(position.y())
        if row < 0 or row >= len(self.results):
            return
        self.sample_table.selectRow(row)
        menu = QtWidgets.QMenu(self.sample_table)
        delete_action = menu.addAction("删除")
        global_pos = self.sample_table.viewport().mapToGlobal(position)
        exec_menu = getattr(menu, "exec_", None) or getattr(menu, "exec")
        if exec_menu(global_pos) == delete_action:
            self.delete_sample_row(row)

    def delete_sample_row(self, row: int) -> None:
        if row < 0 or row >= len(self.results):
            return
        deleted = self.results.pop(row)
        self.visible_results.pop(row)
        self.custom_bet_fit_ranges.pop(id(deleted), None)
        self.custom_langmuir_fit_ranges.pop(id(deleted), None)
        self.custom_t_plot_fit_ranges.pop(id(deleted), None)
        self.custom_t_plot_settings.pop(id(deleted), None)
        self.custom_bjh_settings.pop(id(deleted), None)

        if not self.results:
            self.active_index = -1
            self._reset_all_fit_regions()
            self._isotherm_region_custom = False
            self.refresh_all()
            self.statusBar().showMessage("已删除样品", 3000)
            return

        if row < self.active_index:
            self.active_index -= 1
        elif row == self.active_index:
            self.active_index = min(row, len(self.results) - 1)
        self.refresh_all()
        self.statusBar().showMessage("已删除样品", 3000)

    def sort_samples_by_test_time(self, ascending: bool) -> None:
        active = self.active_result()
        rows = list(zip(self.results, self.visible_results))
        rows.sort(key=lambda row: self._test_time_sort_key(row[0]), reverse=not ascending)
        self.results = [row[0] for row in rows]
        self.visible_results = [row[1] for row in rows]
        self.active_index = 0
        if active is not None:
            for index, result in enumerate(self.results):
                if result is active:
                    self.active_index = index
                    break
        self.refresh_all()

    def sort_samples_by_bet(self, ascending: bool) -> None:
        active = self.active_result()
        rows = list(zip(self.results, self.visible_results))
        rows.sort(key=lambda row: self._bet_sort_key(row[0]), reverse=not ascending)
        self.results = [row[0] for row in rows]
        self.visible_results = [row[1] for row in rows]
        self.active_index = 0
        if active is not None:
            for index, result in enumerate(self.results):
                if result is active:
                    self.active_index = index
                    break
        self.refresh_all()

    def sort_samples_by_langmuir(self, ascending: bool) -> None:
        active = self.active_result()
        rows = list(zip(self.results, self.visible_results))
        rows.sort(key=lambda row: self._langmuir_sort_key(row[0]), reverse=not ascending)
        self.results = [row[0] for row in rows]
        self.visible_results = [row[1] for row in rows]
        self.active_index = 0
        if active is not None:
            for index, result in enumerate(self.results):
                if result is active:
                    self.active_index = index
                    break
        self.refresh_all()

    def sort_samples_by_t_plot(self, ascending: bool) -> None:
        active = self.active_result()
        rows = list(zip(self.results, self.visible_results))
        rows.sort(key=lambda row: self._t_plot_sort_key(row[0]), reverse=not ascending)
        self.results = [row[0] for row in rows]
        self.visible_results = [row[1] for row in rows]
        self.active_index = 0
        if active is not None:
            for index, result in enumerate(self.results):
                if result is active:
                    self.active_index = index
                    break
        self.refresh_all()

    def sort_samples_by_bjh_pore_volume(self, ascending: bool) -> None:
        active = self.active_result()
        rows = list(zip(self.results, self.visible_results))
        rows.sort(key=lambda row: self._bjh_pore_volume_sort_key(row[0]), reverse=not ascending)
        self.results = [row[0] for row in rows]
        self.visible_results = [row[1] for row in rows]
        self.active_index = 0
        if active is not None:
            for index, result in enumerate(self.results):
                if result is active:
                    self.active_index = index
                    break
        self.refresh_all()

    def move_sample_row(self, source_index: int, insert_index: int) -> None:
        if len(self.results) < 2 or not (0 <= source_index < len(self.results)):
            return

        insert_index = max(0, min(int(insert_index), len(self.results)))
        if insert_index in (source_index, source_index + 1):
            return

        active = self.active_result()
        moved_result = self.results.pop(source_index)
        moved_visible = self.visible_results.pop(source_index)

        if insert_index > source_index:
            insert_index -= 1

        self.results.insert(insert_index, moved_result)
        self.visible_results.insert(insert_index, moved_visible)

        self.active_index = insert_index
        if active is not None:
            for index, result in enumerate(self.results):
                if result is active:
                    self.active_index = index
                    break
        self.refresh_all()

    def refresh_all(self) -> None:
        self._load_t_plot_settings_for_active()
        self._load_bjh_settings_for_active()
        self.refresh_isotherm_plot()
        self.refresh_sample_table()
        self.refresh_active_views()
        self.refresh_analysis_plots()

    def refresh_sample_table(self) -> None:
        self._updating_table = True
        try:
            self.sample_table.setRowCount(len(self.results))
            self.sample_items = []
            for row, result in enumerate(self.results):
                bet = self._bet_analysis_for_result(result)
                langmuir = self._langmuir_analysis_for_result(result)
                t_plot = self._t_plot_analysis_for_result(result)
                visible_item = QtWidgets.QTableWidgetItem()
                visible_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
                visible_item.setCheckState(QtCore.Qt.Checked if self.visible_results[row] else QtCore.Qt.Unchecked)
                visible_item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.sample_table.setItem(row, VISIBLE_COLUMN, visible_item)
                self.sample_items.append(visible_item)

                file_item = self._table_item(_display_file_name(result), tooltip=result.header.file_path)
                self.sample_table.setItem(row, FILE_COLUMN, file_item)

                test_time_item = self._table_item(result.header.created_time)
                test_time_item.setToolTip("来自 SMP 文件创建时间，精确到秒")
                self.sample_table.setItem(row, TEST_TIME_COLUMN, test_time_item)

                bet_item = self._table_item(_fmt(bet.surface_area_m2_g), alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self._style_sample_bet_item(bet_item, result)
                self.sample_table.setItem(row, BET_COLUMN, bet_item)

                langmuir_item = self._table_item(
                    _fmt(langmuir.surface_area_m2_g),
                    alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                )
                self._style_sample_langmuir_item(langmuir_item, result)
                self.sample_table.setItem(row, LANGMUIR_COLUMN, langmuir_item)

                t_plot_item = self._table_item(
                    _fmt(t_plot.external_surface_area_m2_g),
                    alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                )
                self._style_sample_t_plot_item(t_plot_item, result)
                self.sample_table.setItem(row, T_PLOT_COLUMN, t_plot_item)

                bjh_volume_item = self._table_item(
                    _fmt(self._bjh_pore_volume_for_result(result)),
                    alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                )
                self._style_sample_bjh_pore_item(bjh_volume_item, result)
                self.sample_table.setItem(row, BJH_PORE_VOLUME_COLUMN, bjh_volume_item)
            if self.results and self.active_index >= 0:
                self.sample_table.selectRow(min(self.active_index, len(self.results) - 1))
            self._sync_select_all_state()
            self._resize_sample_columns()
        finally:
            self._updating_table = False

    def _refresh_sample_bet_cell(self, row: int) -> None:
        if row < 0 or row >= len(self.results):
            return
        result = self.results[row]
        bet = self._bet_analysis_for_result(result)
        bet_item = self._table_item(_fmt(bet.surface_area_m2_g), alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._style_sample_bet_item(bet_item, result)
        self.sample_table.setItem(row, BET_COLUMN, bet_item)

    def _refresh_sample_langmuir_cell(self, row: int) -> None:
        if row < 0 or row >= len(self.results):
            return
        result = self.results[row]
        langmuir = self._langmuir_analysis_for_result(result)
        langmuir_item = self._table_item(
            _fmt(langmuir.surface_area_m2_g),
            alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
        )
        self._style_sample_langmuir_item(langmuir_item, result)
        self.sample_table.setItem(row, LANGMUIR_COLUMN, langmuir_item)

    def _refresh_sample_t_plot_cell(self, row: int) -> None:
        if row < 0 or row >= len(self.results):
            return
        result = self.results[row]
        t_plot = self._t_plot_analysis_for_result(result)
        t_plot_item = self._table_item(
            _fmt(t_plot.external_surface_area_m2_g),
            alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
        )
        self._style_sample_t_plot_item(t_plot_item, result)
        self.sample_table.setItem(row, T_PLOT_COLUMN, t_plot_item)

    def _refresh_sample_bjh_pore_cell(self, row: int) -> None:
        if row < 0 or row >= len(self.results):
            return
        result = self.results[row]
        item = self._table_item(
            _fmt(self._bjh_pore_volume_for_result(result)),
            alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
        )
        self._style_sample_bjh_pore_item(item, result)
        self.sample_table.setItem(row, BJH_PORE_VOLUME_COLUMN, item)

    def _refresh_all_sample_bjh_pore_cells(self) -> None:
        for row in range(len(self.results)):
            self._refresh_sample_bjh_pore_cell(row)

    def _style_sample_bet_item(self, item: QtWidgets.QTableWidgetItem, result) -> None:
        if not self._is_custom_bet_fit(result):
            return
        item.setForeground(QtGui.QBrush(QtGui.QColor(CUSTOM_BET_COLOR)))
        item.setToolTip("BET 拟合区间已人工调整")

    def _style_sample_langmuir_item(self, item: QtWidgets.QTableWidgetItem, result) -> None:
        if not self._is_custom_langmuir_fit(result):
            return
        item.setForeground(QtGui.QBrush(QtGui.QColor(CUSTOM_BET_COLOR)))
        item.setToolTip("Langmuir 拟合区间已人工调整")

    def _style_sample_t_plot_item(self, item: QtWidgets.QTableWidgetItem, result) -> None:
        if not self._is_custom_t_plot_fit(result):
            return
        item.setForeground(QtGui.QBrush(QtGui.QColor(CUSTOM_BET_COLOR)))
        item.setToolTip("t-Plot 厚度曲线、表面积参数或拟合厚度区间已人工调整")

    def _style_sample_bjh_pore_item(self, item: QtWidgets.QTableWidgetItem, result) -> None:
        if self._has_custom_bjh_settings(result):
            item.setForeground(QtGui.QBrush(QtGui.QColor(CUSTOM_BET_COLOR)))
            item.setToolTip("BJH 厚度曲线、公式参数、校正或显示分支已人工调整")
        else:
            item.setToolTip("BJH 2-10 nm 孔容量，按默认 BJH 吸附分支计算")

    def refresh_active_views(self) -> None:
        self.refresh_metrics()
        self.refresh_condition_table()
        self.refresh_isotherm_table()
        self.refresh_target_table()
        self.refresh_report_module_table()
        self.refresh_log_table()

    def refresh_plots(self) -> None:
        self.refresh_isotherm_plot()
        self.refresh_analysis_plots()

    def on_plot_tab_changed(self, _index: int) -> None:
        if self._isotherm_region_custom:
            return
        self.refresh_isotherm_plot()
        self.refresh_analysis_plots()

    def refresh_isotherm_plot(self) -> None:
        raw_region = self._current_pressure_region() if self._isotherm_region_custom else None
        pressure = self._all_pressure_values()
        selected_range = None
        if pressure.size:
            selected_range = self._clamp_pressure_region(raw_region or self._default_isotherm_region(pressure), pressure)
        self._remove_region()
        self._remove_isotherm_selection()
        plot_isotherm_multi(self.isotherm_plot, self.results, self.visible_results, self.sample_colors, active_index=self.active_index)
        if selected_range is not None:
            self._add_region(selected_range, pressure)
            self._refresh_isotherm_selection(selected_range)

    def refresh_analysis_plots(self) -> None:
        active = self.active_result()
        if active is None:
            self._clear_analysis_plots()
            return
        pressure_range = self._current_pressure_region()
        p_min, p_max = pressure_range if pressure_range else (None, None)
        self._refresh_bet_plot(active, p_min, p_max, reset_region=True)
        self._refresh_langmuir_plot(active, p_min, p_max, reset_region=True)
        self._refresh_t_plot_plot(active, p_min, p_max, reset_region=True)
        self.refresh_bjh_plot()

    def refresh_bjh_plot(self) -> None:
        if not self.results:
            plot_pore_distribution_placeholder(self.pore_plot)
            return
        pressure_range = self._bjh_pressure_range()
        bjh_settings_by_index = {
            index: self._bjh_settings_for_result(result)
            for index, result in enumerate(self.results)
        }
        plot_bjh_distribution_multi(
            self.pore_plot,
            self.results,
            self.visible_results,
            self.sample_colors,
            active_index=self.active_index,
            thickness_method=self.bjh_thickness_method,
            thickness_params=self.bjh_thickness_params,
            correction=self.bjh_correction,
            open_pore_fraction=self.bjh_open_pore_fraction,
            show_adsorption=self.bjh_show_adsorption,
            show_desorption=self.bjh_show_desorption,
            smooth=self.bjh_smooth_derivative,
            pressure_range=pressure_range,
            bjh_settings_by_index=bjh_settings_by_index,
        )

    def _bjh_pressure_range(self) -> tuple[float, float] | None:
        if not self._isotherm_region_custom:
            return None
        return self._current_pressure_region()

    def _update_analysis_plots_for_region(self) -> None:
        """等温线选区变化时调用：刷新三个分析图但保留各自的拟合选区。"""
        active = self.active_result()
        if active is None:
            return
        pressure_range = self._current_pressure_region()
        p_min, p_max = pressure_range if pressure_range else (None, None)
        self._refresh_bet_plot(active, p_min, p_max, reset_region=False)
        self._refresh_langmuir_plot(active, p_min, p_max, reset_region=False)
        self._refresh_t_plot_plot(active, p_min, p_max, reset_region=False)
        self.refresh_bjh_plot()

    def _visible_analysis_indices(self) -> list[int]:
        draw_order = [i for i in range(len(self.results)) if i != self.active_index]
        if 0 <= self.active_index < len(self.results):
            draw_order.append(self.active_index)
        return [i for i in draw_order if i < len(self.visible_results) and self.visible_results[i]]

    def _analysis_sample_color(self, index: int) -> str:
        if index == self.active_index:
            return "#dc2626"
        return self.sample_colors[index % len(self.sample_colors)] if self.sample_colors else DEFAULT_COLORS[0]

    def _refresh_bet_plot(self, active, p_min=None, p_max=None, *, reset_region: bool = False) -> None:
        raw_region = None
        if not reset_region and self.bet_region is not None:
            try:
                raw_region = list(self.bet_region.getRegion())
            except RuntimeError:
                pass
        self._remove_bet_region()
        self._remove_bet_selection()
        self._bet_fit_line = None
        self._bet_x_range = None
        data_p_min = p_min if p_min is not None else BET_PLOT_RANGE[0]
        data_p_max = p_max if p_max is not None else BET_PLOT_RANGE[1]
        self._bet_plot_p_range = (data_p_min, data_p_max)
        x_by_index = plot_bet_multi(
            self.bet_plot,
            self.results,
            self.visible_results,
            self.sample_colors,
            active_index=self.active_index,
            p_min=data_p_min,
            p_max=data_p_max,
        )
        for index in self._visible_analysis_indices():
            result = self.results[index]
            x_values = x_by_index.get(index)
            if x_values is None or x_values.size < 2:
                continue
            x_min = float(np.nanmin(x_values))
            x_max = float(np.nanmax(x_values))
            if x_min >= x_max:
                continue
            is_active = result is active
            target_region = (
                raw_region
                if is_active and raw_region and not reset_region
                else self._bet_fit_range_for_result(result)
            )
            lo, hi = self._clamp_fit_region(target_region, x_min, x_max, False)
            item, _ = replace_bet_fit_line(
                self.bet_plot,
                None,
                result,
                lo,
                hi,
                line_x_min=x_min,
                line_x_max=x_max,
                color=self._analysis_sample_color(index),
                name="线性拟合" if is_active else None,
                width=2 if is_active else 1,
            )
            if not is_active:
                continue
            self._bet_x_range = (x_min, x_max)
            self._bet_fit_line = item
            was_setting_bet_region = self._setting_bet_region
            self._setting_bet_region = True
            try:
                self._add_bet_region([lo, hi], [x_min, x_max])
            finally:
                self._setting_bet_region = was_setting_bet_region
            self._refresh_bet_selection(active, (lo, hi), data_p_min, data_p_max)

    def reset_bet_fit_to_default(self) -> None:
        active = self.active_result()
        if active is None:
            return
        self._clear_custom_bet_fit_range(active)
        pressure_range = self._current_pressure_region()
        p_min, p_max = pressure_range if pressure_range else (None, None)
        self._setting_bet_region = True
        try:
            self._refresh_bet_plot(active, p_min, p_max, reset_region=True)
        finally:
            self._setting_bet_region = False
        self.refresh_sample_table()
        self.refresh_metrics()

    def _refresh_langmuir_plot(self, active, p_min, p_max, *, reset_region: bool = False) -> None:
        raw_region = None
        if not reset_region and self.langmuir_region is not None:
            try:
                raw_region = list(self.langmuir_region.getRegion())
            except RuntimeError:
                pass
        self._remove_langmuir_region()
        self._remove_langmuir_selection()
        self._langmuir_fit_line = None
        self._langmuir_x_range = None
        data_p_min = p_min if p_min is not None else LANGMUIR_PLOT_RANGE[0]
        data_p_max = p_max if p_max is not None else LANGMUIR_PLOT_RANGE[1]
        self._langmuir_plot_p_range = (data_p_min, data_p_max)
        x_by_index = plot_langmuir_points_multi(
            self.langmuir_plot,
            self.results,
            self.visible_results,
            self.sample_colors,
            active_index=self.active_index,
            p_min=data_p_min,
            p_max=data_p_max,
        )
        for index in self._visible_analysis_indices():
            result = self.results[index]
            x_values = x_by_index.get(index)
            if x_values is None or x_values.size < 2:
                continue
            x_min = float(np.nanmin(x_values))
            x_max = float(np.nanmax(x_values))
            if x_min >= x_max:
                continue
            is_active = result is active
            target_region = (
                raw_region
                if is_active and raw_region and not reset_region
                else self._langmuir_fit_range_for_result(result)
            )
            lo, hi = self._clamp_fit_region(target_region, x_min, x_max, False)
            item, _ = replace_langmuir_fit_line(
                self.langmuir_plot,
                None,
                result,
                lo,
                hi,
                line_x_min=x_min,
                line_x_max=x_max,
                color=self._analysis_sample_color(index),
                name="线性拟合" if is_active else None,
                width=2 if is_active else 1,
            )
            if not is_active:
                continue
            self._langmuir_x_range = (x_min, x_max)
            self._langmuir_fit_line = item
            was_setting_langmuir_region = self._setting_langmuir_region
            self._setting_langmuir_region = True
            try:
                self._add_langmuir_region([lo, hi], [x_min, x_max])
            finally:
                self._setting_langmuir_region = was_setting_langmuir_region
            self._refresh_langmuir_selection(active, (lo, hi), data_p_min, data_p_max)

    def reset_langmuir_fit_to_default(self) -> None:
        active = self.active_result()
        if active is None:
            return
        self._clear_custom_langmuir_fit_range(active)
        pressure_range = self._current_pressure_region()
        p_min, p_max = pressure_range if pressure_range else (None, None)
        self._setting_langmuir_region = True
        try:
            self._refresh_langmuir_plot(active, p_min, p_max, reset_region=True)
        finally:
            self._setting_langmuir_region = False
        self.refresh_sample_table()
        self.refresh_metrics()

    def _refresh_t_plot_plot(self, active, p_min, p_max, *, reset_region: bool = False) -> None:
        raw_region = None
        if not reset_region and self.t_plot_region is not None:
            try:
                raw_region = list(self.t_plot_region.getRegion())
            except RuntimeError:
                pass
        self._remove_t_plot_region()
        self._remove_t_plot_selection()
        self._t_plot_fit_line = None
        self._t_plot_x_range = None
        data_p_min = p_min if p_min is not None else T_PLOT_PLOT_RANGE[0]
        data_p_max = p_max if p_max is not None else T_PLOT_PLOT_RANGE[1]
        self._t_plot_p_range = (data_p_min, data_p_max)
        thickness_params_by_index = {
            index: dict(self._t_plot_settings_for_result(result)["thickness_params"])
            for index, result in enumerate(self.results)
        }
        thickness_method_by_index = {
            index: str(self._t_plot_settings_for_result(result)["thickness_method"])
            for index, result in enumerate(self.results)
        }
        x_by_index = plot_t_plot_points_multi(
            self.t_plot,
            self.results,
            self.visible_results,
            self.sample_colors,
            active_index=self.active_index,
            p_min=data_p_min,
            p_max=data_p_max,
            thickness_params_by_index=thickness_params_by_index,
            thickness_method_by_index=thickness_method_by_index,
        )
        for index in self._visible_analysis_indices():
            result = self.results[index]
            x_values = x_by_index.get(index)
            if x_values is None or x_values.size < 2:
                continue
            x_min = float(np.nanmin(x_values))
            x_max = float(np.nanmax(x_values))
            if x_min >= x_max:
                continue
            is_active = result is active
            target_region = (
                raw_region
                if is_active and raw_region and not reset_region
                else self._t_plot_fit_range_for_result(result)
            )
            lo, hi = self._clamp_fit_region(target_region, x_min, x_max, False)
            thickness_params = thickness_params_by_index.get(index, self.t_plot_thickness_params)
            thickness_method = thickness_method_by_index.get(index, self.t_plot_thickness_method)
            item, _ = replace_t_plot_fit_line(
                self.t_plot,
                None,
                result,
                lo,
                hi,
                line_x_min=x_min,
                line_x_max=x_max,
                data_p_min=data_p_min,
                data_p_max=data_p_max,
                thickness_params=thickness_params,
                thickness_method=thickness_method,
                color=self._analysis_sample_color(index),
                name="线性拟合" if is_active else None,
                width=2 if is_active else 1,
            )
            if not is_active:
                continue
            self._t_plot_x_range = (x_min, x_max)
            self._t_plot_fit_line = item
            was_setting_t_plot_region = self._setting_t_plot_region
            self._setting_t_plot_region = True
            try:
                self._add_t_plot_region([lo, hi], [x_min, x_max])
            finally:
                self._setting_t_plot_region = was_setting_t_plot_region
            self._refresh_t_plot_selection(active, (lo, hi), data_p_min, data_p_max)

    def reset_t_plot_fit_to_default(self) -> None:
        active = self.active_result()
        if active is None:
            self._load_t_plot_settings_for_active()
            self.refresh_metrics()
            return
        self.custom_t_plot_settings.pop(id(active), None)
        self._clear_custom_t_plot_fit_range(active)
        self._load_t_plot_settings_for_active()
        pressure_range = self._current_pressure_region()
        p_min, p_max = pressure_range if pressure_range else (None, None)
        self._setting_t_plot_region = True
        try:
            self._refresh_t_plot_plot(active, p_min, p_max, reset_region=True)
        finally:
            self._setting_t_plot_region = False
        self._refresh_sample_t_plot_cell(self.active_index)
        self.refresh_metrics()

    @staticmethod
    def _clamp_fit_region(raw_region, x_min: float, x_max: float, reset: bool) -> tuple[float, float]:
        if raw_region and not reset:
            lo = max(x_min, min(float(raw_region[0]), x_max))
            hi = max(x_min, min(float(raw_region[1]), x_max))
            if lo < hi - 1e-10:
                return lo, hi
        return x_min, x_max

    def _remove_bet_region(self) -> None:
        if self.bet_region is None:
            return
        try:
            self.bet_region.sigRegionChanged.disconnect(self.on_bet_region_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self.bet_plot.removeItem(self.bet_region)
        except RuntimeError:
            pass
        self.bet_region = None

    def _add_bet_region(self, values: list, bounds: list) -> None:
        region = self._make_selection_region(values, bounds=bounds, movable=True)
        region.sigRegionChanged.connect(self.on_bet_region_changed)
        self.bet_plot.addItem(region, ignoreBounds=True)
        self.bet_region = region

    def on_bet_region_changed(self) -> None:
        if self._syncing_region_changes or self._setting_bet_region:
            return
        if not self._bet_region_pending:
            self._bet_region_pending = True
            QtCore.QTimer.singleShot(25, self._update_bet_from_region)

    def _update_bet_from_region(self) -> None:
        self._bet_region_pending = False
        active = self.active_result()
        if active is None:
            return
        bet_fit_range = self._current_bet_fit_range()
        if bet_fit_range is None:
            return
        self._set_custom_bet_fit_range(active, bet_fit_range)
        lx_min = self._bet_x_range[0] if self._bet_x_range else None
        lx_max = self._bet_x_range[1] if self._bet_x_range else None
        self._bet_fit_line, _ = replace_bet_fit_line(
            self.bet_plot, self._bet_fit_line, active,
            bet_fit_range[0], bet_fit_range[1],
            line_x_min=lx_min, line_x_max=lx_max,
        )
        data_p_min, data_p_max = self._bet_plot_p_range or BET_PLOT_RANGE
        self._refresh_bet_selection(active, bet_fit_range, data_p_min, data_p_max)
        self._refresh_sample_bet_cell(self.active_index)
        self.refresh_metrics()

    def _current_bet_fit_range(self) -> tuple[float, float] | None:
        if self.bet_region is None:
            return None
        try:
            lo, hi = self.bet_region.getRegion()
            return (min(float(lo), float(hi)), max(float(lo), float(hi)))
        except RuntimeError:
            return None

    # ── Langmuir region ──────────────────────────────────────────────────────

    def _remove_bet_selection(self) -> None:
        if self._bet_selection_item is None:
            return
        try:
            self.bet_plot.removeItem(self._bet_selection_item)
        except RuntimeError:
            pass
        self._bet_selection_item = None

    def _refresh_bet_selection(self, active, fit_range, data_p_min=None, data_p_max=None) -> None:
        self._remove_bet_selection()
        if fit_range is None:
            return
        self._bet_selection_item = plot_bet_selection(
            self.bet_plot,
            active,
            fit_range[0],
            fit_range[1],
            data_p_min=data_p_min,
            data_p_max=data_p_max,
        )

    def _remove_langmuir_selection(self) -> None:
        if self._langmuir_selection_item is None:
            return
        try:
            self.langmuir_plot.removeItem(self._langmuir_selection_item)
        except RuntimeError:
            pass
        self._langmuir_selection_item = None

    def _refresh_langmuir_selection(self, active, fit_range, data_p_min=None, data_p_max=None) -> None:
        self._remove_langmuir_selection()
        if fit_range is None:
            return
        self._langmuir_selection_item = plot_langmuir_selection(
            self.langmuir_plot,
            active,
            fit_range[0],
            fit_range[1],
            data_p_min=data_p_min,
            data_p_max=data_p_max,
        )

    def _remove_langmuir_region(self) -> None:
        if self.langmuir_region is None:
            return
        try:
            self.langmuir_region.sigRegionChanged.disconnect(self.on_langmuir_region_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self.langmuir_plot.removeItem(self.langmuir_region)
        except RuntimeError:
            pass
        self.langmuir_region = None

    def _add_langmuir_region(self, values: list, bounds: list) -> None:
        region = self._make_selection_region(values, bounds=bounds, movable=True)
        region.sigRegionChanged.connect(self.on_langmuir_region_changed)
        self.langmuir_plot.addItem(region, ignoreBounds=True)
        self.langmuir_region = region

    def on_langmuir_region_changed(self) -> None:
        if self._syncing_region_changes or self._setting_langmuir_region:
            return
        if not self._langmuir_region_pending:
            self._langmuir_region_pending = True
            QtCore.QTimer.singleShot(25, self._update_langmuir_from_region)

    def _update_langmuir_from_region(self) -> None:
        self._langmuir_region_pending = False
        active = self.active_result()
        if active is None:
            return
        fit_range = self._current_langmuir_fit_range()
        if fit_range is None:
            return
        self._set_custom_langmuir_fit_range(active, fit_range)
        lx_min = self._langmuir_x_range[0] if self._langmuir_x_range else None
        lx_max = self._langmuir_x_range[1] if self._langmuir_x_range else None
        self._langmuir_fit_line, _ = replace_langmuir_fit_line(
            self.langmuir_plot, self._langmuir_fit_line, active,
            fit_range[0], fit_range[1],
            line_x_min=lx_min, line_x_max=lx_max,
        )
        data_p_min, data_p_max = self._langmuir_plot_p_range or LANGMUIR_PLOT_RANGE
        self._refresh_langmuir_selection(active, fit_range, data_p_min, data_p_max)
        self._refresh_sample_langmuir_cell(self.active_index)
        self.refresh_metrics()

    def _current_langmuir_fit_range(self) -> tuple[float, float] | None:
        if self.langmuir_region is None:
            return None
        try:
            lo, hi = self.langmuir_region.getRegion()
            return (min(float(lo), float(hi)), max(float(lo), float(hi)))
        except RuntimeError:
            return None

    # ── t-Plot region ─────────────────────────────────────────────────────────

    def _remove_t_plot_selection(self) -> None:
        if self._t_plot_selection_item is None:
            return
        try:
            self.t_plot.removeItem(self._t_plot_selection_item)
        except RuntimeError:
            pass
        self._t_plot_selection_item = None

    def _refresh_t_plot_selection(self, active, fit_range, data_p_min=None, data_p_max=None) -> None:
        self._remove_t_plot_selection()
        if fit_range is None:
            return
        self._t_plot_selection_item = plot_t_plot_selection(
            self.t_plot,
            active,
            fit_range[0],
            fit_range[1],
            data_p_min=data_p_min,
            data_p_max=data_p_max,
            thickness_params=self.t_plot_thickness_params,
            thickness_method=self.t_plot_thickness_method,
        )

    def _remove_t_plot_region(self) -> None:
        if self.t_plot_region is None:
            return
        try:
            self.t_plot_region.sigRegionChanged.disconnect(self.on_t_plot_region_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self.t_plot.removeItem(self.t_plot_region)
        except RuntimeError:
            pass
        self.t_plot_region = None

    def _add_t_plot_region(self, values: list, bounds: list) -> None:
        region = self._make_selection_region(values, bounds=bounds, movable=True)
        region.sigRegionChanged.connect(self.on_t_plot_region_changed)
        self.t_plot.addItem(region, ignoreBounds=True)
        self.t_plot_region = region

    def on_t_plot_region_changed(self) -> None:
        if self._syncing_region_changes or self._setting_t_plot_region:
            return
        if not self._t_plot_region_pending:
            self._t_plot_region_pending = True
            QtCore.QTimer.singleShot(25, self._update_t_plot_from_region)

    def _update_t_plot_from_region(self) -> None:
        self._t_plot_region_pending = False
        active = self.active_result()
        if active is None:
            return
        fit_range = self._current_t_plot_fit_range()
        if fit_range is None:
            return
        self._set_custom_t_plot_fit_range(active, fit_range)
        lx_min = self._t_plot_x_range[0] if self._t_plot_x_range else None
        lx_max = self._t_plot_x_range[1] if self._t_plot_x_range else None
        p_min, p_max = self._t_plot_p_range if self._t_plot_p_range else T_PLOT_PLOT_RANGE
        self._t_plot_fit_line, _ = replace_t_plot_fit_line(
            self.t_plot, self._t_plot_fit_line, active,
            fit_range[0], fit_range[1],
            line_x_min=lx_min, line_x_max=lx_max,
            data_p_min=p_min, data_p_max=p_max,
            thickness_params=self.t_plot_thickness_params,
            thickness_method=self.t_plot_thickness_method,
        )
        self._refresh_t_plot_selection(active, fit_range, p_min, p_max)
        self._refresh_sample_t_plot_cell(self.active_index)
        self.refresh_metrics()

    def _current_t_plot_fit_range(self) -> tuple[float, float] | None:
        if self.t_plot_region is None:
            return None
        try:
            lo, hi = self.t_plot_region.getRegion()
            return (min(float(lo), float(hi)), max(float(lo), float(hi)))
        except RuntimeError:
            return None

    # ── reset all fit regions ─────────────────────────────────────────────────

    def _reset_all_fit_regions(self) -> None:
        self._remove_bet_region()
        self._remove_bet_selection()
        self._remove_langmuir_region()
        self._remove_langmuir_selection()
        self._remove_t_plot_region()
        self._remove_t_plot_selection()
        self._bet_fit_line = None
        self._bet_x_range = None
        self._bet_plot_p_range = None
        self._langmuir_fit_line = None
        self._langmuir_x_range = None
        self._langmuir_plot_p_range = None
        self._t_plot_fit_line = None
        self._t_plot_x_range = None
        self._t_plot_p_range = None

    def refresh_metrics(self) -> None:
        active = self.active_result()
        if active is None:
            self.metrics_table.setRowCount(0)
            return
        bet_fit_range = self._current_bet_fit_range() or self._bet_fit_range_for_result(active)
        langmuir_fit_range = self._current_langmuir_fit_range() or self._langmuir_fit_range_for_result(active)
        t_plot_fit_range = self._current_t_plot_fit_range() or self._t_plot_fit_range_for_result(active)
        rows = active_metric_rows(
            active,
            self._current_pressure_region(),
            bet_fit_range,
            langmuir_fit_range,
            t_plot_fit_range,
            t_plot_thickness_method=self.t_plot_thickness_method,
            t_plot_thickness_params=self.t_plot_thickness_params,
            t_plot_surface_area_mode=self.t_plot_surface_area_mode,
            t_plot_input_surface_area=self.t_plot_surface_area_input,
            t_plot_surface_area_correction=self.t_plot_surface_area_correction,
        )
        self._fill_two_column_table(self.metrics_table, rows)

    def refresh_condition_table(self) -> None:
        active = self.active_result()
        if active is None:
            self.condition_table.setRowCount(0)
            return
        rows = condition_rows(active)
        self._fill_two_column_table(self.condition_table, rows)

    def refresh_isotherm_table(self) -> None:
        active = self.active_result()
        if active is None:
            self.isotherm_table.setRowCount(0)
            return
        self.isotherm_table.setRowCount(len(active.isotherm))
        for row, point in enumerate(active.isotherm):
            values = [
                point.index,
                "吸附" if point.phase == "adsorption" else "脱附",
                _fmt(point.relative_pressure, 9),
                _fmt(point.absolute_pressure_mmHg, 6),
                _fmt(point.quantity_adsorbed_cm3_g_stp, 6),
                _fmt(point.quantity_adsorbed_mmol_g, 6),
                _fmt(point.saturation_pressure_mmHg, 6),
                point.elapsed_time,
            ]
            for column, value in enumerate(values):
                self._set_table_item(self.isotherm_table, row, column, str(value))

    def refresh_target_table(self) -> None:
        active = self.active_result()
        if active is None:
            self.target_table.setRowCount(0)
            return
        self.target_table.setRowCount(len(active.target_pressure_table))
        for row, item in enumerate(active.target_pressure_table):
            values = [
                item.row,
                "吸附" if item.branch == "adsorption" else "脱附",
                _fmt(item.starting_pressure_p_po, 9),
                _fmt(item.ending_pressure_p_po, 9),
                _fmt(item.pressure_increment_p_po, 9),
                item.ending_pressure_rel_offset,
            ]
            for column, value in enumerate(values):
                self._set_table_item(self.target_table, row, column, str(value))

    def refresh_log_table(self) -> None:
        active = self.active_result()
        if active is None:
            self.log_table.setRowCount(0)
            return
        rows = []
        rows.extend(("SUBSET705", item.rel_offset, item.text) for item in active.log_messages)
        rows.extend(("SUBSET1021", item.rel_offset, item.text) for item in active.sample_tube_strings)
        self.log_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                self._set_table_item(self.log_table, row, column, str(value))

    def refresh_report_module_table(self) -> None:
        active = self.active_result()
        if active is None:
            self.report_module_table.setRowCount(0)
            return
        report_subset_ids = (311, 312, 314, 315, 316, 331, 332)
        rows = []
        for subset_id in report_subset_ids:
            for item in active.raw_strings.get(subset_id, []):
                rows.append((f"SUBSET{subset_id}", item.rel_offset, item.text))
        self.report_module_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                self._set_table_item(self.report_module_table, row, column, str(value))

    def active_result(self):
        if self.active_index < 0 or self.active_index >= len(self.results):
            return None
        return self.results[self.active_index]

    def on_select_all_changed(self, state: int) -> None:
        if self._updating_sample_checks or not self.visible_results:
            return
        state_value = _check_state_value(state)
        if state_value == _check_state_value(QtCore.Qt.PartiallyChecked):
            return
        checked = state_value == _check_state_value(QtCore.Qt.Checked)
        self.visible_results = [checked] * len(self.visible_results)

        self.sample_table.blockSignals(True)
        for item in self.sample_items:
            item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        self.sample_table.blockSignals(False)

        self._refresh_visibility_dependent_ui()

    def _sync_select_all_state(self) -> None:
        if not hasattr(self, "select_all_check"):
            return
        if not self.visible_results:
            state = QtCore.Qt.Unchecked
        elif all(self.visible_results):
            state = QtCore.Qt.Checked
        elif any(self.visible_results):
            state = QtCore.Qt.PartiallyChecked
        else:
            state = QtCore.Qt.Unchecked

        self._updating_sample_checks = True
        self.select_all_check.setEnabled(bool(self.visible_results))
        self.select_all_check.setCheckState(state)
        self._updating_sample_checks = False

    def _position_header_controls(self, *args) -> None:
        header = self.sample_table.frozen_header()
        if not header.isVisible():
            return
        size = self.select_all_check.sizeHint()
        x = header.sectionViewportPosition(VISIBLE_COLUMN) + (header.sectionSize(VISIBLE_COLUMN) - size.width()) // 2
        y = (header.height() - size.height()) // 2
        self.select_all_check.setVisible(x + size.width() > 0 and x < header.width())
        self.select_all_check.setGeometry(x, y, size.width(), size.height())

    def _refresh_visibility_dependent_ui(self) -> None:
        self.refresh_isotherm_plot()
        self.refresh_sample_table()
        self.refresh_metrics()
        self.refresh_analysis_plots()

    def _clear_analysis_plots(self) -> None:
        self._remove_bet_region()
        self._remove_bet_selection()
        self._remove_langmuir_region()
        self._remove_langmuir_selection()
        self._remove_t_plot_region()
        self._remove_t_plot_selection()
        self._bet_plot_p_range = None
        self._langmuir_plot_p_range = None
        self._t_plot_p_range = None
        for plot in (self.bet_plot, self.langmuir_plot, self.t_plot, self.pore_plot):
            plot.clear()

    def _all_pressure_values(self) -> np.ndarray:
        values = []
        for result, visible in zip(self.results, self.visible_results):
            if not visible:
                continue
            for point in result.isotherm:
                try:
                    pressure = float(point.relative_pressure)
                except (TypeError, ValueError):
                    continue
                if np.isfinite(pressure):
                    values.append(pressure)
        return np.asarray(values, dtype=float)

    @staticmethod
    def _default_pressure_region(pressure: np.ndarray) -> list[float]:
        data_min = float(np.nanmin(pressure))
        data_max = float(np.nanmax(pressure))
        if data_min == data_max:
            return [data_min - 0.01, data_max + 0.01]
        bet_min = max(data_min, 0.05)
        bet_max = min(data_max, 0.30)
        if bet_min < bet_max:
            return [bet_min, bet_max]
        span = data_max - data_min
        return [data_min + span * 0.25, data_min + span * 0.55]

    def _default_isotherm_region(self, pressure: np.ndarray) -> list[float]:
        if self._is_bjh_tab_active():
            return self._full_pressure_region(pressure)
        return self._default_pressure_region(pressure)

    @staticmethod
    def _full_pressure_region(pressure: np.ndarray) -> list[float]:
        data_min = float(np.nanmin(pressure))
        data_max = float(np.nanmax(pressure))
        if data_min == data_max:
            return [data_min - 0.01, data_max + 0.01]
        return [data_min, data_max]

    def _is_bjh_tab_active(self) -> bool:
        return getattr(self, "plot_tabs", None) is not None and self.plot_tabs.currentWidget() is self.bjh_tab

    def _clamp_pressure_region(self, raw_region: list[float] | tuple[float, float], pressure: np.ndarray) -> list[float]:
        data_min = float(np.nanmin(pressure))
        data_max = float(np.nanmax(pressure))
        region_min, region_max = sorted((float(raw_region[0]), float(raw_region[1])))
        region_min = max(data_min, min(region_min, data_max))
        region_max = max(data_min, min(region_max, data_max))
        if region_min < region_max:
            return [region_min, region_max]
        return self._default_pressure_region(pressure)

    def _make_selection_region(self, values, bounds=None, movable: bool = True):
        region = pg.LinearRegionItem(
            values,
            bounds=bounds,
            movable=movable,
            brush=pg.mkBrush(*REGION_FILL_COLOR),
            hoverBrush=pg.mkBrush(*REGION_FILL_HOVER_COLOR),
            pen=_region_pen(REGION_LINE_COLOR),
            hoverPen=_region_pen(REGION_LINE_HOVER_COLOR),
            swapMode="block",
        )
        for line in getattr(region, "lines", []):
            line.setPen(_region_pen(REGION_LINE_COLOR))
            line.setHoverPen(_region_pen(REGION_LINE_HOVER_COLOR))
            line.setCursor(QtCore.Qt.SizeHorCursor)
        return region

    def _remove_isotherm_selection(self) -> None:
        for item in self._isotherm_selection_items:
            try:
                self.isotherm_plot.removeItem(item)
            except RuntimeError:
                pass
        self._isotherm_selection_items = []

    def _refresh_isotherm_selection(self, pressure_range=None) -> None:
        self._remove_isotherm_selection()
        if pressure_range is None:
            pressure_range = self._current_pressure_region()
        if pressure_range is None:
            return
        self._isotherm_selection_items = plot_isotherm_selection(
            self.isotherm_plot,
            self.results,
            self.visible_results,
            self.sample_colors,
            pressure_range,
            active_index=self.active_index,
        )

    def _add_region(self, raw_region: list[float] | tuple[float, float], pressure: np.ndarray) -> None:
        if pressure.size == 0:
            return
        region = self._clamp_pressure_region(raw_region, pressure)
        self._setting_isotherm_region = True
        try:
            self.region = self._make_selection_region(
                region,
                bounds=[float(np.nanmin(pressure)), float(np.nanmax(pressure))],
                movable=True,
            )
            self.isotherm_plot.addItem(self.region, ignoreBounds=True)
            self.region.sigRegionChanged.connect(self.on_region_changed)
            if hasattr(self.region, "sigRegionChangeFinished"):
                self.region.sigRegionChangeFinished.connect(self.on_region_change_finished)
        finally:
            self._setting_isotherm_region = False

    def _remove_region(self) -> None:
        if self.region is None:
            return
        try:
            self.region.sigRegionChanged.disconnect(self.on_region_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            if hasattr(self.region, "sigRegionChangeFinished"):
                self.region.sigRegionChangeFinished.disconnect(self.on_region_change_finished)
        except (RuntimeError, TypeError):
            pass
        try:
            self.isotherm_plot.removeItem(self.region)
        except RuntimeError:
            pass
        self.region = None

    def on_region_changed(self) -> None:
        if self._syncing_region_changes:
            return
        self._mark_isotherm_region_custom()
        self._refresh_isotherm_selection(self._current_pressure_region())
        self.queue_metrics_update()

    def on_region_change_finished(self) -> None:
        if self._syncing_region_changes:
            return
        self._mark_isotherm_region_custom()
        self._refresh_isotherm_selection(self._current_pressure_region())
        self.queue_metrics_update()

    def _mark_isotherm_region_custom(self) -> None:
        if not self._setting_isotherm_region:
            self._isotherm_region_custom = True

    def queue_metrics_update(self) -> None:
        if self._metrics_pending:
            return
        self._metrics_pending = True
        QtCore.QTimer.singleShot(25, self.update_metrics_from_region)

    def update_metrics_from_region(self) -> None:
        self._metrics_pending = False
        self.refresh_sample_table()
        self._update_analysis_plots_for_region()
        self.refresh_metrics()

    def _current_pressure_region(self) -> tuple[float, float] | None:
        if self.region is None:
            return None
        try:
            region_min, region_max = self.region.getRegion()
        except RuntimeError:
            return None
        lo, hi = sorted((float(region_min), float(region_max)))
        return (lo, hi)

    def _analysis_bundle_for_range(self, result, pressure_range: tuple[float, float] | None):
        if pressure_range is None:
            return analysis_bundle(result)
        return analysis_bundle(result, pressure_range[0], pressure_range[1])

    def _bet_fit_range_for_result(self, result) -> tuple[float, float]:
        return self.custom_bet_fit_ranges.get(id(result), BET_DEFAULT_RANGE)

    def _is_custom_bet_fit(self, result) -> bool:
        return id(result) in self.custom_bet_fit_ranges

    def _set_custom_bet_fit_range(self, result, fit_range: tuple[float, float]) -> None:
        lo, hi = sorted((float(fit_range[0]), float(fit_range[1])))
        self.custom_bet_fit_ranges[id(result)] = (lo, hi)

    def _clear_custom_bet_fit_range(self, result) -> None:
        self.custom_bet_fit_ranges.pop(id(result), None)

    def _bet_analysis_for_result(self, result):
        fit_range = self._bet_fit_range_for_result(result)
        return bet_analysis(result, fit_range[0], fit_range[1])

    def _langmuir_fit_range_for_result(self, result) -> tuple[float, float]:
        return self.custom_langmuir_fit_ranges.get(id(result), LANGMUIR_DEFAULT_RANGE)

    def _is_custom_langmuir_fit(self, result) -> bool:
        return id(result) in self.custom_langmuir_fit_ranges

    def _set_custom_langmuir_fit_range(self, result, fit_range: tuple[float, float]) -> None:
        lo, hi = sorted((float(fit_range[0]), float(fit_range[1])))
        self.custom_langmuir_fit_ranges[id(result)] = (lo, hi)

    def _clear_custom_langmuir_fit_range(self, result) -> None:
        self.custom_langmuir_fit_ranges.pop(id(result), None)

    def _langmuir_analysis_for_result(self, result):
        fit_range = self._langmuir_fit_range_for_result(result)
        return langmuir_analysis(result, fit_range[0], fit_range[1])

    def _default_t_plot_fit_range(
        self,
        thickness_method: str | None = None,
        thickness_params: dict[str, float] | None = None,
    ) -> tuple[float, float]:
        p_min, p_max = T_PLOT_DEFAULT_PRESSURE_RANGE
        thickness_method = thickness_method or self.t_plot_thickness_method
        thickness_params = thickness_params or self.t_plot_thickness_params
        t_values = [
            value
            for value in (
                thickness_nm(p_min, thickness_method, thickness_params),
                thickness_nm(p_max, thickness_method, thickness_params),
            )
            if value is not None
        ]
        if len(t_values) == 2:
            return (min(t_values), max(t_values))
        return T_PLOT_DEFAULT_PRESSURE_RANGE

    def _t_plot_fit_range_for_result(self, result) -> tuple[float, float]:
        settings = self._t_plot_settings_for_result(result)
        return self.custom_t_plot_fit_ranges.get(
            id(result),
            self._default_t_plot_fit_range(
                str(settings["thickness_method"]),
                dict(settings["thickness_params"]),
            ),
        )

    def _is_custom_t_plot_fit(self, result) -> bool:
        return id(result) in self.custom_t_plot_fit_ranges or self._has_custom_t_plot_settings(result)

    def _has_custom_t_plot_settings(self, result) -> bool:
        if id(result) not in self.custom_t_plot_settings:
            return False
        settings = self._t_plot_settings_for_result(result)
        method = str(settings["thickness_method"])
        if method != DEFAULT_T_PLOT_THICKNESS_METHOD:
            return True

        default_params = T_PLOT_THICKNESS_PARAM_DEFAULTS.get(method, DEFAULT_T_PLOT_THICKNESS_PARAMS)
        params_by_method = dict(settings.get("thickness_params_by_method", {}))
        active_params = dict(settings.get("thickness_params") or params_by_method.get(method, {}))
        for key, default_value in default_params.items():
            if not _float_equal(active_params.get(key), default_value):
                return True

        if str(settings["surface_area_mode"]) != DEFAULT_T_PLOT_SURFACE_AREA_MODE:
            return True
        if not _float_equal(settings["surface_area_correction"], DEFAULT_T_PLOT_SURFACE_AREA_CORRECTION):
            return True
        return False

    def _set_custom_t_plot_fit_range(self, result, fit_range: tuple[float, float]) -> None:
        lo, hi = sorted((float(fit_range[0]), float(fit_range[1])))
        self.custom_t_plot_fit_ranges[id(result)] = (lo, hi)

    def _clear_custom_t_plot_fit_range(self, result) -> None:
        self.custom_t_plot_fit_ranges.pop(id(result), None)

    def _t_plot_analysis_for_result(self, result):
        settings = self._t_plot_settings_for_result(result)
        fit_range = self._t_plot_fit_range_for_result(result)
        return t_plot_analysis_by_thickness(
            result,
            fit_range[0],
            fit_range[1],
            thickness_params=dict(settings["thickness_params"]),
            thickness_method=str(settings["thickness_method"]),
        )

    @staticmethod
    def _test_time_sort_key(result) -> tuple[int, str]:
        return (int(result.header.created_raw or 0), str(result.header.created_time or ""))

    def _bet_sort_key(self, result) -> float:
        try:
            value = self._bet_analysis_for_result(result).surface_area_m2_g
            return float(value) if value is not None else 0.0
        except Exception:
            return 0.0

    def _langmuir_sort_key(self, result) -> float:
        try:
            value = self._langmuir_analysis_for_result(result).surface_area_m2_g
            return float(value) if value is not None else 0.0
        except Exception:
            return 0.0

    def _t_plot_sort_key(self, result) -> float:
        try:
            value = self._t_plot_analysis_for_result(result).external_surface_area_m2_g
            return float(value) if value is not None else 0.0
        except Exception:
            return 0.0

    def _bjh_pore_volume_sort_key(self, result) -> float:
        try:
            value = self._bjh_pore_volume_for_result(result)
            return float(value) if value is not None else 0.0
        except Exception:
            return 0.0

    def _bjh_pore_volume_for_result(self, result) -> float | None:
        settings = self._bjh_settings_for_result(result)
        phase = self._bjh_pore_volume_phase(settings)
        if phase is None:
            return None
        return bjh_pore_volume_cm3_g(
            result,
            2.0,
            10.0,
            phase=phase,
            thickness_method=str(settings["thickness_method"]),
            thickness_params=dict(settings["thickness_params"]),
            correction=str(settings["correction"]),
            open_pore_fraction=float(settings["open_pore_fraction"]),
        )

    def _has_custom_bjh_settings(self, result) -> bool:
        settings = self._bjh_settings_for_result(result)
        method = str(settings["thickness_method"])
        if method != DEFAULT_T_PLOT_THICKNESS_METHOD:
            return True
        default_params = T_PLOT_THICKNESS_PARAM_DEFAULTS.get(
            method,
            DEFAULT_T_PLOT_THICKNESS_PARAMS,
        )
        active_params = dict(settings["thickness_params"])
        for key, default_value in default_params.items():
            if not _float_equal(active_params.get(key), default_value):
                return True
        if str(settings["correction"]) != DEFAULT_BJH_CORRECTION:
            return True
        if not _float_equal(settings["open_pore_fraction"], DEFAULT_BJH_OPEN_PORE_FRACTION):
            return True
        if bool(settings["smooth_derivative"]) != DEFAULT_BJH_SMOOTH_DERIVATIVE:
            return True
        if bool(settings["show_adsorption"]) != DEFAULT_BJH_SHOW_ADSORPTION:
            return True
        if bool(settings["show_desorption"]) != DEFAULT_BJH_SHOW_DESORPTION:
            return True
        return False

    def _bjh_pore_volume_phase(self, settings: dict[str, object] | None = None) -> str | None:
        if settings is None:
            show_adsorption = self.bjh_show_adsorption
            show_desorption = self.bjh_show_desorption
        else:
            show_adsorption = bool(settings["show_adsorption"])
            show_desorption = bool(settings["show_desorption"])
        if show_adsorption:
            return "adsorption"
        if show_desorption:
            return "desorption"
        return None

    def _resize_sample_columns(self) -> None:
        defaults = {
            VISIBLE_COLUMN: 30,
            FILE_COLUMN: 170,
            TEST_TIME_COLUMN: 250,
            BET_COLUMN: 120,
            LANGMUIR_COLUMN: 200,
            T_PLOT_COLUMN: 200,
            BJH_PORE_VOLUME_COLUMN: 190,
        }
        self._updating_sample_column_widths = True
        try:
            if not self._sample_column_widths_initialized:
                for column, width in defaults.items():
                    self.sample_table.setColumnWidth(column, width)
                self.sample_column_widths = {
                    column: self.sample_table.columnWidth(column)
                    for column in range(self.sample_table.columnCount())
                }
                self._sample_column_widths_initialized = True
            else:
                for column in range(self.sample_table.columnCount()):
                    width = self.sample_column_widths.get(column, defaults.get(column))
                    if width is not None:
                        self.sample_table.setColumnWidth(column, width)
        finally:
            self._updating_sample_column_widths = False
        self._position_header_controls()

    def _make_table(self, headers: list[str]) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _fill_two_column_table(self, table: QtWidgets.QTableWidget, rows: list[tuple[str, str]]) -> None:
        table.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            self._set_table_item(table, row, 0, name)
            self._set_table_item(table, row, 1, value)

    def _set_table_item(self, table: QtWidgets.QTableWidget, row: int, column: int, text: str) -> None:
        table.setItem(row, column, self._table_item(text))

    def _table_item(self, text: str, *, tooltip: str | None = None, alignment=None) -> QtWidgets.QTableWidgetItem:
        item = QtWidgets.QTableWidgetItem(str(text))
        item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        item.setForeground(QtGui.QBrush(QtGui.QColor("#111827")))
        if tooltip:
            item.setToolTip(tooltip)
        if alignment is not None:
            item.setTextAlignment(alignment)
        return item


def active_metric_rows(
    result,
    pressure_range: tuple[float, float] | None = None,
    bet_fit_range: tuple[float, float] | None = None,
    langmuir_fit_range: tuple[float, float] | None = None,
    t_plot_fit_range: tuple[float, float] | None = None,
    t_plot_thickness_method: str = DEFAULT_T_PLOT_THICKNESS_METHOD,
    t_plot_thickness_params: dict[str, float] | None = None,
    t_plot_surface_area_mode: str = "BET",
    t_plot_input_surface_area: float | None = None,
    t_plot_surface_area_correction: float = SURFACE_AREA_CORRECTION_FACTOR,
) -> list[tuple[str, str]]:
    analyses = analysis_bundle(result) if pressure_range is None else analysis_bundle(result, pressure_range[0], pressure_range[1])

    from tristar_bet.analysis import bet_analysis as _bet_analysis
    if bet_fit_range is not None:
        bet = _bet_analysis(result, bet_fit_range[0], bet_fit_range[1])
    else:
        bet = analyses["BET"]
    if langmuir_fit_range is not None:
        langmuir = langmuir_analysis(result, langmuir_fit_range[0], langmuir_fit_range[1])
    else:
        langmuir = analyses["Langmuir"]
    if t_plot_fit_range is not None:
        if pressure_range is None:
            t_plot = t_plot_analysis_by_thickness(
                result,
                t_plot_fit_range[0],
                t_plot_fit_range[1],
                thickness_params=t_plot_thickness_params,
                thickness_method=t_plot_thickness_method,
            )
        else:
            t_plot = t_plot_analysis_by_thickness(
                result,
                t_plot_fit_range[0],
                t_plot_fit_range[1],
                pressure_range[0],
                pressure_range[1],
                t_plot_thickness_params,
                t_plot_thickness_method,
            )
    else:
        t_plot = analyses["t-Plot"]

    rows = [
        ("文件名", _display_file_name(result)),
        ("样品名称", result.sample_name),
        ("测试时间", result.header.created_time),
        ("设备厂家", _instrument_manufacturer(result)),
        ("设备型号", _instrument_model(result)),
        ("当前选区", _pressure_range_text(pressure_range)),
    ]
    if bet_fit_range is not None:
        rows.append(("BET 拟合区间", _pressure_range_text(bet_fit_range)))
    if langmuir_fit_range is not None:
        rows.append(("Langmuir 拟合区间", _pressure_range_text(langmuir_fit_range)))
    if t_plot_fit_range is not None:
        rows.append(("t-Plot 厚度区间", _thickness_range_text(t_plot_fit_range)))
    t_plot_regression = _t_plot_mmol_regression(t_plot)
    t_plot_correlation = _correlation_from_r_squared(t_plot.r_squared)
    t_plot_total_surface_area = _t_plot_total_surface_area(
        t_plot_surface_area_mode,
        bet.surface_area_m2_g,
        langmuir.surface_area_m2_g,
        t_plot_input_surface_area,
    )
    micropore_area = _micropore_area_m2_g(
        t_plot_total_surface_area,
        t_plot.external_surface_area_m2_g,
        t_plot_surface_area_correction,
    )
    rows += [
        ("样品质量", f"{_fmt(result.sample.sample_mass_g)} g"),
        ("吸附质", _adsorptive_label(result)),
        ("数据点数", str(result.point_count)),
        ("BET 状态", status_text(bet.status)),
        ("BET 比表面积", f"{_fmt(bet.surface_area_m2_g)} m2/g"),
        ("BET 单层容量", f"{_fmt(bet.monolayer_capacity_cm3_g_stp)} cm3/g STP"),
        ("BET C 常数", _fmt(bet.c_constant)),
        ("BET R2", _fmt(bet.r_squared, 6)),
        ("Langmuir 状态", status_text(langmuir.status)),
        ("Langmuir 比表面积", f"{_fmt(langmuir.surface_area_m2_g)} m2/g"),
        ("Langmuir 单层容量", f"{_fmt(langmuir.monolayer_capacity_cm3_g_stp)} cm3/g STP"),
        ("Langmuir R2", _fmt(langmuir.r_squared, 6)),
        ("t-Plot 状态", status_text(t_plot.status)),
        ("t-Plot 微孔体积", f"{_fmt(t_plot.micropore_volume_cm3_g)} cm3/g"),
        ("t-Plot 微孔面积", f"{_fmt(micropore_area)} m2/g"),
        ("t-Plot 外比表面积", f"{_fmt(t_plot.external_surface_area_m2_g)} m2/g"),
        ("t-Plot 斜率", _value_pm_text(t_plot_regression["slope"], t_plot_regression["slope_se"], "mmol/g/nm")),
        ("t-Plot Y 截距", _value_pm_text(t_plot_regression["intercept"], t_plot_regression["intercept_se"], "mmol/g")),
        ("t-Plot 相关系数", _fmt(t_plot_correlation, 6)),
        ("t-Plot R2", _fmt(t_plot.r_squared, 6)),
        ("t-Plot 比表面积修正因子", _fmt(t_plot_surface_area_correction, 3)),
        ("t-Plot 密度转换因子", _fmt(density_conversion_factor(result), 9)),
        (f"t-Plot 总表面积({_t_plot_surface_area_label(t_plot_surface_area_mode)})", f"{_fmt(t_plot_total_surface_area)} m2/g"),
        ("t-Plot 厚度方程", _t_plot_thickness_label(t_plot_thickness_method)),
        ("自由空间来源", result.free_space.vfree_factor_source),
    ]
    return rows


def condition_rows(result) -> list[tuple[str, str]]:
    sample = result.sample
    run = result.run_conditions
    free = result.free_space
    props = result.adsorptive_properties
    rows = [
        ("文件路径", result.header.file_path),
        ("文件创建时间", result.header.created_time),
        ("文件修改时间", result.header.modified_time),
        ("样品名称", result.sample_name),
        ("操作员", sample.operator),
        ("样品质量", f"{_fmt(sample.sample_mass_g)} g"),
        ("样品密度", f"{_fmt(sample.sample_density_g_cm3)} g/cm3"),
        ("吸附质助记符", run.adsorptive_short),
        ("吸附质名称", run.adsorptive_name),
        ("浴温", f"{_fmt(run.bath_temperature_K)} K"),
        ("Po 参考压力", f"{_fmt(run.po_reference_mmHg)} mmHg"),
        ("平衡间隔", f"{_fmt(run.equilibration_interval_s)} s"),
        ("自由空间平衡时间", f"{_fmt(run.free_space_equilibration_time_h)} h"),
        ("输入常温自由空间", f"{_fmt(run.ambient_free_space_entered_cm3)} cm3"),
        ("输入分析自由空间", f"{_fmt(run.analysis_free_space_entered_cm3)} cm3"),
        ("实际温自由空间", f"{_fmt(free.warm_free_space_cm3)} cm3"),
        ("冷自由空间", f"{_fmt(free.cold_free_space_cm3)} cm3"),
        ("Stem volume", f"{_fmt(free.stem_volume_cm3)} cm3"),
        ("Vbath", f"{_fmt(free.vbath_cm3)} cm3"),
        ("Vfree factor", f"{_fmt(free.vfree_factor_cm3)} cm3"),
        ("非理想因子", _fmt(free.nonideality_factor, 9)),
    ]
    if props is not None:
        rows.extend(
            [
                ("吸附质属性", props.adsorptive),
                ("最大歧管压力", f"{_fmt(props.max_manifold_pressure_kPa)} kPa"),
                ("分子截面积", f"{_fmt(props.molecular_cross_sectional_area_nm2)} nm2"),
                ("密度转换因子", _fmt(props.density_conversion_factor, 9)),
                ("Psat 表行数", str(len(props.psat_table))),
            ]
        )
    return rows


def status_text(status: str) -> str:
    return {
        "ok": "区间计算完成",
        "warning_negative_c": "区间计算完成；BET C<=0，需核对报告选点",
        "not_enough_points": "区间有效点不足",
        "not_enough_valid_points": "区间有效数值不足",
        "invalid_monolayer_capacity": "单层容量无效",
    }.get(status, status)


def _display_file_name(result) -> str:
    return Path(result.file_name).stem


def _instrument_manufacturer(result) -> str:
    value = result.method_options.get("instrument_manufacturer", "")
    return str(value) if value else "Micromeritics"


def _instrument_model(result) -> str:
    value = result.method_options.get("instrument_model", "")
    return str(value) if value else "TriStar II"


def _adsorptive_label(result) -> str:
    value = result.run_conditions.adsorptive_short or result.run_conditions.adsorptive_name
    if value:
        return value
    props = result.adsorptive_properties
    if props is not None:
        return props.mnemonic or props.adsorptive
    return ""


def _pressure_range_text(pressure_range: tuple[float, float] | None) -> str:
    if pressure_range is None:
        return "默认算法区间"
    return f"P/P0 {_fmt(pressure_range[0], 6)} - {_fmt(pressure_range[1], 6)}"


def _thickness_range_text(thickness_range: tuple[float, float] | None) -> str:
    if thickness_range is None:
        return "默认厚度区间"
    return f"{_fmt(thickness_range[0], 6)} - {_fmt(thickness_range[1], 6)} nm"


def _micropore_area_m2_g(total_surface_area, external_surface_area, correction_factor: float = 1.0):
    if total_surface_area is None or external_surface_area is None:
        return None
    try:
        return max(0.0, float(total_surface_area) - float(external_surface_area) * float(correction_factor))
    except (TypeError, ValueError):
        return None


def _t_plot_total_surface_area(mode: str, bet_area, langmuir_area, input_area):
    if mode == "Langmuir":
        return langmuir_area
    if mode == "Input":
        return input_area
    return bet_area


def _t_plot_surface_area_label(mode: str) -> str:
    if mode == "Langmuir":
        return "Langmuir"
    if mode == "Input":
        return "输入"
    return "BET"


def _correlation_from_r_squared(r_squared):
    if r_squared is None:
        return None
    try:
        return math.sqrt(max(0.0, float(r_squared)))
    except (TypeError, ValueError):
        return None


def _value_pm_text(value, error=None, unit: str = "") -> str:
    if value is None:
        return "n/a"
    text = _fmt(value, 6)
    if error is not None:
        text += f" ± {_fmt(error, 6)}"
    if unit:
        text += f" {unit}"
    return text


def _t_plot_mmol_regression(t_plot) -> dict[str, float | None]:
    result = {"slope": None, "slope_se": None, "intercept": None, "intercept_se": None}
    rows = getattr(t_plot, "rows", None) or []
    x_values = []
    y_values = []
    for row in rows:
        try:
            x = float(row["thickness_nm"])
            y = float(row["quantity_adsorbed_cm3_g_stp"]) / CM3_STP_PER_MMOL
        except (KeyError, TypeError, ValueError):
            continue
        if np.isfinite(x) and np.isfinite(y):
            x_values.append(x)
            y_values.append(y)
    if len(x_values) < 2:
        return result

    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    result["slope"] = float(slope)
    result["intercept"] = float(intercept)

    if x.size <= 2:
        return result
    fitted = slope * x + intercept
    residual = y - fitted
    sxx = float(np.sum((x - np.mean(x)) ** 2))
    if sxx <= 0.0:
        return result
    residual_variance = float(np.sum(residual ** 2)) / float(x.size - 2)
    result["slope_se"] = math.sqrt(residual_variance / sxx)
    result["intercept_se"] = math.sqrt(residual_variance * (1.0 / x.size + float(np.mean(x)) ** 2 / sxx))
    return result


def export_results_xlsx(results, path: str | Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "摘要"
    _write_rows(
        summary_sheet,
        [
            [
                "文件",
                "样品",
                "质量(g)",
                "点数",
                "BET状态",
                "BET面积(m2/g)",
                "BET Vm(cm3/g)",
                "BET C",
                "Langmuir状态",
                "Langmuir面积(m2/g)",
                "t-Plot状态",
                "t-Plot外比表面积(m2/g)",
                "t-Plot微孔体积(cm3/g)",
            ]
        ],
        bold_first=True,
    )
    for result in results:
        analyses = analysis_bundle(result)
        bet = analyses["BET"]
        langmuir = analyses["Langmuir"]
        t_plot = analyses["t-Plot"]
        summary_sheet.append(
            [
                result.file_name,
                result.sample_name,
                result.sample.sample_mass_g,
                result.point_count,
                status_text(bet.status),
                bet.surface_area_m2_g,
                bet.monolayer_capacity_cm3_g_stp,
                bet.c_constant,
                status_text(langmuir.status),
                langmuir.surface_area_m2_g,
                status_text(t_plot.status),
                t_plot.external_surface_area_m2_g,
                t_plot.micropore_volume_cm3_g,
            ]
        )

    isotherm_sheet = workbook.create_sheet("实际等温线")
    isotherm_sheet.append(
        [
            "文件",
            "样品",
            "点",
            "阶段",
            "P/P0",
            "压力(mmHg)",
            "吸附量(cm3/g STP)",
            "吸附量(mmol/g)",
            "Po(mmHg)",
            "Elapsed(s)",
            "Elapsed",
        ]
    )
    for result in results:
        for point in result.isotherm:
            isotherm_sheet.append(
                [
                    result.file_name,
                    result.sample_name,
                    point.index,
                    "吸附" if point.phase == "adsorption" else "脱附",
                    point.relative_pressure,
                    point.absolute_pressure_mmHg,
                    point.quantity_adsorbed_cm3_g_stp,
                    point.quantity_adsorbed_mmol_g,
                    point.saturation_pressure_mmHg,
                    point.elapsed_seconds,
                    point.elapsed_time,
                ]
            )

    target_sheet = workbook.create_sheet("目标压力表")
    target_sheet.append(["文件", "样品", "行", "阶段", "起始P/P0", "终止P/P0", "步长P/P0", "偏移"])
    for result in results:
        for item in result.target_pressure_table:
            target_sheet.append(
                [
                    result.file_name,
                    result.sample_name,
                    item.row,
                    "吸附" if item.branch == "adsorption" else "脱附",
                    item.starting_pressure_p_po,
                    item.ending_pressure_p_po,
                    item.pressure_increment_p_po,
                    item.ending_pressure_rel_offset,
                ]
            )

    for name in ("BET", "Langmuir", "t-Plot"):
        sheet = workbook.create_sheet(name)
        sheet.append(["文件", "样品", "字段", "值"])
        for result in results:
            fit = analysis_bundle(result)[name]
            for key, value in fit.__dict__.items():
                if key == "rows":
                    continue
                sheet.append([result.file_name, result.sample_name, key, value])
            sheet.append([])
            if fit.rows:
                headers = list(fit.rows[0].keys())
                sheet.append(["文件", "样品", *headers])
                for row in fit.rows:
                    sheet.append([result.file_name, result.sample_name, *[row.get(header) for header in headers]])

    conditions_sheet = workbook.create_sheet("样品条件")
    conditions_sheet.append(["文件", "样品", "字段", "值"])
    for result in results:
        for name, value in condition_rows(result):
            conditions_sheet.append([result.file_name, result.sample_name, name, value])

    for sheet in workbook.worksheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        for column_cells in sheet.columns:
            length = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(length + 2, 10), 48)

    workbook.save(path)


def _write_rows(sheet, rows: list[list[object]], *, bold_first: bool = False) -> None:
    from openpyxl.styles import Font

    for row in rows:
        sheet.append(row)
    if bold_first and rows:
        for cell in sheet[1]:
            cell.font = Font(bold=True)


def _fmt(value, digits: int = 6) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math_isfinite(number):
        return ""
    return f"{number:.{digits}g}"


def math_isfinite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def run(argv: list[str] | None = None) -> int:
    app = QtWidgets.QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
    window = MainWindow()
    window.show()
    exec_func = getattr(app, "exec", app.exec_)
    return exec_func()
