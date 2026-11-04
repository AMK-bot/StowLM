# Tiny-chain gold plans (plan rule "ship tiny first"; feeds 7.1 sft_data and the 8.1 quick SFT).
#
# Two modes:
#   strict  - the real path: solver.exact (A* over the v2 feasibility engine) -> planner.finalize -> checker.check,
#             keep only checker-passing plans. This is what gold plans must be (6.1). On 27 Oct no tiny v2 instance
#             gets through: building the engine fails the 3.7 validity-band sweep (C2 unavailable -> dead end at
#             depth 0, ~170 s), and with C2 off the light ship fails IS Code weather / containership checks and
#             finish_ballast returns not_found (see README.md).
#   standin - TINY RUN ONLY, STUB-TAINTED. The feasibility engine runs with C2 (global stability screen) switched
#             off (stub of feasibility.make_engine, extra global_screen=0); local L1/L2 checks and C1 stay on. Plans
#             come from the scalable solver (4.7) over that engine; its weighted objective is compared with the 3.6
#             lower bound, so gap 0 = optimal in weighted lifts (relative to the engine model). The exact solver's
#             relaxed oracle (solve_exact(check_physics=False), UNSHIPPABLE) was tried first and times out (30 s) on
#             most tiny instances that need one relocation. Each plan is replayed action by action through the
#             engine (every action in legal_actions() at its state). Optionally finalize+check is run and recorded
#             (it fails today). Provenance carries the stubs, so require_clean refuses these plans outside tiny runs.
#
# Usage:
#   uv run python experiments/tiny-chain/gold.py --mode standin --splits train val --workers 4 [--check-sample 12]
#   uv run python experiments/tiny-chain/gold.py --mode strict --splits train --limit 4 --workers 4
#   ... --instances <campaign dir with manifest.json> --groups <group ...> --out <folder>   (see NEXT.md)
# Output: <out>/<mode>/<split>/<instance_id>.plan.json + index.jsonl (resumable); default out experiments/tiny-chain/gold.
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

ROOT = Path(__file__).resolve().parents[2]
# Instance set, groups and output folder: set by --instances/--groups/--out. Kept in environment variables so the
# spawned worker processes (macOS start method "spawn" re-imports this module) see the same values.
INSTANCES = Path(os.environ.get("TINY_CHAIN_INSTANCES", ROOT / "experiments/instances-v2"))
GROUPS = os.environ.get("TINY_CHAIN_GROUPS", "random_yard/tiny simulated_yard/tiny").split()
OUT = Path(os.environ.get("TINY_CHAIN_GOLD_OUT", ROOT / "experiments/tiny-chain/gold"))


def configure(instances: str | None = None, groups: list[str] | None = None, out: str | None = None) -> None:
    """Point this module (and its workers) at another campaign folder (with manifest.json), groups, output."""
    global INSTANCES, GROUPS, OUT
    if instances:
        os.environ["TINY_CHAIN_INSTANCES"] = str((ROOT / instances).resolve())
        INSTANCES = Path(os.environ["TINY_CHAIN_INSTANCES"])
    if groups:
        os.environ["TINY_CHAIN_GROUPS"] = " ".join(groups)
        GROUPS = groups
    if out:
        os.environ["TINY_CHAIN_GOLD_OUT"] = str((ROOT / out).resolve())
        OUT = Path(os.environ["TINY_CHAIN_GOLD_OUT"])
STUB_REASON = ("tiny chain only: 3.7 validity-band sweep fails on v2 tiny instances, so C2 (global stability screen) "
               "is switched off; local L1/L2 checks and C1 stay on; the full checker still judges every plan")
ORACLE_REASON = ("tiny chain only: plan from solver.scalable over the C2-off engine; not checker-passing "
                 "(no ballast found for the light ship)")


