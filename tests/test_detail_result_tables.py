import csv
import io
import os
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtGui, QtWidgets, QtTest

from tristar_bet.analysis import FitResult
from tristar_bet.models import SmpHeader, SampleInfo, RunConditions, FreeSpaceInfo, IsothermPoint, TriStarResult, TargetPressureRow
from tristar_bet.ui.main_window import MainWindow, BET_COLUMN, BJH_PORE_VOLUME_COLUMN, DEFAULT_BJH_PORE_VOLUME_RANGE, summary_rows
from tristar_bet.ui.detail_tables import RESULT_STATUS_ROLE, SELECTION_BLUE


def empty_record(cls, **values):
    return cls(**{field.name: values.get(field.name) for field in fields(cls)})


def sample(name, scale=1.0):
    points = []
    for index in range(1, 21):
        p = index * 0.035
        v = scale * 50.0 * p / ((1.0 - p) * (0.05 + p))
        points.append(IsothermPoint(index, "adsorption", 0, p * 760, p, v, 760, index * 60, v, v / 22.414))
    return TriStarResult(
        header=empty_record(SmpHeader, file_name=name + ".SMP", file_path=name + ".SMP", created_raw=int(scale * 100), created_time="2024-01-01", modified_time="2024-01-01"),
        subsets=[], sample=SampleInfo(name, "Operator", "", "", 1.0, None),
        run_conditions=empty_record(RunConditions, adsorptive_short="N2", adsorptive_name="Nitrogen", bath_temperature_K=77.35),
        target_pressure_table=[], free_space=empty_record(FreeSpaceInfo, vfree_factor_source="test"),
        po_records=[], isotherm=points, adsorptive_properties=None, log_messages=[], sample_tube_strings=[],
    )


class DetailResultTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        font = Path("C:/Windows/Fonts/msyh.ttc")
        if font.exists() and QtGui.QFontDatabase.addApplicationFont(str(font)) >= 0:
            cls.app.setFont(QtGui.QFont("Microsoft YaHei", 9))

    def setUp(self):
        # GUI event tests must not start background network update checks.
        with patch.object(MainWindow, "_auto_check_for_updates"):
            self.w = MainWindow()
        self.samples = [sample("A", 1), sample("B", 2), sample("C", 3)]
        self.w.results = list(self.samples)
        self.w.visible_results = [True, False, True]
        self.w.sample_colors = ["#123456", "#234567", "#345678"]
        self.w.active_index = 1
        # Exercise the real tables and signals without spending time on plots.
        self.w.refresh_isotherm_plot = Mock()
        self.w.refresh_analysis_plots = Mock()
        self.w.refresh_all()

    def tearDown(self):
        self.w.close()

    def assert_orders_match(self):
        expected = [Path(result.file_name).stem for result in self.w.results]
        for table in self.w.analysis_result_tables.values():
            self.assertEqual([table.item(row, 0).text() for row in range(table.rowCount())], expected)
        self.assertEqual([self.w.sample_table.item(row, 1).text() for row in range(len(expected))], expected)

    def test_tabs_and_deduplicated_overview(self):
        tabs = [self.w.detail_tabs.tabText(index) for index in range(self.w.detail_tabs.count())]
        self.assertEqual(tabs, ["结果参数", "BET", "Langmuir", "t-Plot", "选区孔容量", "实际等温线", "目标压力表"])
        labels = [label for label, _value in summary_rows(self.samples[0])]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertIn("样品质量", labels)
        self.assertIn("设备型号", labels)
        self.assertFalse(any("BET" in label or "SUBSET" in label or "偏移" in label for label in labels))
        self.assertEqual(self.w.target_table.columnCount(), 5)

    def test_new_tables_keep_existing_detail_table_style(self):
        reference = self.w.isotherm_table
        for table in self.w.analysis_result_tables.values():
            self.assertEqual(table.styleSheet(), reference.styleSheet())
            self.assertEqual(table.alternatingRowColors(), reference.alternatingRowColors())
            self.assertEqual(table.font(), reference.font())
            self.assertEqual(table.verticalHeader().defaultSectionSize(), reference.verticalHeader().defaultSectionSize())
            self.assertEqual(table._frozen_table.styleSheet(), "")
            self.assertEqual(table._frozen_table.alternatingRowColors(), table.alternatingRowColors())

    def test_top_sort_keeps_identity_visibility_colors_and_all_table_orders(self):
        w = self.w
        w.sort_samples_by_bet(False)
        self.assert_orders_match()
        self.assertIs(w.active_result(), self.samples[1])
        self.assertEqual([r.file_name for r in w.results], ["C.SMP", "B.SMP", "A.SMP"])
        self.assertEqual(w.visible_results, [True, False, True])
        self.assertEqual(w.sample_colors, ["#345678", "#234567", "#123456"])
        for sorter in (w.sort_samples_by_test_time, w.sort_samples_by_langmuir, w.sort_samples_by_t_plot, w.sort_samples_by_bjh_pore_volume):
            sorter(True)
            self.assert_orders_match()

    def test_detail_numeric_sort_and_missing_values_last_in_both_directions(self):
        w = self.w
        results = [FitResult("BET", "ok", intercept=10), FitResult("BET", "ok", intercept=2), FitResult("BET", "not_enough_points")]
        mapping = {id(r): fit for r, fit in zip(self.samples, results)}
        w._bet_analysis_for_result = lambda result: mapping[id(result)]
        w._analysis_detail_cache.clear()
        w.refresh_all()
        column = w.bet_results_table._column_keys.index("intercept")
        w._on_analysis_header_clicked("bet", column)
        self.assertEqual([r.file_name for r in w.results], ["B.SMP", "A.SMP", "C.SMP"])
        self.assert_orders_match()
        w._on_analysis_header_clicked("bet", column)
        self.assertEqual([r.file_name for r in w.results], ["A.SMP", "B.SMP", "C.SMP"])
        self.assert_orders_match()
        self.assertIs(w.active_result(), self.samples[1])

    def test_filename_header_does_not_sort_and_stays_frozen(self):
        w = self.w
        w.sort_samples_by_bet(False)
        before = list(w.results)
        w.show()
        table = w.bet_results_table
        w.detail_tabs.setCurrentWidget(table)
        self.app.processEvents()
        header = table.frozen_header()
        QtTest.QTest.mouseClick(header.viewport(), QtCore.Qt.LeftButton, pos=QtCore.QPoint(header.sectionViewportPosition(0) + 15, header.height() // 2))
        self.assert_orders_match()
        self.assertEqual(w.results, before)
        for method in w.analysis_result_tables:
            w._on_analysis_header_clicked(method, 0)
            self.assertEqual(w.results, before)
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        self.assertFalse(table._frozen_table.isColumnHidden(0))
        self.assertTrue(table._frozen_table.isColumnHidden(1))
        self.assertEqual(table.horizontalHeader().height(), header.height())
        self.assertEqual(table.viewport().mapToGlobal(QtCore.QPoint()).y(), table._frozen_table.viewport().mapToGlobal(QtCore.QPoint()).y())

    def test_click_selection_and_hover_are_bidirectional_without_recalculation(self):
        w = self.w
        w.langmuir_results_table.setCurrentCell(2, 2)
        self.assertIs(w.active_result(), self.samples[2])
        self.assertEqual(w.sample_table.currentRow(), 2)
        self.assertTrue(all(table.currentRow() == 2 for table in w.analysis_result_tables.values()))
        w.sample_table.setCurrentCell(0, 1)
        self.assertTrue(all(table.currentRow() == 0 for table in w.analysis_result_tables.values()))
        with patch.object(w, "_analysis_cells_for_result", side_effect=AssertionError("hover recalculated results")):
            w.t_plot_results_table.rowHovered.emit(1)
            self.assertEqual(w.active_index, 0)
            for table in [w.sample_table, *w.analysis_result_tables.values()]:
                self.assertTrue(table.item(1, 1).font().bold())
            w.t_plot_results_table.rowHovered.emit(-1)
            for table in [w.sample_table, *w.analysis_result_tables.values()]:
                self.assertFalse(table.item(1, 1).font().bold())

    def test_each_sample_has_own_fit_and_t_plot_settings(self):
        w = self.w
        first, second = self.samples[:2]
        w.custom_bet_fit_ranges[id(first)] = (0.05, 0.15)
        w.custom_bet_fit_ranges[id(second)] = (0.15, 0.3)
        settings = w._t_plot_settings_for_result(second)
        settings.update(surface_area_mode="Input", surface_area_input=1000.0, surface_area_correction=0.9)
        w.custom_t_plot_settings[id(second)] = settings
        w.refresh_metrics()
        a = w._analysis_cells_for_result(first)
        b = w._analysis_cells_for_result(second)
        self.assertEqual(a["bet"]["p_min"][1], 0.05)
        self.assertEqual(b["bet"]["p_min"][1], 0.15)
        self.assertEqual(b["t_plot"]["correction"][1], 0.9)
        self.assertEqual(b["t_plot"]["total_area"][1], 1000.0)
        self.assertEqual(a["t_plot"]["correction"][1], 1.0)

    def test_single_point_columns_are_shared_and_only_added_when_present(self):
        w = self.w
        self.assertNotIn("single_area", w.bet_results_table._column_keys)
        self.samples[0].method_options["jwgb_single_point_bet_pressure"] = 0.3
        w.refresh_metrics()
        column = w.bet_results_table._column_keys.index("single_area")
        self.assertGreater(column, w.bet_results_table._column_keys.index("count"))
        self.assertIsInstance(w.bet_results_table.item(0, column).data(QtCore.Qt.UserRole), float)
        self.assertEqual(w.bet_results_table.item(1, column).text(), "—")
        w.sample_table.setCurrentCell(2, 1)
        self.assertEqual(w.bet_results_table._column_keys.index("single_area"), column)
        self.samples[0].method_options.pop("jwgb_single_point_bet_pressure")
        w.refresh_metrics()
        self.assertNotIn("single_area", w.bet_results_table._column_keys)

    def test_cached_refresh_does_not_recompute_unchanged_rows(self):
        w = self.w
        with patch.object(w, "_bet_analysis_for_result", side_effect=AssertionError("uncached BET")), patch.object(w, "_t_plot_analysis_for_result", side_effect=AssertionError("uncached t-plot")):
            w.refresh_metrics()

    def test_isotherm_widths_fit_headers_and_values_with_padding(self):
        table = self.w.isotherm_table
        self.assertEqual(table.horizontalHeaderItem(0).text(), "测试点")
        font = QtGui.QFont(table.font())
        font.setBold(True)
        metrics = QtGui.QFontMetrics(font)
        for column in range(table.columnCount()):
            texts = [table.horizontalHeaderItem(column).text()] + [table.item(row, column).text() for row in range(table.rowCount())]
            self.assertGreaterEqual(table.columnWidth(column), max(metrics.horizontalAdvance(text) for text in texts) + 20)
        table.setColumnWidth(0, 180)
        self.w.refresh_isotherm_table()
        self.assertEqual(table.columnWidth(0), 180)
        self.assertTrue(table.horizontalHeader().stretchLastSection())

    def test_last_column_fills_right_edge_and_shrinks_with_window(self):
        for table in (self.w.metrics_table, self.w.isotherm_table):
            self._show_table(table)
            checked_fill = False
            for width in (1900, 1400, 1700):
                self.w.resize(width, 950)
                self.w.centralWidget().setSizes([width - 650, 650])
                self.app.processEvents()
                self.w.refresh_all()
                self.app.processEvents()
                header = table.horizontalHeader()
                self.assertTrue(header.stretchLastSection())
                content_minimum = sum(table.columnWidth(c) for c in range(table.columnCount() - 1))
                if table.viewport().width() > content_minimum + 150:
                    checked_fill = True
                    self.assertEqual(header.length(), table.viewport().width())
                    self.assertEqual(table.horizontalScrollBar().maximum(), 0)
                self.assertNotIn(table.horizontalHeaderItem(table.columnCount() - 1).text(), table._manual_content_widths)
            self.assertTrue(checked_fill)

    def test_sort_indicators_stay_hidden_after_numeric_header_clicks(self):
        for method, table in self.w.analysis_result_tables.items():
            self._show_table(table)
            column = table._column_keys.index("slope")
            # Scroll the numeric header into view before clicking it.
            table.scrollToItem(table.item(0, column), QtWidgets.QAbstractItemView.PositionAtCenter)
            self.app.processEvents()
            header = table.horizontalHeader()
            pos = QtCore.QPoint(header.sectionViewportPosition(column) + 15, header.height() // 2)
            for _ in range(2):
                QtTest.QTest.mouseClick(header.viewport(), QtCore.Qt.LeftButton, pos=pos)
                self.assertEqual(self.w._result_sort_state[:2], (method, "slope"))
                for candidate in (self.w.sample_table, *self.w.analysis_result_tables.values()):
                    self.assertFalse(candidate.horizontalHeader().isSortIndicatorShown())
                    self.assertFalse(candidate.frozen_header().isSortIndicatorShown())

    def test_errors_have_adjacent_numeric_columns_and_copy_separately(self):
        fit = FitResult("BET", "ok", surface_area_m2_g=123.5, surface_area_standard_error=0.25,
                        slope=5.0, slope_standard_error=0.125, intercept=2.5, intercept_standard_error=0.0625)
        self.w._bet_analysis_for_result = lambda result: fit
        self.w._langmuir_analysis_for_result = lambda result: fit
        self.w._analysis_detail_cache.clear()
        self.w.refresh_metrics()
        for method, table in self.w.analysis_result_tables.items():
            cells = self.w._analysis_cells_for_result(self.samples[0])[method]
            pairs = ("slope", "intercept") if method == "t_plot" else ("area", "slope", "intercept")
            for key in pairs:
                column = table._column_keys.index(key)
                self.assertEqual(table._column_keys[column + 1], key + "_error")
                self.assertNotIn("±", table.item(0, column).text())
                self.assertIn("±", table.horizontalHeaderItem(column + 1).text())
                self.assertEqual(table.item(0, column + 1).data(QtCore.Qt.UserRole), cells[key + "_error"][1])
                self._select_rectangle(table, 0, column, 0, column + 1)
                grid = list(csv.reader(io.StringIO(table._copy_controller.mime_data().text()), delimiter="\t"))
                self.assertEqual(grid, [[cells[key][0], cells[key + "_error"][0]]])
        self.assertEqual(self.w._analysis_cells_for_result(self.samples[0])["bet"]["area_error"][1], 0.25)

    def test_error_sort_is_numeric_and_keeps_missing_errors_last(self):
        fits = [FitResult("BET", "ok", slope_standard_error=10),
                FitResult("BET", "ok", slope_standard_error=2), FitResult("BET", "ok")]
        mapping = {id(result): fit for result, fit in zip(self.samples, fits)}
        self.w._bet_analysis_for_result = lambda result: mapping[id(result)]
        self.w._analysis_detail_cache.clear()
        self.w.refresh_metrics()
        column = self.w.bet_results_table._column_keys.index("slope_error")
        self.w._on_analysis_header_clicked("bet", column)
        self.assertEqual([result.file_name for result in self.w.results], ["B.SMP", "A.SMP", "C.SMP"])
        self.w._on_analysis_header_clicked("bet", column)
        self.assertEqual([result.file_name for result in self.w.results], ["A.SMP", "B.SMP", "C.SMP"])
        self.assert_orders_match()

    def test_circle_alignment_and_clicks_preserve_visibility(self):
        w = self.w
        # Startup work may drain the initial single-shot before the window is
        # shown; alignment must also follow the actual header Show/Resize.
        self.app.processEvents()
        w.show()
        self.app.processEvents()
        table = w.sample_table._frozen_table
        rect = table.visualRect(table.model().index(0, 0))
        row_x = table.viewport().mapToGlobal(rect.center()).x()
        header_x = w.select_all_check.mapToGlobal(w.select_all_check.rect().center()).x()
        self.assertLessEqual(abs(row_x - header_x), 1)
        QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, pos=rect.center())
        self.assertFalse(w.visible_results[0])
        QtTest.QTest.mouseClick(w.select_all_check, QtCore.Qt.LeftButton)
        self.assertTrue(all(w.visible_results))

    def test_move_and_delete_refresh_all_result_tables(self):
        self.w.move_sample_row(0, 3)
        self.assert_orders_match()
        self.assertEqual(self.w.results[-1].file_name, "A.SMP")
        self.w.delete_sample_rows([0])
        self.assert_orders_match()
        self.w.delete_sample_rows([0, 1])
        self.assert_orders_match()
        self.assertEqual(self.w.metrics_table.rowCount(), 0)

    def _show_table(self, table):
        self.w.resize(1600, 950)
        self.w.show()
        self.w.detail_tabs.setCurrentWidget(table)
        self.app.processEvents()

    def _select_rectangle(self, table, top, left, bottom, right):
        selection = QtCore.QItemSelection(table.model().index(top, left), table.model().index(bottom, right))
        table.selectionModel().select(selection, QtCore.QItemSelectionModel.ClearAndSelect)

    def test_status_columns_are_replaced_with_badges_and_tooltips(self):
        for table in self.w.analysis_result_tables.values():
            self.assertNotIn("status", table._column_keys)
            self.assertEqual(table.item(0, 0).data(RESULT_STATUS_ROLE), "ok")
            self.assertIn("区间计算完成", table.item(0, 0).toolTip())
        fits = [FitResult("BET", "ok"), FitResult("BET", "warning_negative_c"), FitResult("BET", "not_enough_points")]
        self.w._bet_analysis_for_result = lambda result: fits[self.samples.index(result)]
        self.w._analysis_detail_cache.clear()
        self.w.refresh_metrics()
        table = self.w.bet_results_table
        self.assertEqual([table.item(row, 0).data(RESULT_STATUS_ROLE) for row in range(3)], ["ok", "warning", ""])
        self.assertIn("C<=0", table.item(1, 0).toolTip())
        self.assertIn("不足", table.item(2, 0).toolTip())

    def test_rectangular_copy_preserves_cells_and_excel_rows(self):
        for table in [*self.w.analysis_result_tables.values(), self.w.isotherm_table]:
            self._select_rectangle(table, 0, 0, 1, 2)
            mime = table._copy_controller.mime_data()
            parsed = list(csv.reader(io.StringIO(mime.text()), delimiter="\t"))
            self.assertEqual(parsed, [[table.item(row, column).text() for column in range(3)] for row in range(2)])
            self.assertTrue(mime.hasHtml())
            self.assertNotIn("区间计算完成", mime.text())
            self.assertEqual(table.selectionBehavior(), QtWidgets.QAbstractItemView.SelectItems)
        self.assertTrue(hasattr(self.w.target_table, "_copy_controller"))
        self.assertEqual(self.w.target_table.selectionMode(), QtWidgets.QAbstractItemView.ExtendedSelection)

    def test_disjoint_copy_keeps_empty_gaps_and_does_not_include_context_filename(self):
        table = self.w.bet_results_table
        table.clearSelection()
        for row, column in [(0, 1), (1, 3)]:
            table.selectionModel().select(table.model().index(row, column), QtCore.QItemSelectionModel.Select)
        self.assertEqual(table._copy_controller.selected_grid(), [
            [table.item(0, 1).text(), "", ""], ["", "", table.item(1, 3).text()],
        ])
        self.assertFalse(table.selectionModel().isSelected(table.model().index(0, 0)))

    def test_right_click_copy_keeps_existing_selection(self):
        table = self.w.bet_results_table
        self._show_table(table)
        self._select_rectangle(table, 0, 1, 1, 2)
        before = table._copy_controller.selected_grid()
        clipboard = Mock()
        def choose_copy(menu, *_args):
            self.assertEqual(menu.actions()[0].text(), "复制")
            self.assertTrue(menu.actions()[0].shortcut().isEmpty())
            self.assertIn("padding: 6px 12px", menu.styleSheet())
            self.assertIn("background: #e0ecff", menu.styleSheet())
            menu.actions()[0].trigger()
        with patch.object(QtWidgets.QApplication, "clipboard", return_value=clipboard), patch.object(QtWidgets.QMenu, "exec_", choose_copy):
            pos = table._frozen_table.visualRect(table.model().index(2, 0)).center()
            QtTest.QTest.mouseClick(table._frozen_table.viewport(), QtCore.Qt.RightButton, pos=pos)
            table._copy_controller.show_menu(table._frozen_table, pos)
        self.assertEqual(table._copy_controller.selected_grid(), before)
        self.assertTrue(clipboard.setMimeData.called)
        self.assertEqual(list(csv.reader(io.StringIO(clipboard.setMimeData.call_args.args[0].text()), delimiter="\t")), before)

    def test_copy_empty_selection_does_not_change_clipboard(self):
        table = self.w.bet_results_table
        table.clearSelection()
        with patch.object(QtWidgets.QApplication, "clipboard") as clipboard:
            table._copy_controller.copy()
        clipboard.assert_not_called()

    def test_ctrl_c_copies_selected_cells(self):
        table = self.w.bet_results_table
        self._show_table(table)
        self._select_rectangle(table, 0, 1, 1, 2)
        table.setFocus()
        self.app.processEvents()
        clipboard = Mock()
        with patch.object(QtWidgets.QApplication, "clipboard", return_value=clipboard):
            QtTest.QTest.keyClick(table, QtCore.Qt.Key_C, QtCore.Qt.ControlModifier)
        clipboard.setMimeData.assert_called_once()

    def test_isotherm_and_target_pressure_support_native_cell_drag_copy(self):
        result = self.w.results[self.w.active_index]
        self.w.results[self.w.active_index] = replace(result, target_pressure_table=[
            TargetPressureRow(1, "adsorption", 0.1, 0.2, 0.01, 123),
            TargetPressureRow(2, "desorption", 0.9, 0.8, 0.02, 456),
        ])
        self.w.refresh_target_table()
        for table in (self.w.isotherm_table, self.w.target_table):
            self._show_table(table)
            start = table.visualRect(table.model().index(0, 0)).center()
            end = table.visualRect(table.model().index(1, 2)).center()
            QtTest.QTest.mousePress(table.viewport(), QtCore.Qt.LeftButton, pos=start)
            # Send a held-button move, as delivered by the native mouse driver.
            event = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(end), QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
            QtWidgets.QApplication.sendEvent(table.viewport(), event)
            QtTest.QTest.mouseRelease(table.viewport(), QtCore.Qt.LeftButton, pos=end)
            self.assertEqual({(i.row(), i.column()) for i in table.selectedIndexes()}, {(r, c) for r in range(2) for c in range(3)})
            self.assertEqual(table._copy_controller.selected_grid(), [[table.item(row, column).text() for column in range(3)] for row in range(2)])

    def test_long_mouse_drag_selects_cells_without_moving_samples(self):
        table = self.w.bet_results_table
        self._show_table(table)
        order = list(self.w.results)
        start = table.visualRect(table.model().index(0, 1)).center()
        end = table.visualRect(table.model().index(2, 2)).center()
        QtTest.QTest.mousePress(table.viewport(), QtCore.Qt.LeftButton, pos=start)
        QtTest.QTest.qWait(250)
        QtTest.QTest.mouseMove(table.viewport(), end)
        QtTest.QTest.mouseRelease(table.viewport(), QtCore.Qt.LeftButton, pos=end)
        self.assertEqual(self.w.results, order)
        self.assertEqual({(i.row(), i.column()) for i in table.selectedIndexes()}, {(r, c) for r in range(3) for c in (1, 2)})
        self.assertFalse(table._dragging_row)
        self.assertEqual(self.w.active_index, 2)
        before = table._copy_controller.selected_grid()
        self.w.refresh_metrics()
        self.assertEqual(table._copy_controller.selected_grid(), before)

    def test_drag_from_frozen_filename_into_data_columns(self):
        table = self.w.langmuir_results_table
        self._show_table(table)
        frozen = table._frozen_table
        start = frozen.visualRect(table.model().index(0, 0)).center()
        end = frozen.viewport().mapFromGlobal(table.viewport().mapToGlobal(table.visualRect(table.model().index(1, 2)).center()))
        QtTest.QTest.mousePress(frozen.viewport(), QtCore.Qt.LeftButton, pos=start)
        QtTest.QTest.mouseMove(frozen.viewport(), end)
        QtTest.QTest.mouseRelease(frozen.viewport(), QtCore.Qt.LeftButton, pos=end)
        self.assertEqual({(i.row(), i.column()) for i in table.selectedIndexes()}, {(r, c) for r in (0, 1) for c in range(3)})

    def test_selected_cells_and_frozen_filename_share_sample_list_blue(self):
        table = self.w.bet_results_table
        self._show_table(table)
        self._select_rectangle(table, 1, 1, 1, 1)
        self.app.processEvents()
        for view, column in [(table, 1), (table._frozen_table, 0)]:
            rect = view.visualRect(table.model().index(1, column))
            pixel = view.viewport().grab().toImage().pixelColor(rect.left() + 3, rect.top() + 3)
            self.assertEqual(pixel.name(), SELECTION_BLUE)
        self.assertFalse(table.selectionModel().isSelected(table.model().index(1, 0)))

    def _mock_pore_methods(self):
        methods = {}
        for index, method in enumerate(("bjh", "dh", "hk", "dft"), start=1):
            name = f"_{method}_pore_volume_for_result"
            methods[method] = Mock(return_value=index * 0.01)
            setattr(self.w, name, methods[method])
        return methods

    def test_pore_table_uses_each_methods_range_and_preserves_upper_column(self):
        w = self.w
        methods = self._mock_pore_methods()
        for index, method in enumerate(methods, start=1):
            setattr(w, f"{method}_pore_volume_range", (float(index), float(index * 10)))
        table = w.pore_volume_results_table
        self._show_table(table)
        self.assertEqual(table._column_keys, ["file", "bjh", "dh", "hk", "dft"])
        self.assertEqual(table.rowCount(), len(self.samples))
        self.assertEqual(table.styleSheet(), w.bet_results_table.styleSheet())
        self.assertEqual(w.sample_table.columnCount(), 7)
        self.assertEqual(w.sample_table.horizontalHeaderItem(BJH_PORE_VOLUME_COLUMN).text(), "选区孔容量(cm3/g)")
        self.assertFalse(table._has_completion_badges)
        for column, (method, getter) in enumerate(methods.items(), start=1):
            bounds = getattr(w, f"{method}_pore_volume_range")
            for row, result in enumerate(self.samples):
                getter.assert_any_call(result, bounds)
                self.assertAlmostEqual(table.item(row, column).data(QtCore.Qt.UserRole), column * 0.01)
                self.assertIn("绿色选区", table.item(row, column).toolTip())
                self.assertIn("cm³/g", table.item(row, column).toolTip())

    def test_pore_table_defers_work_until_open_and_reuses_calculated_distributions(self):
        w = self.w
        with patch.object(w, "_pore_volume_cells_for_result", side_effect=AssertionError("hidden table calculated")):
            w.refresh_metrics()
            w._refresh_all_sample_bjh_pore_cells()
            self.app.processEvents()
        # The real calculation path populates caches on first open.
        self._show_table(w.pore_volume_results_table)
        with patch("tristar_bet.ui.main_window.dft_pore_distribution", side_effect=AssertionError("DFT recalculated")), \
             patch("tristar_bet.ui.main_window.bjh_pore_distribution", side_effect=AssertionError("BJH recalculated")), \
             patch("tristar_bet.ui.main_window.dh_pore_distribution", side_effect=AssertionError("DH recalculated")), \
             patch("tristar_bet.ui.main_window.horvath_kawazoe_pore_distribution", side_effect=AssertionError("HK recalculated")):
            w.bjh_pore_volume_range = (2.1, 75.3)
            w.refresh_pore_volume_table()

    def test_pore_table_values_match_sample_column_on_each_plot_tab(self):
        w = self.w
        self._show_table(w.pore_volume_results_table)
        for column, method in enumerate(("bjh", "dh", "hk", "dft"), start=1):
            w.plot_tabs.setCurrentWidget(getattr(w, method + "_tab"))
            w.refresh_pore_volume_table()
            for row in range(len(self.samples)):
                self.assertEqual(w.pore_volume_results_table.item(row, column).text(),
                                 w.sample_table.item(row, BJH_PORE_VOLUME_COLUMN).text())

    def test_pore_table_sort_missing_values_and_shared_row_interactions(self):
        w = self.w
        methods = self._mock_pore_methods()
        values = {id(self.samples[0]): 0.3, id(self.samples[1]): None, id(self.samples[2]): 0.1}
        methods["bjh"].side_effect = lambda result, *args, **kwargs: values[id(result)]
        table = w.pore_volume_results_table
        self._show_table(table)
        w._on_analysis_header_clicked("pore_volume", 1)
        self.assertEqual([r.file_name for r in w.results], ["C.SMP", "A.SMP", "B.SMP"])
        self.assertEqual([table.item(r, 0).text() for r in range(3)], ["C", "A", "B"])
        self.assertEqual(table.item(2, 1).text(), "—")
        self.assert_orders_match()
        w._on_analysis_header_clicked("pore_volume", 1)
        self.assertEqual([r.file_name for r in w.results], ["A.SMP", "C.SMP", "B.SMP"])
        w._on_analysis_header_clicked("pore_volume", 0)
        self.assertEqual([r.file_name for r in w.results], ["A.SMP", "C.SMP", "B.SMP"])
        table.setCurrentCell(1, 2)
        self.assertIs(w.active_result(), self.samples[2])
        self.assertEqual(w.sample_table.currentRow(), 1)
        with patch.object(w, "_pore_volume_cells_for_result", side_effect=AssertionError("hover recalculated")):
            table.rowHovered.emit(0)
            for linked in [w.sample_table, *w.linked_result_tables.values()]:
                self.assertTrue(linked.item(0, 1).font().bold())
        w.sort_samples_by_bet(False)
        self.assertEqual([table.item(r, 0).text() for r in range(3)], ["C", "B", "A"])
        w.delete_sample_rows([0, 1, 2])
        self.assertEqual(table.rowCount(), 0)

    def test_pore_table_drag_copy_and_live_refresh_preserve_selection(self):
        self._mock_pore_methods()
        table = self.w.pore_volume_results_table
        self._show_table(table)
        self.w.centralWidget().setSizes([900, 700])
        self.app.processEvents()
        before = list(self.w.results)
        start = table._frozen_table.visualRect(table.model().index(0, 0)).center()
        end = table._frozen_table.viewport().mapFromGlobal(table.viewport().mapToGlobal(table.visualRect(table.model().index(2, 4)).center()))
        QtTest.QTest.mousePress(table._frozen_table.viewport(), QtCore.Qt.LeftButton, pos=start)
        QtTest.QTest.qWait(250)
        QtTest.QTest.mouseMove(table._frozen_table.viewport(), end)
        QtTest.QTest.mouseRelease(table._frozen_table.viewport(), QtCore.Qt.LeftButton, pos=end)
        self.assertEqual(self.w.results, before)
        selection = {(i.row(), i.column()) for i in table.selectedIndexes()}
        self.assertEqual(selection, {(r, c) for r in range(3) for c in range(5)})
        self.w._hk_pore_volume_for_result.return_value = 0.123
        self.w._refresh_all_sample_bjh_pore_cells()
        QtTest.QTest.qWait(80)
        self.assertEqual(table.item(0, 3).text(), "0.123")
        self.assertEqual({(i.row(), i.column()) for i in table.selectedIndexes()}, selection)
        expected = [[table.item(r, c).text() for c in range(5)] for r in range(3)]
        self.assertEqual(list(csv.reader(io.StringIO(table._copy_controller.mime_data().text()), delimiter="\t")), expected)
        for view, column in [(table, 1), (table._frozen_table, 0)]:
            rect = view.visualRect(table.model().index(0, column))
            self.assertEqual(view.viewport().grab().toImage().pixelColor(rect.left() + 3, rect.top() + 3).name(), SELECTION_BLUE)

    def test_bjh_and_dh_green_ranges_drag_reset_and_switch_independently(self):
        w = self.w
        self.assertEqual(w.bjh_pore_volume_range, w.dh_pore_volume_range)
        with patch.object(w, "_current_bjh_diameter_range", return_value=(3.0, 40.0)), \
             patch.object(w, "_current_dh_diameter_range", return_value=(5.0, 60.0)):
            w._update_bjh_pore_volume_from_region()
            self.assertEqual(w.dh_pore_volume_range, DEFAULT_BJH_PORE_VOLUME_RANGE)
            w._update_dh_pore_volume_from_region()
            self.assertEqual(w.bjh_pore_volume_range, (3.0, 40.0))
            self.assertEqual(w.dh_pore_volume_range, (5.0, 60.0))
        for method, expected in (("bjh", (3., 40.)), ("dh", (5., 60.)), ("bjh", (3., 40.))):
            w.plot_tabs.setCurrentWidget(getattr(w, method + "_tab"))
            self.assertEqual(w._selected_pore_volume_range(), expected)
        with patch.object(w, "refresh_dh_plot"), patch.object(w, "refresh_bjh_plot"):
            w.reset_dh_to_default(reset_region=True)
            self.assertEqual(w.bjh_pore_volume_range, (3., 40.))
            self.assertEqual(w.dh_pore_volume_range, DEFAULT_BJH_PORE_VOLUME_RANGE)
            w.dh_pore_volume_range = (7., 80.)
            w.reset_bjh_to_default(reset_region=True)
            self.assertEqual(w.dh_pore_volume_range, (7., 80.))
            self.assertEqual(w.bjh_pore_volume_range, DEFAULT_BJH_PORE_VOLUME_RANGE)

    def test_dh_volume_does_not_inherit_current_hk_or_bjh_range(self):
        w = self.w
        w.bjh_pore_volume_range = (2., 20.)
        w.dh_pore_volume_range = (4., 40.)
        w.hk_pore_volume_range = (0.4, 2.)
        w.plot_tabs.setCurrentWidget(w.hk_tab)
        with patch.object(w, "_cached_dh_distribution_rows", return_value=[{}]), \
             patch.object(w, "_bjh_pore_volume_from_rows", return_value=0.25) as integrate:
            w._dh_pore_volume_for_result(self.samples[0])
            integrate.assert_called_once_with([{}], (4., 40.))

    def test_bjh_dh_rendered_green_regions_survive_tab_switches(self):
        w = self.w
        w.refresh_isotherm_plot = MainWindow.refresh_isotherm_plot.__get__(w)
        w.refresh_analysis_plots = MainWindow.refresh_analysis_plots.__get__(w)
        expected = {}
        for method, fractions in (("bjh", (0.15, 0.55)), ("dh", (0.45, 0.85))):
            w.plot_tabs.setCurrentWidget(getattr(w, method + "_tab"))
            bounds = getattr(w, "_" + method + "_diameter_log_bounds")
            self.assertIsNotNone(bounds)
            lo, hi = bounds
            selection = tuple(lo + f * (hi - lo) for f in fractions)
            getattr(w, method + "_region").setRegion(selection)
            getattr(w, "_update_" + method + "_pore_volume_from_region")()
            expected[method] = tuple(10 ** x for x in selection)
        for method in ("bjh", "dh", "bjh", "dh"):
            w.plot_tabs.setCurrentWidget(getattr(w, method + "_tab"))
            actual = getattr(w, "_current_" + method + "_diameter_range")()
            for a, b in zip(actual, expected[method]):
                self.assertAlmostEqual(a, b, places=10)
        for method in expected:
            for a, b in zip(getattr(w, method + "_pore_volume_range"), expected[method]):
                self.assertAlmostEqual(a, b, places=10)

    def test_pore_headers_live_ranges_and_multiline_alignment(self):
        w = self.w
        table = w.pore_volume_results_table
        self._show_table(table)
        self.assertEqual(table.horizontalHeaderItem(1).text().splitlines()[0], "BJH(cm³/g)")
        table.setColumnWidth(1, 240)
        # Attach the real green control without running any distribution solver.
        w._add_bjh_region([0.3010299957, 1.0008677215], [0., 3.])
        w.bjh_region.setRegion([0.4, 1.4])
        expected = f"{10 ** .4:.2f}-{10 ** 1.4:.2f} nm"
        self.assertEqual(table.horizontalHeaderItem(1).text().splitlines()[1], expected)
        self.assertNotEqual(table.horizontalHeaderItem(2).text().splitlines()[1], expected)
        self.app.processEvents()
        self.assertEqual(table.horizontalHeader().height(), table._frozen_table.horizontalHeader().height())
        self.assertEqual(table.visualRect(table.model().index(0, 1)).top(),
                         table._frozen_table.visualRect(table.model().index(0, 0)).top())
        self.assertGreaterEqual(table.columnWidth(1), 240)
        self.assertIn("bjh", table._manual_content_widths)
        self.assertFalse(any("nm" in key for key in table._manual_content_widths))

    def test_all_selection_plots_have_reusable_endpoint_controls(self):
        w = self.w
        for method, plot_name in (("bjh", "pore"), ("dh", "dh"), ("hk", "hk"), ("dft", "dft")):
            add = getattr(w, f"_add_{method}_region")
            remove = getattr(w, f"_remove_{method}_region")
            add([.2, 1.2], [0., 2.])
            controls = getattr(w, plot_name + "_plot")._region_endpoint_controls
            self.assertTrue(controls.is_log)
            self.assertIs(controls.region, getattr(w, method + "_region"))
            remove()
            self.assertEqual(controls.labels, [])
            add([.3, 1.3], [0., 2.])
            self.assertIs(getattr(w, plot_name + "_plot")._region_endpoint_controls, controls)
        import numpy as np
        w._add_region([.05, .3], np.array([.001, .99]))
        controls = w.isotherm_plot._region_endpoint_controls
        self.assertFalse(controls.is_log)
        self.assertEqual(controls.values(), [.05, .3])
        w._remove_region()
        self.assertEqual(controls.labels, [])

    def test_pore_table_distinguishes_zero_missing_and_branch_choices(self):
        w = self.w
        result = self.samples[0]
        bjh = w._bjh_settings_for_result(result)
        dh = w._dh_settings_for_result(result)
        bjh.update(show_adsorption=False, show_desorption=False)
        dh.update(show_adsorption=False, show_desorption=True)
        w.custom_bjh_settings[id(result)] = bjh
        w.custom_dh_settings[id(result)] = dh
        with patch.object(w, "_hk_pore_volume_for_result", return_value=0.0), \
             patch.object(w, "_dft_pore_volume_for_result", return_value=None), \
             patch.object(w, "_cached_dh_distribution_rows", return_value=[]) as dh_rows:
            cells = w._pore_volume_cells_for_result(result)
        self.assertEqual(cells[1][0], "—")
        self.assertIn("分支：未开启", cells[1][2])
        self.assertEqual(dh_rows.call_args.kwargs["phase"], "desorption")
        self.assertIn("分支：脱附", cells[2][2])
        self.assertEqual(cells[3][0], "0")
        self.assertEqual(cells[4][0], "—")

    def test_hk_comparison_uses_hk_saved_pressure_not_active_bet_pressure(self):
        w = self.w
        w._custom_isotherm_region_keys.update({"bet", "hk"})
        w._isotherm_region_ranges.update(bet=(0.05, 0.3), hk=(0.001, 0.12))
        self.assertEqual(w._hk_pressure_range(), (0.001, 0.12))
        with patch.object(w, "_cached_hk_distribution_rows", return_value=[]), \
             patch("tristar_bet.ui.main_window.prepare_hk_distribution_rows", return_value=[]) as prepare:
            w._hk_pore_volume_for_result(self.samples[0])
            self.assertEqual(prepare.call_args.kwargs["pressure_range"], (0.001, 0.12))
        w._custom_isotherm_region_keys.remove("hk")
        self.assertNotEqual(w._hk_pressure_range(), w._isotherm_region_ranges["bet"])

    def test_long_summary_path_is_elided_without_widening_and_copies_in_full(self):
        path = "D:/" + "very-long-folder/" * 50 + "sample.SMP"
        result = self.w.results[1]
        self.w.results[1] = replace(result, header=replace(result.header, file_path=path))
        self.w.refresh_all()
        table = self.w.metrics_table
        self._show_table(table)
        row = next(row for row in range(table.rowCount()) if table.item(row, 0).text() == "文件路径")
        item = table.item(row, 1)
        self.assertEqual(item.text(), path)
        self.assertIn(path, item.toolTip())
        self.assertEqual(table.textElideMode(), QtCore.Qt.ElideRight)
        self.assertEqual(table.columnWidth(1), table.viewport().width() - table.columnWidth(0))
        self.assertEqual(table.horizontalScrollBar().maximum(), 0)
        clipboard = Mock()
        with patch.object(QtWidgets.QApplication, "clipboard", return_value=clipboard):
            pos = table.visualRect(table.model().index(row, 1)).center()
            QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, pos=pos)
            QtTest.QTest.mouseDClick(table.viewport(), QtCore.Qt.LeftButton, pos=pos)
        clipboard.setText.assert_called_once_with(path)


if __name__ == "__main__":
    unittest.main()
