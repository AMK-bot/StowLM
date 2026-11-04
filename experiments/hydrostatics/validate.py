"""Reproduce numerical convergence and the deliberately limited Bardu comparison.

Run ``UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/hydrostatics/validate.py``.
No real Bardu offsets are available. Preserve the uncalibrated dimensional
check, then fit only longitudinal waterplane fullness to TPC and test the
independent published deadweight increment. No lightship mass is inferred.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import time

from stowage.params import load_param_file
from stowage.vessel.hydrostatics import HullGeometry


REFERENCE_TPC_KG_PER_CM = 52_130.0
REFERENCE_DRAUGHT_M = 11.6
REFERENCE_DENSITY_KG_M3 = 1025.0
REFERENCE_DESIGN_DRAUGHT_M = 10.1
REFERENCE_DISPLACEMENT_INCREMENT_KG = (34_000 - 26_700) * 1000.0


def historical_family_params(name="feeder_500"):
    """Explicit fixture for the superseded synthetic family, not D20 fleet.

    APPROXIMATION: preserve the former rounded-bilge family for diagnostics.
    Current fleet dimensions/envelope are retained, but shape and analytical
    coefficient targets are explicitly replaced; these are not current targets.
    """
    p = copy.deepcopy(load_param_file("fleet", name))
    p["hull_form"] = dict(family="stowlm_separable_rounded_bilge_v1",
        aft_run_fraction=0.12, fore_run_fraction=0.12,
        bilge_radius_m=0.8, deck_flare_fraction=0.04)
    h = p["hull"]
    cm = 1 - (2 - math.pi / 2) * 0.8**2 / (h["beam_m"] * h["design_draught_m"])
    h.update(block_coefficient=0.88 * cm, midship_coefficient=cm,
        waterplane_coefficient=0.88, prismatic_coefficient=0.88, lcb_m=h["lpp_m"] / 2)
    return p


def bardu_illustrative_params():
    """Bardu dimensions with uncalibrated feeder-500 shape; not Bardu offsets.

    APPROXIMATION: retain feeder-500 taper, bilge radius/beam and deck flare;
    density is assumed 1025 kg/m³ (not stated in the brochure). No coamings
    are credited because their watertight envelope is unknown for Bardu.
    """
    feeder = historical_family_params("feeder_500")
    # Retain geometry inputs only: feeder lightship/coefficients/operating
    # limits are not Bardu particulars and must not masquerade as targets.
    p = {key: copy.deepcopy(feeder[key]) for key in ("hull_form", "envelope")}
    p["hull"] = {}
    p["hull"].update(loa_m=208.9, lpp_m=196.9, beam_m=29.8,
                     depth_m=16.4, design_draught_m=10.1,
                     scantling_draught_m=11.6, fp_x_m=196.9,
                     water_density_kg_m3=REFERENCE_DENSITY_KG_M3)
    p["hull_form"].update(bilge_radius_m=0.8 * 29.8 / 19.2,
                          x_max_m=196.9, z_max_m=16.4)
    p["envelope"].update(deck_z_m=16.4, hatch_coamings=[],
                         buoyant_x_max_m=196.9)
    return p


def result_values(r):
    return {name: float(getattr(r, name)) for name in (
        "volume_m3", "lcb_m", "tcb_m", "vcb_m", "waterplane_area_m2",
        "transverse_inertia_m4", "longitudinal_inertia_m4")}


def bardu_calibrated_params():
    """Fit symmetric taper to TPC only; keep other shape choices unchanged.

    APPROXIMATION: a waterplane-matched synthetic hull, not surveyed Bardu
    geometry. The only fitted value is Q=Cwp below design draught. The
    pre-existing flare changes Cwp at the scantling waterline by F(11.6).
    For equal aft/fore taper a=f, Q=1-a. No displacement datum is fitted.
    """
    p = bardu_illustrative_params()
    h, f = p["hull"], p["hull_form"]
    flare_factor = 1 + f["deck_flare_fraction"] * (
        REFERENCE_DRAUGHT_M - h["design_draught_m"]) / (
        h["depth_m"] - h["design_draught_m"])
    # APPROXIMATION: calibrate only longitudinal waterplane fullness to TPC;
    # keep the published deadweight increment held out for validation.
    q = REFERENCE_TPC_KG_PER_CM * 100 / (
        h["water_density_kg_m3"] * h["lpp_m"] * h["beam_m"] * flare_factor)
    f["aft_run_fraction"] = f["fore_run_fraction"] = 1 - q
    return p


def coefficient_comparison(p, geometry):
    h = p["hull"]
    length, beam, draught = h["lpp_m"], h["beam_m"], h["design_draught_m"]
    r = geometry.submerged(draught, draught, 0)
    cb = r.volume_m3 / (length * beam * draught)
    cm = float(max(r.sectional_areas_m2)) / (beam * draught)
    generated = dict(block_coefficient=cb, midship_coefficient=cm,
        waterplane_coefficient=r.waterplane_area_m2 / (length * beam),
        prismatic_coefficient=cb / cm, lcb_m=r.lcb_m)
    return {"draught_m": draught, "generated": generated,
            "parameter_target": {k: h[k] for k in generated},
            "absolute_residual": {k: generated[k] - h[k] for k in generated}}


def bardu_calibrated_comparison():
    p = bardu_calibrated_params()
    geom = HullGeometry.from_params(p, nx=96, nz=128)
    upper = geom.submerged(REFERENCE_DRAUGHT_M, REFERENCE_DRAUGHT_M, 0)
    lower = geom.submerged(REFERENCE_DESIGN_DRAUGHT_M, REFERENCE_DESIGN_DRAUGHT_M, 0)
    tpc = REFERENCE_DENSITY_KG_M3 * upper.waterplane_area_m2 / 100
    increment = upper.displacement_kg - lower.displacement_kg
    error = 100 * (increment / REFERENCE_DISPLACEMENT_INCREMENT_KG - 1)
    return {"approximation": "Only symmetric longitudinal taper fitted to scantling TPC; bilge/flare/envelope unchanged.",
        "aft_run_fraction": p["hull_form"]["aft_run_fraction"],
        "fore_run_fraction": p["hull_form"]["fore_run_fraction"],
        "design_waterplane_fullness_q": 1 - p["hull_form"]["aft_run_fraction"],
        "fitted_tpc_kg_per_cm": tpc,
        "reference_tpc_kg_per_cm": REFERENCE_TPC_KG_PER_CM,
        "tpc_fit_error_percent": 100 * (tpc / REFERENCE_TPC_KG_PER_CM - 1),
        "independent_check": "Displacement increment between 10.1 and 11.6m equals published deadweight increment; unchanged lightship cancels.",
        "reference_displacement_increment_kg": REFERENCE_DISPLACEMENT_INCREMENT_KG,
        "integrated_displacement_increment_kg": increment,
        "increment_error_percent": error,
        "increment_tolerance_percent": 2.0,
        "increment_passes": abs(error) <= 2,
        "absolute_displacement_validated": False,
        "lightship_inferred": False}


def generate_report():
    started = time.perf_counter()
    report = {"integration": "See HullGeometry module docstring for integration rule.",
              "convergence": [], "kn_convergence": [], "bardu": {},
              "design_coefficient_comparison": {}}
    for name in ("feeder_500", "feeder_800", "feeder_1700"):
        p = historical_family_params(name)
        t = 0.8 * p["hull"]["design_draught_m"]
        coarse = HullGeometry.from_params(p, nx=48, nz=64)
        fine = HullGeometry.from_params(p, nx=96, nz=128)
        report["design_coefficient_comparison"][name] = coefficient_comparison(p, fine)
        for heel_deg in (0, 30, 60):
            h = math.radians(heel_deg)
            a = result_values(coarse.submerged(t + 0.15, t - 0.15, h))
            b = result_values(fine.submerged(t + 0.15, t - 0.15, h))
            report["convergence"].append({"ship": name, "heel_deg": heel_deg,
                "aft_m": t + 0.15, "fwd_m": t - 0.15,
                "coarse": a, "fine": b,
                "absolute_change": {k: abs(a[k] - b[k]) for k in a},
                "volume_change_percent": 100 * abs(a["volume_m3"] / b["volume_m3"] - 1)})
        if name == "feeder_500":
            # Same displacement at both grids; KN solves draught AND free trim.
            mass = fine.submerged(t, t, 0).displacement_kg
            for heel_deg in (30, 60):
                angle = math.radians(heel_deg)
                a = coarse.kn(angle, mass)
                b = fine.kn(angle, mass)
                report["kn_convergence"].append({"ship": name, "heel_deg": heel_deg,
                    "displacement_kg": mass, "coarse_kn_m": a, "fine_kn_m": b,
                    "absolute_change_m": abs(a-b),
                    "relative_change_percent": 100 * abs(a/b-1)})
    p = bardu_illustrative_params()
    geom = HullGeometry.from_params(p, nx=96, nz=128)
    r = geom.submerged(REFERENCE_DRAUGHT_M, REFERENCE_DRAUGHT_M, 0)
    computed = REFERENCE_DENSITY_KG_M3 * r.waterplane_area_m2 / 100
    report["bardu"] = {"reference_tpc_kg_per_cm": REFERENCE_TPC_KG_PER_CM,
        "illustrative_tpc_kg_per_cm": computed,
        "error_percent": 100 * (computed / REFERENCE_TPC_KG_PER_CM - 1),
        "reference_draught_m": REFERENCE_DRAUGHT_M,
        "assumed_density_kg_m3": REFERENCE_DENSITY_KG_M3,
        "independent_validation": False,
        "reason": "No Bardu offsets, hydrostatic table, CG, or KN/GZ supplied; family is an uncalibrated approximation."}
    report["bardu_calibrated"] = bardu_calibrated_comparison()
    report["elapsed_seconds"] = time.perf_counter() - started
    return report


if __name__ == "__main__":
    output = Path(__file__).with_name("results.json")
    report = generate_report()
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
