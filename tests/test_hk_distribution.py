from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from tristar_bet.analysis import (
    horvath_kawazoe_pore_distribution,
    prepare_hk_distribution_rows,
)


PRESSURES_100_TO_02 = (
    0.00023458490644866355,
    0.0003631233441720008,
    0.000537271641685414,
    0.0008277860083939931,
    0.001053417047295697,
    0.0012603866429753083,
    0.001967189321279934,
    0.002886975296482361,
    0.005039454891733816,
    0.010410320484219329,
    0.04869247683592027,
    0.06212798689332037,
    0.09998046276082072,
    0.1493864201838003,
    0.19642835684747093,
)

VOLUMES_100_TO_02 = (
    0.000317678,
    0.00286669,
    0.010412,
    0.0179294,
    0.0211283,
    0.0227159,
    0.0278237,
    0.0310506,
    0.0346468,
    0.038762,
    0.0480901,
    0.0499607,
    0.0541516,
    0.0587952,
    0.0629488,
)


def _hk_rows(widths, volumes=VOLUMES_100_TO_02, pressures=PRESSURES_100_TO_02):
    result = []
    previous = None
    for pressure, width, volume in zip(pressures, widths, volumes):
        reversal = previous is not None and width < previous
        result.append(
            {
                "relative_pressure": float(pressure),
                "pore_width_nm": float(width),
                "pore_diameter_nm": float(width),
                "cumulative_pore_diameter_nm": float(width),
                "cumulative_pore_volume_cm3_g": float(volume),
                "differential_pore_volume_cm3_g": 0.0,
                "differential_pore_volume_per_nm_cm3_g_nm": 0.0,
                "hk_width_reversal": float(reversal),
            }
        )
        previous = width
    return result


class HkDistributionRegressionTests(unittest.TestCase):
    def test_original_hk_smooths_only_selected_pressure_rows(self):
        widths = (
            0.666688,
            0.691732,
            0.716426,
            0.746529,
            0.764913,
            0.779343,
            0.818669,
            0.85684,
            0.921847,
            1.02948,
            1.42232,
            1.52244,
            1.77997,
            2.09674,
            2.40163,
        )
        official = np.asarray(
            (
                0.0424539,
                0.234364,
                0.283438,
                0.208202,
                0.140399,
                0.123949,
                0.110086,
                0.0675754,
                0.045717,
                0.029881,
                0.0194464,
                0.017855,
                0.0152387,
                0.0144925,
                0.0127388,
            ),
            dtype=float,
        )
        rows = _hk_rows(widths)
        rows.append(
            {
                **rows[-1],
                "relative_pressure": 0.24344663447276008,
                "pore_width_nm": 2.72290,
                "pore_diameter_nm": 2.72290,
                "cumulative_pore_diameter_nm": 2.72290,
                "cumulative_pore_volume_cm3_g": 0.0670774,
            }
        )

        smoothed = prepare_hk_distribution_rows(rows, pressure_range=(0.0, 0.2), smooth=True)
        actual = np.asarray(
            [row["differential_pore_volume_per_nm_cm3_g_nm"] for row in smoothed],
            dtype=float,
        )

        self.assertEqual(len(smoothed), 15)
        self.assertLess(float(np.sqrt(np.mean((actual - official) ** 2))), 0.0006)
        self.assertAlmostEqual(float(actual.max()), 0.283438, delta=0.0006)

    def test_cheng_yang_turning_point_is_retained_and_stabilized(self):
        widths = (
            0.6665663977925937,
            0.6904372292443784,
            0.7109794186070594,
            0.7353231843651258,
            0.7502943288947861,
            0.7625678926403757,
            0.7937783336712041,
            0.8244733319806166,
            0.8762382830698947,
            0.9581442929803008,
            1.206569482099752,
            1.255180556669441,
            1.3496427169383063,
            1.3985479258471458,
            1.3633031372307571,
        )
        official = np.asarray(
            (
                0.00176789,
                0.300596,
                0.342982,
                0.263039,
                0.158979,
                0.145803,
                0.136825,
                0.0824515,
                0.0566983,
                0.044454,
                0.0364784,
                0.0391242,
                0.3512,
                0.542321,
                0.542321,
            ),
            dtype=float,
        )

        smoothed = prepare_hk_distribution_rows(_hk_rows(widths), pressure_range=(0.0, 0.2), smooth=True)
        actual = np.asarray(
            [row["differential_pore_volume_per_nm_cm3_g_nm"] for row in smoothed],
            dtype=float,
        )

        self.assertEqual(len(smoothed), 15)
        self.assertEqual(sum(row["hk_width_reversal"] for row in smoothed), 1.0)
        self.assertTrue(np.all(actual >= 0.0))
        self.assertLess(float(np.sqrt(np.mean((actual - official) ** 2))), 0.004)
        self.assertAlmostEqual(actual[-1], actual[-2], places=12)

    def test_hk_analysis_does_not_drop_a_reversing_width(self):
        shell_widths = iter((0.70, 0.80, 0.90, 0.85))
        points = [
            SimpleNamespace(index=index, relative_pressure=pressure, quantity_adsorbed_cm3_g_stp=quantity)
            for index, (pressure, quantity) in enumerate(
                ((0.01, 1.0), (0.05, 2.0), (0.10, 3.0), (0.15, 4.0)),
                start=1,
            )
        ]
        result = SimpleNamespace(run_conditions=SimpleNamespace(bath_temperature_K=77.35))

        def solved_width(*_args, **_kwargs):
            shell_width = next(shell_widths)
            return 10.0 * shell_width + 3.04

        with (
            patch("tristar_bet.analysis.adsorption_points", return_value=points),
            patch("tristar_bet.analysis.density_conversion_factor", return_value=1.0),
            patch("tristar_bet.analysis._hk_cheng_yang_monolayer_capacity", return_value=1.0),
            patch("tristar_bet.analysis._hk_solve_width_angstrom", side_effect=solved_width),
        ):
            distribution = horvath_kawazoe_pore_distribution(
                result,
                cheng_yang_correction=True,
                smooth=False,
                pressure_range=(0.0, 0.2),
            )

        self.assertEqual(len(distribution.rows), 4)
        self.assertEqual(distribution.rows[-1]["hk_width_reversal"], 1.0)
        self.assertAlmostEqual(distribution.rows[-1]["pore_width_nm"], 0.85)
        self.assertGreater(distribution.rows[-1]["raw_differential_pore_volume_per_nm_cm3_g_nm"], 0.0)


if __name__ == "__main__":
    unittest.main()
