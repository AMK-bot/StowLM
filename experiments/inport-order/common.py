"""Shared paths, roster and limits for the in-port lift-order experiment (w2-reseq part 1).

Every entry point runs inside ``stowage.rules.catalogue.rule_freeze_v1(enabled=True)`` (D61/D62/D64 switch).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"
RUNS = ROOT / "runs"
RESULTS = ROOT / "results"
LOGS = ROOT / "logs"
ROSTER = CASES / "roster.json"

SHIPS = ("feeder_500", "feeder_800", "feeder_1700")
RUNGS = ("tiny", "small", "medium", "full")
PER_RUNG = {"tiny": 2, "small": 2, "medium": 4, "full": 4}
CANDIDATES_PER_SLOT = 3  # replacement specs tried in order if generation rejects

# Planner wall limits (s), passed as planner.plan(..., time_limit_s=...). Scalable small/full match
# experiments/scalable-solver/g1_pilot.py defaults (120/600); medium is between. Two-step's benchmark default is
# 10 s per rung, which is too short on full ships under the current epoch, so it gets the same budget as scalable.
TIME_LIMITS = {
    "scalable": {"tiny": 60.0, "small": 120.0, "medium": 300.0, "full": 600.0},
    "twostep": {"tiny": 60.0, "small": 120.0, "medium": 300.0, "full": 600.0},
    "exact": {"tiny": 60.0, "small": 120.0},
}
METHODS = {"tiny": ("scalable", "twostep", "exact"), "small": ("scalable", "twostep", "exact"), "medium": ("scalable", "twostep"), "full": ("scalable", "twostep")}
FINISHER_LIMIT_S = 60.0  # finalize/finish_ballast search clock (arrival calibration + acceptance are outside it)
D57_ONLY_LIMIT_S = 60.0

THREAD_ENV = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1",
              "MKL_NUM_THREADS": "1", "STOWAGE_IN_PORT_WORKERS": "1"}


def single_thread_env():
    for key, value in THREAD_ENV.items():
        os.environ.setdefault(key, value)


# DIAGNOSTIC switches (default = production behaviour). Part 1 had to set both because the production linear
# policy epoch is stale for the current 500/800/1700 fleet ("production linear policy epoch is stale; requalify"):
# INPORT_GLOBAL_SCREEN=0 -> planners' feasibility engine runs with extra={"global_screen": 0} (C2 linear screen off);
# INPORT_C2_OFF=1 -> medium specs are generated with c2_screened=False (the C2 generation screen needs the same model).
def global_screen():
    return int(os.environ.get("INPORT_GLOBAL_SCREEN", "1"))


def c2_off():
    return os.environ.get("INPORT_C2_OFF", "0") == "1"


def make_planner(method):
    cfg = None
    if not global_screen():
        from stowage.feasibility import FeasibilityConfig
        cfg = FeasibilityConfig(extra={"global_screen": 0})
    if method == "scalable":
        from stowage.solver.scalable import ScalableConfig, ScalableSolver
        return ScalableSolver(ScalableConfig(feasibility_config=cfg)) if cfg else ScalableSolver()
    if method == "twostep":
        from stowage.solver.twostep import TwoStepPlanner
        return TwoStepPlanner(feasibility_config=cfg)
    if method == "exact":
        from stowage.solver.exact import ExactPlanner
        return ExactPlanner(feasibility_config=cfg)
    raise ValueError(method)


def load_roster():
    return json.loads(ROSTER.read_text())


def move_key(move):
    ident = getattr(move, "container_id", None) or getattr(move, "cover_id", None)
    return [move.kind, ident, getattr(move, "src", None), getattr(move, "dst", None)]


def fingerprint(plan):
    import hashlib
    keys = sorted(move_key(m) for m in plan.moves)
    blob = json.dumps(keys, separators=(",", ":"))
    return {"n_moves": len(plan.moves), "multiset_sha256": hashlib.sha256(blob.encode()).hexdigest(),
            "kind_counts": _counts(k[0] for k in keys)}


def order_sha(plan):
    import hashlib
    blob = json.dumps([move_key(m) for m in plan.moves], separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _counts(items):
    out = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return dict(sorted(out.items()))


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    tmp.replace(path)
