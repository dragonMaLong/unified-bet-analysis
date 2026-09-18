"""Column definitions and content sizing shared by the detail tables."""

import csv
import html
import io

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets


RESULT_STATUS_ROLE = QtCore.Qt.UserRole + 401
SELECTION_BLUE = "#e0ecff"


ANALYSIS_COLUMNS = {
    "bet": [
        ("file", "文件名"),
        ("area", "多点BET比表面积(m²/g)"), ("area_error", "±比表面积误差(m²/g)"),
        ("slope", "多点BET斜率(g/cm³ STP)"), ("slope_error", "±斜率误差(g/cm³ STP)"),
        ("intercept", "多点BET Y截距(g/cm³ STP)"), ("intercept_error", "±Y截距误差(g/cm³ STP)"),
        ("capacity", "多点BET单层容量(cm³/g STP)"),
        ("c", "多点BET C常数"), ("r", "多点BET相关系数"), ("r2", "多点BET R²"),
        ("p_min", "拟合起点P/P₀"), ("p_max", "拟合终点P/P₀"), ("count", "拟合点数"),
    ],
    "langmuir": [
        ("file", "文件名"), ("area", "比表面积(m²/g)"), ("area_error", "±比表面积误差(m²/g)"),
        ("capacity", "单层容量(cm³/g STP)"), ("slope", "斜率(g/cm³ STP)"),
        ("slope_error", "±斜率误差(g/cm³ STP)"),
        ("intercept", "Y截距(g/cm³ STP)"), ("intercept_error", "±Y截距误差(g/cm³ STP)"),
        ("b", "Langmuir常数b"),
        ("r", "相关系数"), ("r2", "R²"),
        ("p_min", "拟合起点P/P₀"), ("p_max", "拟合终点P/P₀"), ("count", "拟合点数"),
    ],
    "t_plot": [
        ("file", "文件名"), ("external_area", "外比表面积(m²/g)"),
        ("micro_volume", "微孔体积(cm³/g)"), ("micro_area", "微孔面积(m²/g)"),
        ("slope", "斜率(mmol/g/nm)"), ("slope_error", "±斜率误差(mmol/g/nm)"),
        ("intercept", "Y截距(mmol/g)"), ("intercept_error", "±Y截距误差(mmol/g)"),
        ("r", "相关系数"), ("r2", "R²"), ("t_min", "拟合起点厚度(nm)"),
        ("t_max", "拟合终点厚度(nm)"), ("count", "拟合点数"),
        ("method", "厚度方程"), ("params", "厚度方程参数"),
        ("area_source", "总表面积来源"), ("total_area", "总表面积(m²/g)"),
        ("correction", "比表面积修正因子"), ("density", "密度转换因子"),
    ],
}

SINGLE_BET_COLUMNS = [
    ("single_area", "单点BET比表面积(m²/g)"),
    ("single_p", "单点BET相对压力P/P₀"), ("single_capacity", "单点BET单层容量(cm³/g STP)"),
    ("single_source", "单点BET名义来源点"), ("single_points", "单点BET插值点"),
]

PORE_VOLUME_COLUMNS = [
    ("file", "文件名"), ("bjh", "BJH(cm³/g)"), ("dh", "DH(cm³/g)"),
    ("hk", "HK(cm³/g)"), ("dft", "DFT(cm³/g)"),
]


class ResultFileDelegate(QtWidgets.QStyledItemDelegate):
    """Completion badge and row context without adding cells to the copy range."""

    def __init__(self, table, parent):
        super().__init__(parent)
        self.table = table

    def paint(self, painter, option, index):
        if getattr(self.table, "_transposed", False):
            return super().paint(painter, option, index)
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.state &= ~QtWidgets.QStyle.State_HasFocus
        emphasized = index.row() == getattr(self.table, "_active_sample_row", -1) or index.row() in getattr(self.table, "_selected_cell_rows", set())
        if emphasized:
            opt.font.setBold(True)
        for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive):
            opt.palette.setColor(group, QtGui.QPalette.Highlight, QtGui.QColor(SELECTION_BLUE))
            opt.palette.setColor(group, QtGui.QPalette.HighlightedText, opt.palette.color(QtGui.QPalette.Text))
        style = opt.widget.style() if opt.widget else QtWidgets.QApplication.style()
        text = opt.text
        opt.text = ""
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        opt.text = text
        if getattr(self.table, "_has_completion_badges", False):
            opt.rect = opt.rect.adjusted(0, 0, -26, 0)
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        paint_status_badge(painter, option.rect, index.data(RESULT_STATUS_ROLE))


