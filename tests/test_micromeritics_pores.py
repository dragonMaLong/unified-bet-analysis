"""Micromeritics BJH / DH / HK report algorithms (synthetic data only).

Real-file evidence (not shipped): every BJH table of 34 TriStar II 3020,
26 ASAP 2460, 2 ASAP 2020 Plus, 2 3Flex and 172/180 TriStar II Plus reports
and 170/182 Dollimore-Heal tables are reproduced by these functions.
"""
import math
import struct
import unittest
from dataclasses import replace

from tristar_bet import micromeritics_pores as mp
from tristar_bet.analysis import (
    bjh_pore_distribution,
    dh_pore_distribution,
    horvath_kawazoe_pore_distribution,
    vendor_pore_thickness_params,
)
from tristar_bet.models import IsothermPoint
from test_detail_result_tables import sample


def isotherm():
    """Type IV-like isotherm with a hysteresis loop (adsorption, then desorption)."""
    def uptake(p, shift):
        return 120.0 * p / ((1.0 - p) * (0.05 + p)) * 0.2 + 180.0 / (1.0 + math.exp(-(p - shift) / 0.03))

    ads = [0.01, 0.03, 0.06, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.985]
    des = [0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.5, 0.4, 0.3, 0.2, 0.12]
    points = []
    for i, p in enumerate(ads):
        q = uptake(p, 0.72)
        points.append(IsothermPoint(i + 1, "adsorption", 0, p * 760, p, q, 760, 60 * i, q, q / 22.414))
    for j, p in enumerate(des):
        q = uptake(p, 0.66)
        points.append(IsothermPoint(100 + j, "desorption", 0, p * 760, p, q, 760, 60 * (j + 50), q, q / 22.414))
    return points


def options_block(model=1, correction=2, d_min=17.0, d_max=3000.0, pressure_range=(0.1, 0.995), table=None):
    """Layout of an ASAP 2460 / 3Flex subset 315/316 (offsets as in the vendor files)."""
    block = bytearray(b"SUBSET315\x00" + b"\x00" * 8)
    block += bytes([8]) + struct.pack("<H", model) + b"\x04\x00"
    block += struct.pack("<ddd", -5.0, 3.54, 0.333) + b"\x00" * 24 + struct.pack("<ddd", 13.99, 0.034, 0.5)
    if table:
        block += struct.pack("<H", len(table))
        for p, t in table:
            block += b"\x02\x00" + struct.pack("<dd", p, t)
    block += b"\x00" * 60
    limits_at = len(block)
    block += struct.pack("<dd", d_min, d_max) + b"\x00" * 76
    struct.pack_into("<H", block, limits_at + 44, correction)
    block += b"\x01\xe0\xe0\x01\x00" + struct.pack("<I", 6) + "N2\x00".encode("utf-16-le")
    factor_at = len(block)
    block += struct.pack("<d", 9.53) + b"\x00" * 40
    block += b"\x00\x02" + struct.pack("<dd", *pressure_range) + b"\x01\x00"
    return bytes(block), limits_at, factor_at


def fixed_block(**kwargs):
    block, limits_at, factor_at = options_block(**kwargs)
    # Put the limits exactly 92 bytes before the Kelvin factor, as the vendor does.
    block = bytearray(block)
    d_min, d_max = struct.unpack_from("<dd", block, limits_at)
    block[limits_at:limits_at + 16] = b"\x00" * 16
    struct.pack_into("<H", block, limits_at + 44, 0)
    struct.pack_into("<dd", block, factor_at - 92, d_min, d_max)
    struct.pack_into("<H", block, factor_at - 48, kwargs.get("correction", 2))
    return bytes(block)


