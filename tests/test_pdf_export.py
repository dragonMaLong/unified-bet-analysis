import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import QtCore, QtWidgets

from tristar_bet.ui.main_window import MainWindow
from tristar_bet.ui.pdf_report import Figure, Series, render_figure
from test_detail_result_tables import sample
from test_pore_distribution_export import with_desorption


class PdfExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = QtCore.QSettings(str(Path(self.temp.name) / 's.ini'), QtCore.QSettings.IniFormat)
        with patch.object(MainWindow, '_auto_check_for_updates'), patch.object(MainWindow, '_initialize_telemetry'), \
                patch('tristar_bet.ui.main_window.QtCore.QSettings', return_value=settings):
            self.w = MainWindow()
        self.w.refresh_isotherm_plot = Mock()
        self.w.refresh_analysis_plots = Mock()
        self.samples = [with_desorption(sample('A', 1)), with_desorption(sample('B', 2)), sample('C', 3)]
        self.w.results = list(self.samples)
        self.w.visible_results = [True, True, False]
        self.w.active_index = 0
        self.w.refresh_all()
        self.w.export_directory = Path(self.temp.name)

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.temp.cleanup()

    def dialog(self, name, chosen_filter):
        path = str(Path(self.temp.name) / name)
        return patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(path, chosen_filter))

    def test_report_content_has_tables_and_overlay_figures_without_point_tables(self):
        report = self.w._pdf_report_for(self.samples[:2])
        self.assertEqual([t.title for t in report.tables], ['样品信息', '结果参数', '选区孔容量'])
        pore = report.tables[2]
        self.assertEqual([label.split()[0] for label in pore.row_labels], ['BJH', 'DH', 'HK', 'DFT'])
        self.assertEqual(len(pore.columns[0][1]), 4)
        self.assertNotIn('孔容', ' '.join(report.tables[1].row_labels))  # moved to its own table
        expected_params = ['等温线参数', 'BET 参数', 'Langmuir 参数', 't-Plot 参数', 'BJH 参数', 'DH 参数', 'HK 参数', 'DFT 参数']
        self.assertEqual([f.params.title for f in report.figures], expected_params)
        for figure in report.figures:
            self.assertEqual([label for label, _ in figure.params.columns], ['A', 'B'])
            self.assertTrue(all(len(values) == len(figure.params.row_labels) for _l, values in figure.params.columns))
        bet_params = report.figures[1].params
        c_row = bet_params.row_labels.index('C')
        self.assertAlmostEqual(float(bet_params.columns[0][1][c_row]),
                               self.w._bet_analysis_for_result(self.samples[0]).c_constant, places=3)
        results = report.tables[1]
        self.assertEqual([label for label, _ in results.columns], ['A', 'B'])
        bet_area = results.row_labels.index('BET 比表面积 (m2/g)')
        expected = self.w._bet_analysis_for_result(self.samples[0]).surface_area_m2_g
        self.assertAlmostEqual(float(results.columns[0][1][bet_area]), expected, places=3)
        isotherm = report.figures[0]
        self.assertEqual(isotherm.title, '吸附-脱附等温线')
        self.assertEqual({s.label for s in isotherm.series}, {'A', 'A（脱附）', 'B', 'B（脱附）'})
        bet = next(f for f in report.figures if f.title == 'BET 图')
        self.assertEqual(sum(s.style == 'fit' for s in bet.series), 2)

    def test_toolbar_export_offers_both_types_and_writes_pdf(self):
        with self.dialog('report.pdf', 'PDF 报告 (*.pdf)') as dialog:
            self.w.export_xlsx()
        filters = dialog.call_args[0][3]
        self.assertIn('Excel 工作簿 (*.xlsx)', filters)
        self.assertIn('PDF 报告 (*.pdf)', filters)
        data = (Path(self.temp.name) / 'report.pdf').read_bytes()
        self.assertTrue(data.startswith(b'%PDF'))
        self.assertGreater(len(data), 20000)  # embedded figures

    def test_missing_suffix_follows_chosen_filter(self):
        with self.dialog('noext', 'PDF 报告 (*.pdf)'):
            self.w.export_xlsx()
        self.assertTrue((Path(self.temp.name) / 'noext.pdf').exists())
        with self.dialog('noext2', 'Excel 工作簿 (*.xlsx)'):
            self.w.export_xlsx()
        self.assertTrue((Path(self.temp.name) / 'noext2.xlsx').exists())

    def test_row_context_menu_exports_only_selected_samples(self):
        rect = self.w.sample_table.visualItemRect(self.w.sample_table.item(1, 1))
        for label, fmt in (('导出 Excel…', 'xlsx'), ('导出 PDF…', 'pdf')):
            def choose(menu, *_args, label=label):
                return next(a for a in menu.actions() if a.text() == label)
            with patch.object(QtWidgets.QMenu, 'exec_', choose), patch.object(self.w, '_export_results') as export:
                self.w.show_sample_context_menu(rect.center())
            export.assert_called_once_with([self.samples[1]], fmt)

    def test_row_menu_dialog_offers_only_that_type(self):
        with self.dialog('one.pdf', 'PDF 报告 (*.pdf)') as dialog:
            self.w._export_results([self.samples[2]], 'pdf')
        self.assertEqual(dialog.call_args[0][3], 'PDF 报告 (*.pdf)')
        self.assertTrue(dialog.call_args[0][2].endswith('C.pdf'))
        self.assertTrue((Path(self.temp.name) / 'one.pdf').exists())

    def test_empty_figure_is_skipped(self):
        self.assertIsNone(render_figure(Figure('x', 'a', 'b', [Series('s', '#000000', [], [])])))
        image = render_figure(Figure('x', 'a', 'b', [Series('s', '#000000', [1, 2], [3, 4])]))
        self.assertEqual((image.width(), image.height()), (1100, 620))



class BrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_logo_asset_and_website_are_used(self):
        from PyQt5 import QtGui
        from tristar_bet.ui import pdf_report
        self.assertEqual(pdf_report.WEBSITE, 'www.dragonscience.top')
        self.assertTrue(pdf_report.LOGO_PATH.exists(), pdf_report.LOGO_PATH)
        self.assertFalse(QtGui.QImage(str(pdf_report.LOGO_PATH)).isNull())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'r.pdf'
            report = pdf_report.Report('BET 分析报告', 'x', [], [])
            pdf_report.write_pdf(report, path)
            data = path.read_bytes()
        self.assertGreaterEqual(data.count(b'/Subtype /Image'), 1)  # header logo embedded


if __name__ == '__main__':
    unittest.main()
