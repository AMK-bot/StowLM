"""Read-only replay of archived tiny LP; never produces accepted instances.

A linear program relaxes discrete boxes to divisible cell masses. An infeasible
relaxation proves a conflict in these saved rows, without establishing anything
about exact nonlinear feasibility. Source: Bertsimas and Tsitsiklis (1997),
Introduction to Linear Optimization, linear programming relaxations.
"""
from __future__ import annotations

import json
from pathlib import Path
import warnings

import numpy as np
from scipy.optimize import OptimizeWarning, linprog


ROOT = Path(__file__).resolve().parents[3]
ARCHIVE = ROOT / "instances/v3-tiny-c2-lp.npz"


def replay(path=ARCHIVE):
    with np.load(path, allow_pickle=False) as data:
        names = list(data["names"])
        a = np.hstack((data["A"], data["At"]))
        c = data["A"].shape[1]
        bounds = [(0., float(data["u0"]))] * c + list(zip(data["tank_lo"], data["tank_hi"]))
        equality = np.r_[np.ones(c), np.zeros(a.shape[1]-c)][None]
        trim, aft = names.index("trim"), names.index("draught_aft")

        def solve(rows, objective=None):
            aa, bb = [], []
            for j in rows:
                if np.isfinite(data["hi"][j]):
                    aa.append(a[j]); bb.append(data["hi"][j]-data["const"][j])
                if np.isfinite(data["lo"][j]):
                    aa.append(-a[j]); bb.append(data["const"][j]-data["lo"][j])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", OptimizeWarning)
                return linprog(np.zeros(a.shape[1]) if objective is None else objective,
                    A_ub=np.asarray(aa), b_ub=np.asarray(bb), A_eq=equality,
                    b_eq=[float(data["remaining_mass"])], bounds=bounds,
                    method="highs", options={"threads": 1, "random_seed": 0,
                    "primal_feasibility_tolerance": 1e-9})

        minimum = solve([trim], a[aft])
        maximum = solve([aft], -a[trim])
        result = {
            "archive": str(path.relative_to(ROOT.parent)),
            "full_feasible": bool(solve(range(len(names))).success),
            "trim_aft_pair_feasible": bool(solve([trim, aft]).success),
            "without_draught_feasible": bool(solve([j for j, n in enumerate(names)
                                                if not n.startswith("draught")]).success),
            "trim_lower_m": float(data["lo"][trim]),
            "aft_upper_m": float(data["hi"][aft]),
            "minimum_aft_under_trim_m": float(minimum.fun+data["const"][aft]),
            "maximum_trim_under_aft_m": float(-maximum.fun+data["const"][trim]),
            "diagnostic_only": True,
        }
        result["pair_gap_m"] = result["minimum_aft_under_trim_m"]-result["aft_upper_m"]
        result["trim_lower_drop_needed_with_unchanged_aft_m"] = (
            result["trim_lower_m"]-result["maximum_trim_under_aft_m"])
        return result


if __name__ == "__main__":
    print(json.dumps(replay(), indent=2))