def paint_status_badge(painter, rect, status):
    if status not in {"ok", "warning"}:
        return
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    badge = QtCore.QRectF(rect.right() - 21, rect.center().y() - 7, 14, 14)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor("#16a34a" if status == "ok" else "#d97706"))
    painter.drawEllipse(badge)
    painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.7, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    x, y = badge.left(), badge.top()
    if status == "ok":
        path = QtGui.QPainterPath(QtCore.QPointF(x + 3, y + 7))
        path.lineTo(x + 6, y + 10)
        path.lineTo(x + 11, y + 4)
        painter.drawPath(path)
    else:
        painter.drawLine(QtCore.QPointF(x + 7, y + 3), QtCore.QPointF(x + 7, y + 8))
        painter.drawPoint(QtCore.QPointF(x + 7, y + 11))
    painter.restore()


class TableCopyController(QtCore.QObject):
    def __init__(self, table, views):
        super().__init__(table)
        self.table = table
        self.views = views
        self.shortcut = QtWidgets.QShortcut(QtGui.QKeySequence.Copy, table)
        self.shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        self.shortcut.activated.connect(self.copy)
        for view in views:
            view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
            view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda position, owner=view: self.show_menu(owner, position))
            view.viewport().installEventFilter(self)
        table.selectionModel().selectionChanged.connect(self._selection_changed)

    def eventFilter(self, watched, event):
        # A right-click must not replace the rectangular selection with one cell.
        if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.RightButton:
            if self.table.selectionModel().hasSelection():
                return True
        return super().eventFilter(watched, event)

    def _selection_changed(self, *_args):
        self.table._selected_cell_rows = {index.row() for index in self.table.selectionModel().selectedIndexes()}
        self.table._selected_cell_columns = {index.column() for index in self.table.selectionModel().selectedIndexes()}
        for view in self.views:
            view.viewport().update()
            view.horizontalHeader().viewport().update()

    def selected_grid(self):
        selected = self.table.selectionModel().selectedIndexes()
        if not selected:
            return []
        values = {}
        for index in selected:
            value = index.data(QtCore.Qt.DisplayRole)
            values[index.row(), index.column()] = "" if value is None else str(value)
        top, bottom = min(index.row() for index in selected), max(index.row() for index in selected)
        left, right = min(index.column() for index in selected), max(index.column() for index in selected)
        return [[values.get((row, column), "") for column in range(left, right + 1)] for row in range(top, bottom + 1)]

    def mime_data(self):
        grid = self.selected_grid()
        if not grid:
            return None
        stream = io.StringIO(newline="")
        csv.writer(stream, delimiter="\t", lineterminator="\r\n").writerows(grid)
        mime = QtCore.QMimeData()
        mime.setText(stream.getvalue())
        mime.setHtml("<html><body><table>" + "".join(
            "<tr>" + "".join("<td>" + html.escape(value).replace("\n", "<br>") + "</td>" for value in row) + "</tr>"
            for row in grid
        ) + "</table></body></html>")
        return mime

    def copy(self):
        mime = self.mime_data()
        if mime is not None:
            QtWidgets.QApplication.clipboard().setMimeData(mime)

    def show_menu(self, view, position):
        if not self.table.selectionModel().hasSelection():
            index = view.indexAt(position)
            if index.isValid():
                view.selectionModel().setCurrentIndex(index, QtCore.QItemSelectionModel.ClearAndSelect)
        menu = QtWidgets.QMenu(view)
        # Same compact menu as the mercury app's pore-structure tables.
        menu.setStyleSheet("""
            QMenu {
                background: #ffffff;
                border: 1px solid #d1d5db;
                padding: 3px;
            }
            QMenu::item {
                color: #111827;
                background: transparent;
                padding: 6px 12px;
                margin: 0;
            }
            QMenu::item:selected { background: #e0ecff; }
            QMenu::indicator { width: 0; height: 0; }
        """)
        action = menu.addAction("复制")
        action.setEnabled(self.table.selectionModel().hasSelection())
        action.triggered.connect(self.copy)
        menu.exec_(view.viewport().mapToGlobal(position))


