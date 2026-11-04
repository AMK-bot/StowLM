"""Reproducible approximate, calibrated Type B design screen; not certification.

APPROXIMATION: reproduces the bounded physics-review method, including its
rounded metric depth constants. The declared downward 0.250 m correction is
an empirical model calibration plus rounding allowance, not a rule allowance
or an established conservative bound. No checker/operating limit is changed.
"""

from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path('.')
RAW = ROOT / "data/raw/ecfr-46-cfr-42"
DATE = "2026-10-27"
FT_M = 0.3048
IN_MM = 25.4


def _source(section: str) -> dict:
    manifest = json.loads((RAW / "MANIFEST").read_text())
    record = next(r for r in manifest if r["file"] == f"ecfr-42.20-{section}.xml")
    return {"document": "46 CFR Part 42, eCFR captured 2026-01-01",
            "section": f"42.20-{section}", "url": record["url"],
            "read_date": record["retrieved"], "sha256": record["sha256"]}


def _type_b_table() -> list[list[float]]:
    """Read numerical facts from inert XML; never execute downloaded content."""
    root = ET.parse(RAW / "ecfr-42.20-15.xml").getroot()
    for table in root.iter("TABLE"):
        caption = table.find("CAPTION")
        title = "" if caption is None else " ".join(caption.itertext())
        if "42.20-15" in title and "Type" in title and "B" in title:
            rows = []
            for row in table.iter("TR"):
                values = ["".join(c.itertext()).strip() for c in row]
                if len(values) == 2:
                    try:
                        rows.append([float(v.replace(",", "")) for v in values])
                    except ValueError:
                        pass
            if len(rows) >= 2:
                return rows
    raise ValueError("missing Type B freeboard table in captured source")


def _interpolate(rows: list[list[float]], value: float) -> float:
    for (x0, y0), (x1, y1) in zip(rows, rows[1:]):
        if x0 <= value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    raise ValueError("length outside captured Type B table; no extrapolation")


def _raw(length_m: float, depth_m: float, table: list[list[float]]) -> dict:
    # APPROXIMATION: Lpp substitutes for statutory length, zero actual sheer,
    # standard-height effective superstructures S=0.2L, line I deduction.
    ell = length_m / FT_M
    if ell < 279.0:
        raise ValueError("bounded review screen does not implement superstructure deduction below279ft")
    tabular = _interpolate(table, ell) * IN_MM
    rate = length_m / 0.48 if length_m < 120.0 else 250.0
    depth = max(0.0, (depth_m - length_m / 15.0) * rate)
    full_deduction_in = (34.0 + 8.0 * (ell - 279.0) / (400.0 - 279.0)
                         if ell < 400.0 else 42.0)
    aft = ((0.1 * ell + 10.0) + 3.0 * (0.0444 * ell + 4.44)
           + 3.0 * (0.0111 * ell + 1.11)) / 8.0
    fore = ((0.2 * ell + 20.0) + 3.0 * (0.0888 * ell + 8.88)
            + 3.0 * (0.0222 * ell + 2.22)) / 8.0
    sheer = (aft + fore) / 2.0 * IN_MM * (0.75 - 0.2 / 2.0)
    deduction = full_deduction_in * IN_MM * 0.10
    return {"tabular_m": tabular / 1000.0, "depth_correction_m": depth / 1000.0,
            "sheer_correction_m": sheer / 1000.0,
            "superstructure_deduction_m": deduction / 1000.0,
            "required_m": (tabular + depth + sheer - deduction) / 1000.0}


