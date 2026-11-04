"""Empirical directional envelopes for a predictor residual ``linear - exact``.

A lower bound is unsafe when the predictor is too high; an upper bound is
unsafe when it is too low. The opposite signs describe conservative bias.
Report both instead of treating safe bias as an extra required safety margin.
These are measured sample maxima and percentiles, not proven uniform bounds.
Source: elementary one-sided error propagation: exact = linear - residual.
"""
from __future__ import annotations

import numpy as np


def signed_statistics(residuals):
    """Summarise arrays whose first axis contains samples, preserving stations.

    Return maximum dictionaries at the top level and identical dictionaries
    under ``p99``. Values are JSON-ready scalars/lists. GM and list inputs must
    be residuals of the affine solver with its maximum-FSM reserve, so that
    deliberate reserve is reported as conservatism rather than charged twice.
    ``LinearErrors`` separately retains absolute point-prediction errors.
    """
    directions = {name: {} for name in ("lower_unsafe", "upper_unsafe",
                                       "lower_conservatism", "upper_conservatism")}
    result = {**directions, "p99": {name: {} for name in directions}}
    for metric, samples in residuals.items():
        values = np.asarray(samples, dtype=float)
        if values.ndim < 1 or values.shape[0] == 0 or not np.isfinite(values).all():
            raise ValueError(f"signed residuals need finite nonempty samples: {metric}")
        positive, negative = np.maximum(values, 0.), np.maximum(-values, 0.)
        for name, one_sided in (("lower_unsafe", positive), ("upper_unsafe", negative),
                                ("lower_conservatism", negative), ("upper_conservatism", positive)):
            result[name][metric] = np.max(one_sided, axis=0).tolist()
            result["p99"][name][metric] = np.percentile(one_sided, 99, axis=0).tolist()
    return result


def solver_signed_statistics(residuals, *, exact_list_rad, affine_gm_m, gm_min_m):
    """Condition list envelopes on samples capable of violating each bound.

    A positive upper heel limit can only be exceeded by a positive exact heel;
    a negative lower limit can only be exceeded by a negative exact heel. A
    sample below the affine GM minimum already fails the GM row and cannot be
    accepted by the solver. Thus the opposite heel branch's safe bias need not
    become this bound's error margin. The GM minimum must be positive, as the
    affine list rows require a positive restoring moment.

    Percentiles use the conditional branch population, including zero errors.
    An empty branch reports zero and an explicit sample count; it is not a
    proof of coverage. All other metric statistics are unchanged.
    """
    result = signed_statistics(residuals)
    try:
        minimum = float(gm_min_m)
        heel = np.asarray(exact_list_rad, dtype=float)
        gm = np.asarray(affine_gm_m, dtype=float)
        error = np.asarray(residuals["list_rad"], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("conditional list statistics need finite matching scalar samples") from exc
    if (not np.isfinite(minimum) or minimum <= 0 or error.ndim != 1 or
            heel.shape != error.shape or gm.shape != error.shape or
            not np.isfinite(heel).all() or not np.isfinite(gm).all()):
        raise ValueError("conditional list statistics need matching finite samples and positive GM minimum")
    if any(np.asarray(values).shape[0] != len(error) for values in residuals.values()):
        raise ValueError("conditional list statistics need matching sample counts")
    eligible = gm >= minimum
    lower, upper = eligible & (heel < 0), eligible & (heel > 0)
    for direction, branch, sign in (("lower_unsafe", lower, 1.), ("lower_conservatism", lower, -1.),
                                    ("upper_unsafe", upper, -1.), ("upper_conservatism", upper, 1.)):
        values = np.maximum(sign*error[branch], 0.)
        result[direction]["list_rad"] = float(values.max()) if len(values) else 0.
        result["p99"][direction]["list_rad"] = float(np.percentile(values, 99)) if len(values) else 0.
    result["list_branch_counts"] = {
        "total": int(len(error)), "gm_eligible": int(eligible.sum()),
        "gm_rejected": int((~eligible).sum()), "lower": int(lower.sum()),
        "upper": int(upper.sum()), "zero_exact_heel": int((eligible & (heel == 0)).sum()),
    }
    return result
