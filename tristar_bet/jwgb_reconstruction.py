"""Auditable JWGB gas-balance reconstruction shared by import and research.

Reconstructed quantities are candidates, not vendor-certified measurements.
Export closure hypotheses stay in the separate research tool.
"""
from __future__ import annotations

from datetime import datetime
import math

STP_RATIO = 273.15 / 101.325

def number(value):
    n = float(value)
    if not math.isfinite(n):
        raise ValueError("Non-finite input")
    return n


def calibrated_q(records):
    if len(records) != 2 or number(records[0]["Pd"]) != -0.00001:
        raise ValueError("Only reference plus one helium calibration dose is covered")
    row = records[1]
    pd, pcd = number(row["Pd"]), number(row["Pcd"])
    td, tc = number(row["PdT"]) + 273.15, number(row["PcdT"]) + 273.15
    delta_p = pcd - number(records[0]["Pcd"])
    if min(td, tc, delta_p) <= 0:
        raise ValueError("Invalid helium calibration")
    q = STP_RATIO * (pd / td - pcd / tc) / delta_p
    if q <= 0:
        raise ValueError("Invalid calibrated Q")
    return q


def reconstruct(experiment, *, rounded_q=False, nonideal=True):
    """Accumulate every signed dose; emit only balanced records and des reference."""
    vd, mass = number(experiment["vd_cm3"]), number(experiment["mass_g"])
    q = number(experiment["stored_q"]) if rounded_q else calibrated_q(experiment["q_records"])
    coefficient = number(experiment["nonideality_factor"]) if nonideal else 0.0
    if min(vd, mass, q) <= 0 or coefficient < 0:
        raise ValueError("Invalid volume/mass/calibration")
    total, previous, phase = 0.0, None, None
    rows, dose_count, last_time = [], 0, None
    for row in experiment["records"]:
        current_phase = row["phase"]
        pd, pcd = number(row["Pd"]), number(row["Pcd"])
        when = datetime.fromisoformat(row["Day"] + "T" + row["Time"])
        if last_time is not None and when < last_time:
            raise ValueError("Nonchronological pressure records")
        last_time = when
        if current_phase != phase:
            if (phase, current_phase) not in ((None, "adsorption"), ("adsorption", "desorption")):
                raise ValueError("Invalid branch order")
            if pd != -0.00001:
                raise ValueError("Branch must begin with reference")
            if previous is not None and abs(pcd - previous) > 1e-8:
                raise ValueError("Desorption reference differs from adsorption endpoint")
            previous, phase = pcd, current_phase
            if phase == "adsorption":
                continue
            delta = 0.0
        else:
            if pd < 0 or pcd < 0:
                raise ValueError("Unexpected negative pressure/reference")
            td, tc = number(row["PdT"]) + 273.15, number(row["PcdT"]) + 273.15
            if min(td, tc) <= 0:
                raise ValueError("Invalid dose temperatures")
            delta = vd * (STP_RATIO * (pd / td - pcd / tc) - q * (pcd - previous))
            delta *= 1 + coefficient * pcd
            total += delta
            dose_count += 1
            previous = pcd
            if row["Balance"] == "0":
                continue
        if row["Balance"] != "1":
            raise ValueError("Invalid balance flag")
        p0 = number(row["P0"])
        if p0 <= 0:
            raise ValueError("Invalid saturation pressure")
        rows.append({
            "phase": phase, "record_id": row["ID"], "is_reference": pd == -0.00001,
            "measurement": int(row["ID"].split("_")[0]),
            "pressure_kpa": pcd, "relative_pressure": pcd / p0,
            "time": row["Time"], "candidate_cm3_g": total / mass,
            "last_dose_cm3_stp": delta, "cumulative_doses": dose_count,
        })
    return {"q_used": q, "dose_count": dose_count, "rows": rows}