def apply_freeboard(ship: dict) -> dict:
    """Return labelled load_line metadata; assert only the calibrated screen.

    The raw result and its pass/fail remain visible. Unsupported Cb correction
    or table extrapolation raises rather than silently weakening the screen.
    """
    h = ship["hull"]
    length, depth, draught = (float(h[k]) for k in
                              ("lpp_m", "depth_m", "scantling_draught_m"))
    if not (length > 0.0 and depth > draught > 0.0):
        raise ValueError("invalid freeboard design dimensions")
    cb = ship.get("derived_hydrostatics", {}).get("block_coefficient",
                                                 h.get("block_coefficient"))
    if cb is None:
        raise ValueError("integrated block coefficient required for Type B screen")
    if float(cb) > 0.68:
        raise ValueError("Cb > 0.68 correction not implemented by bounded review screen")
    table = _type_b_table()
    raw = _raw(length, depth, table)
    calibration_raw = _raw(124.41, 11.3, table)["required_m"]
    observed_bias = calibration_raw - 2.609
    correction = 0.250
    extra_allowance = correction - observed_bias
    required = max(0.0, raw["required_m"] - correction)
    # APPROXIMATION: declared12mm deck-stringer thickness at the deck line,
    # applied to all three synthetic designs; not a surveyed plate thickness.
    stringer = 0.012
    available = depth + stringer - draught
    result = {
        "assumption": "APPROXIMATION: Low-confidence (L) calibrated preliminary Type B screen, NOT conservative or certified assigned freeboard. Lpp substitutes statutory length; zero actual sheer; S=0.2L standard-height forecastle/no detached bridge line I. Available freeboard is moulded depth plus assumed0.012m deck-stringer thickness minus draught. Omit other deck-line/bow-height and special Type B details. Rounded metric depth constants reproduce physics-review design/sourced_values.py. Downward0.250m correction comprises one Dina datum bias plus an explicit rounding/model allowance. Raw failure remains visible; no operating draught/checker limit is changed.",
        "confidence": "L",
        "method": "physics-review approximate Type B, fixed Dina-calibrated screen",
        "raw_required_m": raw["required_m"],
        "available_freeboard_m": available,
        "moulded_depth_minus_draught_m": depth - draught,
        "deck_stringer_thickness_m": stringer,
        "raw_screen_passed": available >= raw["required_m"],
        "approx_required_m": required,
        "approx_screen_passed": available >= required,
        "approx_margin_m": available - required,
        "calibration_bias_m": correction,
        "observed_dina_bias_m": observed_bias,
        "rounding_model_allowance_m": extra_allowance,
        "raw_components": {"assumption": "DERIVED: Raw review components in metres before calibration.",
                           **{k: v for k, v in raw.items() if k != "required_m"}},
        "inputs": {"assumption": "APPROXIMATION: Geometry is the fixed synthetic design; effective superstructure fraction0.2 and actual sheer0 are low-confidence model inputs, not surveyed arrangements.",
                   "length_m": length, "depth_m": depth, "draught_m": draught,
                   "block_coefficient": float(cb), "superstructure_fraction": 0.2,
                   "actual_sheer_m": 0.0, "deck_stringer_thickness_m": stringer},
        "calibration": {"assumption": "APPROXIMATION: One real partly open-top vessel calibrates an incomplete closed-top synthetic design screen.0.250m downward correction is not an authorised rule deduction or proven bound. Reference available freeboard includes the same assumed0.012m stringer; raw reference requirement and the2.609m published assigned datum are not changed.",
                        "reference_length_m": 124.41, "reference_depth_m": 11.3,
                        "assigned_freeboard_m": 2.609,
                        "raw_reference_freeboard_m": calibration_raw,
                        "approx_reference_freeboard_m": max(0.0, calibration_raw-correction),
                        "reference_draught_m": 8.7,
                        "reference_available_freeboard_m": 11.3+stringer-8.7,
                        "reference_available_minus_assigned_m": 11.3+stringer-8.7-2.609,
                        "reference_assigned_screen_passed": 11.3+stringer-8.7>=2.609,
                        "source": {"document": "Dina Trader owner brochure June2022",
                                   "section": "PDF p1 classification/freeboard2.609m and Dimensions",
                                   "url": "https://mmred.no/images/Dina_Trader_Brochure-2022.pdf",
                                   "read_date": DATE}, "confidence": "L"},
        "table": {"source": _source("15"), "section": "42.20-15(b)(1) Type B",
                  "length_unit": "ft", "freeboard_unit": "in", "rows": table,
                  "confidence": "H"},
        "sources": [_source(s) for s in ("15", "25", "30", "60", "65")],
        "method_constants": {
            "metres_per_foot": {"value": FT_M, "unit": "m/ft", "confidence": "H",
                                "assumption": "Exact international-foot unit conversion."},
            "millimetres_per_inch": {"value": IN_MM, "unit": "mm/in", "confidence": "H",
                                     "assumption": "Exact international-inch unit conversion."},
            "effective_superstructure_fraction": {"value": 0.2, "unit": "1", "confidence": "L",
                                                   "assumption": "APPROXIMATION: S=0.2L of standard-height enclosed superstructures; not measured geometry."},
            "deck_stringer_thickness_m": {"value": stringer, "unit": "m", "confidence": "L",
                                           "assumption": "APPROXIMATION: Synthetic12mm deck-stringer thickness used at the freeboard deck line for all three ships, per CONVERGENCE section5 item8; not surveyed or class-approved."},
            "line_i_deduction_fraction": {"value": 0.10, "unit": "1", "confidence": "H",
                                           "source": _source("60")},
            "metric_depth_transition_m": {"value": 120.0, "unit": "m", "confidence": "L",
                                           "assumption": "APPROXIMATION: Rounded metric transition from review method, not exact imperial conversion."},
            "metric_depth_denominator": {"value": 0.48, "unit": "1", "confidence": "L",
                                          "assumption": "APPROXIMATION: Rounded metric depth rate L/0.48 in mm per m; reproduces review method."},
            "metric_depth_large_rate": {"value": 250.0, "unit": "mm/m", "confidence": "L",
                                         "assumption": "APPROXIMATION: Rounded metric depth rate; reproduces review method."},
            "length_depth_denominator": {"value": 15.0, "unit": "1", "confidence": "H",
                                          "source": _source("30")},
            "sheer_formula_coefficients": {"value": [0.1, 10.0, 0.0444, 4.44, 0.0111, 1.11,
                                                        0.2, 20.0, 0.0888, 8.88, 0.0222, 2.22],
                                             "unit": "source ft/in formula coefficients", "confidence": "H",
                                             "source": _source("65")},
            "sheer_simpson_weights": {"value": [1.0, 3.0, 3.0, 1.0], "unit": "1",
                                        "confidence": "H", "source": _source("65")},
            "full_deduction_length_ft": {"value": [279.0, 400.0], "unit": "ft",
                                          "confidence": "H", "source": _source("60")},
            "full_deduction_in": {"value": [34.0, 42.0], "unit": "in", "confidence": "H",
                                   "source": _source("60")},
        },
        "provenance": {},
    }
    if ship.get("nominal_teu") == 800:
        result["dina_assigned_comparison"] = {
            "assumption": "APPROXIMATION: Compare this synthetic design's stringer-adjusted available freeboard with the real partly open-top Dina's assigned datum; this is not transfer of its assigned load line.",
            "confidence": "L", "available_freeboard_m": available,
            "raw_required_m": raw["required_m"], "approx_required_m": required,
            "assigned_freeboard_m": 2.609, "available_minus_assigned_m": available-2.609,
            "assigned_screen_passed": available>=2.609,
            "source": result["calibration"]["source"],
        }
    # Every scalar physical output has an explicit derivation/assumption label;
    # source-number tables inherit their document provenance above.
    for key, val in result.items():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            result["provenance"][key] = {
                "assumption": "DERIVED APPROXIMATION: Reproduce declared raw components and fixed calibrated model; not a statutory limit.",
                "confidence": "L"}
    assert result["approx_screen_passed"], (
        f"{ship.get('id', 'ship')}: calibrated APPROXIMATE freeboard screen fails: "
        f"available={available:.9f}m, approximate_required={required:.9f}m; "
        f"raw_required={raw['required_m']:.9f}m")
    return result