class DetailTableInteractionController(QtCore.QObject):
    """Keep copy selections local to the current interaction, without tooltips."""
    def __init__(self, window, tables):
        super().__init__(window)
        self.window = window
        self.tables = tables
        QtWidgets.QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QtCore.QEvent.ToolTip, QtCore.QEvent.MouseButtonPress):
            return False
        if not isinstance(watched, QtWidgets.QWidget):
            return False
        owner = next((table for table in self.tables if watched is table or table.isAncestorOf(watched)), None)
        if kind == QtCore.QEvent.ToolTip:
            if owner is not None:
                QtWidgets.QToolTip.hideText()
                return True
            return False
        if event.button() != QtCore.Qt.LeftButton:
            return False
        ancestor = watched
        while ancestor is not None:
            # Clicking the Copy action must retain the selection until copied.
            if isinstance(ancestor, QtWidgets.QMenu):
                if ancestor.rect().contains(ancestor.mapFromGlobal(event.globalPos())):
                    return False
                break
            ancestor = ancestor.parentWidget()
        if watched is not self.window and not self.window.isAncestorOf(watched):
            return False
        for table in self.tables:
            views = [table]
            frozen = getattr(table, "_frozen_table", None)
            if frozen is not None:
                views.append(frozen)
            inside_cell = any(watched is view.viewport() and view.indexAt(event.pos()).isValid() for view in views)
            if not inside_cell:
                table.clearSelection()
        return False


def configure_cell_copy(table, frozen_view=None):
    # Only change selection styling; retain the existing fonts, headers and grid.
    table.setStyleSheet(table.styleSheet() + f"\nQTableView::item:selected {{ background: {SELECTION_BLUE}; color: #111827; }}")
    views = [table] + ([frozen_view] if frozen_view is not None else [])
    table._copy_controller = TableCopyController(table, views)


def _column_width_key(table, column, title):
    keys = getattr(table, "_column_keys", [])
    return keys[column] if column < len(keys) else title


def configure_content_widths(table):
    """Remember user widths by header, including dynamically added columns."""
    table._manual_content_widths = {}
    table._sizing_content = False
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)

    def resized(column, _old, width):
        if table.horizontalHeader().stretchLastSection() and column == table.columnCount() - 1:
            # Window resizing is not a manual column-width choice.
            return
        if not table._sizing_content:
            item = table.horizontalHeaderItem(column)
            if item is not None:
                table._manual_content_widths[_column_width_key(table, column, item.text())] = width

    table.horizontalHeader().sectionResized.connect(resized)


def fit_content_widths(table):
    """Fit both ordinary and hovered (bold) text with explicit padding."""
    table._sizing_content = True
    try:
        header_font = QtGui.QFont(table.horizontalHeader().font())
        header_font.setBold(True)
        header_metrics = QtGui.QFontMetrics(header_font)
        font = QtGui.QFont(table.font())
        font.setBold(True)
        metrics = QtGui.QFontMetrics(font)
        for column in range(table.columnCount()):
            header = table.horizontalHeaderItem(column)
            title = header.text() if header is not None else ""
            width = max(header_metrics.horizontalAdvance(line) for line in title.split("\n")) + 24
            if getattr(table, "_transpose_enabled", False):
                width += 30 if column == 0 else (26 if getattr(table, "_transposed", False) else 0)
            reference = getattr(table, "_column_width_references", {}).get(column)
            if reference is not None:
                width = max(width, metrics.horizontalAdvance(reference) + 24)
            for row in range(table.rowCount() if reference is None else 0):
                item = table.item(row, column)
                if item is not None:
                    extra = 26 if column == 0 and getattr(table, "_has_completion_badges", False) and not getattr(table, "_transposed", False) else 0
                    width = max(width, metrics.horizontalAdvance(item.text()) + 24 + extra)
            width = max(40, width)
            # Do not overwrite a user's wider choice; grow if new content needs it.
            width = max(width, table._manual_content_widths.get(_column_width_key(table, column, title), 0))
            table.setColumnWidth(column, width)
    finally:
        table._sizing_content = False
