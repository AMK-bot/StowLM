"""Inspect saved C2 LP constraints; slack/removal is diagnosis, never acceptance.

LP relaxation uses divisible cell masses rather than discrete boxes (Bertsimas
and Tsitsiklis, Introduction to Linear Optimization, 1997). A pair of bounds
that is infeasible even with every other row removed proves a joint conflict
in this particular linear model, not a violation in the nonlinear ship.
"""
import argparse
import json
from pathlib import Path
import warnings

import numpy as np
from scipy.optimize import OptimizeWarning, linprog

parser = argparse.ArgumentParser()
parser.add_argument("--prefix", default="v3-tiny")
args = parser.parse_args()
root = Path(__file__).parent
z = np.load(root / f"{args.prefix}-c2-lp.npz", allow_pickle=False)
names = list(z["names"])
matrix = np.hstack([z["A"], z["At"]])
cells = z["A"].shape[1]
equality = np.r_[np.ones(cells), np.zeros(z["At"].shape[1])][None]
bounds = [(0, float(z["u0"]))] * cells + list(zip(z["tank_lo"], z["tank_hi"]))


def solve(indices, objective=None):
    rows, rhs = [], []
    for j in indices:
        if np.isfinite(z["hi"][j]):
            rows.append(matrix[j]); rhs.append(z["hi"][j]-z["const"][j])
        if np.isfinite(z["lo"][j]):
            rows.append(-matrix[j]); rhs.append(z["const"][j]-z["lo"][j])
    a, b = np.array(rows), np.array(rhs)
    scale = np.maximum(1., np.abs(a).max(axis=1))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OptimizeWarning)
        return linprog(np.zeros(matrix.shape[1]) if objective is None else objective,
            A_ub=a/scale[:, None], b_ub=b/scale,
            A_eq=equality, b_eq=[float(z["remaining_mass"])], bounds=bounds,
            method="highs", options={"threads": 1, "random_seed": 0,
                                     "primal_feasibility_tolerance": 1e-9})


trim, aft = names.index("trim"), names.index("draught_aft")
full = solve(range(len(names)))
without_draught = solve([j for j, name in enumerate(names) if not name.startswith("draught")])
pair = solve([trim, aft])
minimum = solve([trim], matrix[aft])
maximum = solve([aft], -matrix[trim])
summary = dict(full_lp_status=full.message,
               without_draught_feasible=bool(without_draught.success),
               trim_aft_pair_feasible=bool(pair.success),
               draught_trim_group_feasible=bool(solve([j for j, name in enumerate(names)
                    if name.startswith("draught") or name == "trim"]).success),
               trim_lower_m=float(z["lo"][trim]), aft_upper_m=float(z["hi"][aft]))
if minimum.success:
    summary["minimum_aft_with_trim_m"] = float(minimum.fun+z["const"][aft])
if maximum.success:
    summary["maximum_trim_with_aft_m"] = float(-maximum.fun+z["const"][trim])
(root / f"{args.prefix}-lp-diagnosis.json").write_text(json.dumps(summary, indent=2)+"\n")
print(json.dumps(summary), flush=True)
