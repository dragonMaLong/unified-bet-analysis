"""Native-style headers for the two orientations of analysis result tables."""
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from .detail_tables import RESULT_STATUS_ROLE, paint_status_badge


def transpose_icon():
    pixmap = QtGui.QPixmap(20, 20)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtGui.QPen(QtGui.QColor("#4b5563"), 1.25,
                            QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    painter.drawRect(QtCore.QRectF(2, 2, 10, 10))
    painter.drawLine(QtCore.QPointF(2, 7), QtCore.QPointF(12, 7))
    painter.drawLine(QtCore.QPointF(7, 2), QtCore.QPointF(7, 12))
    for start, end in (((4, 17), (17, 17)), ((14, 14), (17, 17)),
                       ((14, 19), (17, 17)), ((17, 4), (17, 12)),
                       ((14, 9), (17, 12)), ((19, 9), (17, 12))):
        painter.drawLine(QtCore.QPointF(*start), QtCore.QPointF(*end))
    painter.end()
    return QtGui.QIcon(pixmap)


class AnalysisHeader(QtWidgets.QHeaderView):
    def __init__(self, table, parent):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self.table = table
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setDefaultAlignment(QtCore.Qt.AlignCenter)
        self.transpose_button = None
        self._section_click = None
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.sectionResized.connect(self.position_button)
        self.geometriesChanged.connect(self.position_button)

    def enable_transpose(self, callback):
        button = QtWidgets.QToolButton(self.viewport())
        button.setAutoRaise(True)
        button.setIcon(transpose_icon())
        button.setIconSize(QtCore.QSize(18, 18))
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setAccessibleName("行列切换")
        button.clicked.connect(callback)
        self.transpose_button = button
        self.update_layout_state()

    def update_layout_state(self):
        if self.transpose_button is not None:
            target = "行" if getattr(self.table, "_transposed", False) else "列"
            self.transpose_button.setToolTip(f"切换为：样品作为{target}")
            self.position_button()
        self.viewport().update()

    def position_button(self, *_args):
        button = self.transpose_button
        if button is None or self.count() == 0:
            return
        size = min(22, self.height() - 2)
        # Leave the section divider free for resizing the frozen column.
        button.setGeometry(self.sectionViewportPosition(0) + self.sectionSize(0) - size - 7,
                           max(1, (self.height() - size) // 2), size, size)
        button.show()
        button.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_button()

    def _on_divider(self, position):
        x = position.x()
        section = self.logicalIndexAt(position)
        if section < 0:
            return False
        left = self.sectionViewportPosition(section)
        return (section > 0 and abs(x - left) <= 5) or abs(x - left - self.sectionSize(section)) <= 5

    def mousePressEvent(self, event):
        self._section_click = None
        if event.button() == QtCore.Qt.LeftButton and not self._on_divider(event.pos()):
            self.table.clearSelection()
            self._section_click = (self.logicalIndexAt(event.pos()), event.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        pending = self._section_click
        self._section_click = None
        if pending is not None and event.button() == QtCore.Qt.LeftButton:
            section, start = pending
            if section >= 0 and section == self.logicalIndexAt(event.pos()) and (event.pos() - start).manhattanLength() < QtWidgets.QApplication.startDragDistance():
                # Do not emit sectionPressed/sectionEntered: QTableView uses
                # those signals to select entire columns before sorting.
                self.sectionClicked.emit(section)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._on_divider(event.pos()):
            super().mouseDoubleClickEvent(event)
        else:
            event.accept()

    def mouseMoveEvent(self, event):
        if getattr(self.table, "_transposed", False):
            self.table._set_hovered_sample_column(self.logicalIndexAt(event.pos()) - 1)
        if self._section_click is not None:
            event.accept()
            return
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if getattr(self.table, "_transposed", False):
            self.table._set_hovered_sample_column(-1)
        super().leaveEvent(event)

    def paintSection(self, painter, rect, section):
        is_sample = getattr(self.table, "_transposed", False) and section > 0
        if not is_sample and not (section == 0 and self.transpose_button is not None):
            return super().paintSection(painter, rect, section)
        item = self.table.horizontalHeaderItem(section)
        if item is None:
            return super().paintSection(painter, rect, section)
        painter.save()
        opt = QtWidgets.QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = section
        opt.text = ""
        opt.position = QtWidgets.QStyleOptionHeader.Beginning if section == 0 else QtWidgets.QStyleOptionHeader.Middle
        painter.save()
        self.style().drawControl(QtWidgets.QStyle.CE_Header, opt, painter, self)
        painter.restore()
        selected = is_sample and (section - 1 == getattr(self.table, "_active_sample_row", -1)
                                  or section in getattr(self.table, "_selected_cell_columns", set()))
        font = QtGui.QFont(self.font())
        if selected or (is_sample and section - 1 == getattr(self.table, "_header_hover_sample", -1)):
            font.setBold(True)
        painter.setFont(font)
        painter.setPen(self.palette().color(QtGui.QPalette.WindowText))
        reserved = 26 if is_sample else 32
        text_rect = rect.adjusted(6, 0, -reserved, 0)
        text = QtGui.QFontMetrics(font).elidedText(item.text(), QtCore.Qt.ElideRight, max(0, text_rect.width()))
        painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)
        if is_sample:
            paint_status_badge(painter, rect, item.data(RESULT_STATUS_ROLE))
        painter.restore()
