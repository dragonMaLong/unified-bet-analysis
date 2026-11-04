import json
from pathlib import Path
import tempfile
import unittest

from scripts.jwgb_zone_bounds_probe import audit, zone_bounds
from tristar_bet.jwgb_reconstruction import STP_RATIO


class ZoneBoundsTests(unittest.TestCase):
    def test_physical_two_volume_balance_recovers_known_uptake_with_temperature_changes(self):
        # Independent physical volumes, not fitted to either customer file.
        warm_volume, cold_volume, vd, mass = 2.0, .3, 30.0, .1
        tcal, tcold, c = 300.0, 77.35, .00049467
        q = STP_RATIO * (warm_volume / tcal + cold_volume / tcold) / vd
        w = (warm_volume / tcal) / (warm_volume / tcal + cold_volume / tcold)

        def gas(p, t):
            return STP_RATIO * p * (warm_volume / t + cold_volume / tcold * (1 + c * p))

        for adsorption in ([0, 0, 0, 0], [.01, .03, .05, .02]):
            records = [{'phase': 'adsorption', 'ID': '0', 'Pd': -.00001,
                        'Pcd': .1, 'PcdT': 26.85}]
            prior_gas, prior_ads = gas(.1, tcal), 0
            for i, (p, t, ads) in enumerate(zip([2, 5, 10, 5], [302, 298, 305, 303], adsorption), 1):
                phase = 'adsorption' if i < 4 else 'desorption'
                if i == 4:
                    records.append({'phase': phase, 'ID': '0', 'Pd': -.00001,
                                    'Pcd': 10, 'PcdT': 305 - 273.15})
                dose = gas(p, t) - prior_gas + ads - prior_ads
                td = t + .2
                pd = td * (dose / (STP_RATIO * vd) + p / t)
                records.append({'phase': phase, 'ID': str(i), 'Pd': pd, 'Pcd': p,
                                'PdT': td - 273.15, 'PcdT': t - 273.15,
                                'P0': 100, 'Balance': '1'})
                prior_gas, prior_ads = gas(p, t), ads
            rows = zone_bounds(records, q=q, vd=vd, mass=mass,
                               calibration_temperature_k=tcal, coefficient=c)
            for row, expected in zip(rows, adsorption):
                value = (1 - w) * row['all_cold_cm3_g'] + w * row['all_warm_cm3_g']
                self.assertAlmostEqual(value, expected / mass, places=12)
                self.assertLessEqual(row['minimum_cm3_g'] - 1e-12, value)
                self.assertGreaterEqual(row['maximum_cm3_g'] + 1e-12, value)

    def test_recorded_sample_has_negative_bound_even_at_best_warm_fraction(self):
        fixtures = json.loads((Path(__file__).parent / 'fixtures/jwgb_bk200c_auto_q.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as d:
            for i, fixture in enumerate(fixtures):
                path = Path(d) / f'{i}.jwgbt'
                path.write_text(fixture['xml'], encoding='utf-8')
                report = audit(path)
                self.assertFalse(report['quantitative_validation_passed'])
                self.assertFalse(report['same_run_vendor_uptake_available'])
                if i == 0:
                    self.assertEqual(report['negative_for_every_warm_fraction_ids'], ['2', '3'])
                    point2 = report['saved_range_rows'][0]
                    self.assertAlmostEqual(point2['minimum_cm3_g'], -.1043455, places=7)
                    self.assertAlmostEqual(point2['maximum_cm3_g'], -.0642939, places=7)
                else:
                    self.assertEqual(report['negative_for_every_warm_fraction_ids'], [])
                    # Absence of this contradiction does not provide a chosen split or area.
                    self.assertNotIn('bet_area', report)
                    self.assertNotIn('chosen_warm_fraction', report)

    def test_invalid_volume_parameters_are_rejected(self):
        for q, mass in [(0, .1), (.01, 0), (float('nan'), .1)]:
            with self.assertRaises(ValueError):
                zone_bounds([], q=q, vd=30, mass=mass, calibration_temperature_k=300, coefficient=.00049467)
