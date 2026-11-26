"""Quantachrome .qps data reduction: corrections, tagged points and BJH/DH.

Reference numbers are values printed by ASiQwin 3.01 for its demo files; the
.qps file used by the parser tests is generated here, so no instrument data is
needed to run them.
"""
import struct
import tempfile
import unittest
from pathlib import Path

from tristar_bet import quantachrome_native as native
from tristar_bet.analysis import bet_analysis, bjh_pore_distribution, dh_pore_distribution, t_plot_analysis
from tristar_bet.quantachrome import load_qps


def _record(tag, payload):
    return bytes([tag]) + struct.pack("<H", len(payload)) + payload


def build_qps(groups):
    """Minimal QBIN container: ``{group: {key: value}}`` with str, int or float values."""
    strings = []

    def ref(text):
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    body = []
    for group, items in groups.items():
        body.append(_record(7, struct.pack("<i", ref(group))))
        body.append(_record(4, struct.pack("<i", len(items))))
        for key, value in items.items():
            body.append(_record(5, struct.pack("<i", ref(key))))
            if isinstance(value, str):
                payload = b"\x01" + struct.pack("<i", ref(value)) + b"\0\0\0\0"
            elif isinstance(value, float):
                payload = b"\x02" + struct.pack("<d", value)
            else:
                payload = b"\x03" + struct.pack("<i", int(value)) + b"\0\0\0\0"
            body.append(_record(6, payload))
    head = [_record(0, b""), _record(1, b""), _record(2, struct.pack("<i", len(strings)))]
    head += [_record(3, text.encode("ascii")) for text in strings]
    head.append(_record(8, b""))
    return b"QBINa" + b"".join(head) + b"".join(body)


def sample_groups(**overrides):
    # Type II-like isotherm: 12 adsorption points, 4 desorption points.
    pressures = [0.02, 0.05, 0.08, 0.12, 0.16, 0.20, 0.25, 0.30, 0.45, 0.60, 0.80, 0.95, 0.80, 0.60, 0.45, 0.30]
    volumes = [3.0, 3.5, 3.8, 4.1, 4.4, 4.7, 5.0, 5.3, 6.4, 8.0, 12.0, 20.0, 14.0, 9.5, 6.9, 5.4]
    tags = (
        ["A______P________"] + ["A_M___LP________"] * 2 + ["A_M_T_LP________"] * 3 + ["A___T__P________"] * 2
        + ["A______P________"] * 3 + ["AD___V_P________"] + ["_D_____P________"] * 4
    )
    data = {"NColumns": 7, "NPoints": len(pressures)}
    for index, (pressure, volume, tag) in enumerate(zip(pressures, volumes, tags)):
        data[f"Pt#0#{index:03d}"] = pressure
        data[f"Pt#1#{index:03d}"] = 760.0
        data[f"Pt#2#{index:03d}"] = volume * 0.5  # total cm3 STP for 0.5 g
        data[f"Pt#3#{index:03d}"] = 0.0
        data[f"Pt#4#{index:03d}"] = 3.0
        data[f"Pt#5#{index:03d}"] = 20.0 + 10.0 * index
        data[f"Pt#6#{index:03d}"] = 0.0
        data[f"PtFlg{index:03d}"] = tag
    groups = {
        "Header": {"FileVersion": 211, "hdrType": "IQ"},
        "runData": {"analysisDate": "2010/01/30", "adsName": "Nitrogen", "vvReMeasure": 0, "stationId": 1,
                    "reportedPo": 760.0},
        "adminData": {"sampleId": "Synthetic", "operatorId": "tester", "ambientTemp": 300.0, "bathTemp": 77.3,
                      "adsCrossSectionArea": 16.2, "adsMolecWeight": 28.013, "adsNonIdeality": 6.58e-05,
                      "sampleWeight": 0.5, "canTempComp": 1, "canThermTransp": 1,
                      "instrumentType": "Autosorb iQ", "pcVersion": "1.0"},
        "DRedParams": {"tMethodType": 1, "thermalOn": 1, "bjhdhIgnoreBelow": 0, "effMolDiameter": 3.54,
                       "effCellStemDiameter": 4.0, "PoOverride": 0.0, "overridePo": 0},
        "DRedParams.TempComp": {"cellType": 1, "enabled": 0, "applicable": 1},
        "material": {"AdsOverride": 1},
        "material.Adsorbate": {"name": "Nitrogen", "AdsTemp": 77.35, "MolWt": 28.0134, "XArea": 16.2,
                               "LiqDen": 0.808, "SurfTen": 8.85, "NonIdeality": 6.58e-05},
        "Data": data,
    }
    for group, items in overrides.items():
        groups[group.replace("__", ".")].update(items)
    return groups


