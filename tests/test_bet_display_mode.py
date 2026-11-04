import json
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import QtCore, QtWidgets

from tristar_bet import load_file
from tristar_bet.analysis import single_point_bet_analysis
from tristar_bet.ui.main_window import BET_COLUMN, MainWindow, export_results_xlsx
from test_detail_result_tables import sample

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/jwgb_bk200c_auto_q.json').read_text(encoding='utf-8'))[0]


class BetDisplayModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QtCore.QSettings(str(Path(self.temp.name) / 's.ini'), QtCore.QSettings.IniFormat)
        self.w = self.make_window()
        # JWGB run 3: multipoint invalid, file stores single-point P/P0 = 0.2
        root = ET.fromstring(FIXTURE['xml'])
        ET.SubElement(ET.SubElement(root.find('Config'), 'SingleBETReport'), 'Pressure').text = '0.200000'
        path = Path(self.temp.name) / 'run3.jwgbt'
        path.write_bytes(ET.tostring(root, encoding='utf-8'))
        self.jwgb = load_file(path)
        self.smp = sample('A', 1)  # Micromeritics-like: valid multipoint, no stored single-point target
        self.w.results = [self.smp, self.jwgb]
        self.w.visible_results = [True, True]
        self.w.active_index = 0
        self.w.refresh_all()

    def make_window(self):
        with patch.object(MainWindow, '_auto_check_for_updates'), patch.object(MainWindow, '_initialize_telemetry'), \
                patch('tristar_bet.ui.main_window.QtCore.QSettings', return_value=self.settings):
            w = MainWindow()
        w.refresh_isotherm_plot = Mock()
        w.refresh_analysis_plots = Mock()
        return w

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.temp.cleanup()

    def cell(self, row):
        item = self.w.sample_table.item(row, BET_COLUMN)
        return item.text(), item.toolTip()

    def header(self):
        return self.w.sample_table.horizontalHeaderItem(BET_COLUMN).text()

    def test_default_keeps_existing_rule(self):
        multi = self.w._bet_analysis_for_result(self.smp).surface_area_m2_g
        self.assertTrue(self.cell(0)[0])
        self.assertAlmostEqual(float(self.cell(0)[0]), multi, places=3)
        self.assertTrue(self.cell(1)[0].endswith('（单点）'))
        self.assertEqual(self.header(), 'BET(m2/g)')

    def test_single_mode_uses_file_target_else_software_pressure(self):
        self.w.set_bet_display_mode('single')
        self.assertEqual(self.header(), '单点BET(m2/g)')
        text, tip = self.cell(0)
        self.assertAlmostEqual(float(text), single_point_bet_analysis(self.smp, 0.2).surface_area_m2_g, places=3)
        self.assertIn('软件设置', tip)
        text, tip = self.cell(1)
        self.assertNotIn('（单点）', text)
        self.assertAlmostEqual(float(text), single_point_bet_analysis(self.jwgb).surface_area_m2_g, places=4)
        self.assertIn('文件设置', tip)

    def test_menu_has_only_three_modes_and_pressure_is_fixed(self):
        header = self.w.sample_table.horizontalHeader()
        position = QtCore.QPoint(header.sectionViewportPosition(BET_COLUMN) + 5, header.height() // 2)
        seen = []

        def capture(menu, *_args):
            seen.extend(a.text() for a in menu.actions() if not a.isSeparator())
            return None
        with patch.object(QtWidgets.QMenu, 'exec_', capture):
            self.w.show_sample_header_context_menu(position)
        self.assertEqual(seen, ['默认', '单点', '多点'])
        self.settings.setValue('results/single_point_pressure', 0.35)  # old saved value is ignored
        other = self.make_window()
        try:
            self.assertEqual(other.single_point_default_pressure, 0.2)
        finally:
            other.close()
            other.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_multi_mode_leaves_invalid_multipoint_blank(self):
        self.w.set_bet_display_mode('multi')
        self.assertEqual(self.header(), '多点BET(m2/g)')
        self.assertTrue(self.cell(0)[0])
        text, tip = self.cell(1)
        self.assertEqual(text, '')
        self.assertIn('多点 BET 不可用', tip)

    def test_sorting_follows_mode(self):
        self.w.set_bet_display_mode('multi')
        self.w.sort_samples_by_bet(True)
        self.assertIs(self.w.results[-1], self.jwgb)  # unavailable values go last

    def test_right_click_menu_switches_and_mode_persists(self):
        header = self.w.sample_table.horizontalHeader()
        position = QtCore.QPoint(header.sectionViewportPosition(BET_COLUMN) + 5, header.height() // 2)

        def choose(label):
            def fake_exec(menu, *_args):
                return next(a for a in menu.actions() if a.text() == label)
            return fake_exec
        with patch.object(QtWidgets.QMenu, 'exec_', choose('单点')):
            self.w.show_sample_header_context_menu(position)
        self.assertEqual(self.w.bet_display_mode, 'single')
        other = self.make_window()
        try:
            self.assertEqual(other.bet_display_mode, 'single')
        finally:
            other.close()
            other.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        with patch.object(QtWidgets.QMenu, 'exec_', choose('默认')):
            self.w.show_sample_header_context_menu(position)
        self.assertEqual(self.w.bet_display_mode, 'default')

    def test_menu_ignores_other_columns(self):
        header = self.w.sample_table.horizontalHeader()
        position = QtCore.QPoint(header.sectionViewportPosition(BET_COLUMN + 1) + 5, header.height() // 2)
        with patch.object(QtWidgets.QMenu, 'exec_', side_effect=AssertionError('menu shown')):
            self.w.show_sample_header_context_menu(position)

    def test_export_single_point_columns_cover_all_instruments(self):
        from openpyxl import load_workbook
        path = Path(self.temp.name) / 'out.xlsx'
        export_results_xlsx([self.smp, self.jwgb], path, single_point_pressure=0.2)
        sheet = load_workbook(BytesIO(path.read_bytes()), data_only=True)['摘要']
        headers = [c.value for c in sheet[1]]
        rows = [[c.value for c in r] for r in sheet.iter_rows(min_row=2)]
        area, source = headers.index('单点BET面积(m2/g)'), headers.index('单点压力来源')
        self.assertAlmostEqual(rows[0][area], single_point_bet_analysis(self.smp, 0.2).surface_area_m2_g)
        self.assertEqual((rows[0][source], rows[1][source]), ('软件设置', '文件设置'))


if __name__ == '__main__':
    unittest.main()
