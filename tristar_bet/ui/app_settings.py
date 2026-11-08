"""Gear-button settings dialog: export defaults, result display, about & feedback."""
from __future__ import annotations

from tristar_bet.i18n import LANGUAGES, current_language, tr

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

CONTACT_EMAIL = "kuailelong101@163.com"
EXPORT_FORMATS = {"xlsx": "Excel 工作簿 (.xlsx)", "pdf": "PDF 报告 (.pdf)"}
SINGLE_POINT_PRESSURE_RANGE = (0.05, 0.35)


@dataclass
class AppSettings:
    export_format: str
    export_directory: Path
    bet_display_mode: str
    samples_as_columns: bool
    single_point_pressure: float


def make_gear_icon(size: int = 28, color: str = "#475569") -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    c = size / 2.0
    teeth, outer, inner, hole = 8, size * 0.46, size * 0.34, size * 0.15
    gear = QtGui.QPainterPath()
    gear.setFillRule(QtCore.Qt.OddEvenFill)
    points = []
    for k in range(teeth):
        base = 2.0 * math.pi * k / teeth
        half_tooth = math.pi / teeth * 0.45
        for angle, radius in (
            (base - math.pi / teeth + 0.05, inner), (base - half_tooth, outer),
            (base + half_tooth, outer), (base + math.pi / teeth - 0.05, inner),
        ):
            points.append(QtCore.QPointF(c + radius * math.cos(angle), c + radius * math.sin(angle)))
    gear.addPolygon(QtGui.QPolygonF(points))
    gear.closeSubpath()
    gear.addEllipse(QtCore.QPointF(c, c), hole, hole)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(color))
    painter.drawPath(gear)
    painter.end()
    return QtGui.QIcon(pixmap)


class SupportedInstrumentsDialog(QtWidgets.QDialog):
    """Read-only list of supported instruments with a quick filter."""

    COLUMNS = ("厂商", "型号 / 软件版本", "文件格式", "支持程度", "说明")

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        from tristar_bet.supported_instruments import LEVEL_PARTIAL, NOT_SUPPORTED_NOTE, SUPPORTED_INSTRUMENTS

        super().__init__(parent)
        self.setWindowTitle(tr("支持的设备型号"))
        self.resize(1080, 620)
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(tr("以下仪器软件导出的物理吸附文件可直接导入。按厂商、型号或格式（如 SMP）筛选："))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText(tr("输入关键字，例如 ASAP、TriStar、QPS"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)
        self.table = QtWidgets.QTableWidget(len(SUPPORTED_INSTRUMENTS), len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(column) for column in self.COLUMNS])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        for row, item in enumerate(SUPPORTED_INSTRUMENTS):
            for column, text in enumerate((item.vendor, item.model, item.formats, item.level, item.note)):
                cell = QtWidgets.QTableWidgetItem(tr(text))
                if column == 3 and item.level == LEVEL_PARTIAL:
                    cell.setForeground(QtGui.QColor("#b45309"))
                self.table.setItem(row, column, cell)
        self.table.setTextElideMode(QtCore.Qt.ElideNone)
        header = self.table.horizontalHeader()
        for column, width in ((0, 180), (2, 155), (3, 80), (4, 250)):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
            label_width = header.fontMetrics().horizontalAdvance(tr(self.COLUMNS[column])) + 24
            self.table.setColumnWidth(column, max(width, label_width))
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.sectionResized.connect(lambda *_: self.table.resizeRowsToContents())
        layout.addWidget(self.table, 1)
        footer = QtWidgets.QLabel(
            tr('{0}<br>如果您的设备不在列表中，或文件导入失败，请将文件发送至 <a href="mailto:{1}">{2}</a>，我们会尽快适配。', tr(NOT_SUPPORTED_NOTE), CONTACT_EMAIL, CONTACT_EMAIL)
        )
        footer.setTextFormat(QtCore.Qt.RichText)
        footer.setOpenExternalLinks(True)
        footer.setWordWrap(True)
        layout.addWidget(footer)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close.button(QtWidgets.QDialogButtonBox.Close).setText(tr("关闭"))
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def showEvent(self, event) -> None:  # row heights need the final column widths
        super().showEvent(event)
        self.table.resizeRowsToContents()

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            haystack = " ".join(self.table.item(row, c).text() for c in range(self.table.columnCount())).lower()
            self.table.setRowHidden(row, bool(needle) and needle not in haystack)


class AppSettingsDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        current: AppSettings,
        bet_display_labels: dict[str, str],
        version: str,
        import_directory: Path | None = None,
        language_changed: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("设置"))
        self.setMinimumWidth(460)
        self._import_directory = import_directory
        self._language_changed = language_changed
        layout = QtWidgets.QVBoxLayout(self)
        # Wrapped labels (longer in English) keep their full height.
        layout.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)

        # First, and labelled in both languages, so it is found without reading Chinese.
        language_box = QtWidgets.QGroupBox(tr("语言 / Language"))
        language_form = QtWidgets.QFormLayout(language_box)
        self.language_combo = QtWidgets.QComboBox()
        for code, name in LANGUAGES.items():
            self.language_combo.addItem(name, code)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(current_language())))
        self.language_combo.setToolTip(tr("选择后立即生效，下次启动保持此语言"))
        self.language_combo.currentIndexChanged.connect(self._on_language_selected)
        language_form.addRow(tr("界面语言 / Language"), self.language_combo)
        layout.addWidget(language_box)

        export_box = QtWidgets.QGroupBox(tr("导出设置"))
        export_form = QtWidgets.QFormLayout(export_box)
        self.format_combo = QtWidgets.QComboBox()
        for key, label in EXPORT_FORMATS.items():
            self.format_combo.addItem(tr(label), key)
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(current.export_format)))
        export_form.addRow(tr("默认导出格式"), self.format_combo)
        directory_row = QtWidgets.QHBoxLayout()
        self.directory_edit = QtWidgets.QLineEdit(str(current.export_directory))
        self.directory_edit.setReadOnly(True)
        browse = QtWidgets.QPushButton(tr("更改..."))
        browse.clicked.connect(self._choose_directory)
        directory_row.addWidget(self.directory_edit, 1)
        directory_row.addWidget(browse)
        export_form.addRow(tr("默认导出文件夹"), directory_row)
        layout.addWidget(export_box)

        display_box = QtWidgets.QGroupBox(tr("结果显示"))
        display_form = QtWidgets.QFormLayout(display_box)
        self.bet_combo = QtWidgets.QComboBox()
        for key, label in bet_display_labels.items():
            self.bet_combo.addItem(label, key)
        self.bet_combo.setCurrentIndex(max(0, self.bet_combo.findData(current.bet_display_mode)))
        self.bet_combo.setToolTip(tr("样品列表 BET 列：默认优先有效多点 BET；也可固定显示单点或多点。"))
        display_form.addRow(tr("BET 列显示"), self.bet_combo)
        self.orientation_combo = QtWidgets.QComboBox()
        self.orientation_combo.addItem(tr("样品按列"), True)
        self.orientation_combo.addItem(tr("样品按行"), False)
        self.orientation_combo.setCurrentIndex(0 if current.samples_as_columns else 1)
        display_form.addRow(tr("结果表方向"), self.orientation_combo)
        self.single_point_spin = QtWidgets.QDoubleSpinBox()
        self.single_point_spin.setDecimals(3)
        self.single_point_spin.setSingleStep(0.01)
        self.single_point_spin.setRange(*SINGLE_POINT_PRESSURE_RANGE)
        self.single_point_spin.setValue(current.single_point_pressure)
        self.single_point_spin.setToolTip(tr("文件本身没有单点 BET 压力设置时使用；文件有设置时仍以文件为准。默认 0.200。"))
        display_form.addRow(tr("单点 BET 默认 P/P₀"), self.single_point_spin)
        layout.addWidget(display_box)

        about_box = QtWidgets.QGroupBox(tr("关于与反馈"))
        about_layout = QtWidgets.QVBoxLayout(about_box)
        about_layout.addWidget(QtWidgets.QLabel(tr('当前版本：v{0}', version)))
        contact = QtWidgets.QLabel(
            tr('导入失败或发现问题，请将失败文件发送至：<a href="mailto:{0}">{1}</a>', CONTACT_EMAIL, CONTACT_EMAIL)
        )
        contact.setTextFormat(QtCore.Qt.RichText)
        contact.setOpenExternalLinks(True)
        contact.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        # Line breaks come from the text itself: a word-wrapped rich-text label in
        # a group box is laid out too short and hides the e-mail link.
        contact.setWordWrap(False)
        about_layout.addWidget(contact)
        about_buttons = QtWidgets.QHBoxLayout()
        models_button = QtWidgets.QPushButton(tr("查看支持的设备型号"))
        models_button.setToolTip(tr("确认您的仪器和文件格式是否在支持范围内"))
        models_button.clicked.connect(self._show_supported_instruments)
        about_buttons.addWidget(models_button)
        copy_button = QtWidgets.QPushButton(tr("复制邮箱"))
        copy_button.clicked.connect(self._copy_email)
        about_buttons.addWidget(copy_button)
        open_import = QtWidgets.QPushButton(tr("打开上次导入的文件夹"))
        open_import.setToolTip(tr("方便找到需要发送的失败文件"))
        open_import.setEnabled(bool(import_directory and Path(import_directory).is_dir()))
        open_import.clicked.connect(self._open_import_directory)
        about_buttons.addWidget(open_import)
        about_buttons.addStretch(1)
        about_layout.addLayout(about_buttons)
        layout.addWidget(about_box)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText(tr("确定"))
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText(tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_language_selected(self, _index: int) -> None:
        if self._language_changed is not None:
            self._language_changed(str(self.language_combo.currentData()))

    def _choose_directory(self) -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, tr("选择默认导出文件夹"), self.directory_edit.text())
        if chosen:
            self.directory_edit.setText(str(Path(chosen)))

    def _show_supported_instruments(self) -> None:
        SupportedInstrumentsDialog(self).exec_()

    def _copy_email(self) -> None:
        QtWidgets.QApplication.clipboard().setText(CONTACT_EMAIL)

    def _open_import_directory(self) -> None:
        if self._import_directory:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._import_directory)))

    def values(self) -> AppSettings:
        return AppSettings(
            export_format=str(self.format_combo.currentData()),
            export_directory=Path(self.directory_edit.text()),
            bet_display_mode=str(self.bet_combo.currentData()),
            samples_as_columns=bool(self.orientation_combo.currentData()),
            single_point_pressure=round(float(self.single_point_spin.value()), 3),
        )
