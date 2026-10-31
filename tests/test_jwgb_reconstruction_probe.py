import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.jwgb_reconstruction_probe import STP_RATIO, TEMPERATURE_MODELS, probe, volume_terms
from tristar_bet.jwgb_native import load_jwgb_native


def sample_xml():
    # An exactly reversible synthetic cycle at constant temperature: the
    # reference pressure is 10 kPa. Doses 30 -> 20 and 40 -> 30 are reversed
    # by desorption 10 -> 20 and 0 -> 10, returning to the reference state.
    def row(i, pd, pcd, minute):
        pd = '-0.00001' if pd == -0.00001 else str(pd)
        return (f'<Item ID="{i}" Pd="{pd}" Pcd="{pcd}" P0="100" '
                f'PdT="26.85" PcdT="26.85" Balance="1" SubIndex="0" '
                f'Day="2026-10-12" Time="11:{minute:02}:00"/>')
    return ('<doc Ver="1"><General Ver="1"><GeneralConfig><AdsorbGas>N2</AdsorbGas>'
            '<AdsorbTemp>77.35</AdsorbTemp><IsRealGas>0</IsRealGas><IsInsitu>0</IsInsitu>'
            '<CrossSectionalArea>0.162</CrossSectionalArea></GeneralConfig></General>'
            '<StationConfig Ver="1"><Station><Model>BK200C</Model></Station></StationConfig>'
            '<Config Ver="1"><Base><PressUnits>0</PressUnits></Base>'
            '<SampleInfo><SampleWeight>0.1</SampleWeight></SampleInfo>'
            '<Vd>30</Vd><QValue>0.001</QValue><AutoTestQ>0</AutoTestQ><FastTestQ>0</FastTestQ>'
            '<IsMicroproe>0</IsMicroproe><MultiBETReport><MinRange>0.08</MinRange>'
            '<MaxRange>0.3</MaxRange></MultiBETReport></Config>'
            '<Data Ver="1"><BaseInfo><StartTime>2026-10-12 11:00:00</StartTime>'
            '<EndTime>2026-10-12 12:00:00</EndTime></BaseInfo><Adsorb>'
            + row(0, -0.00001, 10, 1) + row(1, 30, 20, 10) + row(2, 40, 30, 20)
            + '</Adsorb><Doff>' + row(1, -0.00001, 30, 21) + row(2, 10, 20, 30)
            + row(3, 0, 10, 40) + '</Doff></Data></doc>')


class ReconstructionProbeTests(unittest.TestCase):
    def test_empty_chamber_conservation_for_every_temperature_model(self):
        # Equal STP capacities in manifold and sample chamber, no adsorbent:
        # a 20 kPa charge equilibrates to 10 kPa; all gas remains free.
        for model in TEMPERATURE_MODELS:
            manifold, free = volume_terms(20, 10, 0, 26.85, 26.85, 30, STP_RATIO / 300, model)
            self.assertAlmostEqual(manifold - free, 0.0)

    def test_reversible_cycle_and_no_production_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cycle.jwgbt'
            path.write_text(sample_xml(), encoding='utf-8')
            report = probe(path)
            self.assertEqual(report['status'], 'unvalidated_research_only')
            self.assertEqual(report['source_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(len(report['candidates']), 4)
            for candidate in report['candidates']:
                values = [row['candidate_uptake_cm3_g_stp'] for row in candidate['rows']]
                self.assertEqual(len(values), 4)
                self.assertGreater(values[1], values[0])
                self.assertAlmostEqual(values[2], values[0])
                self.assertAlmostEqual(values[3], 0.0)
            self.assertTrue(all(p.quantity_adsorbed_cm3_g_stp is None for p in load_jwgb_native(path).isotherm))

    def test_rejects_uncovered_calibration_and_branch_gap(self):
        changes = [('<AutoTestQ>0', '<AutoTestQ>1'), ('<IsRealGas>0', '<IsRealGas>1'),
                   ('SubIndex="0"', 'SubIndex="1"'), ('PcdT="26.85"', 'PcdT="NaN"'),
                   ('Pd="-0.00001" Pcd="30"', 'Pd="-0.00001" Pcd="31"')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unsupported.jwgbt'
            for old, new in changes:
                with self.subTest(field=old):
                    path.write_text(sample_xml().replace(old, new), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        probe(path)

    def test_separate_temperature_gas_balance(self):
        # Constant amount of gas: pressure rises in proportion to temperature.
        manifold, free = volume_terms(30, 31, 31, 26.85, 36.85, 30, 0.001, 'separate')
        self.assertAlmostEqual(manifold - free, 0.0)
        with self.assertRaises(ValueError):
            volume_terms(30, 31, 31, -273.15, 36.85, 30, 0.001, 'separate')

    def test_cross_model_rule_remains_a_separate_unvalidated_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'transfer.jwgbt'
            path.write_text(sample_xml().replace('</GeneralConfig>',
                            '<NonIdealityFactor>0.00049467</NonIdealityFactor></GeneralConfig>'), encoding='utf-8')
            report = probe(path)
            transfer = report['sync440_transfer']
            self.assertEqual(len(report['candidates']), 4)
            self.assertFalse(transfer['quantitative_validation_passed'])
            self.assertFalse(transfer['same_run_reference_available'])
            self.assertEqual(transfer['raw_balance']['q_used'], .001)
            self.assertEqual(transfer['raw_balance']['dose_count'], 4)
            self.assertEqual(transfer['closure_hypothesis']['status'], 'unsupported')
            self.assertIn('extrapolation', transfer['closure_hypothesis']['reason'])
            self.assertTrue(all(p.quantity_adsorbed_cm3_g_stp is None for p in load_jwgb_native(path).isotherm))


if __name__ == '__main__':
    unittest.main()