def tiny_instances(splits: list[str]) -> list[tuple[str, str]]:
    """(split, path) for every instance of GROUPS in INSTANCES/manifest.json whose split is in ``splits``."""
    m = json.loads((INSTANCES / "manifest.json").read_text())
    rows = []
    for group in GROUPS:
        for shard in m["groups"][group]["shards"]:
            if shard["split"] in splits:
                rows.append((shard["split"], str(INSTANCES / group / shard["instance"])))
    return rows


def c2_off_engine(instance, space, *, config=None):
    """Stub for feasibility.make_engine: the real IncrementalEngine with extra['global_screen']=0."""
    from dataclasses import replace

    from stowage.feasibility import FeasibilityConfig
    from stowage.feasibility._engine import IncrementalEngine

    c = config or FeasibilityConfig()
    return IncrementalEngine(instance, space, replace(c, extra={**c.extra, "global_screen": 0}))


def _check(instance, plan, row):
    from stowage.checker import check
    from stowage.model import PlannerResult
    from stowage.planner import finalize

    t = time.perf_counter()
    res = finalize(instance, PlannerResult(status="ok", plan=plan, planner="tiny-chain.gold", planner_version="0",
                                           seed=0, provenance=plan.provenance), time_limit_s=30.0)
    row["finalize_status"], row["ballast_status"] = res.status, res.metrics.get("ballast_status")
    row["finalize_s"] = round(time.perf_counter() - t, 1)
    if res.plan is not None and res.plan.ballast is not None:
        t = time.perf_counter()
        rep = check(instance, res.plan)
        row["checker_passed"] = rep.passed
        row["check_s"] = round(time.perf_counter() - t, 1)
        row["violations"] = sorted({v.rule_id for v in rep.violations if v.severity == "hard"})
        return res.plan, rep.passed
    row["checker_passed"] = False
    row["violations"] = [f"no ballast: {res.message}"[:200]]
    return plan, False


