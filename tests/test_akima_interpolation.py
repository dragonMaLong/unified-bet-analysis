"""The batched interpolation must preserve existing plots and calculations."""

import unittest

import numpy as np

from tristar_bet.analysis import (
    _akima_interpolate_array,
    _akima_interpolate_scalar,
    _unique_sorted_xy,
)


class AkimaInterpolationTests(unittest.TestCase):
    def test_batch_matches_scalar_for_uneven_knots_and_endpoints(self):
        rng = np.random.default_rng(2718)
        for count in (5, 15, 29, 91, 200):
            x = np.cumsum(rng.uniform(0.001, 2.0, count))
            for y in (rng.normal(size=count), np.ones(count), 3.0 * x + 2.0):
                targets = np.concatenate(([x[0] - 1.0], x, np.linspace(x[0], x[-1], 1600), [x[-1] + 1.0]))
                expected = np.asarray([_akima_interpolate_scalar(t, x, y) for t in targets])
                with self.subTest(count=count, flat=bool(np.all(y == y[0]))):
                    np.testing.assert_allclose(
                        _akima_interpolate_array(x, y, targets), expected, rtol=2e-13, atol=2e-13,
                    )

    def test_reversed_duplicate_knots_keep_existing_sorting_behavior(self):
        x = np.asarray([10.0, 5.0, 3.0, 3.0, 2.0, 1.0, 0.5])
        y = np.asarray([0.0, 1.0, 3.0, 2.0, 1.0, 0.5, 0.0])
        targets = np.asarray([11.0, 10.0, 7.0, 3.0, 1.5, 0.5, 0.0])
        xs, ys = _unique_sorted_xy(x, y)
        expected = np.asarray([_akima_interpolate_scalar(t, xs, ys) for t in targets])
        np.testing.assert_allclose(_akima_interpolate_array(x, y, targets), expected, atol=1e-14)

    def test_empty_targets_and_short_inputs(self):
        for count in range(1, 7):
            x = np.arange(count, dtype=float)
            y = x * x
            self.assertEqual(_akima_interpolate_array(x, y, np.asarray([])).size, 0)
            targets = np.asarray([-1.0, 0.25, 0.5, 3.5, 8.0])
            if count < 2:
                expected = np.zeros_like(targets)
            elif count < 5:
                expected = np.interp(targets, x, y)
            else:
                expected = np.asarray([_akima_interpolate_scalar(t, x, y) for t in targets])
            np.testing.assert_allclose(_akima_interpolate_array(x, y, targets), expected)


if __name__ == "__main__":
    unittest.main()
