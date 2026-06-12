from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg

from pyqtgraph.Qt import QtCore

from tristar_bet.analysis import (
    FitResult,
    adsorption_points,
    bet_analysis,
    bjh_pore_distribution,
    desorption_points,
    langmuir_analysis,
    t_plot_analysis,
    t_plot_analysis_by_thickness,
)


pg.setConfigOptions(antialias=True, useOpenGL=False)

DEFAULT_COLORS = (
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#f97316",
    "#0891b2",
    "#4f46e5",
    "#be123c",
    "#65a30d",
    "#b45309",
    "#0f766e",
    "#db2777",
)
SELECTED_SYMBOL_SIZE = 8
SELECTED_SYMBOL_PEN_WIDTH = 2


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


def make_plot(title: str, left_label: str, bottom_label: str) -> pg.PlotWidget:
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
    return plot


def plot_isotherm_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
    active_index: int = -1,
) -> None:
    plot.clear()
    plot.setTitle("吸附/脱附等温线")
    plot.setLabel("left", "吸附量 (cm3/g STP)")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=False, y=False)
    all_x = []
    all_y = []

    def _collect_xy(pts):
        for p in pts:
            all_x.append(float(p.relative_pressure))
            all_y.append(float(p.quantity_adsorbed_cm3_g_stp or 0.0))

    # 先画非活跃样品，再画活跃样品（确保红色曲线在最上层）
    draw_order = [i for i in range(len(results)) if i != active_index] + (
        [active_index] if 0 <= active_index < len(results) else []
    )
    for index in draw_order:
        if index >= len(visible) or not visible[index]:
            continue
        result = results[index]
        is_active = index == active_index
        color = "#dc2626" if is_active else colors[index % len(colors)]
        width = 3 if is_active else 2
        name = _legend_name(result)
        adsorption = adsorption_points(result)
        desorption = desorption_points(result)
        _plot_points(plot, adsorption, color, name, solid=True, width=width)
        _plot_points(plot, desorption, color, None, solid=False, width=width)
        _collect_xy(adsorption)
        _collect_xy(desorption)

    _fit_range(plot, all_x, all_y)


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
        color = "#dc2626" if index == active_index else colors[index % len(colors)]
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
    data_min = p_min if p_min is not None else 0.05
    data_max = p_max if p_max is not None else 0.30
    analysis = bet_analysis(result, data_min, data_max)

    plot.clear()
    plot.setTitle("BET 拟合")
    plot.setLabel("left", "P/[V(P0-P)]")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=False, y=False)

    if not analysis.rows:
        _plot_message(plot, f"BET {_range_text(analysis, '默认区间 0.05-0.30')} 内有效点不足")
        return np.array([])

    x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["bet_y"] for row in analysis.rows], dtype=float)
    plot.plot(
        x, y,
        pen=None, symbol="o", symbolSize=7,
        symbolPen=pg.mkPen("#2563eb", width=1),
        symbolBrush=pg.mkBrush("#ffffff"),
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
) -> dict[int, np.ndarray]:
    """绘制所有勾选样品的 BET 散点，返回每个样品的 x 坐标数组。"""
    data_min = p_min if p_min is not None else 0.05
    data_max = p_max if p_max is not None else 0.30

    plot.clear()
    plot.setTitle("BET 拟合")
    plot.setLabel("left", "P/[V(P0-P)]")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=False, y=False)

    x_by_index: dict[int, np.ndarray] = {}
    all_x = []
    all_y = []
    for index in _analysis_draw_order(results, visible, active_index):
        analysis = bet_analysis(results[index], data_min, data_max)
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
        _plot_analysis_xy(plot, x, y, color, _legend_name(results[index]), index == active_index)
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
):
    data_min = data_p_min if data_p_min is not None else 0.05
    data_max = data_p_max if data_p_max is not None else 0.30
    analysis = bet_analysis(result, data_min, data_max)
    if not analysis.rows:
        return None

    lo, hi = sorted((float(fit_p_min), float(fit_p_max)))
    x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["bet_y"] for row in analysis.rows], dtype=float)
    mask = np.isfinite(x) & np.isfinite(y) & (x >= lo) & (x <= hi)
    if not np.any(mask):
        return None
    return _plot_selected_xy(plot, x[mask], y[mask], "#2563eb")


