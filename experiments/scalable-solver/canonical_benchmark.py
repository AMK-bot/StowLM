"""Resumable train/validation-only scalable gold-plan measurements.

D11 is lexicographic: hard violations, shut-outs, weighted nonproductive
lifts, then safety margins. A lower bound proves unavoidable work; the
reported gap is (objective - lower bound) / objective (see stowage.bounds).
Only clean plans passing ballast, the full checker and public-mask replay
are written as approved artifacts. No test instance file is ever opened.
Each process uses one core; orchestration/admission belongs to the caller.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import logging
import math
import os
import signal
import traceback
from pathlib import Path
from statistics import mean
import time

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

RUNGS = ("tiny", "small", "medium", "full", "dense")
SCOPE = Path(__file__).resolve().parent
ROOT = SCOPE.parent.parent

from stowage.solver.scalable_oracle import SearchDeadline  # noqa: E402 - thread limits precede project imports


@contextmanager
def search_deadline(seconds):
    """Bound this standalone POSIX benchmark's planner call, including setup.

    The alarm belongs only to this process. It changes no constraint or worker
    setting; a timed-out model/plan is never accepted. C calls deliver the
    Python signal when they return to the interpreter.
    """
    previous = signal.getsignal(signal.SIGALRM)
    timer = signal.getitimer(signal.ITIMER_REAL)
    if timer[0]:
        raise RuntimeError("benchmark requires ownership of its process alarm")
    def expired(signum, frame):
        stack = [{"path": str(Path(item.filename).relative_to(ROOT)),
                  "function": item.name, "line": item.lineno}
                 for item in traceback.extract_stack(frame)
                 if Path(item.filename).is_relative_to(ROOT)]
        raise SearchDeadline(stack[-16:])
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hash() -> str:
    """Hash all executable model/physics/planner inputs, not only solver code."""
    checksum = hashlib.sha256()
    paths = [Path(__file__).resolve()]
    for folder, pattern in ((ROOT / "src/stowage", "*.py"), (ROOT / "params", "*.toml"),
                            (ROOT / "src/stowage/rules", "*.toml"),
                            (ROOT / "src/stowage/rules/imdg/data", "*.json")):
        paths.extend(folder.rglob(pattern))
    for path in sorted(set(paths)):
        checksum.update(str(path.relative_to(ROOT)).encode())
        checksum.update(path.read_bytes())
    return checksum.hexdigest()


def select(manifest: dict, rungs: list[str], splits: list[str], generators: list[str],
           count: int, instance_name: str | None = None) -> list[dict]:
    """Selection uses manifest metadata exclusively, filtering split first."""
    selected = []
    for rung in rungs:
        candidates = []
        # Canonical v2 calls its 400-box feeder workload realistic_500.
        source_profile = rung
        if rung == "dense" and not any(group.get("profile") == "dense"
                                       for group in manifest["groups"].values()):
            source_profile = "realistic_500"
        for group_name, group in sorted(manifest["groups"].items()):
            if group.get("profile", group_name.split("/")[-1]) != source_profile:
                continue
            if group_name.split("/")[0] not in generators:
                continue
            for shard in group["shards"]:
                if shard.get("split") not in splits or shard.get("split") not in ("train", "val"):
                    continue
                relative = str(Path(group_name) / shard["instance"])
                if instance_name and instance_name not in (relative, shard["instance"]):
                    continue
                candidates.append({"rung": rung, "source_profile": source_profile,
                                   "relative": relative, "shard": shard})
        candidates.sort(key=lambda item: (item["relative"], item["shard"]["seed"]))
        selected.extend(candidates[:count])
    return selected


def replay(instance, plan) -> dict:
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    from stowage.frozen import require_clean
    space = get_action_space()
    engine = make_feasibility_engine(instance, space)
    actions = space.from_plan(instance, plan)
    for index, action in enumerate(actions):
        if not engine.is_legal(action):
            dead = engine.dead_end()
            return {"status": "failed", "action_index": index,
                    "dead_end": asdict(dead) if dead else None}
        engine.push(action)
    if not engine.is_complete():
        return {"status": "failed", "reason": "replay incomplete"}
    require_clean(engine.to_plan(), what="canonical scalable mask replay")
    return {"status": "passed", "actions": len(actions), "stats": asdict(engine.stats())}


def measure(instance, planner, args, plan_path: Path) -> dict:
    from stowage.bounds import lower_bound
    from stowage.checker import check
    from stowage.frozen import require_clean
    from stowage.io import save_plan
    from stowage.planner import finalize
    started = time.monotonic()
    row = {"approved": False, "checker_passed": False, "status": "error",
           "mask": {"status": "no_plan"}, "objective_d11": None, "gap": None,
           "lifts": None, "timing_s": {}, "approved_plan": None}
    try:
        require_clean(instance, what="canonical scalable input")
        bound = lower_bound(instance)
        row["lower_bound"] = bound.model_dump(mode="json")
        phase = time.monotonic()
        with search_deadline(args.time_limit_s):
            result = planner.plan(instance, seed=args.seed, time_limit_s=args.time_limit_s)
        row["timing_s"]["search"] = time.monotonic() - phase
        row.update(raw_status=result.status, solver_metrics=result.metrics,
                   solver_wall_time_s=result.wall_time_s, message=result.message)
        phase = time.monotonic()
        result = finalize(instance, result, time_limit_s=args.ballast_limit_s)
        row["timing_s"]["finalize"] = time.monotonic() - phase
        row.update(status=result.status, final_message=result.message)
        if result.plan is not None:
            phase = time.monotonic()
            report = check(instance, result.plan)
            row["timing_s"]["checker"] = time.monotonic() - phase
            require_clean(instance, result, result.plan, report, what="canonical scalable result")
            row["checker_passed"] = report.passed
            row["violations"] = [v.model_dump(mode="json") for v in report.violations]
            row["checker_parts"] = {name: part.model_dump(mode="json")
                                    for name, part in report.parts.items()}
            weighted = report.count.weighted_nonproductive if report.count is not None else None
            row["objective_d11"] = {
                "hard_violations": sum(v.severity == "hard" for v in report.violations),
                "shut_out": len(report.shut_out), "weighted_nonproductive": weighted,
                "margins": {name: part.margins for name, part in report.parts.items()},
            }
            if report.count is not None:
                row["lifts"] = report.count.model_dump(mode="json")
                row["lifts"]["total_lifts_now"] = report.count.total_lifts_now
                row["lifts"]["future_restow_lifts"] = sum(x.restow_lifts for x in report.count.future)
                row["lifts"]["future_hatch_cover_lifts"] = sum(x.hatch_cover_lifts for x in report.count.future)
            if report.passed and weighted is not None:
                row["gap"] = ((weighted - bound.weighted) / weighted if weighted else
                              (0.0 if bound.weighted == 0 else None))
            if result.status == "ok":
                phase = time.monotonic()
                row["mask"] = replay(instance, result.plan)
                row["timing_s"]["mask"] = time.monotonic() - phase
            else:
                row["mask"] = {"status": "incomplete"}
            row["approved"] = (result.status == "ok" and report.passed
                               and row["mask"]["status"] == "passed")
            if row["approved"]:
                save_plan(result.plan, plan_path)
                row["approved_plan"] = str(plan_path.relative_to(SCOPE))
                row["approved_plan_sha256"] = digest(plan_path)
    except SearchDeadline as exc:
        row.update(status="timeout", raw_status="timeout", search_deadline_enforced=True,
                   message="Search deadline reached inside planner/oracle initialization or search")
        row["search_deadline_stack"] = exc.args[0] if exc.args else []
        row["timing_s"]["search"] = time.monotonic() - phase
    except KeyboardInterrupt:
        row.update(status="interrupted", message="Recorded benchmark interrupted; no plan approved")
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["approved"] = False
    row["timing_s"]["total"] = time.monotonic() - started
    row["approved_plans_hour_core"] = (3600 / row["timing_s"]["total"] if row["approved"] else 0.0)
    return row


def summary(rows: list[dict], rungs: list[str]) -> str:
    lines = ["| Rung (source profile) | n | Boxes actual | Approved | Objective D11 L3¹ | Gap¹ | Search s | Pipeline s | Checker | Mask | Plans/hour/core |",
             "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for rung in rungs:
        group = [row for row in rows if row["rung"] == rung]
        if not group:
            lines.append(f"| {rung} | 0 | — | unmeasured | — | — | — | — | — | — | — |")
            continue
        n = len(group)
        measurements = [row["measurement"] for row in group]
        approved = [row for row in measurements if row["approved"]]
        objectives = [row["objective_d11"]["weighted_nonproductive"] for row in approved]
        gaps = [row["gap"] for row in approved if row["gap"] is not None]
        objective = f"{mean(objectives):.3f}" if objectives else "—"
        gap = f"{mean(gaps):.1%}" if gaps else "—"
        total = sum(row["timing_s"]["total"] for row in measurements)
        search = mean(row["timing_s"].get("search", 0.0) for row in measurements)
        boxes = ",".join(str(value) for value in sorted({row["boxes"] for row in group}))
        source_profiles = ",".join(sorted({row["source_profile"] for row in group}))
        label = rung if source_profiles == rung else f"{rung} ({source_profiles})"
        lines.append(f"| {label} | {n} | {boxes} | {len(approved)}/{n} | {objective} | {gap} | {search:.3f} | {total/n:.3f} | "
                     f"{sum(row['checker_passed'] for row in measurements)}/{n} | "
                     f"{sum(row['mask']['status'] == 'passed' for row in measurements)}/{n} | "
                     f"{3600*len(approved)/total if total else 0:.3f} |")
    return "\n".join(lines) + ("\n\n¹ Objective and gap means over approved plans only. Pipeline includes lower bound, search, ballast, "
                               "checker and public mask replay; excludes input loading. Each process reserves one core. "
                               "Unmeasured means no matching train/val manifest shards selected.\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "experiments/instances-v2/manifest.json")
    parser.add_argument("--output", type=Path, default=SCOPE / "canonical-results")
    parser.add_argument("--rungs", nargs="+", default=list(RUNGS))
    parser.add_argument("--split", nargs="+", choices=("train", "val"), default=["train", "val"])
    parser.add_argument("--generators", nargs="+", choices=("random_yard", "simulated_yard"), default=["random_yard"])
    parser.add_argument("--count", type=int, default=1, help="Maximum instances per rung")
    parser.add_argument("--instance", help="Exact relative manifest path, or basename (may select both generators)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--time-limit-s", type=float, default=60)
    parser.add_argument("--ballast-limit-s", type=float, default=30)
    parser.add_argument("--beam-width", type=int, default=2)
    parser.add_argument("--branch-factor", type=int, default=4)
    parser.add_argument("--candidate-boxes", type=int, default=16)
    parser.add_argument("--candidate-slots", type=int, default=32)
    parser.add_argument("--max-neighborhoods", type=int, default=12)
    parser.add_argument("--destroy-fraction", type=float, default=.25)
    parser.add_argument("--backtrack-limit", type=int, default=128)
    parser.add_argument("--list-only", action="store_true", help="Show selected metadata without opening any instance")
    parser.add_argument("--progress", action="store_true", help="Log oracle readiness and beam progress")
    args = parser.parse_args(argv)
    if args.progress:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.count < 1 or not (0 < args.time_limit_s <= 600) or not math.isfinite(args.time_limit_s):
        parser.error("positive count and 0 < time-limit-s <= 600 required")
    if not math.isfinite(args.ballast_limit_s) or args.ballast_limit_s <= 0:
        parser.error("ballast-limit-s must be finite and positive")
    args.output = args.output.resolve()
    if not args.output.is_relative_to(SCOPE):
        parser.error("output must stay inside experiments/scalable-solver")
    args.manifest = args.manifest.resolve()
    manifest = json.loads(args.manifest.read_text())
    selection = select(manifest, args.rungs, args.split, args.generators, args.count, args.instance)
    if args.list_only:
        print(json.dumps(selection, indent=2, sort_keys=True))
        return 0
    if args.instance and not selection:
        parser.error("instance has no allowed train/val shard matching the requested selection")
    from stowage.io import load_instance
    from stowage.solver.scalable import ScalableConfig, ScalableSolver
    from stowage.versions import component_versions
    config = ScalableConfig(default_time_limit_s=args.time_limit_s, beam_width=args.beam_width,
                            branch_factor=args.branch_factor, candidate_boxes=args.candidate_boxes,
                            candidate_slots=args.candidate_slots, max_neighborhoods=args.max_neighborhoods,
                            destroy_fraction=args.destroy_fraction, backtrack_limit=args.backtrack_limit)
    planner = ScalableSolver(config)
    code_hash = source_hash()
    common = {"manifest_sha256": digest(args.manifest), "source_sha256": code_hash,
              "component_versions": component_versions(), "solver_version": planner.version,
              "solver_config": asdict(config), "seed": args.seed,
              "ballast_limit_s": args.ballast_limit_s, "cores": 1}
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for selected in selection:
        shard = selected["shard"]
        assert shard["split"] in ("train", "val")
        path = (args.manifest.parent / selected["relative"]).resolve()
        if not path.is_relative_to(args.manifest.parent):
            raise ValueError("manifest instance path escapes manifest folder")
        input_hash = digest(path)
        if input_hash != shard["sha256"]:
            raise ValueError(f"instance hash mismatch: {selected['relative']}")
        identity = {**common, "relative": selected["relative"], "split": shard["split"],
                    "input_sha256": input_hash, "spec_key": shard.get("spec_key")}
        name = selected["relative"].replace("/", "__").removesuffix(".json")
        target = args.output / f"{name}.result.json"
        plan_target = args.output / f"{name}.approved-plan.json"
        if target.exists():
            row = json.loads(target.read_text())
            if row["identity"] != identity:
                raise ValueError(f"resume identity mismatch: {target}; choose a fresh --output")
            if row["measurement"]["approved"] and (not plan_target.exists() or
                    digest(plan_target) != row["measurement"]["approved_plan_sha256"]):
                raise ValueError(f"approved artifact missing or changed: {plan_target}")
        else:
            instance = load_instance(path)
            row = {"rung": selected["rung"], "source_profile": selected["source_profile"],
                   "boxes": len(instance.load_list),
                   "instance_id": instance.id, "identity": identity,
                   "measurement": measure(instance, planner, args, plan_target)}
            temporary = target.with_suffix(".partial.json")
            temporary.write_text(json.dumps(row, indent=2, sort_keys=True, allow_nan=False) + "\n")
            temporary.replace(target)
        rows.append(row)
        print(json.dumps({"instance": selected["relative"], "status": row["measurement"]["status"],
                          "approved": row["measurement"]["approved"]}), flush=True)
        (args.output / "summary.md").write_text(summary(rows, args.rungs))
        if row["measurement"]["status"] == "interrupted":
            return 130
    table = summary(rows, args.rungs)
    (args.output / "summary.md").write_text(table)
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
