#!/usr/bin/env python3
"""Resumable frozen-test classical references; run sequentially under nice -n 10.

Example: uv run python experiments/mvp/refs_tools/run_refs.py solver --rung tiny
An explicit --instance-list accepts newline paths or a JSON list of paths. Paths
may be absolute, repository-relative, or relative to --instance-root. Every input
must be pinned by that root's SHA256SUMS. No corpus files are written.
"""
from __future__ import annotations

import os

# Native libraries are pinned before any numerical/project imports.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS"):
    os.environ[_name] = "1"

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import resource
import shlex
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(json_safe(value), sort_keys=True, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def source_hashes(source_root=ROOT):
    # Snapshot keys keep their normal logical src/... names. Parameter loading
    # finds the real repository's pyproject upward, so pin those real inputs too.
    result = {str(path.relative_to(source_root)): digest(path)
              for path in sorted((source_root / "src").rglob("*"))
              if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
    for path in sorted((ROOT / "params").rglob("*")):
        if path.is_file():
            result[str(path.relative_to(ROOT))] = digest(path)
    result[str(Path(__file__).resolve().relative_to(ROOT))] = digest(__file__)
    return result


def source_changes(first, second):
    return sorted(key for key in first.keys() | second.keys() if first.get(key) != second.get(key))


def relevant_changes(paths):
    # Generator edits cannot affect already frozen inputs. Other stowage edits
    # are conservatively relevant, including a solver used by an inner window.
    return [p for p in paths if p.startswith("params/") or
            (p.startswith("src/stowage/") and not p.startswith("src/stowage/generate/"))]


def selection(args):
    root = args.instance_root.resolve()
    sha_file = root / "SHA256SUMS"
    pins = {}
    for line in sha_file.read_text().splitlines():
        if line.strip():
            sha, name = line.split(maxsplit=1)
            pins[name.lstrip("*")] = sha
    if args.instance_list:
        raw = args.instance_list.read_text()
        names = json.loads(raw) if raw.lstrip().startswith("[") else [
            line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError("instance list must contain path strings")
        paths = []
        for name in names:
            path = Path(name)
            if not path.is_absolute():
                path = (ROOT / path) if (ROOT / path).is_file() else root / path
            paths.append(path.resolve())
    else:
        paths = sorted(path.resolve() for path in (root / args.rung).rglob("*.json")
                       if not path.name.endswith(".audit.json"))
    if args.limit is not None:
        paths = paths[:args.limit]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("selection is empty or contains duplicate paths")
    selected = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        sha = digest(path)
        if rel not in pins or pins[rel] != sha:
            raise ValueError(f"missing or mismatched frozen SHA256SUMS pin: {rel}")
        selected.append((path, rel, sha))
    return root, sha_file, selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", choices=("twostep", "solver", "exact", "bounds"))
    parser.add_argument("--instance-list", type=Path)
    parser.add_argument("--instance-root", type=Path, default=ROOT / "experiments/mvp/corpus/test")
    parser.add_argument("--rung", choices=("tiny", "small", "medium"), default="tiny")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--time-limit-s", type=float, default=60.0)
    parser.add_argument("--max-nodes", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT,
                        help="runtime root containing src/; instances and outputs stay in the original repository")
    parser.add_argument("--rerun-ineligible", action="store_true",
                        help="archive old ineligible checkpoints beneath each instance before recomputing")
    args = parser.parse_args()
    if not math.isfinite(args.time_limit_s) or args.time_limit_s <= 0:
        parser.error("time limit must be finite and positive")
    if args.method == "exact" and args.time_limit_s > 60:
        parser.error("exact deadline must be <=60 seconds")
    if args.max_nodes < 1 or (args.limit is not None and args.limit < 1):
        parser.error("max-nodes and limit must be positive")
    root, sha_file, selected = selection(args)
    source_root = args.source_root.resolve()
    if not (source_root / "src/stowage").is_dir():
        raise ValueError(f"source root lacks src/stowage: {source_root}")
    sys.path.insert(0, str(source_root / "src"))
    imported_sources = source_hashes(source_root)

    from stowage.bounds import lower_bound
    from stowage.checker import report_matches_inputs
    from stowage.feasibility import FeasibilityConfig
    from stowage.io import load_instance
    from stowage.planner import run_planner
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.versions import SCHEMA_VERSION, component_versions
    from eval.harness_integrity import report_has_current_versions

    if rule_freeze_v1_enabled():
        raise RuntimeError("Rule freeze must remain OFF")
    # APPROXIMATION: D79 disables the C2 global screen; full checking remains the gate.
    feasibility = FeasibilityConfig(extra={"global_screen": 0})
    planner = None
    if args.method == "twostep":
        from stowage.solver.twostep import TwoStepPlanner
        planner = TwoStepPlanner(feasibility_config=feasibility)
    elif args.method == "solver":
        from stowage.solver.scalable import ScalableConfig, ScalableSolver
        planner = ScalableSolver(ScalableConfig(feasibility_config=feasibility))
    elif args.method == "exact":
        from stowage.solver.exact import ExactPlanner
        planner = ExactPlanner(num_workers=1, max_nodes=args.max_nodes, feasibility_config=feasibility)
    out = (args.output_dir or ROOT / f"experiments/mvp/runs/test-{args.method}").resolve()
    fence = ROOT / "experiments/mvp/runs"
    out.relative_to(fence)
    if not out.name.startswith("test-"):
        raise ValueError("output directory must be a test-* run directory")
    out.mkdir(parents=True, exist_ok=True)
    config = dict(method=args.method, time_limit_s=args.time_limit_s, max_nodes=args.max_nodes,
                  seed=args.seed, allow_repair=False, c2_global_screen=0,
                  rule_freeze_v1_enabled=False, native_threads=1,
                  planner=None if planner is None else planner.name,
                  planner_version=None if planner is None else planner.version)
    config_sha = json_hash(config)
    manifest_path = out / "manifest.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if old and old.get("config_sha256") != config_sha:
        raise ValueError("existing run uses a different configuration; choose a new test-* output")
    previous_selection = {item["path"]: item for item in old.get("selection", [])}
    for _, rel, sha in selected:
        if rel in previous_selection and previous_selection[rel]["input_sha256"] != sha:
            raise ValueError(f"previously evaluated input hash changed: {rel}")
        previous_selection[rel] = dict(path=rel, input_sha256=sha)
    manifest = dict(config=config, config_sha256=config_sha,
                    planner_kind="classical_reference", method=args.method, runtime="python",
                    base_model=None, adapter_sha256=None, adapter_clean=None,
                    created_at=old.get("created_at", datetime.now(timezone.utc).isoformat()),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                    command=shlex.join([sys.executable, *sys.argv]),
                    instance_root=str(root.relative_to(ROOT)), sha256sums_sha256=digest(sha_file),
                    component_versions=component_versions(), source_at_import=imported_sources,
                    source_at_import_sha256=json_hash(imported_sources),
                    source_root=str(source_root),
                    selection=sorted(previous_selection.values(), key=lambda row: row["path"]),
                    complete=False)
    write(manifest_path, manifest)
    records = []
    selected_keys = {rel.removesuffix(".json") for _, rel, _ in selected}
    # Preserve previous rungs when a later explicit instance-list adds new rows.
    for checkpoint in sorted((out / "instances").glob("*/record.json")):
        cached = json.loads(checkpoint.read_text())
        if cached.get("spec_key") not in selected_keys:
            if cached.get("config_sha256") != config_sha:
                raise ValueError(f"existing record configuration differs: {checkpoint}")
            records.append(cached)
    ids = set()
    for path, rel, sha in selected:
        if digest(path) != sha:
            raise ValueError(f"input changed before load: {rel}")
        instance = load_instance(path)
        if args.instance_list is None and instance.size_class != args.rung:
            raise ValueError(f"selection rung {instance.size_class} differs from --rung {args.rung}")
        if args.method == "exact" and instance.size_class != "tiny":
            raise ValueError("exact references are restricted to tiny")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", instance.id) or instance.id in ids:
            raise ValueError(f"unsafe or duplicate instance id: {instance.id}")
        ids.add(instance.id)
        dest = out / "instances" / instance.id
        checkpoint = dest / "record.json"
        if checkpoint.exists():
            record = json.loads(checkpoint.read_text())
            if args.rerun_ineligible and record.get("reference_eligible") is False:
                archive = dest / "pilot-before-source-snapshot"
                if archive.exists():
                    archive = dest / ("pilot-before-source-snapshot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
                archive.mkdir(parents=True)
                for artifact in ("record.json", "result.json", "report.json"):
                    original = dest / artifact
                    if original.exists():
                        original.rename(archive / artifact)
                print(f"archived ineligible {args.method} {instance.id} to {archive}", flush=True)
        if checkpoint.exists():
            record = json.loads(checkpoint.read_text())
            if record.get("input_sha256") != sha or record.get("config_sha256") != config_sha:
                raise ValueError(f"checkpoint identity/configuration differs: {instance.id}")
            required = [dest / "report.json"]
            if args.method != "bounds":
                required.append(dest / "result.json")
            if not all(p.is_file() for p in required):
                raise ValueError(f"incomplete checkpoint: {instance.id}")
            records.append(record)
            print(f"resume {args.method} {instance.id}", flush=True)
            continue
        before = source_hashes(source_root)
        started = perf_counter()
        result = report = None
        if planner is not None:
            result, report = run_planner(planner, instance, seed=args.seed,
                                        time_limit_s=args.time_limit_s, allow_repair=False)
        pipeline_s = perf_counter() - started
        bounds_started = perf_counter()
        bound = lower_bound(instance)
        bounds_s = perf_counter() - bounds_started
        binding = bool(report is not None and result is not None and result.plan is not None
                       and report_matches_inputs(report, instance, result.plan)
                       and report_has_current_versions(report))
        after = source_hashes(source_root)
        changed_since_import = source_changes(imported_sources, after)
        changed_during_instance = source_changes(before, after)
        relevant_drift = relevant_changes(sorted(set(changed_since_import + changed_during_instance)))
        if digest(path) != sha:
            raise ValueError(f"input changed during execution: {rel}")
        count = None if report is None or report.count is None else report.count.model_dump(mode="json")
        hard = [] if report is None else [v for v in report.violations if v.severity == "hard"]
        exact_label = None
        if args.method == "exact":
            if not binding or report is None or not report.passed or result.plan is None:
                exact_label = "no validated incumbent"
            elif result.metrics.get("optimality_status") == "OPTIMAL":
                exact_label = "optimal"
            elif result.status == "timeout":
                exact_label = "best-found (timeout)"
            else:
                exact_label = "best-found"
        record = dict(instance_id=instance.id, input_sha256=sha, spec_key=rel.removesuffix(".json"),
                      split="test", rung=instance.size_class, instance_path=str(path.relative_to(ROOT)),
                      planner="bounds.lower_bound" if planner is None else planner.name,
                      config_sha256=config_sha, result=None if result is None else result.model_dump(mode="json"),
                      report=None if report is None else report.model_dump(mode="json"), count=count,
                      shippable=bool(result is not None and binding and result.shippable and not relevant_drift),
                      pipeline_shippable=bool(result is not None and binding and result.shippable),
                      reference_eligible=not relevant_drift,
                      reference_exclusion_reason="relevant_source_drift" if relevant_drift else None,
                      report_binding_valid=binding, checker_passed=None if report is None else report.passed,
                      checker_outcome="not_applicable" if planner is None else "passed" if report and report.passed else "failed" if report else "not_run",
                      first_failing_rule=None if not hard else hard[0].rule_id,
                      fallback_needed=bool(result and result.metrics.get("fallback_needed")),
                      timing=dict(total_s=bounds_s if planner is None else pipeline_s,
                                  plan_s=0.0 if result is None else result.metrics.get("pipeline_timing", {}).get("planning_s", result.wall_time_s),
                                  planner_reported_plan_s=None if result is None else result.wall_time_s,
                                  bounds_s=bounds_s),
                      lower_bound=bound.model_dump(mode="json"), exact_label=exact_label,
                      component_versions=component_versions(), source_before=before, source_after=after,
                      source_at_import=imported_sources,
                      source_root=str(source_root),
                      source_at_import_sha256=json_hash(imported_sources),
                      source_changed_since_import=changed_since_import,
                      source_changed_during_instance=changed_during_instance,
                      relevant_source_changes=relevant_drift,
                      source_drift=before != after or before != imported_sources,
                      source_before_sha256=json_hash(before), source_after_sha256=json_hash(after),
                      instance=dict(vessel_id=instance.vessel.id, vessel_teu=instance.vessel.nominal_teu,
                                    objective=instance.objective.model_dump(mode="json"),
                                    boxes_to_load=len(instance.load_list), ports=len(instance.rotation.calls), family=instance.family,
                                    voyage=None if instance.voyage is None else instance.voyage.model_dump(mode="json")),
                      resources=dict(process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                                     children_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                                     rss_scope="process lifetime high-water mark; children separate"),
                      repaired=False, official=False, tiny_run=False,
                      reference_scope="MVP frozen test references; standalone campaign, not an official harness epoch")
        if result is not None:
            # File envelope is load_result-compatible; record.result remains
            # the unwrapped PlannerResult.model_dump required by the harness.
            write(dest / "result.json", {"schema_version": SCHEMA_VERSION,
                                         **result.model_dump(mode="json")})
        write(dest / "report.json", None if report is None else report.model_dump(mode="json"))
        write(dest / "record.json", record)
        records.append(record)
        _write_records(out, records)
        print(f"done {args.method} {instance.id} checker={record['checker_passed']} shippable={record['shippable']} total_s={record['timing']['total_s']:.3f} exact={exact_label}", flush=True)
    _write_records(out, records)
    manifest.update(complete=True, n_records=len(records),
                    source_drift=any(r.get("source_drift") for r in records),
                    completed_at=datetime.now(timezone.utc).isoformat())
    write(manifest_path, manifest)


def _write_records(out, records):
    path = out / "records.jsonl"
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text("".join(json.dumps(json_safe(r), sort_keys=True, allow_nan=False) + "\n"
                            for r in sorted(records, key=lambda row: row["spec_key"])))
    temp.replace(path)


if __name__ == "__main__":
    main()
