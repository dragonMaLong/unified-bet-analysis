import json
from io import BytesIO
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
import xml.etree.ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from tristar_bet import load_file
from tristar_bet.analysis import bet_analysis, single_point_bet_analysis, surface_area_factor_m2_per_cm3
from tristar_bet.ui.main_window import MainWindow, active_metric_rows, export_results_xlsx


class NativeSinglePointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixtures = json.loads((Path(__file__).parent / 'fixtures/jwgb_bk200c_auto_q.json').read_text(encoding='utf-8'))

    def tearDown(self):
        self.temp.cleanup()

    def load(self, index=0, pressure='0.200000'):
        root = ET.fromstring(self.fixtures[index]['xml'])
        report = ET.SubElement(root.find('Config'), 'SingleBETReport')
        ET.SubElement(report, 'Pressure').text = pressure
        path = Path(self.temp.name) / f'{index}.jwgbt'
        path.write_bytes(ET.tostring(root, encoding='utf-8'))
        return load_file(path)

    def test_native_target_and_independent_interpolation(self):
        # Pins updated when production stopped applying the dose non-ideality
        # term (BK200C 1(1).xlsx same-run match); previously .8338506/2.3412590.
        for index, expected in enumerate([0.8261342126381043, 2.324081613483056]):
            result = self.load(index)
            self.assertEqual(result.method_options['jwgb_single_point_bet_pressure'], .2)
            self.assertIsNone(bet_analysis(result).surface_area_m2_g)
            fit = single_point_bet_analysis(result)
            points = {p.index: p for p in result.isotherm if p.phase == 'adsorption'}
            lo, hi = points[3], points[4]
            quantity = lo.quantity_adsorbed_cm3_g_stp + (.2-lo.relative_pressure)/(hi.relative_pressure-lo.relative_pressure)*(hi.quantity_adsorbed_cm3_g_stp-lo.quantity_adsorbed_cm3_g_stp)
            self.assertAlmostEqual(fit.surface_area_m2_g, quantity*.8*surface_area_factor_m2_per_cm3(result), places=12)
            self.assertAlmostEqual(fit.surface_area_m2_g, expected, places=10)
            self.assertEqual(result.method_options['quantity_status'], 'reconstructed_unvalidated')

    def test_no_interpolation_across_invalid_measured_neighbours(self):
        result = self.load()
        for value in (None, -1., 0., float('nan')):
            broken = replace(result, isotherm=[replace(p, quantity_adsorbed_cm3_g_stp=value)
                             if p.phase == 'adsorption' and p.index == 4 else p for p in result.isotherm])
            self.assertIsNone(single_point_bet_analysis(broken).surface_area_m2_g)
            exact = result.isotherm[3].relative_pressure
            self.assertIsNone(single_point_bet_analysis(broken, exact).surface_area_m2_g)

    def test_bad_optional_target_does_not_break_import_or_guess_target(self):
        for pressure in ('NaN', 'bad', '0', '1', '-.2'):
            result = self.load(pressure=pressure)
            self.assertNotIn('jwgb_single_point_bet_pressure', result.method_options)
            self.assertIsNone(single_point_bet_analysis(result).surface_area_m2_g)
            self.assertEqual(result.point_count, 37)

    def test_display_fallback_is_single_and_does_not_replace_custom_fit(self):
        result = self.load()
        window = Mock()
        window._is_custom_bet_fit.return_value = False
        fit = bet_analysis(result)
        value, kind, tooltip = MainWindow._sample_bet_display(window, result, fit)
        self.assertAlmostEqual(value, .8261342126381043)
        self.assertEqual(kind, 'single_point')
        self.assertIn('单点 BET', tooltip)
        window._is_custom_bet_fit.return_value = True
        self.assertIsNone(MainWindow._sample_bet_display(window, result, fit)[0])
        window._is_custom_bet_fit.return_value = False
        valid = replace(fit, surface_area_m2_g=5., status='ok')
        self.assertEqual(MainWindow._sample_bet_display(window, result, valid)[:2], (5., 'multipoint'))

    def test_details_and_export_keep_single_and_multi_separate(self):
        from openpyxl import load_workbook
        result = self.load()
        rows = dict(active_metric_rows(result))
        self.assertAlmostEqual(float(rows['JWGB 单点 BET 比表面积'].split()[0]), .8261342126381043, places=6)
        path = Path(self.temp.name) / 'export.xlsx'
        export_results_xlsx([result], path)
        workbook = load_workbook(BytesIO(path.read_bytes()))
        sheet = workbook.worksheets[0]
        headers = [cell.value for cell in sheet[1]]
        values = [cell.value for cell in sheet[2]]
        single = [i for i, h in enumerate(headers) if '单点' in str(h) and '面积' in str(h)]
        self.assertEqual(len(single), 1)
        self.assertAlmostEqual(values[single[0]], .8261342126381043)
        workbook.close()
