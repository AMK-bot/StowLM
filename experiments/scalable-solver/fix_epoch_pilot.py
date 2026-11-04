"""Bounded current-epoch, train/validation-only production G1 measurements.

Generate new inputs with the current canonical generator, rather than retagging
old vessel snapshots. Freeze requests before solving. Checkpoint every request
and variant; refuse a changed source/config on resume. No cache warming, cloud,
threads or changes to physics. A narrow search changes effort, never hard rules.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
from statistics import mean
import time

for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
            "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "1"

HERE = Path(__file__).resolve().parent


def driver():
    spec = importlib.util.spec_from_file_location("fix_epoch_g1", HERE / "g1_pilot_configured.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def requests(profiles, generators, count, seed, max_attempts):
    from stowage.generate.instances_v3 import scenario_specs, split_metadata
    chosen = []
    for generator in generators:
        for profile in profiles:
            candidates = scenario_specs(profiles=[profile], generators=[generator],
                seed=seed, max_attempts=max_attempts, c2_screened=True)
            # Split membership is checked on specifications before generation.
            eligible = [s for s in candidates if split_metadata(s)["split"] in ("train", "val")]
            chosen.extend(eligible[:count])
    if len(chosen) > 30:
        raise ValueError("bounded pilot accepts at most 30 input requests")
    return chosen


def summarize(rows, variants=("default",)):
    out = {}
    for profile in sorted({r["spec"]["profile"] for r in rows}):
        group = [r for r in rows if r["spec"]["profile"] == profile]
        names = set(variants) | {name for row in group for name in row.get("variants", {})}
        for variant in sorted(names):
            observations = [r["variants"][variant] for r in group if variant in r.get("variants", {})]
            qualified = [r for r in observations if r.get("approved")]
            seconds = [r["timings_s"]["pipeline"] for r in observations]
            total = sum(seconds)
            out[f"{profile}/{variant}"] = dict(requests=len(group), attempted=len(observations),
                generation_failed=sum(r["generation"]["status"] != "accepted" for r in group),
                complete=sum(r.get("completed", False) for r in observations),
                full_checker_pass=sum(r.get("checker_passed", False) for r in observations),
                mask_pass=sum(r.get("replay", {}).get("status") == "passed" for r in observations),
                approved=len(qualified), ballast_status_counts=dict(Counter(
                    r.get("ballast_status", "not_reached") for r in observations)),
                status_counts=dict(Counter(r.get("status") for r in observations)),
                pipeline_seconds=total, mean_seconds_per_attempt=mean(seconds) if seconds else None,
                approved_seconds_per_plan=total/len(qualified) if qualified else None,
                approved_plans_per_core_hour=3600*len(qualified)/total if total else None,
                approved_objectives=[r["objective"] for r in qualified],
                approved_gaps_to_lower_bound=[r["gap_to_lb"] for r in qualified])
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profiles", nargs="+", choices=("tiny", "small", "medium", "full"),
                        default=["tiny", "small", "medium", "full"])
    parser.add_argument("--generators", nargs="+", choices=("random_yard", "simulated_yard"),
                        default=["random_yard", "simulated_yard"])
    parser.add_argument("--count-per-rung", type=int, default=1)
    parser.add_argument("--seed", type=int, default=2026102700)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument("--generation-limit-s", type=float, default=60)
    parser.add_argument("--search-limit-s", type=float, default=60)
    parser.add_argument("--overhead-limit-s", type=float, default=40)
    parser.add_argument("--compare-narrow", action="store_true")
    args = parser.parse_args(argv)
    if min(args.count_per_rung, args.max_attempts, args.generation_limit_s,
           args.search_limit_s, args.overhead_limit_s) <= 0:
        parser.error("counts and limits must be positive")
    args.output = args.output.resolve()
    if args.output == HERE or not args.output.is_relative_to(HERE):
        parser.error("dedicated output directory must remain within scalable-solver")
    from stowage.generate.instances_v3 import generate, CampaignRejection, physics_metadata, split_metadata
    from stowage.io import load_instance, save_instance, save_plan
    from stowage.solver.scalable import ScalableSolver
    from stowage.solver.scalable_oracle import SearchDeadline
    from stowage.versions import component_versions
    g1 = driver()
    helper = g1.helpers()
    selected = requests(args.profiles, args.generators, args.count_per_rung, args.seed, args.max_attempts)
    variants = {"default": {}}
    if args.compare_narrow:
        variants["narrow"] = dict(beam_width=1, early_beam_width=1, branch_factor=2,
            candidate_boxes=8, candidate_slots=16, max_neighborhoods=2,
            exact_window_time_limit_s=2, exact_whole_plan_time_limit_s=10)
    identity = dict(source_sha256=helper.source_hash(), driver_sha256=g1.sha(Path(__file__)),
        gate_driver_sha256=g1.sha(HERE / "g1_pilot_configured.py"), versions=component_versions(),
        physics=physics_metadata(c2_screened=True), requests=selected, variants=variants,
        generation_limit_s=args.generation_limit_s, search_limit_s=args.search_limit_s,
        overhead_limit_s=args.overhead_limit_s, diagnostic_c2_off=False, cores=1)
    admission = args.output / "admission.json"
    if admission.exists() and json.loads(admission.read_text()) != identity:
        raise ValueError("source/config/request transition: use a fresh output, never retag old shards")
    g1.save(admission, identity)
    def unchanged():
        return (helper.source_hash() == identity["source_sha256"] and
                physics_metadata(c2_screened=True) == identity["physics"])
    rows = []
    for spec in selected:
        if not unchanged():
            raise ValueError("source epoch changed; stop admission and retain completed checkpoints")
        name = f"{spec['generator']}-{spec['profile']}-{spec['seed']}"
        path = args.output / "results" / f"{name}.json"
        if path.exists():
            row = json.loads(path.read_text())
        else:
            row = dict(spec=spec, split=split_metadata(spec), variants={}, started_utc=datetime.now(timezone.utc).isoformat())
        input_path = args.output / "inputs" / f"{name}.json"
        if "generation" not in row:
            started = time.monotonic()
            try:
                with helper.search_deadline(args.generation_limit_s):
                    instance, audit = generate(spec)
                save_instance(instance, input_path)
                row["generation"] = dict(status="accepted", audit=audit,
                    input_sha256=g1.sha(input_path), boxes=len(instance.load_list))
            except CampaignRejection as exc:
                row["generation"] = dict(status="rejected", audit=exc.audit, error=str(exc))
            except (SearchDeadline, Exception) as exc:
                row["generation"] = dict(status="error", error=f"{type(exc).__name__}: {exc}")
            row["generation"]["wall_s"] = time.monotonic()-started
            g1.save(path, row)
        if row["generation"]["status"] == "accepted":
            if g1.sha(input_path) != row["generation"]["input_sha256"]:
                raise ValueError("input checkpoint hash mismatch")
            instance = load_instance(input_path)
            for variant, config in variants.items():
                if variant in row["variants"]:
                    continue
                if not unchanged():
                    raise ValueError("source epoch changed before variant; retain checkpoints")
                measure_args = argparse.Namespace(solver_seed=0, solver_config=config,
                    overhead_reserve_s=args.overhead_limit_s, ballast_limit_s=args.overhead_limit_s/4)
                original = ScalableSolver.plan
                candidates = []
                def capture(solver, instance_arg, *, seed=0, time_limit_s=None):
                    result = original(solver, instance_arg, seed=seed, time_limit_s=time_limit_s)
                    if result.plan is not None:
                        artifact = args.output / "candidates" / f"{name}-{variant}.unverified.json"
                        save_plan(result.plan, artifact)
                        candidates.append(dict(path=str(artifact), sha256=g1.sha(artifact), status=result.status))
                    return result
                from unittest.mock import patch
                with patch.object(ScalableSolver, "plan", capture):
                    measurement = g1.measure(instance, measure_args, args.search_limit_s, helper,
                        args.output / "plans" / f"{name}-{variant}.approved-plan.json")
                measurement["candidates"] = candidates
                if not unchanged():
                    transition = dict(measurement=measurement)
                    g1.quarantine_source_transition(transition)
                    measurement["source_transition"] = True
                row["variants"][variant] = measurement
                g1.save(path, row)
                # Preserve aggregate evidence even if an epoch transition stops
                # admission before the next variant or request.
                g1.save(args.output / "summary.json", summarize(rows + [row], variants))
        rows.append(row)
        g1.save(args.output / "summary.json", summarize(rows, variants))
        print(json.dumps(dict(request=name, generation=row["generation"]["status"],
            variants={k:v["status"] for k,v in row["variants"].items()})), flush=True)
    g1.save(args.output / "summary.json", summarize(rows, variants))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
