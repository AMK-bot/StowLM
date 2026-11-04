"""Fresh, resumable train/validation campaign for scalable solver 4.7.

The frozen split is selected BEFORE generation. Dense is explicitly a derived
simulated/full specification (400 dry-20-compatible bookings on feeder_800),
not a new frozen split. Its overrides are recorded beside the full spec.
Diagnostic C2-off measures construction only; it never produces approved gold.
Throughput counts only plans passing finalize, checker, and strict public-mask
replay, divided by all measured one-core pipeline seconds, including failures.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
import logging
import math
import os
import resource
import sys
from pathlib import Path
from types import SimpleNamespace
import time

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

SCOPE = Path(__file__).resolve().parent
ROOT = SCOPE.parent.parent
RUNGS = ("tiny", "small", "medium", "full", "dense")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def helpers():
    spec = importlib.util.spec_from_file_location("scalable_canonical_helpers", SCOPE / "canonical_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selections(rungs, count, seed_start, allowed):
    from eval.splits import InstanceSpec, canonical_spec, spec_key, split_of
    if not allowed or any(split not in ("train", "val") for split in allowed):
        raise ValueError("only train/val specifications may be selected")
    rows = []
    for index, rung in enumerate(rungs):
        seed = seed_start + index * 100000
        found = 0
        while found < count:
            dense = rung == "dense"
            spec = InstanceSpec(generator="simulated" if dense else "random_yard",
                rung="full" if dense else rung, slice="full" if dense else rung,
                vessel="feeder_800" if dense else "feeder_500",
                port="laem_chabang_like", noise=.2 if dense else None,
                blocking_ratio=None if dense else .3, seed=seed)
            split = split_of(spec)
            seed += 1
            if split not in allowed:
                continue
            rows.append({"rung": rung, "spec": canonical_spec(spec), "split": split,
                         "spec_key": spec_key(spec),
                         "overrides": {"n_boxes": 400, "dense_20ft": True} if dense else {}})
            found += 1
    return rows


def generation_hash():
    """Physics/generator/parameter code fingerprint, excluding solver-only edits."""
    checksum = hashlib.sha256()
    paths = list((ROOT / "src/stowage").rglob("*.py")) + list((ROOT / "params").rglob("*.toml"))
    paths += list((ROOT / "src/stowage/rules").rglob("*.toml"))
    paths += list((ROOT / "src/stowage/rules/imdg/data").rglob("*.json"))
    for path in sorted(paths):
        if (ROOT / "src/stowage/solver") in path.parents:
            continue
        checksum.update(str(path.relative_to(ROOT)).encode())
        checksum.update(path.read_bytes())
    return checksum.hexdigest()


def generate(entry, directory):
    from eval.splits import spec_from_instance, spec_key, split_of
    from stowage.io import load_instance, save_instance
    if entry["split"] not in ("train", "val"):
        raise ValueError("refusing test generation or resume")
    spec = entry["spec"]
    if split_of(spec) != entry["split"] or spec_key(spec) != entry["spec_key"]:
        raise ValueError("pre-generation specification split/key mismatch")
    identity = {**entry, "generation_source_sha256": generation_hash()}
    path = directory / entry["rung"] / f"seed-{spec['seed']:08d}.json"
    audit_path = path.with_suffix(".audit.json")
    if path.exists() or audit_path.exists():
        if not (path.exists() and audit_path.exists()):
            raise ValueError(f"incomplete generation checkpoint: {path}")
        audit = json.loads(audit_path.read_text())
        if audit["identity"] != identity or audit["sha256"] != sha(path):
            raise ValueError(f"generation resume mismatch: {path}")
        instance = load_instance(path)
        return instance, audit, path
    if entry["split"] not in ("train", "val"):
        raise ValueError("refusing test generation")
    started = time.monotonic()
    common = dict(seed=spec["seed"], vessel_name=spec["vessel"], port_name=spec["port"])
    if spec["generator"] == "random_yard":
        from stowage.generate.random_yard import RandomGenerationReport, generate_random_instance
        report = RandomGenerationReport()
        instance = generate_random_instance(spec["rung"], blocking_ratio=spec["blocking_ratio"],
                                            report=report, **common)
    else:
        from stowage.generate.instances import GenerationReport, generate_simulated_instance
        report = GenerationReport()
        instance = generate_simulated_instance(spec["rung"], noise=spec["noise"], report=report,
                                               **entry["overrides"], **common)
    actual = spec_from_instance(instance)
    if spec_key(actual) != entry["spec_key"] or split_of(actual) != entry["split"]:
        raise ValueError("generated identity disagrees with pre-selected train/val specification")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial.json")
    save_instance(instance, temporary)
    temporary.replace(path)
    audit = {"identity": identity, "sha256": sha(path), "generation_s": time.monotonic()-started,
             "boxes": len(instance.load_list), "cells": len(instance.vessel.cells),
             "generator_audit": asdict(report)}
    save(audit_path, audit)
    return instance, audit, path



def load_generated(entry, directory):
    """Read a frozen generated shard without regenerating under newer code.

    The audit pins the original generator/physics source and file contents.
    Unlike generation resume, read-only measurement may use a saved input after
    code changes; its original identity is retained in every result.
    """
    from eval.splits import spec_from_instance, spec_key, split_of
    from stowage.io import load_instance
    if (entry["split"] not in ("train", "val") or
            split_of(entry["spec"]) != entry["split"] or
            spec_key(entry["spec"]) != entry["spec_key"]):
        raise ValueError("refusing invalid/test frozen input")
    path = directory / entry["rung"] / f"seed-{entry['spec']['seed']:08d}.json"
    audit = json.loads(path.with_suffix(".audit.json").read_text())
    original = audit["identity"]
    if (any(original.get(key) != value for key, value in entry.items()) or
            not original.get("generation_source_sha256") or audit["sha256"] != sha(path)):
        raise ValueError(f"frozen generation identity/content mismatch: {path}")
    instance = load_instance(path)
    if spec_key(spec_from_instance(instance)) != entry["spec_key"]:
        raise ValueError("frozen instance disagrees with selected specification")
    return instance, audit, path



def shared_data_preflight(instance):
    """Reuse the solver's public-checker required-data screen."""
    from stowage.solver.scalable_oracle import required_data_violations
    return required_data_violations(instance)


