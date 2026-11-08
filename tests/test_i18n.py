"""Interface language: catalogue completeness, live switching, persistence, exports."""
import ast
import io
import json
import os
import re
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets  # before pyqtgraph, which then uses the same binding
import pyqtgraph as pg

from tristar_bet import i18n

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")
PLACEHOLDER = re.compile(r"\{\d+(?:![rsa])?(?::[^{}]*)?\}")
# Parsers match these Chinese labels inside vendor files; they are data, never shown.
DATA_ONLY_FILES = {"excel_import.py", "i18n.py"}
ASAP_EXAMPLE = Path(r"C:\ASAP 2020 Plus\data\Silica Alumina Reference Material with Nitrogen.SMP")


def source_strings() -> dict[str, str]:
    """Chinese texts passed to tr() and Chinese constants translated where they are used."""
    found: dict[str, str] = {}
    for path in sorted((ROOT / "tristar_bet").rglob("*.py")):
        if path.name in DATA_ONLY_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            text = None
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args:
                if isinstance(node.args[0], ast.Constant):
                    text = node.args[0].value
            elif (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == "trc"
                  and len(node.args) >= 2 and all(isinstance(arg, ast.Constant) for arg in node.args[:2])):
                text = node.args[0].value + i18n.CONTEXT_SEPARATOR + node.args[1].value
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK.search(node.value):
                child, parent = node, parents.get(node)
                while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    child, parent = parent, parents.get(parent)
                inside = parent is not None and ((child is parent.body) if isinstance(parent, ast.Lambda) else child in parent.body)
                if not inside and not isinstance(parents.get(node), ast.Expr):
                    text = node.value
            if isinstance(text, str) and CJK.search(text):
                found.setdefault(text, f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = json.loads((ROOT / "tristar_bet" / "locales" / "en.json").read_text(encoding="utf-8"))

    def test_every_interface_text_has_an_english_translation(self):
        missing = {text: where for text, where in source_strings().items() if not self.catalogue.get(text)}
        self.assertEqual(missing, {}, "add these texts to tristar_bet/locales/en.json")

    def test_translations_keep_placeholders_line_breaks_and_markup(self):
        for zh, en in self.catalogue.items():
            with self.subTest(zh=zh):
                self.assertEqual(sorted(PLACEHOLDER.findall(zh.replace("{{", "").replace("}}", ""))),
                                 sorted(PLACEHOLDER.findall(en.replace("{{", "").replace("}}", ""))))
                self.assertEqual(zh.count("\n"), en.count("\n"))
                # <br> may be added where a translation needs its own line break.
                tags = lambda text: sorted(tag for tag in re.findall(r"<[^>]+>", text) if tag != "<br>")
                self.assertEqual(tags(zh), tags(en))
                self.assertIsNone(CJK.search(en), en)

    def test_english_texts_map_back_to_one_chinese_source(self):
        # Switching back from English looks the source up by the English text.
        by_english: dict[str, list[str]] = {}
        for zh, en in self.catalogue.items():
            by_english.setdefault(en, []).append(zh)
        # Spacing / full-width punctuation variants of one text may share a translation.
        norm = lambda text: re.sub(r"\s", "", unicodedata.normalize("NFKC", text))
        self.assertEqual({en: zh for en, zh in by_english.items() if len({norm(z) for z in zh}) > 1}, {})

    def test_language_names_are_written_in_their_own_language(self):
        self.assertEqual(i18n.LANGUAGES, {"zh": "简体中文", "en": "English"})
        for code in i18n.LANGUAGES:
            if code != i18n.SOURCE_LANGUAGE:
                self.assertTrue((ROOT / "tristar_bet" / "locales" / f"{code}.json").is_file())


class TranslationFunctionTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("zh")

    def test_tr_formats_and_falls_back_to_chinese(self):
        self.assertEqual(i18n.tr("可导入 {0} 个，待导入 {1} 个", 3, 0), "可导入 3 个，待导入 0 个")
        i18n.set_language("en")
        self.assertNotIn("可导入", i18n.tr("可导入 {0} 个，待导入 {1} 个", 3, 0))
        self.assertIn("3", i18n.tr("可导入 {0} 个，待导入 {1} 个", 3, 0))
        self.assertEqual(i18n.tr("某段不在目录中的文字"), "某段不在目录中的文字")

    def test_unknown_language_falls_back_to_chinese(self):
        self.assertFalse(i18n.set_language("xx"))
        self.assertEqual(i18n.current_language(), "zh")

    def test_shown_texts_round_trip_including_values(self):
        for zh in ("导入文件", "可导入 3 个，待导入 0 个", "正在下载软件更新... 1.5 / 3.0 MB"):
            with self.subTest(zh=zh):
                en = i18n.retranslate_text(zh, "zh", "en")
                self.assertIsNone(CJK.search(en), en)
                self.assertEqual(i18n.retranslate_text(en, "en", "zh"), zh)
        self.assertEqual(i18n.retranslate_text("A.SMP", "zh", "en"), "A.SMP")

    def test_a_failing_listener_neither_blocks_others_nor_is_dropped(self):
        calls = []

        def failing(_old, _new):
            raise RuntimeError("wrapped C/C++ object has been deleted")

        def other(_old, new):
            calls.append(new)

        i18n.add_listener(failing)
        i18n.add_listener(other)
        self.addCleanup(i18n.remove_listener, failing)
        self.addCleanup(i18n.remove_listener, other)
        with patch("traceback.print_exc"):
            i18n.set_language("en")
            i18n.set_language("zh")
        self.assertEqual(calls, ["en", "zh"])
        self.assertIn(failing, i18n._listeners)

def visible_texts(window) -> list[str]:
    """Texts a user can read: widgets, items, headers, tooltips and plot labels."""
    texts = [window.windowTitle()]
    for widget in [window, *window.findChildren(QtWidgets.QWidget)]:
        texts += [widget.toolTip(), widget.windowTitle()]
        for action in widget.actions():
            texts += [action.text(), action.toolTip()]
        if isinstance(widget, (QtWidgets.QLabel, QtWidgets.QAbstractButton)):
            texts.append(widget.text())
        if isinstance(widget, QtWidgets.QGroupBox):
            texts.append(widget.title())
        if isinstance(widget, QtWidgets.QLineEdit):
            texts.append(widget.placeholderText())
        if isinstance(widget, QtWidgets.QComboBox):
            texts += [widget.itemText(i) for i in range(widget.count())]
        if isinstance(widget, QtWidgets.QTabBar):
            texts += [widget.tabText(i) for i in range(widget.count())]
            texts += [widget.tabToolTip(i) for i in range(widget.count())]
        if isinstance(widget, QtWidgets.QTableWidget):
            for column in range(widget.columnCount()):
                item = widget.horizontalHeaderItem(column)
                texts += [item.text(), item.toolTip()] if item else []
            for row in range(widget.rowCount()):
                item = widget.verticalHeaderItem(row)
                texts += [item.text()] if item else []
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    texts += [item.text(), item.toolTip()] if item else []
        if isinstance(widget, pg.GraphicsView) and widget.scene() is not None:
            for item in widget.scene().items():
                if isinstance(item, pg.AxisItem):
                    texts.append(item.labelText)
                elif isinstance(item, pg.LabelItem):
                    texts.append(item.text)
                elif isinstance(item, pg.TextItem):
                    texts.append(item.textItem.toPlainText())
    return [text for text in texts if isinstance(text, str) and text]


class LiveSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temp.name) / "settings.ini"
        self.windows = []

    def tearDown(self):
        i18n.set_language("zh")
        for window in self.windows:
            window.close()
            window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.temp.cleanup()

    def make_window(self):
        from tristar_bet.ui.main_window import MainWindow

        settings = QtCore.QSettings(str(self.settings_path), QtCore.QSettings.IniFormat)
        with patch.object(MainWindow, "_auto_check_for_updates"), patch.object(MainWindow, "_initialize_telemetry"), \
                patch("tristar_bet.ui.main_window.QtCore.QSettings", return_value=settings):
            window = MainWindow()
        self.windows.append(window)
        return window

    def loaded_window(self):
        from test_detail_result_tables import sample

        window = self.make_window()
        results = [sample("A", 1), sample("B", 2)]
        if ASAP_EXAMPLE.is_file():
            from tristar_bet.smp import load_smp

            results.append(load_smp(ASAP_EXAMPLE))
        window.results = results
        window.visible_results = [True] * len(results)
        window.sample_colors = ["#123456", "#234567", "#345678"][: len(results)]
        window.active_index = 0
        window.refresh_all()
        return window

    def chinese_left(self, window):
        names = {result.sample_name for result in window.results} | {result.file_name for result in window.results}
        return sorted({text for text in visible_texts(window)
                       if CJK.search(text) and not any(name and name in text for name in names)})

    def test_switch_translates_the_whole_window_without_restart(self):
        window = self.loaded_window()
        for tab in range(window.plot_tabs.count()) if hasattr(window, "plot_tabs") else ():
            window.plot_tabs.setCurrentIndex(tab)
        chinese = sorted(set(visible_texts(window)))
        window.set_interface_language("en")
        self.assertEqual(self.chinese_left(window), [])
        window.set_interface_language("zh")
        self.assertEqual(sorted(set(visible_texts(window))), chinese)

    def test_choice_is_saved_and_used_at_next_start(self):
        window = self.make_window()
        window.set_interface_language("en")
        QtCore.QSettings(str(self.settings_path), QtCore.QSettings.IniFormat).sync()
        i18n.set_language("zh")  # as after a restart, before the saved choice is read
        restarted = self.make_window()
        self.assertEqual(i18n.current_language(), "en")
        self.assertEqual(self.chinese_left(restarted), [])

    def test_language_switch_rebuilds_cached_result_values_before_transposing(self):
        from tristar_bet.ui.main_window import status_text

        window = self.loaded_window()
        result = window.results[0]
        for language in ("en", "zh"):
            window.set_interface_language(language)
            window._toggle_analysis_table_orientation()
            cells = window._analysis_cells_for_result(result)
            for method, table in window.analysis_result_tables.items():
                status = status_text(cells[method]["status_code"][1])
                self.assertEqual(cells[method]["status"][0], status)
                file_item = table.horizontalHeaderItem(1) if table._transposed else table.item(0, 0)
                self.assertEqual(file_item.toolTip(), result.header.file_path + "\n" + status)

    def test_language_switch_preserves_sample_names_matching_ui_labels(self):
        from test_detail_result_tables import sample
        from tristar_bet.ui.main_window import FILE_COLUMN

        names = ["默认", "Default", "吸附"]
        window = self.make_window()
        window.results = [sample(name, index + 1) for index, name in enumerate(names)]
        window.visible_results = [True] * len(names)
        window.sample_colors = ["#123456", "#234567", "#345678"]
        window.active_index = 0
        window.refresh_all()
        for language in ("en", "zh"):
            window.statusBar().showMessage(i18n.tr("已切换到样品：{0}", names[0]))
            window.set_interface_language(language)
            self.assertEqual(window.statusBar().currentMessage(), i18n.tr("已切换到样品：{0}", names[0]))
            self.assertEqual([window.sample_table.item(row, FILE_COLUMN).text() for row in range(len(names))], names)
            for _ in range(2):
                for table in window.analysis_result_tables.values():
                    shown = [table.horizontalHeaderItem(row + 1).text() if table._transposed else table.item(row, 0).text()
                             for row in range(len(names))]
                    self.assertEqual(shown, names)
                window._toggle_analysis_table_orientation()
            self.assertEqual([result.file_name for result in window.results], [name + ".SMP" for name in names])

    def test_window_built_in_english_matches_a_switched_window(self):
        switched = self.loaded_window()
        switched.set_interface_language("en")
        built = self.loaded_window()
        self.assertEqual(self.chinese_left(built), [])
        self.assertEqual(built.sample_table.horizontalHeaderItem(1).text(),
                         switched.sample_table.horizontalHeaderItem(1).text())

    def test_settings_dialog_offers_language_first_and_switches_immediately(self):
        from tristar_bet.ui.app_settings import AppSettings, AppSettingsDialog

        window = self.make_window()
        current = AppSettings("xlsx", Path(self.temp.name), "default", True, 0.2)
        dialog = AppSettingsDialog(window, current, {"default": "默认"}, "9.9.9", None,
                                   language_changed=window.set_interface_language)
        first_group = dialog.findChildren(QtWidgets.QGroupBox)[0]
        self.assertIn("Language", first_group.title())
        self.assertEqual([dialog.language_combo.itemText(i) for i in range(dialog.language_combo.count())],
                         ["简体中文", "English"])
        def dialog_texts():
            texts = [dialog.windowTitle()]
            for widget in dialog.findChildren(QtWidgets.QWidget):
                texts.append(widget.toolTip())
                if isinstance(widget, (QtWidgets.QLabel, QtWidgets.QAbstractButton)):
                    texts.append(widget.text())
                if isinstance(widget, QtWidgets.QGroupBox):
                    texts.append(widget.title())
                if isinstance(widget, QtWidgets.QComboBox) and widget is not dialog.language_combo:
                    texts += [widget.itemText(i) for i in range(widget.count())]
            return sorted(text for text in texts if text)

        chinese = dialog_texts()
        # The dialog is a child of the main window: repeated switches in one open
        # dialog must keep translating it (it was once switched only the first time).
        for _ in range(2):
            dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("en"))
            self.assertEqual(i18n.current_language(), "en")
            self.assertEqual([text for text in dialog_texts() if CJK.search(text)], [])
            dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("zh"))
            self.assertEqual(dialog_texts(), chinese)
        dialog.deleteLater()

    def test_english_export_has_no_chinese_labels(self):
        from openpyxl import load_workbook

        from tristar_bet.ui.main_window import export_results_xlsx

        window = self.loaded_window()
        window.set_interface_language("en")
        path = Path(self.temp.name) / "export.xlsx"
        export_results_xlsx(window.results, path)
        names = {result.sample_name for result in window.results} | {result.file_name for result in window.results}
        workbook = load_workbook(io.BytesIO(path.read_bytes()))
        left = []
        for sheet in workbook.worksheets:
            if CJK.search(sheet.title):
                left.append(sheet.title)
            for row in sheet.iter_rows(values_only=True):
                for value in row:
                    if isinstance(value, str) and CJK.search(value) and not any(n and n in value for n in names):
                        left.append(value)
        workbook.close()
        self.assertEqual(sorted(set(left)), [])


if __name__ == "__main__":
    unittest.main()
