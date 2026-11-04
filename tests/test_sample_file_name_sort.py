import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtTest import QTest

from tristar_bet.ui.main_window import FILE_COLUMN, MainWindow, _display_file_name, _natural_sort_key

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/jwgb_bk200c_auto_q.json').read_text(encoding='utf-8'))[0]
NAMES = ['10 [2_S2] run', '2 [2_S1] run', 'B sample', '1 [2_S1] run', 'a sample', '20 [2_S1] run']
ASCENDING = ['1 [2_S1] run', '2 [2_S1] run', '10 [2_S2] run', '20 [2_S1] run', 'a sample', 'B sample']


class NaturalKeyTests(unittest.TestCase):
    def test_numbers_by_value_and_letters_case_insensitive(self):
        self.assertEqual(sorted(NAMES, key=_natural_sort_key), ASCENDING)
        self.assertEqual(sorted(['x10', 'x9', 'X1'], key=_natural_sort_key), ['X1', 'x9', 'x10'])
        self.assertEqual(sorted(['样品2', '样品11', '样品1'], key=_natural_sort_key), ['样品1', '样品2', '样品11'])


class HeaderClickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = []
        for name in NAMES:
            path = Path(self.temp.name) / f'{name}.jwgbt'
            path.write_text(FIXTURE['xml'], encoding='utf-8')
            self.paths.append(str(path))
        settings = QtCore.QSettings(str(Path(self.temp.name) / 'settings.ini'), QtCore.QSettings.IniFormat)
        with patch.object(MainWindow, '_auto_check_for_updates'), patch.object(MainWindow, '_initialize_telemetry'), \
                patch('tristar_bet.ui.main_window.QtCore.QSettings', return_value=settings):
            self.window = MainWindow()
        self.window.load_files(self.paths, replace=True)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.temp.cleanup()

    def names(self):
        return [_display_file_name(r) for r in self.window.results]

    def click_file_header(self):
        header = self.window.sample_table.frozen_header()
        x = header.sectionViewportPosition(FILE_COLUMN) + header.sectionSize(FILE_COLUMN) // 2
        QTest.mouseClick(header.viewport(), QtCore.Qt.LeftButton, pos=QtCore.QPoint(x, header.height() // 2))

    def test_clicking_file_name_header_sorts_then_reverses(self):
        self.assertEqual(len(self.window.results), len(NAMES))
        active = self.window.results[self.window.active_index]
        self.click_file_header()
        self.assertEqual(self.names(), ASCENDING)
        table = [self.window.sample_table.item(r, FILE_COLUMN).text() for r in range(len(NAMES))]
        self.assertEqual(table, ASCENDING)
        self.click_file_header()
        self.assertEqual(self.names(), ASCENDING[::-1])
        self.click_file_header()
        self.assertEqual(self.names(), ASCENDING)
        self.assertIs(self.window.results[self.window.active_index], active)

    def test_analysis_tables_keep_file_header_non_sorting(self):
        # Deliberate existing behaviour (see test_detail_result_tables): only the sample list sorts by name.
        if self.window.linked_result_tables['bet']._transposed:
            self.window._toggle_analysis_table_orientation()
        before = self.names()
        column = self.window.linked_result_tables['bet']._column_keys.index('file')
        self.window._on_analysis_header_clicked('bet', column)
        self.assertEqual(self.names(), before)


if __name__ == '__main__':
    unittest.main()
