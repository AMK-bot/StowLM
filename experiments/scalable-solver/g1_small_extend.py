"""Four disjoint one-core small-instance extensions using the frozen G1 driver.

A frozen, audited roster is partitioned by original index modulo worker count.
No constraints or solver search settings change. A scoped output-only wrapper
captures the public planner's returned candidate (including partial plans);
all qualification still uses the unchanged driver's ballast/checker/index gate.
Candidate artifacts are unverified and never presented as approved gold.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

SCOPE = Path(__file__).resolve().parent
ROOT = SCOPE.parent.parent


def driver():
    spec = importlib.util.spec_from_file_location("frozen_g1_small_extension", SCOPE / "g1_pilot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checked_entries(roster):
    """Validate frozen metadata and input roots before opening any instance."""
    from eval.splits import spec_key, split_of
    allowed = {(ROOT / "experiments/instances-v3").resolve(),
               (ROOT / "experiments/base-mask/instances-v3-val-c2").resolve(),
               (ROOT / "experiments/base-mask/instances-v3-val-c2-small").resolve()}
    entries = roster["entries"]
    seen = set()
    for entry in entries:
        shard = entry["shard"]
        if entry.get("rung") != "small" or entry.get("profile") != "small":
            raise ValueError("extension accepts only small roster entries")
        if shard.get("split") not in ("train", "val"):
            raise ValueError("refusing test/unknown split before instance access")
        spec = shard.get("split_spec")
        if spec is None or split_of(spec) != shard["split"] or spec_key(spec) != shard["spec_key"]:
            raise ValueError("roster frozen specification split/key mismatch")
        root = Path(entry["manifest_root"]).resolve()
        if root not in allowed or not (root / entry["relative"]).resolve().is_relative_to(root):
            raise ValueError("roster input path is outside an admitted source root")
        if shard["sha256"] in seen:
            raise ValueError("duplicate input SHA in frozen roster")
        seen.add(shard["sha256"])
    return entries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-index", type=int, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--stop-admission-utc", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT23:10:00Z"))
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4 or not 0 <= args.worker_index < args.workers:
        parser.error("1 <= workers <= 4 and 0 <= worker-index < workers required")
    args.output, args.roster = args.output.resolve(), args.roster.resolve()
    if not args.output.is_relative_to(SCOPE):
        parser.error("output must be under scalable-solver experiments")
    stop = datetime.fromisoformat(args.stop_admission_utc.replace("Z", "+00:00"))
    if stop.tzinfo is None:
        parser.error("stop admission must have a timezone")
    stop = stop.astimezone(timezone.utc)
    frozen = driver()
    roster = json.loads(args.roster.read_text())
    all_entries = checked_entries(roster)
    entries = [dict(entry, roster_index=index) for index, entry in enumerate(all_entries) if index % args.workers == args.worker_index]
    if args.list_only:
        print(json.dumps(dict(worker=args.worker_index, selections=entries), indent=2)); return 0
    from stowage.solver.scalable import ScalableConfig, ScalableSolver, SCALABLE_VERSION
    from stowage.versions import component_versions
    from stowage.io import load_instance, save_plan
    from eval.splits import spec_from_instance, spec_key, split_of
    helper = frozen.helpers()
    output = args.output / f"worker-{args.worker_index}"
    identity = dict(schema="scalable-v3-g1-small-extension-v1", source_sha256=helper.source_hash(),
        driver_sha256=frozen.sha(Path(__file__)), frozen_driver_sha256=frozen.sha(SCOPE / "g1_pilot.py"),
        roster=str(args.roster), roster_sha256=frozen.sha(args.roster), versions=component_versions(),
        solver_version=SCALABLE_VERSION, solver_config=asdict(ScalableConfig()), diagnostic_c2_off=False,
        worker_index=args.worker_index, workers=args.workers, cores=1, budgets_s={"small":120},
        overhead_reserve_s=60, stop_admission_utc=stop.isoformat(), solver_seed=0,
        variant_note="Default 120-second small search; output-only returned-plan capture; do not pool source cohorts")
    availability = dict(small=dict(eligible_train_val=len(all_entries), selected=len(entries), status="available" if entries else "empty_worker"))
    snapshot = dict(identity=identity, selections=entries, availability=availability,
                    roster_availability=roster.get("availability"), source_manifests=roster.get("source_manifests"))
    target = output / "selected-manifest.json"
    if target.exists() and json.loads(target.read_text()) != snapshot:
        raise ValueError("frozen roster/source/config changed; choose a fresh output")
    frozen.save(target, snapshot)
    rows = []
    measure_args = argparse.Namespace(solver_seed=0, overhead_reserve_s=60, ballast_limit_s=30)
    for entry in entries:
        name = f"small-{entry['shard']['seed']}-{entry['shard']['sha256'][:10]}"
        target = output / "results" / f"{name}.json"
        if target.exists():
            row = json.loads(target.read_text())
            if row["identity"] != identity or row["selection"] != entry:
                raise ValueError("result resume identity mismatch")
            rows.append(row); continue
        now = datetime.now(timezone.utc)
        row = dict(identity=identity, selection=entry, admitted=False, admission_utc=now.isoformat())
        if (stop-now).total_seconds() < 180:
            row.update(status="not_admitted_deadline", required_seconds=180,
                       remaining_seconds=max(0,(stop-now).total_seconds()))
        else:
            row["admitted"] = True
            load_started = time.monotonic()
            hard_deadline = load_started+180
            try:
                path = (Path(entry["manifest_root"]) / entry["relative"]).resolve()
                if frozen.sha(path) != entry["shard"]["sha256"]:
                    raise ValueError("input SHA differs from frozen roster")
                instance = load_instance(path)
                actual = spec_from_instance(instance)
                if split_of(actual) != entry["shard"]["split"] or spec_key(actual) != entry["shard"]["spec_key"]:
                    raise ValueError("loaded frozen split/key differs from audited roster")
                row.update(input_sha256=frozen.sha(path), input_load_s=time.monotonic()-load_started,
                           boxes=len(instance.load_list), returned_candidates=[])
                original = ScalableSolver.plan
                def capture(solver, instance_arg, *, seed=0, time_limit_s=None):
                    result = original(solver, instance_arg, seed=seed, time_limit_s=time_limit_s)
                    if result.plan is not None:
                        candidate = output / "candidates" / f"{name}.returned-candidate-plan.json"
                        artifact = dict(status=result.status, qualification="Unverified returned candidate; may be partial")
                        try:
                            save_plan(result.plan, candidate)
                            artifact.update(path=str(candidate), sha256=frozen.sha(candidate))
                        except Exception as exc:
                            artifact["save_error"] = f"{type(exc).__name__}: {exc}"
                        row["returned_candidates"].append(artifact)
                    return result
                # Instrumentation only: delegates the unchanged public method,
                # preserves arguments/result and restores it at this boundary.
                with patch.object(ScalableSolver, "plan", capture):
                    row["measurement"] = frozen.measure(instance, measure_args, 120, helper,
                        output / "plans" / f"{name}.approved-plan.json", hard_deadline=hard_deadline)
            except Exception as exc:
                row.update(input_load_s=time.monotonic()-load_started, measurement=dict(status="input_error",
                    error=f"{type(exc).__name__}: {exc}", approved=False, timings_s={"pipeline":0}))
            row["source_sha256_after"] = helper.source_hash()
            row["source_changed_during_run"] = row["source_sha256_after"] != identity["source_sha256"]
            if row["source_changed_during_run"]:
                frozen.quarantine_source_transition(row)
        frozen.save(target, row); rows.append(row)
        frozen.save(output / "summary.json", frozen.summary(rows, availability))
        print(json.dumps(dict(worker=args.worker_index, roster_index=entry["roster_index"],
            source=entry.get("source_label"), seed=entry["shard"]["seed"],
            status=row.get("measurement",{}).get("status",row.get("status")))), flush=True)
    frozen.save(output / "summary.json", frozen.summary(rows, availability))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