class OptionsBlockTests(unittest.TestCase):
    def test_reads_model_limits_correction_range(self):
        settings = mp.parse_pore_options_block(fixed_block())
        self.assertEqual(settings["thickness_method"], "harkins_jura")
        self.assertEqual(settings["correction"], "faas")
        self.assertEqual(settings["kelvin_factor_angstrom"], 9.53)
        self.assertEqual((settings["diameter_min_angstrom"], settings["diameter_max_angstrom"]), (17.0, 3000.0))
        self.assertEqual(settings["pressure_range"], [0.1, 0.995])
        self.assertEqual(settings["thickness_params"], {"numerator": 13.99, "offset": 0.034, "exponent": 0.5})

    def test_halsey_and_reference(self):
        self.assertEqual(
            mp.parse_pore_options_block(fixed_block(model=0))["thickness_params"],
            {"numerator": -5.0, "prefactor": 3.54, "exponent": 0.333},
        )
        table = [(0.001 * k, 3.0 + k) for k in range(1, 30)]
        settings = mp.parse_pore_options_block(fixed_block(model=4, correction=0, table=table))
        self.assertEqual(settings["correction"], "standard")
        self.assertEqual(settings["reference_table_angstrom"][0], [0.001, 4.0])
        thickness = mp.thickness_function(settings)
        self.assertEqual(thickness(0.5), mp.REFERENCE_OUTSIDE_TABLE_A)  # outside the table: vendor -1 A
        self.assertAlmostEqual(thickness(0.002), 5.0)

    def test_unverified_models_are_not_used(self):
        self.assertIsNone(mp.parse_pore_options_block(fixed_block(model=2)))


class BranchPointTests(unittest.TestCase):
    def test_order_range_and_monotonic(self):
        iso = isotherm()
        ads = mp.branch_points(iso, "adsorption", {"pressure_range": [0.1, 0.995]})
        self.assertEqual(ads[0][0], 0.985)
        self.assertEqual(ads[-1][0], 0.1)
        des = mp.branch_points(iso, "desorption", {})
        self.assertEqual(des[0][0], 0.985)  # last adsorption point leads the desorption branch
        self.assertEqual(des[1][0], 0.95)
        self.assertTrue(all(a[0] > b[0] for a, b in zip(des, des[1:])))


class AlgorithmTests(unittest.TestCase):
    settings = {
        "thickness_method": "harkins_jura",
        "thickness_params": {"numerator": 13.99, "offset": 0.034, "exponent": 0.5},
        "kelvin_factor_angstrom": 9.53, "diameter_min_angstrom": 17.0, "diameter_max_angstrom": 3000.0,
    }

    def test_faas_pore_volume_is_bounded_by_liquid_volume(self):
        iso = isotherm()
        for phase in ("adsorption", "desorption"):
            points = mp.branch_points(iso, phase, {})
            rows = mp.faas_bjh(points, self.settings, 0.0015468)
            self.assertGreater(len(rows), 5)
            total = sum(r["volume"] for r in rows)
            self.assertLess(total, 1.05 * points[0][1] * 0.0015468)
            self.assertTrue(all(r["high"] > r["low"] for r in rows))

    def test_standard_and_dh_rows(self):
        points = mp.branch_points(isotherm(), "desorption", {})
        standard = mp.standard_bjh(points, self.settings, 0.0015468)
        dh = mp.dollimore_heal(points, self.settings, 0.0015468)
        self.assertGreater(len(standard), 5)
        self.assertEqual(len(dh), len(points) - 1)
        self.assertTrue(all(r["volume"] >= 0.0 for r in dh))
        for row in standard:
            self.assertGreater(row["high"], row["low"])

    def test_faas_walls_only_thinning_never_exceeds_film_change(self):
        # Desorption with a flat tail: the walls of open pores supply everything.
        iso = [p for p in isotherm() if p.phase == "adsorption"]
        flat = [replace(p, quantity_adsorbed_cm3_g_stp=200.0 + p.relative_pressure) if p.relative_pressure < 0.5 else p for p in iso]
        rows = mp.faas_bjh(mp.branch_points(flat, "adsorption", {}), self.settings, 0.0015468)
        self.assertTrue(rows)