class QpsFileCase(unittest.TestCase):
    def load(self, groups=None, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.qps"
            path.write_bytes(build_qps(groups or sample_groups()))
            return load_qps(path, **kwargs)


class CorrectionFormulaTests(unittest.TestCase):
    def test_thermal_transpiration_matches_asiqwin(self):
        # ASiQwin shows P/P0 9.1150e-07 for a stored 1.589689e-06 (argon at 87.5 K, 0.00124791 Torr).
        factor = native.thermal_transpiration_factor(0.00124791, 87.5, 300.0, 3.54, 4.0)
        self.assertAlmostEqual(factor, 9.1150e-07 / 1.589689e-06, places=5)
        # At ordinary BET pressures the correction vanishes.
        self.assertAlmostEqual(native.thermal_transpiration_factor(100.0, 77.3, 300.0, 3.54, 4.0), 1.0, places=6)

    def test_temperature_compensation_matches_asiqwin(self):
        # Krypton demo file, last point: stored 0.123297 cm3, ASiQwin reports 0.1202 cm3/g for 0.9806 g.
        correction = native.temperature_compensation_volume("iq_77", 1, 0.305787, 0.804220, 94.76667, {})
        self.assertAlmostEqual(0.123297 - correction, 0.1202 * 0.9806, delta=6e-5)

    def test_temperature_compensation_rules(self):
        admin = {"bathTemp": 77.3, "adsMolecWeight": 28.013}
        self.assertEqual(native.temperature_compensation_kind("IQ", admin, {"vvReMeasure": 0}), "iq_77")
        self.assertEqual(native.temperature_compensation_kind("IQ", {"bathTemp": 87.5}, {}), "iq_87")
        self.assertIsNone(native.temperature_compensation_kind("IQ", {"bathTemp": 90.0}, {}))
        self.assertIsNone(native.temperature_compensation_kind("QW", admin, {}))
        self.assertIsNone(native.temperature_compensation_kind("AS", admin, {}))
        self.assertEqual(native.temperature_compensation_kind("AS", {"adsMolecWeight": 28.01}, {}), "as1")


class ParserTests(QpsFileCase):
    def test_strings_tags_and_saved_ranges(self):
        result = self.load()
        self.assertEqual(result.sample.sample_name, "Synthetic")
        self.assertEqual(result.sample.operator, "tester")
        self.assertEqual(result.run_conditions.adsorptive_name, "Nitrogen")
        options = result.method_options
        self.assertEqual(options["vendor_bjh_thickness_method"], "carbon_black_stsa")
        self.assertEqual(options["vendor_t_plot_thickness_method"], "carbon_black_stsa")
        self.assertAlmostEqual(options["stored_bet_pressure_min"], result.isotherm[1].relative_pressure)
        self.assertAlmostEqual(options["stored_bet_pressure_max"], result.isotherm[5].relative_pressure)
        self.assertAlmostEqual(options["stored_t_plot_pressure_min"], result.isotherm[3].relative_pressure)
        self.assertAlmostEqual(options["stored_t_plot_pressure_max"], result.isotherm[7].relative_pressure)
        self.assertEqual(result.isotherm[0].elapsed_seconds, 1200)
        self.assertEqual([point.phase for point in result.isotherm].count("adsorption"), 12)
        self.assertEqual(bet_analysis(result).point_count, 5)
        self.assertEqual(t_plot_analysis(result).point_count, 5)

    def test_corrections_follow_file_and_can_be_overridden(self):
        saved = self.load()
        self.assertTrue(saved.method_options["quantachrome_thermal_transpiration_applied"])
        self.assertFalse(saved.method_options["quantachrome_temperature_compensation_applied"])
        self.assertTrue(saved.method_options["quantachrome_temperature_compensation_available"])
        off = self.load(thermal_transpiration=False)
        self.assertAlmostEqual(off.isotherm[0].relative_pressure, 0.02, places=12)
        self.assertLess(saved.isotherm[0].relative_pressure, 0.02)
        self.assertAlmostEqual(saved.isotherm[0].quantity_adsorbed_cm3_g_stp, 3.0, places=12)
        compensated = self.load(temperature_compensation=True)
        self.assertLess(compensated.isotherm[-1].quantity_adsorbed_cm3_g_stp, saved.isotherm[-1].quantity_adsorbed_cm3_g_stp)

    def test_corrections_unavailable_without_instrument_support(self):
        result = self.load(sample_groups(adminData={"canThermTransp": 0, "ambientTemp": 0.0}, Header={"hdrType": "QW"}))
        options = result.method_options
        self.assertFalse(options["quantachrome_thermal_transpiration_available"])
        self.assertFalse(options["quantachrome_temperature_compensation_available"])
        forced = self.load(
            sample_groups(adminData={"canThermTransp": 0, "ambientTemp": 0.0}, Header={"hdrType": "QW"}),
            thermal_transpiration=True, temperature_compensation=True,
        )
        self.assertAlmostEqual(forced.isotherm[0].relative_pressure, 0.02, places=12)

    def test_adsorbate_named_in_run_header_wins_over_stale_block(self):
        groups = sample_groups(
            material={"AdsOverride": 0}, runData={"adsName": "Krypton"},
            adminData={"adsCrossSectionArea": 20.5, "adsMolecWeight": 83.8},
        )
        result = self.load(groups)
        self.assertEqual(result.run_conditions.adsorptive_name, "Krypton")
        self.assertAlmostEqual(result.adsorptive_properties.molecular_cross_sectional_area_nm2, 0.205)
        expected = (83.8 / 2.413) / 22414.0
        self.assertAlmostEqual(result.adsorptive_properties.density_conversion_factor, expected, places=12)
        kept = self.load(sample_groups(runData={"adsName": "Krypton"}))  # AdsOverride = 1 keeps the block
        self.assertAlmostEqual(kept.adsorptive_properties.molecular_cross_sectional_area_nm2, 0.162)

    def test_po_override(self):
        result = self.load(sample_groups(DRedParams={"overridePo": 1, "PoOverride": 800.0, "thermalOn": 0}))
        self.assertAlmostEqual(result.isotherm[0].relative_pressure, 0.02 * 760.0 / 800.0, places=12)


class PoreSizeTests(QpsFileCase):
    def test_tagged_points_drive_bjh_and_dh(self):
        result = self.load(sample_groups(DRedParams={"thermalOn": 0}))
        for phase, intervals in (("adsorption", 11), ("desorption", 4)):
            bjh = bjh_pore_distribution(result, phase, thickness_method="carbon_black_stsa")
            dh = dh_pore_distribution(result, phase, thickness_method="carbon_black_stsa")
            self.assertEqual(len(bjh.rows), intervals)
            self.assertEqual(bjh.rows[0]["bjh_correction"], "quantachrome_qps_tagged_points")
            self.assertEqual(dh.rows[0]["bjh_correction"], "quantachrome_qps_tagged_points")
            self.assertLess(bjh.rows[0]["pore_diameter_nm"], bjh.rows[-1]["pore_diameter_nm"])
            for row in bjh.rows:
                # cylindrical pores: A = 4V/D
                self.assertAlmostEqual(
                    row["incremental_pore_area_m2_g"],
                    4000.0 * row["incremental_pore_volume_cm3_g"] / row["pore_diameter_nm"], places=9,
                )
        # another thickness equation is the user's own setting, not the file's
        other = bjh_pore_distribution(result, "adsorption", thickness_method="halsey")
        self.assertNotEqual(other.rows[0].get("bjh_correction"), "quantachrome_qps_tagged_points")

    def test_ignore_below_035(self):
        result = self.load(sample_groups(DRedParams={"thermalOn": 0, "bjhdhIgnoreBelow": 1}))
        rows = bjh_pore_distribution(result, "adsorption", thickness_method="carbon_black_stsa").rows
        self.assertEqual(len(rows), 3)
        self.assertGreater(min(row["relative_pressure_low"] for row in rows), 0.35)

    def test_bjh_interval_arithmetic(self):
        # Two intervals worked by hand from the BJH relations ASiQwin uses.
        rows = native.pore_size_intervals(
            [(0.9, 100.0), (0.8, 80.0), (0.7, 70.0)], dollimore_heal=False, thickness_type=0,
            thickness_pre_exponent=0.0, thickness_exponent=0.0, surface_tension=8.85,
            molecular_weight=28.0134, liquid_density=0.808, temperature_k=77.35,
        )
        import math
        liquid = 28.0134 / 0.808 / 22414.0
        kelvin = 2 * 8.85 * 28.0134 / 0.808 / (8.31441e7 * 77.35 * math.log(10)) * 1e8
        t = [math.sqrt(13.99 / (0.034 - math.log10(p))) for p in (0.9, 0.8, 0.7)]
        k = [-kelvin / math.log10(p) for p in (0.9, 0.8, 0.7)]
        r1 = 0.5 * (k[0] + t[0] + k[1] + t[1])
        v1 = (r1 / (0.5 * (k[0] + k[1]) + 0.5 * (t[0] - t[1]))) ** 2 * liquid * 20.0
        a1 = 20000.0 * v1 / r1
        r2 = 0.5 * (k[1] + t[1] + k[2] + t[2])
        wall = (1 - 0.5 * (t[1] + t[2]) / r1) * a1
        v2 = (r2 / (0.5 * (k[1] + k[2]) + 0.5 * (t[1] - t[2]))) ** 2 * (liquid * 10.0 - 1e-4 * (t[1] - t[2]) * wall)
        self.assertAlmostEqual(rows[0]["incremental_pore_volume_cm3_g"], v1, places=12)
        self.assertAlmostEqual(rows[1]["incremental_pore_volume_cm3_g"], v2, places=12)
        self.assertAlmostEqual(rows[0]["pore_diameter_nm"], 0.2 * r1, places=12)


if __name__ == "__main__":
    unittest.main()
