"""MOD005 regression data are extracted numeric inputs, not vendor binaries.

Runtime and these tests use NumPy only; neither MicroActive, a desktop Excel
file, nor SciPy is needed. Reference units are nm and cm3/g (per nm for dV/dW).
"""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from tristar_bet.analysis import DFT_REGULARIZATION_VALUES, dft_pore_distribution
from tristar_bet.dft_models import load_dft_model_kernel
from tristar_bet.halsey_dft import (
    akima_interpolate, log_grid_penalty, nonnegative_least_squares, prepare_halsey,
)


class HalseyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((Path(__file__).parent/'fixtures/halsey_microactive_reference.json').read_text(encoding='utf-8'))
        cls.kernel = load_dft_model_kernel('micromeritics_mod005')
        cls.pressure = np.array(cls.data['pressure'])
        cls.quantity = np.array(cls.data['quantity_stp'])

    def sample(self, phase='adsorption', pressure=None, quantity=None):
        p = self.pressure if pressure is None else pressure
        q = self.quantity if quantity is None else quantity
        return SimpleNamespace(adsorptive_properties=None, isotherm=[
            SimpleNamespace(index=i+1, phase=phase, relative_pressure=float(x),
                            quantity_adsorbed_cm3_g_stp=float(y), quantity_adsorbed_mmol_g=float(y/22.414))
            for i, (x, y) in enumerate(zip(p, q))])

    def test_production_entry_matches_all_three_official_curves(self):
        # Whole-curve relative L2 tolerances, not a guarantee for every point.
        for ref in self.data['reference']:
            with self.subTest(regularization=ref['regularization']):
                with patch('tristar_bet.analysis._dft_regularized_nonnegative_solution',
                           side_effect=AssertionError('old solver called')):
                    d = dft_pore_distribution(self.sample(), model='micromeritics_mod005',
                                             regularization=ref['regularization'], include_diagnostics=False)
                self.assertEqual(d.status, 'ok')
                self.assertEqual(len(d.rows), 79)
                self.assertEqual(len(d.fit_rows), 160)
                for row in d.rows:
                    self.assertAlmostEqual(row['differential_pore_volume_cm3_g'] * row['dlog_diameter'],
                                           row['incremental_pore_volume_cm3_g'], places=12)
                    self.assertAlmostEqual(row['dlog_diameter'],
                                           np.log10(row['pore_width_high_nm']/row['pore_width_low_nm']), places=12)
                np.testing.assert_allclose([r['pore_width_nm'] for r in d.rows], ref['width_nm'], rtol=1e-5)
                for field, key, tolerance in [
                    ('cumulative_pore_volume_cm3_g', 'cumulative', .0007),
                    ('differential_pore_volume_per_nm_cm3_g_nm', 'differential_per_nm', .002),
                ]:
                    actual = np.array([r[field] for r in d.rows])
                    expected = np.array(ref[key])
                    self.assertLess(np.linalg.norm(actual-expected)/np.linalg.norm(expected), tolerance)
                    self.assertTrue(np.all(actual >= 0))

    def test_threshold_guard_columns_and_background_are_not_reported(self):
        k = self.kernel
        self.assertAlmostEqual(k.filling_threshold, .022952017267142622)
        original = k.kernel.copy()
        p = prepare_halsey(k, self.pressure, self.quantity)
        self.assertEqual((p.report_low, p.report_high), (0, 78))
        np.testing.assert_array_equal(p.columns, np.r_[np.arange(81), 91])
        area = np.zeros(len(p.columns)); area[-1] = 100; area[-2] = 20
        self.assertTrue(np.any(p.matrix @ area > 0))
        rows = p.distribution(area, 10, 'adsorption')
        self.assertEqual(rows[-1]['cumulative_pore_volume_cm3_g'], 0)
        np.testing.assert_array_equal(k.kernel, original)

    def test_diagnostics_use_the_same_solution_and_residual_units(self):
        d = dft_pore_distribution(self.sample(), model='micromeritics_mod005', regularization=.316)
        self.assertEqual(d.status, 'ok')
        self.assertEqual(len(d.diagnostic_rows), len(DFT_REGULARIZATION_VALUES))
        p = prepare_halsey(self.kernel, self.pressure, self.quantity)
        for row in d.diagnostic_rows:
            rms, roughness = p.metrics(p.solve(row['regularization']))
            self.assertAlmostEqual(row['rms_error_mmol_g'], rms, places=10)
            self.assertAlmostEqual(row['distribution_roughness'], roughness, places=10)
        rms = [r['rms_error_mmol_g'] for r in d.diagnostic_rows]
        rough = [r['distribution_roughness'] for r in d.diagnostic_rows]
        self.assertTrue(np.all(np.diff(rms) >= -1e-8))
        self.assertTrue(np.all(np.diff(rough) <= 1e-8))

    def test_repeated_pressure_and_sample_scaling(self):
        a = prepare_halsey(self.kernel, self.pressure, self.quantity)
        b = prepare_halsey(self.kernel, self.pressure[:-1], self.quantity[:-1])
        np.testing.assert_allclose(a.target_stp, b.target_stp)
        c = prepare_halsey(self.kernel, self.pressure, self.quantity*2)
        np.testing.assert_allclose(c.solve(.316), 2*a.solve(.316), atol=1e-8, rtol=1e-8)

    def test_invalid_or_uncovered_input_has_no_plausible_fallback(self):
        for p in [np.full(6, .2), np.linspace(1e-8, 2e-8, 6)]:
            d = dft_pore_distribution(self.sample(pressure=p, quantity=np.ones(6)),
                                     model='micromeritics_mod005')
            self.assertNotEqual(d.status, 'ok')
            self.assertFalse(d.rows)
        with self.assertRaises(ValueError):
            prepare_halsey(replace(self.kernel, filling_threshold=None), self.pressure, self.quantity)
        with patch('tristar_bet.halsey_dft.HalseyProblem.solve', side_effect=RuntimeError('test')):
            d = dft_pore_distribution(self.sample(), model='micromeritics_mod005')
            self.assertEqual(d.status, 'solver_not_converged')

    def test_other_models_and_unverified_desorption_keep_their_existing_path(self):
        for model, phase in [('n2_dft_model', 'adsorption'),
                             ('n2_77_carbon_slit_qsdft_eq', 'adsorption'),
                             ('micromeritics_mod005', 'desorption')]:
            with self.subTest(model=model, phase=phase):
                with patch('tristar_bet.halsey_dft.prepare_halsey', side_effect=AssertionError('wrong profile')):
                    d = dft_pore_distribution(self.sample(phase), model=model, phase=phase,
                                             include_diagnostics=False)
                    self.assertEqual(d.status, 'ok')

    def test_akima_linear_and_constant_data_and_no_extrapolation(self):
        x = np.array([.01, .02, .1, .3, .9])
        query = np.linspace(.01, .9, 100)
        for y in [3*x+2, np.ones_like(x)*4]:
            expected = 3*query+2 if y[0] != 4 else np.ones_like(query)*4
            np.testing.assert_allclose(akima_interpolate(x, y, query), expected, atol=1e-14)
            np.testing.assert_allclose(akima_interpolate(x, y, x), y, atol=1e-14)
        with self.assertRaises(ValueError):
            akima_interpolate(x, x, np.array([1.]))

    def test_uniform_log_grid_reduces_to_second_difference(self):
        w = np.geomspace(1, 100, 12)
        np.testing.assert_allclose(log_grid_penalty(w), np.diff(np.eye(12), n=2, axis=0), atol=1e-13)

    def test_nnls_optimality_nonnegativity_and_rank_deficiency(self):
        rng = np.random.default_rng(37)
        for n in [3, 12, 25]:
            a = rng.normal(size=(n*2, n)); b = rng.normal(size=n*2)
            a[:, -1] = a[:, 0]  # singular passive sets must remain safe
            x = nonnegative_least_squares(a, b)
            gradient = a.T @ (a@x-b)
            self.assertTrue(np.all(x >= 0))
            self.assertGreaterEqual(gradient.min(), -1e-9)
            self.assertLess(np.max(np.abs(x*gradient)), 1e-8)


if __name__ == '__main__':
    unittest.main()
