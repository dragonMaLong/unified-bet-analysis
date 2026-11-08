"""Re-translate the texts of existing widgets after the interface language changes.

Every text that was set through ``tr()`` is mapped from the old language to the
new one (``i18n.retranslate_text``).  Result tables and plot contents are
redrawn by the main window afterwards, so values computed for the old language
are replaced as well.
"""
from __future__ import annotations

import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets

from tristar_bet.i18n import retranslate_text


# (object id, slot) -> {language: text shown in that language}.  Several Chinese
# spellings of one label ("吸附量(cm3/g STP)", "吸附量 (cm3/g STP)") share an
# English text; switching back restores the exact text this slot showed.
_shown_texts: dict[tuple, dict[str, str]] = {}


def _swap(getter, setter, old: str, new: str, key: tuple | None = None) -> None:
    try:
        text = getter()
    except RuntimeError:  # deleted C++ object
        return
    if not isinstance(text, str) or not text:
        return
    if key is None and getattr(getter, "__self__", None) is not None:
        key = (id(getter.__self__), getter.__name__)
    remembered = _shown_texts.get(key) if key is not None else None
    if remembered is not None and remembered.get(old) == text and new in remembered:
        translated = remembered[new]
    else:
        translated = retranslate_text(text, old, new)
        remembered = {old: text}
    if key is not None:
        remembered[new] = translated
        _shown_texts[key] = remembered
    if translated != text:
        setter(translated)


def _table_item(item, old: str, new: str, key: tuple) -> None:
    # Item wrappers are not stable objects; the slot is identified by its owner and position.
    if item is None:
        return
    _swap(item.text, item.setText, old, new, (*key, "text"))
    _swap(item.toolTip, item.setToolTip, old, new, (*key, "toolTip"))


def _action(action, old: str, new: str) -> None:
    _swap(action.text, action.setText, old, new)
    _swap(action.toolTip, action.setToolTip, old, new)
    _swap(action.statusTip, action.setStatusTip, old, new)
    menu = action.menu()
    if menu is not None:
        _swap(menu.title, menu.setTitle, old, new)
        for child in menu.actions():
            _action(child, old, new)