class AnalysisHookTests(unittest.TestCase):
    def result(self, correction="faas"):
        settings = mp.parse_pore_options_block(fixed_block(correction={"faas": 2, "standard": 0}[correction]))
        settings.pop("pressure_range")
        return replace(sample("A"), isotherm=isotherm(), method_options={
            "instrument_model": "ASAP 2460",
            "vendor_bjh_report_settings": {"adsorption": settings, "desorption": settings},
            "vendor_dh_report_settings": {"adsorption": dict(settings, diameter_min_angstrom=7.5)},
            "vendor_bjh_thickness_method": "harkins_jura", "vendor_bjh_correction": correction,
            "vendor_dh_thickness_method": "harkins_jura",
        })

    def test_vendor_rows_when_settings_unchanged(self):
        result = self.result()
        params = vendor_pore_thickness_params(result.method_options["vendor_bjh_report_settings"]["desorption"])
        dist = bjh_pore_distribution(result, "desorption", "harkins_jura", params, "faas", 0.0, smooth=False)
        self.assertEqual(dist.status, "ok")
        self.assertTrue(all(row["vendor_report_algorithm"] == 1.0 for row in dist.rows))
        expected = mp.distribution_rows(result, "desorption", result.method_options["vendor_bjh_report_settings"]["desorption"], False, 0.0015468)
        self.assertAlmostEqual(dist.rows[0]["pore_diameter_nm"], expected[0]["average"] / 10.0)
        self.assertAlmostEqual(dist.rows[-1]["incremental_pore_area_m2_g"], 4000.0 * expected[-1]["volume"] / (expected[-1]["average"] / 10.0))

    def test_changed_settings_fall_back(self):
        result = self.result()
        for args in (("harkins_jura", None, "standard", 0.0), ("halsey", None, "faas", 0.0), ("harkins_jura", None, "faas", 0.2)):
            dist = bjh_pore_distribution(result, "desorption", *args, smooth=False)
            self.assertFalse(any(row.get("vendor_report_algorithm") for row in dist.rows))
        params = dict(vendor_pore_thickness_params(result.method_options["vendor_bjh_report_settings"]["desorption"]))
        params["numerator"] = 14.5
        dist = bjh_pore_distribution(result, "desorption", "harkins_jura", params, "faas", 0.0, smooth=False)
        self.assertFalse(any(row.get("vendor_report_algorithm") for row in dist.rows))

    def test_dh_uses_its_own_settings(self):
        result = self.result()
        dist = dh_pore_distribution(result, "adsorption", smooth=False)
        self.assertTrue(dist.rows and all(row["vendor_report_algorithm"] == 1.0 for row in dist.rows))
        self.assertEqual(dist.rows[0]["bjh_correction"], "dollimore_heal")


class HorvathKawazoeTests(unittest.TestCase):
    def test_decimal_search(self):
        # ln P = ln(L / 10): the search lands on the first factor within 0.1%.
        target = 0.9706884 * 1.0005
        width = mp.vendor_hk_width(6.04, target, lambda w: math.log(w / 10.0))
        # 1.7 overshoots, 1.60 undershoots, then 1.601 ... 1.607 is the first within 0.1%.
        self.assertAlmostEqual(width / 6.04, 1.607, places=12)
        self.assertLessEqual(abs(width / 10.0 - target), 1e-3 * target)

    def test_options_block(self):
        block = bytearray(b"SUBSET317\x00" + b"\x00" * 9)
        block += b"\x06\x01\x00\x01\x00\x01\x00\x00\x00\x00\x00\x01\x00" + struct.pack("<d", 3.49e-43)
        block += struct.pack("<H", 0) + b"\x00\x00\x03\xe0\x01\x00" + struct.pack("<I", 6) + "N2\x00".encode("utf-16-le")
        block += struct.pack("<H", 4) + b"\x00" * 80 + b"\x02" + struct.pack("<dd", 0.0, 1.0) + b"\x01\x00\x00\x00"
        settings = mp.parse_hk_options_block(bytes(block))
        self.assertEqual(settings["geometry"], "slit")
        self.assertEqual(settings["adsorbent_key"], "zeolite")
        self.assertEqual(settings["adsorptive_key"], "N2")
        self.assertEqual(settings["interaction_parameter_mode"], "input")
        self.assertEqual(settings["pressure_range"], [0.0, 1.0])

    def test_hk_vendor_iteration_used_only_for_matching_settings(self):
        iso = [p for p in isotherm() if p.phase == "adsorption"]
        hk = {"geometry": "slit", "adsorbent_key": "zeolite", "adsorptive_key": "N2",
              "interaction_parameter_mode": "input", "interaction_parameter": 3.49e-43,
              "pressure_range": [0.0, 1.0], "bath_temperature_K": 77.35}
        result = replace(sample("A"), isotherm=iso, method_options={"vendor_hk_report_settings": hk})
        vendor = horvath_kawazoe_pore_distribution(result, adsorbent_key="zeolite", interaction_parameter_erg_cm4=3.49e-43)
        other = horvath_kawazoe_pore_distribution(result, adsorbent_key="aluminosilicate", interaction_parameter_erg_cm4=3.49e-43)
        exact = horvath_kawazoe_pore_distribution(replace(result, method_options={}), adsorbent_key="zeolite",
                                                  interaction_parameter_erg_cm4=3.49e-43)
        self.assertEqual(len(vendor.rows), len(exact.rows))
        # Within the vendor's 0.1% pressure tolerance, not identical to the exact root.
        self.assertTrue(any(abs(a["pore_width_nm"] - b["pore_width_nm"]) > 1e-9 for a, b in zip(vendor.rows, exact.rows)))
        self.assertTrue(all(abs(a["pore_width_nm"] - b["pore_width_nm"]) / b["pore_width_nm"] < 0.02
                            for a, b in zip(vendor.rows, exact.rows) if a["relative_pressure"] < 0.3))
        self.assertTrue(other.rows)