def measure(instance, planner, args, plan_path, helper):
    from stowage.bounds import lower_bound
    from stowage.frozen import require_clean
    from stowage.io import save_plan
    from stowage.moves import apply_plan
    from stowage.solver.scalable_oracle import SearchDeadline
    started = time.monotonic()
    row = dict(status="error", completed=False, approved=False, checker_passed=False,
        checker_status="not_requested", mask={"status": "not_run"}, objective_d11=None,
        gap=None, gap_valid=False, lifts=None, timing_s={}, diagnostic_c2_off=args.diagnostic_c2_off)
    phase = started
    try:
        require_clean(instance, what="fresh scalable campaign input")
        if getattr(args, "preflight_data", False):
            unavailable = shared_data_preflight(instance)
            if unavailable:
                row.update(status="unsupported_data", required_data_violations=unavailable,
                           message="Required static-rule data unavailable at every geometric slot; not an infeasibility proof")
                row["timing_s"]["total"] = time.monotonic()-started
                peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                row["process_peak_rss_bytes"] = peak if sys.platform == "darwin" else peak * 1024
                return row
        bound = lower_bound(instance)
        row["lower_bound"] = bound.model_dump(mode="json")
        phase = time.monotonic()
        with helper.search_deadline(args.time_limit_s):
            result = planner.plan(instance, seed=args.solver_seed, time_limit_s=args.time_limit_s)
        row["timing_s"]["search"] = time.monotonic()-phase
        row.update(status=result.status, message=result.message, solver_metrics=result.metrics)
        if result.plan is not None:
            applied = apply_plan(instance, result.plan)
            loaded = {placement.container_id for placement in applied.final_state.ship.placements}
            shutout = len(set(instance.load_list)-loaded)
            row["completed"] = bool(result.status == "ok" and applied.legal and not shutout)
            row["lifts"] = applied.count.model_dump(mode="json")
            row["objective_d11"] = dict(hard_violations=None, physical_violations=len(applied.violations),
                shut_out=shutout, weighted_nonproductive=applied.count.weighted_nonproductive,
                certification="move-engine-only; hard stowage and physics unverified")
        if args.check and row["completed"]:
            from stowage.planner import finalize
            from stowage.checker import check
            row["checker_status"] = "started"
            phase = time.monotonic()
            with helper.search_deadline(args.validation_limit_s):
                result = finalize(instance, result, time_limit_s=args.ballast_limit_s)
                row["timing_s"]["finalize"] = time.monotonic()-phase
                row["status"] = result.status
                phase = time.monotonic()
                report = check(instance, result.plan)
                row["timing_s"]["checker"] = time.monotonic()-phase
            require_clean(result, result.plan, report, what="fresh campaign checked result")
            row.update(checker_passed=report.passed, checker_status="passed" if report.passed else "failed",
                violations=[v.model_dump(mode="json") for v in report.violations])
            row["objective_d11"] = dict(hard_violations=sum(v.severity == "hard" for v in report.violations),
                shut_out=len(report.shut_out), weighted_nonproductive=report.count.weighted_nonproductive,
                margins={name: part.margins for name, part in report.parts.items()}, certification="full-checker")
            if report.passed:
                weighted = report.count.weighted_nonproductive
                row.update(gap=(weighted-bound.weighted)/weighted if weighted else (0. if bound.weighted == 0 else None),
                           gap_valid=True)
                # Diagnostic construction is never admitted as approved gold, even
                # when a checker happens to accept it: strict replay is mandatory.
                if not args.diagnostic_c2_off:
                    phase = time.monotonic()
                    with helper.search_deadline(args.validation_limit_s):
                        row["mask"] = helper.replay(instance, result.plan)
                    row["timing_s"]["mask"] = time.monotonic()-phase
                    row["approved"] = result.status == "ok" and row["mask"]["status"] == "passed"
                    if row["approved"]:
                        save_plan(result.plan, plan_path)
                        row.update(approved_plan=str(plan_path), approved_plan_sha256=sha(plan_path))
    except SearchDeadline as exc:
        row["deadline_stack"] = exc.args[0] if exc.args else []
        if row["checker_status"] == "started":
            row["checker_status"] = "validation_timeout"
        else:
            row["status"] = "timeout"
        row["timing_s"].setdefault("search", time.monotonic()-phase)
    except KeyboardInterrupt:
        row["status"] = "interrupted"
    except Exception as exc:
        row["approved"] = False
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["timing_s"]["total"] = time.monotonic()-started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    row["process_peak_rss_bytes"] = peak if sys.platform == "darwin" else peak * 1024
    return row


