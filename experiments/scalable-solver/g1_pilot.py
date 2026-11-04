"""Sequential, one-core G1 qualification on frozen v3 train/validation shards.

A necessary completion screen is not a certificate. Each candidate must pass
ballast finishing, the full checker, and an independent action-by-action replay
through the default public feasibility engine. Place membership is tested in
legal_index(), then checked against decode_index() before push(). Overrides
outside this index contract fail closed. No diagnostic engine switch exists.
Production throughput counts all attempted planning/validation/replay seconds,
including failures; cache warming and input loading are reported separately.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
from stowage.conventions import canonical_vessel_id
import time

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

SCOPE = Path(__file__).resolve().parent
FULL_PROFILES = ("full", "realistic_500", "realistic_800")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False)+"\n")
    temporary.replace(path)


def helpers():
    spec = importlib.util.spec_from_file_location("g1_deadline_helpers", SCOPE / "canonical_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def select(manifest, rungs, count):
    """Read metadata only; reject test specifications before opening inputs."""
    from eval.splits import spec_key, split_of
    selected, availability = [], {}
    for rung in rungs:
        choices = []
        for group_name, group in sorted(manifest["groups"].items()):
            profile = canonical_vessel_id(group.get("profile", group_name.split("/")[-1]))
            if profile not in (FULL_PROFILES if rung == "fullship" else (rung,)):
                continue
            for shard in group.get("shards", []):
                if shard.get("split") not in ("train", "val"):
                    continue
                split_spec = shard.get("split_spec")
                if split_spec is None or split_of(split_spec) != shard["split"] or spec_key(split_spec) != shard["spec_key"]:
                    raise ValueError("manifest frozen split/key disagrees with accepted train/val shard")
                choices.append(dict(rung=rung, profile=profile,
                    relative=str(Path(group_name) / shard["instance"]), shard=shard))
        choices.sort(key=lambda x: (x["profile"], x["relative"], x["shard"]["seed"]))
        availability[rung] = dict(eligible_train_val=len(choices), selected=min(count, len(choices)),
                                  status="available" if choices else "no_accepted_train_val_shards")
        selected.extend(choices[:count])
    return selected, availability


def replay(instance, plan):
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    from stowage.feasibility import make_feasibility_engine
    from stowage.frozen import require_clean
    space = get_action_space()
    engine = make_feasibility_engine(instance, space)  # default production C2 on
    actions = space.from_plan(instance, plan)
    for number, action in enumerate(actions):
        if not isinstance(action, Place):
            return dict(status="failed", action_index=number, reason="action_outside_legal_index_contract",
                        action=space.encode(action), verified_actions=number)
        flat = engine.index_of(action)
        legal = engine.legal_index()
        offset = bisect_left(legal, flat) if flat is not None else len(legal)
        if offset == len(legal) or int(legal[offset]) != flat:
            dead = engine.dead_end()
            return dict(status="failed", action_index=number, reason="action_masked",
                        action=space.encode(action), flat_index=flat, verified_actions=number,
                        dead_end=asdict(dead) if dead is not None else None)
        if engine.decode_index(int(legal[offset])) != action:
            return dict(status="failed", action_index=number, reason="index_decode_mismatch", verified_actions=number)
        engine.push(action)
    if not engine.is_complete():
        return dict(status="failed", reason="incomplete_departure", verified_actions=len(actions))
    require_clean(engine.to_plan(), what="G1 pilot public index replay")
    return dict(status="passed", verified_actions=len(actions), complete=True, stats=asdict(engine.stats()))


def measure(instance, args, budget, helper, approved_path, *, hard_deadline=None):
    from stowage.bounds import lower_bound
    from stowage.checker import check
    from stowage.frozen import require_clean
    from stowage.io import save_plan
    from stowage.moves import apply_plan
    from stowage.planner import finalize
    from stowage.solver.scalable import ScalableSolver
    from stowage.solver.scalable_oracle import SearchDeadline
    started = time.monotonic()
    hard_deadline = hard_deadline or started+budget+args.overhead_reserve_s
    row = dict(status="error", stage="search", approved=False, completed=False,
               checker_passed=False, plan_returned=False, joint_gate_passed=False, replay=dict(status="no_plan"), timings_s={},
               objective=None, gap_to_lb=None, gap_valid=False)
    result = raw = None
    bound = None
    phase = started
    try:
        require_clean(instance, what="G1 pilot input")
        search_budget = min(budget, hard_deadline-started-args.overhead_reserve_s)
        if search_budget <= 0:
            raise SearchDeadline("input loading exhausted reserved search time")
        replay_reserve = args.overhead_reserve_s/2
        guard_budget = min(search_budget+min(5.0, args.overhead_reserve_s/4),
                           hard_deadline-started-replay_reserve)
        row.update(nominal_search_budget_s=search_budget, outer_search_guard_s=guard_budget)
        with helper.search_deadline(guard_budget):
            bound = lower_bound(instance)
            row["lower_bound"] = bound.model_dump(mode="json")
            remaining = search_budget-(time.monotonic()-started)
            if remaining <= 0:
                raise SearchDeadline("lower-bound setup exhausted search budget")
            result = ScalableSolver().plan(instance, seed=args.solver_seed, time_limit_s=remaining)
        row.update(status=result.status, message=result.message, solver_metrics=result.metrics)
        raw = result.plan
        row["plan_returned"] = raw is not None
    except SearchDeadline as exc:
        row.update(status="timeout", timeout_stage="search", error=str(exc))
    except Exception as exc:
        row.update(status="error", error=f"{type(exc).__name__}: {exc}")
    row["timings_s"]["search"] = time.monotonic()-phase
    if raw is None:
        row["timings_s"]["pipeline"] = time.monotonic()-started
        row["case_deadline_overrun_s"] = max(0, time.monotonic()-hard_deadline)
        return row
    replay_plan = raw
    # Reserve half the bounded overhead for independent mask replay, even when
    # ballast or checker fails. Ballast finishing never changes the actions.
    overhead = min(args.overhead_reserve_s, hard_deadline-time.monotonic())
    validation_budget = max(0, overhead/2)
    row["stage"] = "finalize_check"
    phase = time.monotonic()
    try:
        if validation_budget <= 0:
            raise SearchDeadline("no validation reserve remains")
        with helper.search_deadline(validation_budget):
            applied = apply_plan(instance, raw)
            aboard = {p.container_id for p in applied.final_state.ship.placements}
            shutout = len(set(instance.load_list)-aboard)
            cost = applied.count.weighted_nonproductive
            row.update(lifts=applied.count.model_dump(mode="json"), shut_out=shutout,
                raw_candidate_cost=cost,
                raw_candidate_gap_to_lb=(cost-bound.weighted)/cost if cost and bound else (0 if bound and bound.weighted == 0 else None),
                raw_candidate_qualification="Uncertified move-engine count; excluded from approved objective averages",
                physical_violations=[v.model_dump(mode="json") for v in applied.violations])
            row["completed"] = bool(result.status == "ok" and applied.legal and shutout == 0)
            if row["completed"]:
                result = finalize(instance, result, time_limit_s=min(args.ballast_limit_s,
                    max(.001, validation_budget-(time.monotonic()-phase))))
                if result.plan is not None:
                    replay_plan = result.plan
                row.update(finalized_status=result.status, finalized_message=result.message,
                           ballast_status=result.metrics.get("ballast_status"))
                report = check(instance, replay_plan)
                require_clean(result, report, what="G1 pilot finalized checker result")
                row.update(checker_passed=report.passed,
                    checker_violations=[v.model_dump(mode="json") for v in report.violations],
                    objective_d11=dict(hard_violations=sum(v.severity == "hard" for v in report.violations),
                        shut_out=len(report.shut_out), weighted_nonproductive=report.count.weighted_nonproductive),
                    checker_count=report.count.model_dump(mode="json"))
                if report.passed:
                    cost = report.count.weighted_nonproductive
                    row.update(objective=cost, gap_to_lb=(cost-bound.weighted)/cost if cost else
                               (0.0 if bound.weighted == 0 else None), gap_valid=True)
                else:
                    row["status"] = "checker_failed"
    except SearchDeadline as exc:
        row.update(status="validation_timeout", validation_error=str(exc))
    except Exception as exc:
        row.update(status="validation_error", validation_error=f"{type(exc).__name__}: {exc}")
    row["timings_s"]["finalize_check"] = time.monotonic()-phase
    row["stage"] = "replay"
    phase = time.monotonic()
    try:
        replay_budget = min(args.overhead_reserve_s-(time.monotonic()-started-row["timings_s"]["search"]),
                            hard_deadline-time.monotonic())
        if replay_budget <= 0:
            raise SearchDeadline("bounded replay reserve exhausted")
        with helper.search_deadline(replay_budget):
            row["replay"] = replay(instance, replay_plan)
        row["approved"] = bool(row["completed"] and row["checker_passed"] and
                               row.get("finalized_status") == "ok" and row["replay"]["status"] == "passed")
        row["joint_gate_passed"] = row["approved"]
        if row["approved"]:
            row["status"] = "approved"
            save_plan(replay_plan, approved_path)
            row.update(approved_plan=str(approved_path), approved_plan_sha256=sha(approved_path))
        elif row["replay"]["status"] != "passed" and row["checker_passed"]:
            row["status"] = "mask_failed"
    except SearchDeadline as exc:
        row["replay"] = dict(status="timeout", error=str(exc))
        if row["checker_passed"]:
            row["status"] = "mask_timeout"
    except Exception as exc:
        row["replay"] = dict(status="error", error=f"{type(exc).__name__}: {exc}")
        if row["checker_passed"]:
            row["status"] = "mask_error"
    row["timings_s"]["replay"] = time.monotonic()-phase
    row["timings_s"]["pipeline"] = time.monotonic()-started
    row["case_deadline_overrun_s"] = max(0, time.monotonic()-hard_deadline)
    return row


def quarantine_source_transition(row):
    """Keep the joint gate observation while removing any mixed-source gold artifact."""
    measurement = row["measurement"]
    measurement["raw_joint_gate_passed"] = measurement.get("joint_gate_passed", False)
    measurement["approved"] = False
    measurement["source_transition_qualification"] = "Exploratory only: executable source changed during the case; excluded from approved aggregates"
    approved = measurement.pop("approved_plan", None)
    measurement.pop("approved_plan_sha256", None)
    if approved is not None:
        approved_path = Path(approved)
        quarantined = approved_path.with_name(approved_path.name.replace(".approved-plan.json", ".source-transition-plan.json"))
        approved_path.rename(quarantined)
        measurement.update(quarantined_plan=str(quarantined), quarantined_plan_sha256=sha(quarantined))
    if measurement.get("joint_gate_passed"):
        measurement["status"] = "source_transition_joint_pass"


def summary(rows, availability):
    from collections import Counter
    from statistics import mean
    out = {}
    for rung, available in availability.items():
        group = [r for r in rows if r["selection"]["rung"] == rung]
        admitted = [r for r in group if r.get("admitted")]
        total = sum(r.get("measurement", {}).get("timings_s", {}).get("pipeline", 0) for r in admitted)
        accepted = [r for r in admitted if r.get("measurement", {}).get("approved") and not r.get("source_changed_during_run", False)]
        costs = [r["measurement"]["objective"] for r in accepted]
        checked_gaps = [r["measurement"]["gap_to_lb"] for r in admitted
                        if r.get("measurement", {}).get("gap_valid") and r["measurement"].get("gap_to_lb") is not None]
        approved_gaps = [r["measurement"]["gap_to_lb"] for r in accepted if r["measurement"].get("gap_to_lb") is not None]
        out[rung] = dict(**available, recorded=len(group), attempted=len(admitted),
            not_admitted=sum(not r.get("admitted") for r in group), approved=len(accepted),
            plans_returned=sum(r.get("measurement", {}).get("plan_returned", False) for r in admitted),
            raw_joint_gate_passed=sum(r.get("measurement", {}).get("joint_gate_passed", False) for r in admitted),
            source_transition_cases=sum(r.get("source_changed_during_run", False) for r in admitted),
            mean_search_s=mean([r.get("measurement", {}).get("timings_s", {}).get("search", 0) for r in admitted]) if admitted else None,
            mean_checker_qualified_gap_to_lb=mean(checked_gaps) if checked_gaps else None,
            mean_approved_gap_to_lb=mean(approved_gaps) if approved_gaps else None,
            completed=sum(r.get("measurement", {}).get("completed", False) for r in admitted),
            checker_passed=sum(r.get("measurement", {}).get("checker_passed", False) for r in admitted),
            mask_passed=sum(r.get("measurement", {}).get("replay", {}).get("status") == "passed" for r in admitted),
            mask_status_counts=dict(Counter(r.get("measurement", {}).get("replay", {}).get("status", "no_plan") for r in admitted)),
            status_counts=dict(Counter(r.get("measurement", {}).get("status", r.get("status")) for r in group)),
            core_seconds=total, approved_plans_per_core_hour=3600*len(accepted)/total if total else None,
            mean_approved_objective=mean(costs) if costs else None,
            denominator="All admitted one-core pipeline seconds, including failures; excludes cache warm and input loading",
            warm_seconds_excluded="No cache warming performed by this driver",
            input_load_seconds_excluded=sum(r.get("input_load_s", 0) for r in admitted))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("experiments/instances-v3/manifest.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rungs", nargs="+", choices=("tiny", "small", "full", "realistic_500", "realistic_800", "fullship"), default=["tiny", "small", "full", "realistic_500", "realistic_800"])
    parser.add_argument("--count-per-rung", type=int, default=2)
    parser.add_argument("--tiny-limit-s", type=float, default=60)
    parser.add_argument("--small-limit-s", type=float, default=120)
    parser.add_argument("--full-limit-s", type=float, default=600)
    parser.add_argument("--ballast-limit-s", type=float, default=30)
    parser.add_argument("--overhead-reserve-s", type=float, default=120)
    parser.add_argument("--stop-admission-utc", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT23:10:00Z"))
    parser.add_argument("--solver-seed", type=int, default=0)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args(argv)
    if args.count_per_rung < 1 or not all(math.isfinite(v) and v > 0 for v in
        (args.tiny_limit_s, args.small_limit_s, args.full_limit_s, args.ballast_limit_s, args.overhead_reserve_s)):
        parser.error("counts and budgets must be positive and finite")
    stop = datetime.fromisoformat(args.stop_admission_utc.replace("Z", "+00:00"))
    if stop.tzinfo is None:
        parser.error("stop-admission-utc requires an explicit timezone")
    stop = stop.astimezone(timezone.utc)
    args.output, args.manifest = args.output.resolve(), args.manifest.resolve()
    if not args.output.is_relative_to(SCOPE):
        parser.error("output must remain within scalable-solver experiments")
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    selected, availability = select(manifest, args.rungs, args.count_per_rung)
    if args.list_only:
        print(json.dumps(dict(selected=selected, availability=availability), indent=2)); return 0
    helper = helpers()
    from stowage.solver.scalable import SCALABLE_VERSION, ScalableConfig
    from stowage.versions import component_versions
    budgets = dict(tiny=args.tiny_limit_s, small=args.small_limit_s, **{key: args.full_limit_s for key in (*FULL_PROFILES, "fullship")})
    identity = dict(schema="scalable-v3-g1-pilot-v1", manifest=str(args.manifest),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(), manifest_campaign_version=manifest.get("campaign_version"),
        manifest_physics_sha256=manifest.get("physics_sha256"), source_sha256=helper.source_hash(),
        driver_sha256=sha(Path(__file__)), versions=component_versions(), solver_version=SCALABLE_VERSION,
        solver_config=asdict(ScalableConfig()), budgets_s=budgets, ballast_limit_s=args.ballast_limit_s,
        overhead_reserve_s=args.overhead_reserve_s, stop_admission_utc=stop.isoformat(), solver_seed=args.solver_seed,
        diagnostic_c2_off=False, cores=1)
    snapshot = dict(identity=identity, availability=availability, selections=selected)
    snapshot_path = args.output / "selected-manifest.json"
    if snapshot_path.exists() and json.loads(snapshot_path.read_text()) != snapshot:
        raise ValueError("pinned source/manifest/config/selection changed; use a fresh output")
    save(snapshot_path, snapshot)
    rows = []
    for entry in selected:
        target = args.output / "results" / f"{entry['rung']}-{entry['shard']['seed']}.json"
        if target.exists():
            row = json.loads(target.read_text())
            if row["identity"] != identity or row["selection"] != entry:
                raise ValueError("result checkpoint identity mismatch")
            rows.append(row); continue
        now = datetime.now(timezone.utc)
        budget = budgets[entry["rung"]]
        row = dict(identity=identity, selection=entry, admitted=False, admission_utc=now.isoformat())
        if (stop-now).total_seconds() < budget+args.overhead_reserve_s:
            row.update(status="not_admitted_deadline", required_seconds=budget+args.overhead_reserve_s,
                       remaining_seconds=max(0, (stop-now).total_seconds()))
        else:
            row["admitted"] = True
            load_started = time.monotonic()
            hard_deadline = load_started+budget+args.overhead_reserve_s
            try:
                from eval.splits import spec_from_instance, spec_key, split_of
                from stowage.io import load_instance
                path = (args.manifest.parent / entry["relative"]).resolve()
                if not path.is_relative_to(args.manifest.parent):
                    raise ValueError("manifest shard path escapes its root")
                if sha(path) != entry["shard"]["sha256"]:
                    raise ValueError("instance SHA disagrees with frozen selected manifest")
                instance = load_instance(path)
                actual = spec_from_instance(instance)
                if split_of(actual) != entry["shard"]["split"] or spec_key(actual) != entry["shard"]["spec_key"]:
                    raise ValueError("loaded instance frozen specification differs from selected manifest")
                row.update(input_sha256=sha(path), input_load_s=time.monotonic()-load_started, boxes=len(instance.load_list))
                row["measurement"] = measure(instance, args, budget, helper,
                    args.output / "plans" / f"{entry['rung']}-{entry['shard']['seed']}.approved-plan.json", hard_deadline=hard_deadline)
            except Exception as exc:
                row.update(input_load_s=time.monotonic()-load_started,
                    measurement=dict(status="input_error", error=f"{type(exc).__name__}: {exc}",
                                     approved=False, timings_s=dict(pipeline=0)))
            row["source_sha256_after"] = helper.source_hash()
            row["source_changed_during_run"] = row["source_sha256_after"] != identity["source_sha256"]
            if row["source_changed_during_run"]:
                quarantine_source_transition(row)
        save(target, row); rows.append(row)
        save(args.output / "summary.json", summary(rows, availability))
        print(json.dumps(dict(rung=entry["rung"], seed=entry["shard"]["seed"],
            status=row.get("measurement", {}).get("status", row.get("status")))), flush=True)
    save(args.output / "summary.json", summary(rows, availability))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