if __name__ == "__main__":
    unittest.main()


class SmoothingAndHkDetailTests(unittest.TestCase):
    def test_smoothed_log_derivative_of_a_straight_cumulative_curve(self):
        # Cumulative volume linear in log10(D): the smoothed derivative is that constant slope.
        lows = [100.0 / 1.25 ** k for k in range(1, 25)]
        cumulative = [0.05 * math.log10(100.0 / d) for d in lows]
        values = mp.smoothed_log_derivative(lows, 100.0, cumulative)
        for value in values:
            self.assertAlmostEqual(value, 0.05, places=9)

    def test_vendor_rows_use_micromeritics_smoothing(self):
        result = AnalysisHookTests().result()
        params = vendor_pore_thickness_params(result.method_options["vendor_bjh_report_settings"]["desorption"])
        raw = bjh_pore_distribution(result, "desorption", "harkins_jura", params, "faas", 0.0, smooth=False)
        smooth = bjh_pore_distribution(result, "desorption", "harkins_jura", params, "faas", 0.0, smooth=True)
        expected = mp.smoothed_log_derivative(
            [row["pore_diameter_range_low_nm"] for row in raw.rows],
            raw.rows[0]["pore_diameter_range_high_nm"],
            [row["cumulative_pore_volume_cm3_g"] for row in raw.rows],
        )
        for row, value in zip(smooth.rows, expected):
            self.assertAlmostEqual(row["differential_pore_volume_cm3_g"], value)
            self.assertAlmostEqual(
                row["differential_pore_volume_per_nm_cm3_g_nm"],
                value / (math.log(10.0) * row["pore_diameter_range_low_nm"]),
            )

    def test_temperature_from_mean_p0(self):
        self.assertAlmostEqual(mp._temperature_from_p0(773.2543131510416), 77.4988, places=4)
        self.assertIsNone(mp._temperature_from_p0(100.0))

    def test_cylinder_series_terms(self):
        from tristar_bet.analysis import HK_ADSORBENT_PRESETS, HK_ADSORPTIVE_PRESETS, _hk_cylinder_ln_relative_pressure
        ads, gas = HK_ADSORBENT_PRESETS["aluminosilicate"], HK_ADSORPTIVE_PRESETS["N2"]
        full = _hk_cylinder_ln_relative_pressure(200.0, 77.35, ads, gas, 1.7e-43)
        fifty = _hk_cylinder_ln_relative_pressure(200.0, 77.35, ads, gas, 1.7e-43, terms=50)
        self.assertNotAlmostEqual(full, fifty, places=6)
