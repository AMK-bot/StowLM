"""Freeze the declared Frontier scale cohort without importing or running planners.

Run once before the experiment. Subsequent invocations verify the existing freeze;
they never replace the registered manifest or its selection snapshots.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FRONTIER = ROOT / "experiments/mvp/frontier"
SUMMARIES = ROOT / "experiments/mvp/strategy/fillsweep/runs/main/maps"
CUTOFF = datetime(2026, 10, 31, 18, 30, tzinfo=timezone.utc)
PASS_IDS = (
    "medium-feeder_500-f40-high-s192200000",
    "medium-feeder_500-f40-high-s192200001",
    "medium-feeder_500-f40-low-s192200000",
    "medium-feeder_500-f40-low-s192200001",
    "medium-feeder_500-f50-low-s192200000",
    "medium-feeder_500-f60-high-s192200000",
    "medium-feeder_800-f40-high-s192200101",
    "medium-feeder_800-f40-low-s192200101",
)
FAIL_GROUPS = ("feeder_800-f40", "feeder_500-f60", "feeder_1700-f40", "feeder_800-f60")
FAIL_IDS = (
    "medium-feeder_800-f40-high-s192200100",
    "medium-feeder_500-f60-high-s192200001",
    "medium-feeder_1700-f40-high-s192200200",
    "medium-feeder_800-f60-high-s192200100",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def file_hashes(directory: Path, *, python_only: bool = False) -> dict[str, str]:
    return {
        relative(path): digest(path)
        for path in sorted(directory.rglob("*.py" if python_only else "*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in (".pyc", ".pyo")
    }


def source_sha256() -> str:
    # run_maps has only standard-library imports at module scope. Calling its
    # published hash function neither imports planner.py nor executes a planner.
    spec = importlib.util.spec_from_file_location("frontier_scale_source_hash", FRONTIER / "run_maps.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.source_sha256()


def planner_version() -> str:
    for node in ast.parse((FRONTIER / "planner.py").read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "VERSION" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("No literal Frontier VERSION")


def verify_existing(manifest: dict) -> None:
    assert manifest["frontier_version"] == planner_version() == "0.9.0"
    assert manifest["source_sha256"] == source_sha256(), "Frontier source hash changed"
    for group in ("frontier_python_sha256", "src_sha256", "params_sha256"):
        for rel, expected in manifest[group].items():
            assert digest(ROOT / rel) == expected, f"Frozen dependency changed: {rel}"
    assert manifest["frontier_python_sha256"] == file_hashes(FRONTIER, python_only=True)
    assert manifest["src_sha256"] == file_hashes(ROOT / "src")
    assert manifest["params_sha256"] == file_hashes(ROOT / "params")
    for row in manifest["selection_evidence"]:
        assert digest(ROOT / row["snapshot"]) == row["sha256"], "Selection snapshot changed"
    for job in manifest["jobs"]:
        assert digest(ROOT / job["input"]) == job["input_sha256"], "Input changed"


def main() -> None:
    target = HERE / "manifest.json"
    if target.exists():
        manifest = json.loads(target.read_text())
        verify_existing(manifest)
        print(json.dumps({"status": "verified_existing", "maps": len(manifest["jobs"]), "manifest_sha256": digest(target)}))
        return

    assert planner_version() == "0.9.0"
    rows = []
    for path in sorted(SUMMARIES.glob("*/summary.json")):
        data = path.read_bytes()
        summary = json.loads(data)
        if summary.get("updated_at", float("inf")) <= CUTOFF.timestamp():
            rows.append((path.parent.name, path, data, summary))
    passing = [map_id for map_id, _, _, s in rows if s.get("selected", {}).get("compact", {}).get("planner", {}).get("passed") is True]
    assert passing == list(PASS_IDS), f"Passing cohort differs from preregistered eight: {passing}"
    failures = [next(map_id for map_id, _, _, s in rows if group in map_id and not s.get("selected", {}).get("compact", {}).get("planner", {}).get("passed")) for group in FAIL_GROUPS]
    assert failures == list(FAIL_IDS), f"Failure picks differ: {failures}"
    selected_ids = list(PASS_IDS) + failures
    assert len(set(selected_ids)) == 12
    by_id = {map_id: (path, data, summary) for map_id, path, data, summary in rows}
    evidence = []
    snapshots = []
    # Preserve the full cutoff-visible selection population, not only winners.
    for map_id, path, data, summary in rows:
        snapshot = HERE / "selection" / map_id / "summary.json"
        evidence.append({"map_id": map_id, "source": relative(path), "snapshot": relative(snapshot),
                         "sha256": hashlib.sha256(data).hexdigest(), "updated_at": summary["updated_at"],
                         "updated_at_utc": datetime.fromtimestamp(summary["updated_at"], timezone.utc).isoformat(),
                         "complete": summary.get("complete"),
                         "selected_compact_planner_passed": summary.get("selected", {}).get("compact", {}).get("planner", {}).get("passed") is True})
        snapshots.append((snapshot, data))
    jobs = []
    for map_id in selected_ids:
        _, _, summary = by_id[map_id]
        job = summary["job"]
        parts = Path(job["input"]).parts
        assert job["split"] == "train" and "train" in parts
        assert "test" not in parts and "test-v3" not in parts
        assert digest(ROOT / job["input"]) == job["input_sha256"]
        n = job["n_loads"]
        jobs.append({"map_id": map_id, "n_boxes": n, "input": job["input"],
                     "input_sha256": job["input_sha256"], "job": job,
                     "selection_reason": "selected_compact_planner_passed" if map_id in PASS_IDS else "first_sorted_nonpassing_in_group",
                     "route": "plan_big" if n >= 121 else "plan_and_check",
                     "route_status": "explicit extrapolation: smallest historical column-route case is 121 boxes; no automatic cutoff exists",
                     "historical_baselines_provenance_only": {
                         "two_step": summary["selected"]["compact"]["default"],
                         "block_rule": summary["selected"]["compact"]["rule"],
                     }})
    x4_ids = [min(j["map_id"] for j in jobs if j["n_boxes"] == n and j["map_id"] in PASS_IDS) for n in (59, 74, 108)]
    manifest = {
        "schema": 1, "experiment": "frontier-scale", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_cutoff_utc": CUTOFF.isoformat(), "selection_cutoff_local": "2026-10-31T14:30:00-04:00",
        "selection_cutoff_field": "summary.updated_at (Unix seconds)",
        "selection_rule": "All eight cutoff-visible selected.compact.planner passes; first sorted nonpass in each declared group",
        "selection_failure_groups": list(FAIL_GROUPS), "map_ids": selected_ids, "passing_selection_ids": list(PASS_IDS),
        "failure_selection_ids": failures, "budget_x4_map_ids": x4_ids,
        "base_budget": {"search_seconds_per_box": 6, "search_cap_seconds": None, "finisher_seconds_per_start": 600, "seed": 0},
        "budget_x4": {"search_seconds_per_box": 24, "search_cap_seconds": None, "finisher_seconds_per_start": 2400, "seed": 0},
        "frontier_version": planner_version(), "source_sha256": source_sha256(),
        "source_sha256_function": "experiments/mvp/frontier/run_maps.py:source_sha256",
        "frontier_python_sha256": file_hashes(FRONTIER, python_only=True),
        "src_sha256": file_hashes(ROOT / "src"), "params_sha256": file_hashes(ROOT / "params"),
        "historical_baseline_warning": "Selection snapshots and historical_baselines_provenance_only are provenance, not the final required same-run two-step/block-rule measurements. Rerun both baselines in this experiment and freshly audit every pass with the full checker without repair.",
        "route_assumption": "plan_big at n_boxes >= 121; otherwise plan_and_check. This explicit routing assumption follows the historical medium column route (RESULTS.md and dev/medium12.txt), whose smallest case has 121 boxes; the caller has no automatic size cutoff. The plan_big master_s default remains 240 seconds.",
        "plan_big_master_seconds": 240,
        "selection_evidence": evidence, "jobs": jobs,
    }
    # Complete and verify snapshots before publishing the frozen manifest. No
    # planner may be launched until this manifest exists.
    for path, data in snapshots:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            assert path.read_bytes() == data, f"Existing selection snapshot differs: {path}"
        else:
            path.write_bytes(data)
    verify_existing(manifest)
    with target.open("x") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"status": "created", "maps": len(jobs), "selection_snapshots": len(evidence),
                      "source_sha256": manifest["source_sha256"], "manifest_sha256": digest(target), "budget_x4_map_ids": x4_ids}))


if __name__ == "__main__":
    main()