def solve_one(mode: str, split: str, path: str, do_check: bool, time_limit: float) -> dict:
    import stowage.interfaces  # noqa: F401  (registers every frozen interface)
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    from stowage.frozen import stub_override
    from stowage.io import load_instance, save_plan
    from stowage.model.provenance import Provenance
    from stowage.moves import count_moves

    instance = load_instance(path)
    row = {"instance": instance.id, "path": os.path.relpath(path, ROOT), "split": split, "mode": mode,
           "diagnostic": True, "shippable": False,
           "n_boxes": len(instance.load_list), "vessel": instance.vessel.id}
    t0 = time.perf_counter()
    try:
        if mode == "strict":
            from stowage.solver.exact import ExactPlanner

            result = ExactPlanner().plan(instance, seed=0, time_limit_s=time_limit)
            row.update(planner_status=result.status, message=(result.message or "")[:300],
                       solve_s=round(time.perf_counter() - t0, 1))
            if result.status != "ok" or result.plan is None:
                return row
            plan, passed = _check(instance, result.plan, row)
            if passed:  # only checker-passing plans are gold
                row["plan_file"] = str(save_plan(plan, OUT / mode / split / f"{instance.id}.plan.json").relative_to(ROOT))
            return row

        from stowage.bounds import lower_bound
        from stowage.solver.scalable import ScalableSolver

        space = get_action_space()
        with stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON):
            solved = ScalableSolver().plan(instance, seed=0, time_limit_s=time_limit)
        lb = lower_bound(instance).weighted
        row.update(solver="solver.scalable", solver_status=solved.status, solver_message=(solved.message or "")[:200],
                   lower_bound=lb, solve_s=round(time.perf_counter() - t0, 1))
        if solved.status != "ok" or solved.plan is None:
            return row
        actions = space.from_plan(instance, solved.plan) if solved.plan.actions is None else \
            [space.decode(a) for a in solved.plan.actions]
        with stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON):
            engine = make_feasibility_engine(instance, space)
            for i, action in enumerate(actions):
                if engine.dead_end() is not None:
                    row["replay"] = f"dead end at {i}: {engine.dead_end().reason[:200]}"
                    return row
                if action not in engine.legal_actions():  # 7.1 criterion: no masked action
                    row["replay"] = f"action {i} not legal: {space.encode(action)}"
                    return row
                engine.push(action)
            if not engine.is_complete():
                row["replay"] = "replay incomplete"
                return row
            plan = engine.to_plan()
        taint = Provenance(stubs={"feasibility.make_engine": STUB_REASON, "tiny_chain.gold_standin": ORACLE_REASON},
                           created_by="tiny-chain.gold.standin", seed=0)
        plan = plan.model_copy(update={"id": f"{instance.id}.tiny-standin.0",
                                       "provenance": plan.provenance.merge(taint, parent_ids=[instance.id])})
        count = count_moves(instance, plan)
        row.update(replay="ok", n_actions=len(actions), lifts_now=count.total_lifts_now,
                   weighted_nonproductive=count.weighted_nonproductive,
                   gap_to_lb=round(count.weighted_nonproductive - lb, 6), stubs=sorted(plan.provenance.stubs))
        if do_check:
            _, passed = _check(instance, plan, row)
        row["plan_file"] = str(save_plan(plan, OUT / mode / split / f"{instance.id}.plan.json",
                                         allow_stubs=True).relative_to(ROOT))
    except Exception as exc:  # recorded, never hidden
        row["error"] = f"{type(exc).__name__}: {exc}"[:400]
        row["traceback"] = traceback.format_exc()[-800:]
    row["wall_s"] = round(time.perf_counter() - t0, 1)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["strict", "standin"], required=True)
    ap.add_argument("--splits", nargs="+", default=["train", "val"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None, help="first N instances per split (manifest order)")
    ap.add_argument("--check-sample", type=int, default=0,
                    help="standin: run finalize+check on all val and this many sampled train instances")
    ap.add_argument("--time-limit", type=float, default=20.0)
    ap.add_argument("--redo-missing", action="store_true", help="retry index rows that produced no plan")
    ap.add_argument("--instances", help="campaign folder with manifest.json (default experiments/instances-v2)")
    ap.add_argument("--groups", nargs="+", help="manifest groups (default random_yard/tiny simulated_yard/tiny)")
    ap.add_argument("--out", help="output folder (default experiments/tiny-chain/gold)")
    args = ap.parse_args()
    configure(args.instances, args.groups, args.out)

    rows = tiny_instances(args.splits)
    if args.limit:
        per = {}
        rows = [r for r in rows if per.setdefault(r[0], []).append(r) or len(per[r[0]]) <= args.limit]
    rng = random.Random(0)
    train = [r for r in rows if r[0] == "train"]
    checked = {p for s, p in rows if s != "train"} | {p for _, p in rng.sample(train, min(args.check_sample, len(train)))}
    index = OUT / args.mode / "index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    old = [json.loads(l) for l in index.read_text().splitlines()] if index.exists() else []
    if args.redo_missing:
        old = [r for r in old if r.get("plan_file") or args.mode == "strict"]
        index.write_text("".join(json.dumps(r) + "\n" for r in old))
    done = {r["path"] for r in old}
    todo = [(s, p) for s, p in rows if os.path.relpath(p, ROOT) not in done]
    print(f"{len(rows)} instances, {len(todo)} to do, checking {len(checked)}", flush=True)
    with ProcessPoolExecutor(args.workers) as pool, open(index, "a") as f:
        futs = [pool.submit(solve_one, args.mode, s, p, args.mode == "strict" or p in checked, args.time_limit)
                for s, p in todo]
        for n, fut in enumerate(as_completed(futs), 1):
            row = fut.result()
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(n, row["instance"], row.get("replay") or row.get("planner_status"), row.get("checker_passed"),
                  row.get("error", ""), flush=True)


if __name__ == "__main__":
    sys.exit(main())
