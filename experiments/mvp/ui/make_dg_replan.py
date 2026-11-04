"""Pre-recorded solver replan of the feeder_1700 showcase call under one dangerous-goods instruction (D144).

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONPATH=.:src \
        nice uv run python experiments/mvp/ui/make_dg_replan.py

Re-runnable: a finished solve in ``instances/<id>/result.json`` is kept (``--force`` replans); everything derived
from it (fresh checker, instruction check, manifest) is recomputed each run.

Why not the instruction the order named ("keep class 3 at least 1 bay from bay 1"): this vessel slice has only bays
01 and 03, so every 40-ft slot (bay 002) spans bay 01, and class 3 box QZXU0000117 is a 40-ft box. The instruction
leaves it no slot on this ship, although ``capacity_shortfall()`` (a cell count) sees nothing wrong. Stage ``class3``
below measures this and records it in ``config.json``. The nearest instruction that is satisfiable on the same call
is "keep class 8 at least 1 bay from bay 3": it flags the one class 8 box, a 20-ft box the gold plan puts in bay 03.

Why not ``replan()``'s solver hook alone: it patches ``securing_geometry_violation`` in the scalable, exact and
sequence modules. ``twostep`` and ``twostep_terminal`` hold their own imported reference to that function, so the
two-step planner gold is built on never sees it. Stage ``hook_probe`` measures this. Instead the two-step planner's
feasibility engine is wrapped, for the duration of this planner call only, in ``llm.instructions.mask_filter``'s
``InstructionEngine``: the same filter StowLM's mask uses. ``_preplan`` and ``terminal_plan`` both get their engine
through the module name patched here, and ``replan()``'s hook still covers the scalable constructor fallback.

This is a SOLVER plan, never StowLM. The sentence -> constraint step here is authored by hand, not a Spark parse.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[var] = "1"
os.environ.pop("STOWAGE_BALLAST_V4", None)

INPUT = ROOT / "experiments/mvp/corpus/showcase/feeder_1700-small.json"
INSTANCE_ID = "random.small.29000201.0.7"
GOLD = ROOT / f"experiments/mvp/runs/showcase-v2-gold/instances/{INSTANCE_ID}/result.json"
OUT = ROOT / "experiments/mvp/runs/instruction-replan-dg"
SEED = 29000201
SENTENCE = "Keep class 8 at least 1 bay from bay 3."
CONSTRAINT = {"type": "dg_keep_away", "imdg_class": "8", "min_bays": 1, "away_from": {"kind": "bay", "bay": 3}}
ORDERED_SENTENCE = "keep class 3 at least 1 bay from bay 1"
ORDERED_CONSTRAINT = {"type": "dg_keep_away", "imdg_class": "3", "min_bays": 1, "away_from": {"kind": "bay", "bay": 1}}
PLANNER = "solver.gold_warmstart.instruction"
LABEL = "Solver replan under the instruction"
METHOD = ("gold warm start (two-step + scoped V4 ballast + yard LNS) with the two-step feasibility engine wrapped in "
          "llm.instructions.mask_filter.InstructionEngine; solver plan, not StowLM")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False))
    tmp.replace(path)


@contextmanager
def scoped_v4():
    """The gold producer's environment: V4 ballast for the API call only, restored before any independent check."""
    previous = os.environ.get("STOWAGE_BALLAST_V4")
    os.environ["STOWAGE_BALLAST_V4"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("STOWAGE_BALLAST_V4", None)
        else:
            os.environ["STOWAGE_BALLAST_V4"] = previous


@contextmanager
def filtered_twostep(constrained, counters: dict):
    """Two-step's engines (pre-stow and terminal) exclude the placements the instruction forbids; restored on exit."""
    from llm.instructions.mask_filter import wrap_engine
    from stowage.solver import twostep

    original = twostep.make_feasibility_engine

    def factory(instance, space=None, **kwargs):
        counters["engines_wrapped"] = counters.get("engines_wrapped", 0) + 1
        return wrap_engine(original(instance, space, **kwargs), constrained)

    twostep.make_feasibility_engine = factory
    try:
        yield
    finally:
        twostep.make_feasibility_engine = original


class ConstrainedGoldPlanner:
    """``solve_gold`` under the instruction. ``replan()`` adds its hook, the full checker and the violations check."""

    name, sets_ballast, deterministic = PLANNER, True, False

    def __init__(self, constrained, counters: dict):
        from stowage.solver.gold_warmstart import VERSION
        self.version, self.constrained, self.counters = VERSION, constrained, counters

    def plan(self, instance, *, seed=0, time_limit_s=None):
        from stowage.solver.gold_warmstart import GoldWarmstartPlanner
        with scoped_v4(), filtered_twostep(self.constrained, self.counters):
            return GoldWarmstartPlanner().plan(instance, seed=seed, time_limit_s=time_limit_s)


def stage_class3(instance) -> dict:
    """Measured: the ordered class 3 instruction leaves the 40-ft class 3 box no slot on this ship."""
    from app.instruct import boxes_without_a_slot
    from llm.instructions.apply import apply, describe
    from llm.instructions.schema import DgKeepAway
    from stowage.conventions import parse_slot

    constrained = apply(instance, DgKeepAway.model_validate(ORDERED_CONSTRAINT))
    return {"sentence": ORDERED_SENTENCE, "constraint": ORDERED_CONSTRAINT, "applied": describe(constrained),
            "capacity_shortfall": constrained.capacity_shortfall(),
            "boxes_without_a_slot": boxes_without_a_slot(constrained),
            "vessel_bays": sorted({parse_slot(c.id).bay for c in instance.vessel.cells}),
            "verdict": "unsatisfiable on this ship: every 40-ft slot spans bay 01"}


def stage_hook_probe(constrained, seed: int) -> dict:
    """Measured: replan()'s hook alone does not constrain two-step (planner only, no ballast or checker)."""
    from llm.instructions.replan import constraint_rule
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.twostep import TwoStepPlanner

    started = time.monotonic()
    with constraint_rule(constrained):
        result = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={"global_screen": 0})).plan(
            constrained.instance, seed=seed, time_limit_s=120.)
    return {"status": result.status, "violations": constrained.violations(result.plan) if result.plan else None,
            "wall_s": round(time.monotonic() - started, 2),
            "reached_two_step": bool(result.plan) and not constrained.violations(result.plan)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--force", action="store_true", help="Replan even if a recorded result exists")
    args = parser.parse_args(argv)

    from llm.instructions.apply import apply, describe
    from llm.instructions.replan import replan
    from llm.instructions.schema import DgKeepAway
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import MovePlan, PlannerResult
    from stowage.moves import apply_plan
    from stowage.solver.gold_warmstart import _mask_replay

    input_sha = sha256(INPUT)
    instance = load_instance(INPUT)
    if instance.id != INSTANCE_ID:
        raise SystemExit(f"unexpected instance {instance.id}")
    constraint = DgKeepAway.model_validate(CONSTRAINT)
    constrained = apply(instance, constraint)
    if constrained.capacity_shortfall():
        raise SystemExit(constrained.capacity_shortfall())
    target = OUT / "instances" / INSTANCE_ID
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(INPUT, target / "instance.json")

    evidence: dict = {"class3": stage_class3(instance)}
    print("class 3 instruction:", evidence["class3"]["boxes_without_a_slot"], flush=True)

    gold_record = json.loads(GOLD.read_text())
    gold_plan = MovePlan.model_validate(gold_record["plan"])
    started = time.monotonic()
    gold_report = check(instance, gold_plan)
    gold_replay = apply_plan(instance, gold_plan)
    evidence["gold"] = {"plan_source": str(GOLD.relative_to(ROOT)), "checker_passed": gold_report.passed,
                        "hard_rules_failed": sorted({v.rule_id for v in gold_report.violations
                                                     if v.severity == "hard"}),
                        "check_s": round(time.monotonic() - started, 1),
                        "weighted": gold_replay.count.weighted_nonproductive,
                        "total_lifts_now": gold_replay.count.total_lifts_now,
                        "instruction_violations": constrained.violations(gold_plan)}
    print("gold fresh:", evidence["gold"], flush=True)

    result_path = target / "result.json"
    wall = None
    if args.force or not result_path.is_file():
        evidence["hook_probe"] = stage_hook_probe(constrained, SEED)
        print("hook probe:", evidence["hook_probe"], flush=True)
        counters: dict = {}
        started = time.monotonic()
        run = replan(constrained, time_limit_s=600., seed=SEED,
                     planner_factory=lambda _limit: ConstrainedGoldPlanner(constrained, counters))
        wall = round(time.monotonic() - started, 1)
        evidence["solve"] = {"wall_s": wall, "precheck": run.precheck, "counters": counters,
                             "status": run.result.status if run.result else None,
                             "message": run.result.message if run.result else None,
                             "replan_shown": run.shown, "replan_violations": run.constraint_violations}
        print("solve:", evidence["solve"], flush=True)
        if run.result is None or run.result.plan is None:
            save(OUT / "last-failure.json", {**evidence, "metrics": run.result.metrics if run.result else None})
            raise SystemExit("no plan under the instruction; see last-failure.json")
        record = run.result.model_dump(mode="json")
        record.update(planner=PLANNER, input_sha256=input_sha)
        save(result_path, record)
        save(OUT / "solve-evidence.json", {k: evidence[k] for k in ("hook_probe", "solve")})
    else:
        print("kept recorded solve", result_path, flush=True)
        evidence.update(json.loads((OUT / "solve-evidence.json").read_text()))

    record = json.loads(result_path.read_text())
    plan = MovePlan.model_validate(record["plan"])
    violations = constrained.violations(plan)
    assert "STOWAGE_BALLAST_V4" not in os.environ, "restore the environment before the independent checker"
    started = time.monotonic()
    report = check(instance, plan)
    check_s = round(time.monotonic() - started, 1)
    replay = apply_plan(instance, plan)
    mask = _mask_replay(instance, plan)
    record["report"] = report.model_dump(mode="json")
    save(result_path, record)
    save(target / "report.json", record["report"])
    count = report.count.model_dump(mode="json") if report.count is not None else None
    if count is not None:
        count["total_lifts_now"] = report.count.total_lifts_now
    honoured = report.passed and not violations and replay.legal
    save(target / "outcome.json", {
        "checker_passed": report.passed, "checker_report_source": "fresh stowage.checker.check in make_dg_replan.py",
        "count": count, "outcome": "own pass" if honoured else "checker fail", "constraint_violations": violations,
        "mask_replay": mask, "status": record["status"], "weighted": replay.count.weighted_nonproductive})
    evidence["replan"] = {"checker_passed": report.passed, "check_s": check_s, "move_replay_legal": replay.legal,
                          "public_mask_replay": mask, "instruction_violations": violations,
                          "weighted": replay.count.weighted_nonproductive,
                          "total_lifts_now": replay.count.total_lifts_now,
                          "hard_rules_failed": sorted({v.rule_id for v in report.violations if v.severity == "hard"})}
    print("replan fresh:", evidence["replan"], flush=True)

    save(OUT / "config.json", {
        "format": "instruction-replan-v1", "instance_id": INSTANCE_ID, "seed": SEED,
        "input": {"path": str(INPUT.relative_to(ROOT)), "sha256": input_sha},
        "instruction": {"sentence": SENTENCE, "constraint": CONSTRAINT,
                        "parse": "authored by hand for this recording; not a Muse Spark parse"},
        "applied": describe(constrained), "method": METHOD, "label": LABEL, "not_stowlm": True,
        "gold_plan": evidence["gold"]["plan_source"], "evidence": evidence,
        "script": "experiments/mvp/ui/make_dg_replan.py"})
    manifest = {
        "command": "experiments/mvp/ui/make_dg_replan.py", "created_at": datetime.now(timezone.utc).isoformat(),
        "entries": [{"checker_passed": report.passed, "entry": f"instances/{INSTANCE_ID}/result.json",
                     "input_sha256": input_sha, "instance_id": INSTANCE_ID,
                     "status": "passed" if honoured else "failed"}],
        "format": "showcase-reference-v1", "method": PLANNER, "planner": PLANNER, "source": "solver-reference",
        "status": "passed" if honoured else "failed", "label": LABEL}
    save(OUT / "manifest.json", manifest)
    (OUT / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.relative_to(OUT)}\n" for p in sorted(target.iterdir())
                                            if p.is_file() and p.name != "SHA256SUMS"))
    print("honoured and passed:", honoured, flush=True)
    return 0 if honoured else 1


if __name__ == "__main__":
    raise SystemExit(main())
