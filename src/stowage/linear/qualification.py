"""Independent empirical holdout gate for directional error reserves.

The maximum error on calibration data is not an independent qualification.
Compare untouched holdout errors with each actual bound's candidate reserve;
any excess rejects qualification, including a single strength station. Finite
passing measurements remain empirical, not a proof over a continuous domain.
"""
from __future__ import annotations

import numpy as np


def qualify_holdout(measured_unsafe, reference_transfer, holdout_unsafe, *,
                    station_x_m, calibration_seed, holdout_seed,
                    calibration_samples, holdout_samples, multiplier=1.1):
    """Return a passing record, or raise ValueError naming every failed bound.

    Inputs are lower_unsafe/upper_unsafe metric dictionaries. SF/BM vectors
    retain the supplied station order. Upper GM has no constraint and is ignored.
    Candidate reserves are ``multiplier*(measured unsafe + reference transfer)``.
    """
    if (type(calibration_seed) is not int or type(holdout_seed) is not int
            or min(calibration_seed, holdout_seed) < 0 or calibration_seed == holdout_seed):
        raise ValueError("qualification requires distinct nonnegative integer seeds")
    if (type(calibration_samples) is not int or type(holdout_samples) is not int
            or min(calibration_samples, holdout_samples) <= 0):
        raise ValueError("qualification requires nonzero calibration and holdout samples")
    if multiplier != 1.1:
        raise ValueError("qualification requires the default1.1 multiplier")
    stations = np.asarray(station_x_m, dtype=float)
    if (stations.ndim != 1 or not np.isfinite(stations).all()
            or np.any(np.diff(stations) <= 0)):
        raise ValueError("qualification requires finite ordered station positions")
    failures = []
    excess_table = {}
    validated_holdout = {}
    for side in ("lower_unsafe", "upper_unsafe"):
        excess_table[side] = {}
        validated_holdout[side] = {}
        metrics = ("gm_m", "trim_m", "list_rad", "draught_m", "sf_n", "bm_nm",
                   "draught_aft_m", "draught_forward_m")
        for metric in metrics:
            if metric == "gm_m" and side == "upper_unsafe":
                continue
            values = []
            for source in (measured_unsafe, reference_transfer, holdout_unsafe):
                try:
                    value = np.asarray(source[side][metric], dtype=float)
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"missing qualification {side}/{metric}") from exc
                shape = stations.shape if metric in ("sf_n", "bm_nm") else ()
                if value.shape != shape or not np.isfinite(value).all() or np.any(value < 0):
                    raise ValueError(f"invalid qualification {side}/{metric}")
                values.append(value)
            excess = values[2]-multiplier*(values[0]+values[1])
            validated_holdout[side][metric] = values[2].tolist()
            excess_table[side][metric] = excess.tolist()
            if excess.ndim == 0:
                if excess > 0:
                    failures.append(f"{side}/{metric}: excess={float(excess):.12g}")
            else:
                for index in np.flatnonzero(excess > 0):
                    failures.append(f"{side}/{metric}@{stations[index]:g}: excess={excess[index]:.12g}")
    if failures:
        raise ValueError("independent holdout failed: "+"; ".join(failures))
    return dict(format=1, independent_holdout_passed=True, multiplier=multiplier,
        calibration_seed=calibration_seed, holdout_seed=holdout_seed,
        calibration_samples=calibration_samples, holdout_samples=holdout_samples,
        station_x_m=stations.tolist(), max_excess=excess_table,
        holdout_unsafe=validated_holdout)


def validate_holdout_record(record, measured, transfer, *, station_x_m):
    """Recompute an optional bank holdout record; a claimed pass is insufficient."""
    if not isinstance(record, dict) or record.get("format") != 1:
        raise ValueError("invalid independent holdout qualification")
    if "station_x_m" in record and not np.array_equal(record["station_x_m"], station_x_m):
        raise ValueError("independent holdout qualification station mismatch")
    if "multiplier" in record and record["multiplier"] != 1.1:
        raise ValueError("independent holdout qualification multiplier mismatch")
    seeds = record.get("calibration_seeds")
    if seeds is not None:
        if (not isinstance(seeds,list) or not seeds
                or any(type(seed) is not int or seed < 0 for seed in seeds)
                or len(set(seeds)) != len(seeds)
                or record.get("calibration_seed") not in seeds
                or record.get("holdout_seed") in seeds):
            raise ValueError("independent holdout seed overlaps or lacks valid calibration seeds")
    try:
        result = qualify_holdout(measured, transfer, record["holdout_unsafe"],
            station_x_m=station_x_m, calibration_seed=record["calibration_seed"],
            holdout_seed=record["holdout_seed"], calibration_samples=record["calibration_samples"],
            holdout_samples=record["holdout_samples"])
    except KeyError as exc:
        raise ValueError("missing independent holdout qualification inputs") from exc
    if record.get("independent_holdout_passed") is not True:
        raise ValueError("independent holdout qualification lacks passing status")
    if seeds is not None:
        result["calibration_seeds"] = list(seeds)
    return result
