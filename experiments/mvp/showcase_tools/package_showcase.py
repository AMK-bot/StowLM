"""Package a solved/failed planner artifact for the demo, without model claims.

Example (run from the repository root)::

    nice -n 10 uv run python experiments/mvp/showcase_tools/package_showcase.py \
        --instance I.json --result R.json \
        --run-folder experiments/mvp/runs/showcase-solver --report C.json

The printed timeline command uses the UI's existing resumable implementation,
one worker, and a cache inside the run folder. Running it is a separate compute
job: reserve its resources in the fleet ledger first. No UI files are modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[3]
for directory in (ROOT, ROOT / "src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[variable] = "1"


def read_json(path):
    path = Path(path)
    content = path.read_bytes()
    return json.loads(gzip.decompress(content) if path.suffix == ".gz" else content)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timeline_argv(run_folder, entry, *, max_frames=None):
    """The UI owns physics/keyframe policy; its cache stays within our fence."""
    run_folder = Path(run_folder).resolve()
    argv = ["nice", "-n", "10", "uv", "run", "python",
            str(ROOT / "experiments/mvp/showcase_tools/precompute_timeline.py"),
            "--run-folder", str(run_folder), "--entry", str(entry),
            "--workers", "1", "--cache-dir", str(run_folder / "timeline-cache"),
            "--numerical-cache-root", str(run_folder.parent / (run_folder.name + "-numerical-cache"))]
    if max_frames is not None:
        argv += ["--max-frames", str(max_frames)]
    return argv


def package_run(instance_path, artifact_path, run_folder, *, report_path=None,
                method=None, status=None, command=None, bound_path=None):
    """Return the entry directory. No solving, repair, ballast, or physics jobs.

    A supplied checker report is reused only when bound to the exact input and
    plan; otherwise a fresh full check runs. The app independently checks again.
    The result keeps canonical PlannerResult fields at top level, with the
    app-supported input_sha256/report envelope for self-contained discovery.
    """
    from app.identity import instance_content_digest
    from app.inputs import load_inputs
    from stowage.checker import check, report_matches_inputs
    from stowage.io import load_instance
    from stowage.model import CheckReport, MovePlan, PlannerResult
    from stowage.moves import apply_plan
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.versions import SCHEMA_VERSION, component_versions

    instance_path, artifact_path, run_folder = map(Path, (instance_path, artifact_path, run_folder))
    manifest_path = run_folder / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    forbidden = {"planner_kind", "base_model", "adapter_sha256", "mask_version"}
    if forbidden.intersection(manifest):
        raise ValueError("Refusing to modify model-manifest run folder")
    instance = load_instance(instance_path)
    if Path(instance.id).name != instance.id or instance.id in {".", ".."}:
        raise ValueError("instance.id must be a single safe directory name")
    source = read_json(artifact_path)
    artifact = source.get("result", source.get("planner_result", source))
    is_result = "planner" in artifact
    if is_result:
        payload = {key: value for key, value in artifact.items()
                   if key not in {"schema_version", "input_sha256", "instance", "instance_id", "report", "check_report"}}
        result = PlannerResult.model_validate(payload)
    else:
        plan = MovePlan.model_validate(artifact)
        result = PlannerResult(status="ok", plan=plan, planner="supplied-plan",
                               planner_version=SCHEMA_VERSION, provenance=plan.provenance,
                               message="Imported MovePlan; original planning time unavailable.")
    plan = result.plan
    if plan is not None and (plan.instance_id != instance.id or plan.call_index != instance.call_index):
        raise ValueError("Artifact instance_id/call_index differs from selected instance")
    report, report_source, checker_seconds = None, None, None
    if plan is not None:
        saved_report = read_json(report_path) if report_path else source.get("report", source.get("check_report"))
        if saved_report is not None:
            saved_report = saved_report.get("report", saved_report)
            candidate = CheckReport.model_validate(saved_report)
            if report_matches_inputs(candidate, instance, plan):
                report, report_source = candidate, "provided report; exact content binding verified"
        if report is None:
            from run_hero import isolate_caches
            # Numerical tables are not UI plan records; keep their writes in
            # a separate owned sibling rather than the shared checker cache.
            isolate_caches(run_folder.resolve().parent / (run_folder.name + "-numerical-cache"))
            started = monotonic()
            report = check(instance, plan)
            checker_seconds = monotonic() - started
            report_source = "fresh full checker"
        result._record_full_check(instance, report)
    shippable = result.shippable
    replay = apply_plan(instance, plan) if plan is not None else None
    if status == "passed" and not shippable:
        raise ValueError("Cannot label an unshippable result as passed")
    if status not in {None, "passed", "failed", "partial"}:
        raise ValueError("status must be passed, failed, or partial")
    derived_status = ("passed" if shippable else "partial" if plan is not None and
                      (result.status != "ok" or report.shut_out or replay.executed < len(plan.moves)) else "failed")
    bundle_status = status or derived_status
    # A replay label never changes the canonical planner/checker status.
    hard = next((violation.model_dump(mode="json") for violation in report.violations
                 if violation.severity == "hard"), None) if report else None
    entry = run_folder / "instances" / instance.id
    entry.mkdir(parents=True, exist_ok=True)
    instance_target = entry / ("instance.json.gz" if instance_path.suffix == ".gz" else "instance.json")
    instance_target.write_bytes(instance_path.read_bytes())
    input_sha = sha256(instance_target)
    report_payload = report.model_dump(mode="json") if report else None
    result_payload = result.model_dump(mode="json")
    result_payload["schema_version"] = plan.schema_version if plan else SCHEMA_VERSION
    result_payload["input_sha256"] = input_sha
    if report is not None:
        result_payload["report"] = report_payload
        write_json(entry / "report.json", report_payload)
    elif (entry / "report.json").exists():
        (entry / "report.json").unlink()
    write_json(entry / "result.json", result_payload)
    if bound_path:
        write_json(entry / "bound.json", read_json(bound_path))
    entry_relative = (entry / "result.json").relative_to(run_folder)
    timeline = timeline_argv(run_folder, entry_relative)
    loaded_ids = {placement.container_id for placement in replay.final_state.ship.placements} if replay else set()
    outcome = {
        "status": bundle_status, "planner_status": result.status,
        "planner": result.planner, "method": method or result.planner,
        "instance_id": instance.id, "input_sha256": input_sha,
        "instance_content_sha256": instance_content_digest(instance),
        "checker_passed": report.passed if report else None,
        "departure_approved": shippable and bundle_status == "passed",
        "checker_report_source": report_source, "checker_seconds": checker_seconds,
        "first_hard_violation": hard,
        "checker_unavailable_reason": None if report else "Planner produced no plan",
        "planned_moves": len(plan.moves) if plan else None,
        "executed_moves": replay.executed if replay else 0,
        "physical_replay_legal": replay.legal if replay else None,
        "loaded_required": len(set(instance.load_list) & loaded_ids),
        "required_loads": len(instance.load_list),
        "count": report.count.model_dump(mode="json") if report and report.count else None,
        "planning_seconds": result.wall_time_s if is_result else None,
        "pipeline_timing": result.metrics.get("pipeline_timing"),
        "repaired": result.repaired, "repair_note": result.repair_note,
        "repair_status": result.metrics.get("repair_status"),
        "fallback_needed": result.metrics.get("fallback_needed", result.repaired),
        "metrics": result.metrics, "message": result.message,
        "rule_freeze_v1": rule_freeze_v1_enabled(),
        "timeline_command": shlex.join(timeline) if plan else None,
        "timeline_cache_dir": str((run_folder / "timeline-cache").resolve()),
        "notice": "Partial replay only; departure is not approved." if bundle_status == "partial" else
                  "Full checker and shipping gates passed." if bundle_status == "passed" else
                  "Failed/unavailable plan; departure is not approved.",
    }
    if plan is not None:
        loaded = load_inputs(run_folder=run_folder, entry=entry_relative)
        if loaded.instance.id != instance.id or loaded.plan.moves != plan.moves:
            raise ValueError("App import round-trip changed the selected instance/plan")
        outcome["ui_import"] = "passed; app.inputs.load_inputs round-trip"
    else:
        outcome["ui_import"] = "unavailable: app no-plan ordinary solver import requires model manifest; artifact retained only"
    write_json(entry / "outcome.json", outcome)
    entries = {item["instance_id"]: item for item in manifest.get("entries", [])}
    entries[instance.id] = {"instance_id": instance.id, "entry": str(entry_relative),
                            "input_sha256": input_sha, "status": bundle_status,
                            "checker_passed": outcome["checker_passed"],
                            "departure_approved": outcome["departure_approved"]}
    statuses = {item["status"] for item in entries.values()}
    manifest.update({"format": "showcase-reference-v1", "source": "solver-reference",
                     "method": method or result.planner, "planner": result.planner,
                     "status": next(iter(statuses)) if len(statuses) == 1 else "mixed",
                     "created_at": manifest.get("created_at", datetime.now(timezone.utc).isoformat()),
                     "updated_at": datetime.now(timezone.utc).isoformat(),
                     "command": command or shlex.join(sys.argv),
                     "versions": component_versions(), "entries": list(entries.values())})
    write_json(manifest_path, manifest)
    hashes = [f"{sha256(path)}  {path.relative_to(entry)}" for path in sorted(entry.iterdir())
              if path.is_file() and path.name != "SHA256SUMS"]
    (entry / "SHA256SUMS").write_text("\n".join(hashes) + "\n")
    return entry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instance", required=True)
    parser.add_argument("--result", "--plan", dest="artifact", required=True)
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--report")
    parser.add_argument("--bound")
    parser.add_argument("--method")
    parser.add_argument("--status", choices=("passed", "failed", "partial"))
    parser.add_argument("--command", help="Exact solver command; defaults to this packaging invocation")
    parser.add_argument("--precompute-timeline", action="store_true",
                        help="Run separate physics job now; caller must have admitted/reserved it")
    parser.add_argument("--max-frames", type=int, help="Limit timeline to a timing probe; resumable")
    args = parser.parse_args(argv)
    entry = package_run(args.instance, args.artifact, args.run_folder, report_path=args.report,
                        method=args.method, status=args.status, command=args.command, bound_path=args.bound)
    outcome = read_json(entry / "outcome.json")
    print(json.dumps({"entry": str(entry / "result.json"), "status": outcome["status"],
                      "checker_passed": outcome["checker_passed"],
                      "timeline_command": outcome["timeline_command"]}, indent=2))
    if args.precompute_timeline:
        if outcome["timeline_command"] is None:
            parser.error("No plan exists for timeline computation")
        return subprocess.run(timeline_argv(args.run_folder,
                              (entry / "result.json").relative_to(args.run_folder), max_frames=args.max_frames),
                              cwd=ROOT, check=False).returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