def plot_langmuir_selection(
    plot: pg.PlotWidget,
    result,
    fit_p_min: float,
    fit_p_max: float,
    data_p_min: float | None = None,
    data_p_max: float | None = None,
):
    data_min = data_p_min if data_p_min is not None else 0.05
    data_max = data_p_max if data_p_max is not None else 0.30
    analysis = langmuir_analysis(result, data_min, data_max)
    if not analysis.rows:
        return None

    lo, hi = sorted((float(fit_p_min), float(fit_p_max)))
    x = np.asarray([row["relative_pressure"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["langmuir_y"] for row in analysis.rows], dtype=float)
    mask = np.isfinite(x) & np.isfinite(y) & (x >= lo) & (x <= hi)
    if not np.any(mask):
        return None
    return _plot_selected_xy(plot, x[mask], y[mask], "#2563eb")


def replace_bet_fit_line(
    plot: pg.PlotWidget,
    old_item,
    result,
    fit_p_min: float,
    fit_p_max: float,
    line_x_min: float | None = None,
    line_x_max: float | None = None,
    color: str = "#dc2626",
    name: str | None = "线性拟合",
    width: int = 2,
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

    analysis = bet_analysis(result, fit_p_min, fit_p_max)
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
        pen=None, symbol="o", symbolSize=7,
        symbolPen=pg.mkPen("#2563eb", width=1),
        symbolBrush=pg.mkBrush("#ffffff"),
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
) -> dict[int, np.ndarray]:
    """绘制所有勾选样品的 Langmuir 散点，返回每个样品的 x 坐标数组。"""
    data_min = p_min if p_min is not None else 0.05
    data_max = p_max if p_max is not None else 0.30

    plot.clear()
    plot.setTitle("Langmuir 拟合")
    plot.setLabel("left", "(P/P0) / V")
    plot.setLabel("bottom", "相对压力 (P/P0)")
    plot.setLogMode(x=False, y=False)

    x_by_index: dict[int, np.ndarray] = {}
    all_x = []
    all_y = []
    for index in _analysis_draw_order(results, visible, active_index):
        analysis = langmuir_analysis(results[index], data_min, data_max)
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
        _plot_analysis_xy(plot, x, y, color, _legend_name(results[index]), index == active_index)
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
    color: str = "#dc2626",
    name: str | None = "线性拟合",
    width: int = 2,
):
    """移除旧 Langmuir 拟合线并重绘，返回 (new_item, FitResult)。"""
    if old_item is not None:
        try:
            plot.removeItem(old_item)
        except RuntimeError:
            pass

    analysis = langmuir_analysis(result, fit_p_min, fit_p_max)
    if not analysis.ok or analysis.slope is None or analysis.intercept is None:
        return None, analysis

    x_start = line_x_min if line_x_min is not None else fit_p_min
    x_end = line_x_max if line_x_max is not None else fit_p_max
    line_x = np.linspace(x_start, x_end, 120)
    line_y = analysis.slope * line_x + analysis.intercept
    item = plot.plot(line_x, line_y, pen=pg.mkPen(color, width=width), name=name)
    return item, analysis


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
    plot.setLabel("left", "液体体积 (cm3/g)")
    plot.setLabel("bottom", "统计膜厚 t (nm)")
    plot.setLogMode(x=False, y=False)

    if not analysis.rows:
        _plot_message(plot, f"t-Plot {_range_text(analysis, '默认区间 0.20-0.50')} 内有效点不足")
        return np.array([])

    x = np.asarray([row["thickness_nm"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["liquid_volume_cm3_g"] for row in analysis.rows], dtype=float)
    plot.plot(
        x, y,
        pen=None, symbol="o", symbolSize=7,
        symbolPen=pg.mkPen("#2563eb", width=1),
        symbolBrush=pg.mkBrush("#ffffff"),
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
) -> dict[int, np.ndarray]:
    """绘制所有勾选样品的 t-Plot 散点，返回每个样品的厚度 x 坐标数组。"""
    data_min = p_min if p_min is not None else 0.20
    data_max = p_max if p_max is not None else 0.50

    plot.clear()
    plot.setTitle("t-Plot")
    plot.setLabel("left", "液体体积 (cm3/g)")
    plot.setLabel("bottom", "统计膜厚 t (nm)")
    plot.setLogMode(x=False, y=False)

    x_by_index: dict[int, np.ndarray] = {}
    all_x = []
    all_y = []
    for index in _analysis_draw_order(results, visible, active_index):
        thickness_params = None
        if thickness_params_by_index is not None:
            thickness_params = thickness_params_by_index.get(index)
        thickness_method = "harkins_jura"
        if thickness_method_by_index is not None:
            thickness_method = thickness_method_by_index.get(index, thickness_method)
        analysis = t_plot_analysis(results[index], data_min, data_max, thickness_params, thickness_method)
        if not analysis.rows:
            continue
        x = np.asarray([row["thickness_nm"] for row in analysis.rows], dtype=float)
        y = np.asarray([row["liquid_volume_cm3_g"] for row in analysis.rows], dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        if not np.any(mask):
            continue
        x = x[mask]
        y = y[mask]
        color = _analysis_color(colors, index, active_index)
        _plot_analysis_xy(plot, x, y, color, _legend_name(results[index]), index == active_index)
        x_by_index[index] = x
        all_x.extend(x.tolist())
        all_y.extend(y.tolist())

    if all_x:
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
):
    analysis = t_plot_analysis_by_thickness(
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
    y = np.asarray([row["liquid_volume_cm3_g"] for row in analysis.rows], dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if not np.any(mask):
        return None
    return _plot_selected_xy(plot, x[mask], y[mask], "#2563eb")


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
    color: str = "#dc2626",
    name: str | None = "线性拟合",
    width: int = 2,
):
    """移除旧 t-Plot 拟合线并重绘（按厚度范围选点），返回 (new_item, FitResult)。"""
    if old_item is not None:
        try:
            plot.removeItem(old_item)
        except RuntimeError:
            pass

    analysis = t_plot_analysis_by_thickness(
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
        symbolSize=7,
        symbolPen=pg.mkPen("#2563eb", width=1),
        symbolBrush=pg.mkBrush("#ffffff"),
        name="Langmuir 点",
    )
    _plot_fit_line(plot, analysis, x, "#dc2626")
    _fit_range(plot, x, y)


def plot_t_plot(plot: pg.PlotWidget, result, p_min: float | None = None, p_max: float | None = None) -> None:
    analysis = t_plot_analysis(result) if p_min is None or p_max is None else t_plot_analysis(result, p_min, p_max)
    plot.clear()
    plot.setTitle("t-Plot")
    plot.setLabel("left", "液体体积 (cm3/g)")
    plot.setLabel("bottom", "统计膜厚 t (nm)")
    plot.setLogMode(x=False, y=False)
    if not analysis.rows:
        _plot_message(plot, f"t-Plot {_range_text(analysis, '默认区间 0.20-0.50')} 内有效点不足")
        return
    x = np.asarray([row["thickness_nm"] for row in analysis.rows], dtype=float)
    y = np.asarray([row["liquid_volume_cm3_g"] for row in analysis.rows], dtype=float)
    plot.plot(
        x,
        y,
        pen=None,
        symbol="o",
        symbolSize=7,
        symbolPen=pg.mkPen("#2563eb", width=1),
        symbolBrush=pg.mkBrush("#ffffff"),
        name="t-Plot 点",
    )
    _plot_fit_line(plot, analysis, x, "#dc2626")
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
) -> None:
    plot.clear()
    plot.setTitle("BJH 孔径分布")
    plot.setLabel("left", "dV/dlogD (cm3/g)")
    plot.setLabel("bottom", "孔径 (nm)")
    plot.setLogMode(x=False, y=False)
    all_x = []
    all_y = []
    phases: list[tuple[str, bool, QtCore.Qt.PenStyle]] = [
        ("adsorption", show_adsorption, QtCore.Qt.SolidLine),
        ("desorption", show_desorption, QtCore.Qt.DashLine),
    ]
    if not show_adsorption and not show_desorption:
        _plot_message(plot, "请选择 BJH 吸附或 BJH 脱附")
        return

    for index in _analysis_draw_order(results, visible, active_index):
        result = results[index]
        color = _analysis_color(colors, index, active_index)
        width = 3 if index == active_index else 2
        for phase, enabled, line_style in phases:
            if not enabled:
                continue
            distribution = bjh_pore_distribution(
                result,
                phase=phase,
                thickness_method=thickness_method,
                thickness_params=thickness_params,
                correction=correction,
                open_pore_fraction=open_pore_fraction,
                smooth=smooth,
            )
            if not distribution.rows:
                continue
            x = np.asarray([row["pore_diameter_nm"] for row in distribution.rows], dtype=float)
            y = np.asarray([row["differential_pore_volume_cm3_g"] for row in distribution.rows], dtype=float)
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
            phase_label = "吸附" if phase == "adsorption" else "脱附"
            plot.plot(
                x,
                y,
                pen=pen,
                symbol="o",
                symbolSize=6 if index == active_index else 5,
                symbolPen=pg.mkPen(color, width=1),
                symbolBrush=pg.mkBrush("#ffffff"),
                name=f"{_legend_name(result)} BJH{phase_label}",
            )
            all_x.extend(x.tolist())
            all_y.extend(y.tolist())

    if all_x:
        _fit_range(plot, all_x, all_y)
    else:
        _plot_message(plot, "当前样品没有足够的 BJH 孔径分布点")


def plot_pore_distribution_placeholder(plot: pg.PlotWidget) -> None:
    plot.clear()
    plot.setTitle("BJH 孔径分布")
    plot.setLabel("left", "dV/dlogD (cm3/g)")
    plot.setLabel("bottom", "孔径 (nm)")
    plot.setLogMode(x=False, y=False)
    _plot_message(plot, "当前没有可显示的 BJH 孔径分布")


def _plot_points(plot: pg.PlotWidget, points, color: str, name: str | None, *, solid: bool, width: int = 2) -> None:
    if not points:
        return
    x = np.asarray([float(point.relative_pressure) for point in points], dtype=float)
    y = np.asarray([float(point.quantity_adsorbed_cm3_g_stp or 0.0) for point in points], dtype=float)
    pen = pg.mkPen(color, width=width)
    if not solid:
        pen.setStyle(QtCore.Qt.DashLine)
    plot.plot(
        x,
        y,
        pen=pen,
        symbol="o",
        symbolSize=5,
        symbolPen=pg.mkPen(color, width=1),
        symbolBrush=pg.mkBrush(color if solid else "#ffffff"),
        name=name,
    )


def _analysis_draw_order(results, visible: list[bool], active_index: int) -> list[int]:
    draw_order = [i for i in range(len(results)) if i != active_index]
    if 0 <= active_index < len(results):
        draw_order.append(active_index)
    return [i for i in draw_order if i < len(visible) and visible[i]]


def _analysis_color(colors: list[str], index: int, active_index: int) -> str:
    if index == active_index:
        return "#dc2626"
    return colors[index % len(colors)] if colors else "#2563eb"


def _plot_analysis_xy(
    plot: pg.PlotWidget,
    x: np.ndarray,
    y: np.ndarray,
    color: str,
    name: str | None,
    is_active: bool,
) -> None:
    plot.plot(
        x,
        y,
        pen=None,
        symbol="o",
        symbolSize=8 if is_active else 6,
        symbolPen=pg.mkPen(color, width=2 if is_active else 1),
        symbolBrush=pg.mkBrush("#ffffff"),
        name=name,
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


def _plot_selected_xy(plot: pg.PlotWidget, x: np.ndarray, y: np.ndarray, color: str):
    return plot.plot(
        x,
        y,
        pen=None,
        symbol="o",
        symbolSize=SELECTED_SYMBOL_SIZE,
        symbolPen=pg.mkPen(color, width=SELECTED_SYMBOL_PEN_WIDTH),
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
    plot.plot(line_x, line_y, pen=pg.mkPen(color, width=2), name="线性拟合")


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


def _fit_range(plot: pg.PlotWidget, x_values, y_values) -> None:
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if not np.any(mask):
        return
    x = x[mask]
    y = y[mask]
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
