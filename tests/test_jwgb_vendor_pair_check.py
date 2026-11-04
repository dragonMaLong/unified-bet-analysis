"""Mechanics of the end-to-end vendor pair check (synthetic export, no vendor truth)."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from scripts.jwgb_bk200c_vendor_pair_check import check
from tristar_bet.jwgb_native import load_jwgb_native

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/jwgb_bk200c_auto_q.json').read_text(encoding='utf-8'))[0]


class VendorPairCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.native = Path(self.temp.name) / 'run.jwgbt'
        self.native.write_text(FIXTURE['xml'], encoding='utf-8')
        self.result = load_jwgb_native(self.native)

    def tearDown(self):
        self.temp.cleanup()

    def write_export(self, bump=0.0, drop=False):
        from openpyxl import Workbook
        start = datetime.fromisoformat(self.result.method_options['test_started_time'])
        wb = Workbook()
        ws = wb.active
        ws.title = 'Isotherm'
        ws.append(['ID', 'p (kPa)', 'p/p0', 'V (cm³/g STP)', 'p0 (kPa)', 'Time'])

        def row(i, p, extra=0.0):
            kpa = p.absolute_pressure_mmHg * 101.325 / 760
            when = (start + timedelta(seconds=p.elapsed_seconds)).strftime('%H:%M:%S')
            ws.append([i, round(kpa, 8), round(p.relative_pressure, 8),
                       round(p.quantity_adsorbed_cm3_g_stp + extra, 5),
                       round(p.saturation_pressure_mmHg * 101.325 / 760, 3), when])
        ads = [p for p in self.result.isotherm if p.phase == 'adsorption']
        des = [p for p in self.result.isotherm if p.phase == 'desorption']
        for i, p in enumerate(ads):
            row(i, p, bump if i == 3 else 0.0)
        ws.append(['---'] * 6)
        row(1, ads[-1])
        for i, p in enumerate(des[:-1] if drop else des, start=2):
            row(i, p)
        path = Path(self.temp.name) / 'export.xlsx'
        wb.save(path)
        return path

    def test_self_consistent_export_matches_and_perturbation_is_detected(self):
        self.assertTrue(check(self.native, self.write_export())['all_points_match'])
        report = check(self.native, self.write_export(bump=0.0001))
        self.assertFalse(report['all_points_match'])
        self.assertEqual(report['summary']['adsorption']['matching'], report['summary']['adsorption']['points'] - 1)

    def test_unpaired_export_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'imported vs'):
            check(self.native, self.write_export(drop=True))


if __name__ == '__main__':
    unittest.main()