def summarize(rows):
    from statistics import mean
    result = {}
    for rung in RUNGS:
        group = [row for row in rows if row["identity"]["rung"] == rung]
        if not group:
            continue
        measurements = [row["measurement"] for row in group]
        core_seconds = sum(row["timing_s"]["total"] for row in measurements)
        approved = sum(row["approved"] for row in measurements)
        completed = sum(row["completed"] for row in measurements)
        from collections import Counter
        statuses = dict(Counter(row["status"] for row in measurements))
        result[rung] = dict(n=len(group), completed=completed, completion_rate=completed/len(group),
            status_counts=statuses, censored=sum(row["status"] in ("interrupted", "timeout") for row in measurements),
            denominator="All attempted one-core pipeline seconds, including failure and censoring; excludes generation and unstarted instances",
            approved=approved, checker_passed=sum(row["checker_passed"] for row in measurements),
            diagnostic_c2_off=any(row["diagnostic_c2_off"] for row in measurements),
            boxes=sorted({row["boxes"] for row in group}), core_seconds=core_seconds,
            approved_plans_per_core_hour=3600*approved/core_seconds if core_seconds else None,
            mean_pipeline_s=core_seconds/len(group),
            mean_search_s=mean(row["timing_s"].get("search",0) for row in measurements),
            objective_d11=[row["objective_d11"] for row in measurements],
            valid_gaps=[row["gap"] for row in measurements if row["gap_valid"]])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instances-dir", type=Path, help="Reuse a separate fresh-generation checkpoint directory")
    parser.add_argument("--rungs", nargs="+", choices=RUNGS, default=list(RUNGS))
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--seed-start", type=int, default=71000000)
    parser.add_argument("--split", nargs="+", choices=("train","val"), default=["train","val"])
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--diagnostic-c2-off", action="store_true")
    parser.add_argument("--preflight-data", action="store_true", help="Reject required static data absent at every geometric placement")
    parser.add_argument("--check", action="store_true", help="Finalize/full-check complete plans; strict replay only with C2 enabled")
    parser.add_argument("--time-limit-s", type=float, default=120)
    parser.add_argument("--ballast-limit-s", type=float, default=30)
    parser.add_argument("--validation-limit-s", type=float, default=120)
    parser.add_argument("--solver-seed", type=int, default=0)
    parser.add_argument("--max-neighborhoods", type=int, default=12)
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--beam-width", type=int, default=2)
    parser.add_argument("--early-beam-width", type=int, default=4)
    parser.add_argument("--initial-strategy", type=int, choices=range(4), default=0)
    args = parser.parse_args(argv)
    if args.progress:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("0 <= shard-index < shard-count required")
    if args.count < 1 or args.seed_start < 0 or not all(math.isfinite(x) and x>0 for x in
            (args.time_limit_s,args.ballast_limit_s,args.validation_limit_s)) or args.time_limit_s>600:
        parser.error("positive counts/deadlines required; search deadline <=600s")
    args.output = args.output.resolve()
    if not args.output.is_relative_to(SCOPE):
        parser.error("output must remain under experiments/scalable-solver")
    if args.instances_dir is not None:
        args.instances_dir = args.instances_dir.resolve()
        if not args.instances_dir.is_relative_to(SCOPE):
            parser.error("instances-dir must remain under experiments/scalable-solver")
    selected = selections(args.rungs,args.count,args.seed_start,args.split)[args.shard_index::args.shard_count]
    if args.list_only:
        print(json.dumps(selected,indent=2)); return 0
    args.output.mkdir(parents=True,exist_ok=True)
    manifest_path = args.output / "manifest.json"
    manifest = dict(schema="fresh-scalable-campaign-v1", selections=selected)
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("manifest selection differs; choose fresh output")
    save(manifest_path,manifest)
    helper = helpers()
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.scalable import ScalableConfig, ScalableSolver
    config = ScalableConfig(default_time_limit_s=args.time_limit_s,
        max_neighborhoods=args.max_neighborhoods, max_restarts=args.max_restarts,
        beam_width=args.beam_width, early_beam_width=args.early_beam_width, initial_strategy=args.initial_strategy,
        feasibility_config=FeasibilityConfig(extra={"global_screen":0}) if args.diagnostic_c2_off else None)
    planner = ScalableSolver(config)
    from stowage.versions import component_versions
    identity_common = dict(source_sha256=helper.source_hash(), driver_sha256=sha(Path(__file__)),
        solver_version=planner.version, component_versions=component_versions(),
        config=asdict(config), check=args.check, diagnostic_c2_off=args.diagnostic_c2_off,
        ballast_limit_s=args.ballast_limit_s, validation_limit_s=args.validation_limit_s,
        solver_seed=args.solver_seed, preflight_data=args.preflight_data, cores=1)
    rows=[]
    for entry in selected:
        instance,audit,path=(load_generated(entry,args.instances_dir) if args.instances_dir else
                             generate(entry,args.output/"instances"))
        print(json.dumps(dict(event="generated",rung=entry["rung"],seed=entry["spec"]["seed"],
            boxes=audit["boxes"],generation_s=audit["generation_s"])),flush=True)
        if args.generate_only:
            continue
        identity={**identity_common,**entry,"input_sha256":audit["sha256"],
                  "generation_source_sha256":audit["identity"]["generation_source_sha256"]}
        name=f"{entry['rung']}-{entry['spec']['seed']}"
        target=args.output/"results"/f"{name}.result.json"
        if target.exists():
            row=json.loads(target.read_text())
            if row["identity"]!=identity:
                raise ValueError(f"result resume mismatch: {target}")
            measurement=row["measurement"]
            if measurement["approved"] and sha(Path(measurement["approved_plan"]))!=measurement["approved_plan_sha256"]:
                raise ValueError("approved plan changed")
        else:
            row=dict(identity=identity,boxes=len(instance.load_list),measurement=measure(instance,planner,args,
                args.output/"results"/f"{name}.approved-plan.json",helper))
            save(target,row)
        rows.append(row)
        save(args.output/"summary.json",summarize(rows))
        print(json.dumps(dict(event="measured",rung=entry["rung"],seed=entry["spec"]["seed"],
            status=row["measurement"]["status"],completed=row["measurement"]["completed"],
            approved=row["measurement"]["approved"])),flush=True)
        if row["measurement"]["status"]=="interrupted":
            return 130
    return 0


if __name__=="__main__":
    raise SystemExit(main())