def retranslate_widget(widget: QtWidgets.QWidget, old: str, new: str) -> None:
    _swap(widget.windowTitle, widget.setWindowTitle, old, new)
    _swap(widget.toolTip, widget.setToolTip, old, new)
    _swap(widget.statusTip, widget.setStatusTip, old, new)
    _swap(widget.whatsThis, widget.setWhatsThis, old, new)
    for action in widget.actions():
        _action(action, old, new)
    if isinstance(widget, (QtWidgets.QLabel, QtWidgets.QAbstractButton)):
        _swap(widget.text, widget.setText, old, new)
    if isinstance(widget, QtWidgets.QGroupBox):
        _swap(widget.title, widget.setTitle, old, new)
    if isinstance(widget, QtWidgets.QMenu):
        _swap(widget.title, widget.setTitle, old, new)
    if isinstance(widget, QtWidgets.QLineEdit):
        _swap(widget.placeholderText, widget.setPlaceholderText, old, new)
        if widget.isReadOnly():
            _swap(widget.text, widget.setText, old, new)
    if isinstance(widget, QtWidgets.QAbstractSpinBox):
        _swap(widget.specialValueText, widget.setSpecialValueText, old, new)
        if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            _swap(widget.prefix, widget.setPrefix, old, new)
            _swap(widget.suffix, widget.setSuffix, old, new)
    if isinstance(widget, QtWidgets.QComboBox):
        for index in range(widget.count()):
            _swap(lambda i=index: widget.itemText(i), lambda text, i=index: widget.setItemText(i, text), old, new,
                  (id(widget), "itemText", index))
            _swap(lambda i=index: widget.itemData(i, 3), lambda text, i=index: widget.setItemData(i, text, 3), old, new,
                  (id(widget), "itemToolTip", index))
    if isinstance(widget, QtWidgets.QTabBar):
        for index in range(widget.count()):
            _swap(lambda i=index: widget.tabText(i), lambda text, i=index: widget.setTabText(i, text), old, new,
                  (id(widget), "tabText", index))
            _swap(lambda i=index: widget.tabToolTip(i), lambda text, i=index: widget.setTabToolTip(i, text), old, new,
                  (id(widget), "tabToolTip", index))
    if isinstance(widget, QtWidgets.QTableWidget):
        for column in range(widget.columnCount()):
            _table_item(widget.horizontalHeaderItem(column), old, new, (id(widget), "h", column))
        for row in range(widget.rowCount()):
            _table_item(widget.verticalHeaderItem(row), old, new, (id(widget), "v", row))
            for column in range(widget.columnCount()):
                _table_item(widget.item(row, column), old, new, (id(widget), "cell", row, column))
    if isinstance(widget, QtWidgets.QTreeWidget):
        header = widget.headerItem()
        for column in range(widget.columnCount()):
            _swap(lambda c=column: header.text(c), lambda text, c=column: header.setText(c, text), old, new,
                  (id(widget), "headerText", column))
        iterator = QtWidgets.QTreeWidgetItemIterator(widget)
        while iterator.value() is not None:
            item = iterator.value()
            for column in range(widget.columnCount()):
                _swap(lambda c=column: item.text(c), lambda text, c=column: item.setText(c, text), old, new)
                _swap(lambda c=column: item.toolTip(c), lambda text, c=column: item.setToolTip(c, text), old, new)
                # (tree items: no stable position key; spelling variants fall back to the catalogue)
            iterator += 1
    if isinstance(widget, QtWidgets.QListWidget):
        for row in range(widget.count()):
            _table_item(widget.item(row), old, new, (id(widget), "item", row))
    if isinstance(widget, QtWidgets.QHeaderView):
        model = widget.model()
        if model is not None and not isinstance(widget.parent(), QtWidgets.QTableWidget):
            orientation = widget.orientation()
            for section in range(model.columnCount() if orientation == 1 else model.rowCount()):
                _swap(lambda s=section: model.headerData(s, orientation),
                      lambda text, s=section: model.setHeaderData(s, orientation, text), old, new)
    if isinstance(widget, pg.GraphicsView):
        _retranslate_scene(widget, old, new)


def _retranslate_scene(view, old: str, new: str) -> None:
    scene = view.scene()
    if scene is None:
        return
    for item in scene.items():
        if isinstance(item, pg.AxisItem):
            _swap(lambda axis=item: axis.labelText,
                  lambda text, axis=item: axis.setLabel(text, units=axis.labelUnits or None,
                                                        unitPrefix=axis.labelUnitPrefix or None, **axis.labelStyle),
                  old, new, (id(item), "axisLabel"))
        elif isinstance(item, pg.LabelItem):
            _swap(lambda label=item: label.text, lambda text, label=item: label.setText(text, **label.opts),
                  old, new, (id(item), "labelText"))
        elif isinstance(item, pg.TextItem):
            _swap(item.textItem.toPlainText, item.setText, old, new, (id(item), "plainText"))

def retranslate_application(old: str, new: str) -> None:
    """Every existing top-level window (main window, open dialogs) and its children."""
    # A dialog is both a top-level window and a child of the main window: every
    # widget is translated exactly once, or the second pass would treat the text
    # it already switched as text in the old language.
    done: set[int] = set()
    for top in QtWidgets.QApplication.topLevelWidgets():
        for widget in [top, *top.findChildren(QtWidgets.QWidget)]:
            if id(widget) in done:
                continue
            done.add(id(widget))
            try:
                retranslate_widget(widget, old, new)
            except RuntimeError:
                continue
        if isinstance(top, QtWidgets.QDialog) and top.isVisible() and top.layout() is not None:
            # Longer translations wrap onto more lines; open dialogs grow to show them.
            top.layout().activate()
            top.resize(top.width(), max(top.height(), top.sizeHint().height()))
