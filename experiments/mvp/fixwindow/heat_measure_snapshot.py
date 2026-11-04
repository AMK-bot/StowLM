"""Private frozen-runtime full-checker comparison of the proposed E8 heat-ID patch.

All writes are confined to fixwindow. `prepare` only reads JSON and hashes;
`run` executes the full checker twice per saved plan, never a planner. Parent
compute admission is required before `run`. Missing official references remain
missing, and ablation/prefix evidence is labeled separately.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time
import traceback

sys.dont_write_bytecode = True
for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
             "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
FENCE = Path(__file__).resolve().parent
ROOT = FENCE / "heat_runtime"
LIVE_ROOT = FENCE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
CORPUS = ROOT / "experiments/mvp/corpus"
RUNS = ROOT / "experiments/mvp/runs"
COHORTS = {
    "tiny_exact": ("test-exact", "tiny", "primary exact"),
    "tiny_solver": ("test-solver", "tiny", "primary solver"),
    "tiny_twostep": ("test-twostep", "tiny", "primary two-step"),
    "small_solver_screen": ("test-solver-small-v21-screen", "small", "official screened solver"),
    "small_solver_screen_tail": ("test-solver-small-v21-screen-tail", "small", "official tail attempt; separate checkpoint cohort"),
    "small_solver_no_screen_original": ("test-solver-small-v2", "small", "ablation: original ten, unchanged inputs"),
    "small_solver_no_screen_added": ("test-solver-small-v21-noscreen-extra", "small", "ablation: added five v2.1 inputs"),
}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def object_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def within_fence(path):
    path = Path(path).resolve()
    if not path.is_relative_to(FENCE):
        raise ValueError(f"Output outside fixwindow: {path}")
    return path


def save(path, value):
    path = within_fence(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temp.replace(path)


def install_write_guard():
    """Allow existing shared-cache reads; block every out-of-fence mutation."""
    denied = []
    def check(path, event):
        if isinstance(path, int):
            return
        target = Path(os.fsdecode(path)).resolve()
        if not target.is_relative_to(FENCE) or target.is_relative_to(ROOT):
            denied.append({"event": event, "path": str(target)})
            raise PermissionError(f"heat measurement read-only fence: {target}")
    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            if ((isinstance(mode, str) and any(c in mode for c in "wax+"))
                    or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                check(path, event)
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime", "os.truncate"}:
            check(args[0], event)
        elif event in {"os.rename", "os.link", "os.symlink"}:
            check(args[0], event)
            check(args[1], event)
    sys.addaudithook(audit)
    return denied


def prepare(args):
    destination = within_fence(args.inventory)
    if destination.exists():
        raise ValueError("Inventory is immutable; specify a fresh --inventory path")
    manifest_path = CORPUS / "manifest.json"
    manifest = read(manifest_path)
    selected = [r for r in manifest["files"] if r.get("split") == "test"
        and r.get("rung") in ("tiny", "small") and r.get("active_for_current_release", True)]
    assert Counter(r["rung"] for r in selected) == {"tiny": 15, "small": 15}
    assert len({r["sha256"] for r in selected}) == 30
    admitted = {r["sha256"]: r for r in selected}
    test_hash_list = CORPUS / "test/SHA256SUMS"
    listed = {line.split(maxsplit=1)[1].lstrip("*"): line.split(maxsplit=1)[0]
              for line in test_hash_list.read_text().splitlines() if line.strip()}
    release_path = ROOT / "experiments/mvp/refs_tools/small-v21-release-frozen.json"
    release = read(release_path)
    assert release["current_release"] == "v2.1"
    inputs = []
    for row in selected:
        path = CORPUS / row["path"]
        digest = sha(path)
        assert digest == row["sha256"]
        hash_list_match = listed.get(row["path"], listed.get(row["path"].removeprefix("test/")))
        assert hash_list_match == digest, (path, "test SHA256SUMS mismatch")
        if row["rung"] == "small":
            assert release["hashes"][row["path"]] == digest
        instance = read(path)
        inputs.append(dict(path=str(path), relative_path=row["path"], sha256=digest,
            instance_id=instance["id"], rung=row["rung"], ship=row["ship"],
            family=row["family"], release_epoch=row.get("release_epoch"),
            load_count=len(instance["load_list"])))
    inputs_by_hash = {r["sha256"]: r for r in inputs}
    plans, slots, exclusions = [], [], []
    for cohort, (folder, rung, label) in COHORTS.items():
        for checkpoint in sorted((RUNS / folder / "instances").glob("*/record.json")):
            row = read(checkpoint)
            digest = row.get("input_sha256")
            if digest not in admitted or inputs_by_hash[digest]["rung"] != rung:
                exclusions.append(dict(path=str(checkpoint), reason="unmatched frozen input or rung"))
                continue
            selected_input = inputs_by_hash[digest]
            assert row["instance_id"] == selected_input["instance_id"]
            result = row.get("result") or {}
            plan = result.get("plan")
            eligible = row.get("reference_eligible") is True
            completed = row.get("completed", "source_after" in row) is True
            slot = dict(cohort=cohort, label=label, checkpoint=str(checkpoint),
                checkpoint_sha256=sha(checkpoint), input_sha256=digest,
                instance_id=row["instance_id"], plan_present=isinstance(plan, dict),
                completed=completed, reference_eligible=eligible,
                saved_checker_passed=row.get("checker_passed"),
                saved_status=result.get("status"), measurable=False)
            slots.append(slot)
            if not isinstance(plan, dict) or not eligible or not completed:
                slot["blocker"] = "no final plan" if not isinstance(plan, dict) else "incomplete/ineligible reference checkpoint"
                continue
            canonical = checkpoint.parent / "result.json"
            if canonical.exists():
                # Older tiny journals omit result.schema_version; the saved
                # plan itself must still match the canonical result exactly.
                assert read(canonical).get("plan") == plan, canonical
            assert plan["instance_id"] == selected_input["instance_id"]
            plan_id = f"{cohort}--{checkpoint.parent.name}"
            plan_path = destination.parent / "plans" / (plan_id + ".json")
            save(plan_path, plan)
            entry = dict(key=plan_id, cohort=cohort, label=label, input=selected_input,
                plan_path=str(plan_path), plan_sha256=sha(plan_path),
                plan_object_sha256=object_sha(plan), checkpoint=str(checkpoint),
                checkpoint_sha256=slot["checkpoint_sha256"],
                saved_checker_passed=row.get("checker_passed"), saved_status=result.get("status"),
                certified_full_plan=row.get("shippable") is True,
                role="reference" if row.get("shippable") is True else "failed/partial reference diagnostic")
            plans.append(entry)
            slot.update(measurable=True, key=plan_id)
    # The source diagnostic pins attempt 3's exact 17/18 prefix, not another attempt.
    explanation_path = RUNS / "showcase-v2-800-heat-explanation/explanation.json"
    explanation = read(explanation_path)
    showcase_path = CORPUS / "showcase/feeder_800-small.json"
    prefix_path = RUNS / "showcase-v2-800-attempt3/raw-plan.json"
    assert sha(showcase_path) == "d2453d9d2055f306fd6cbcea461fdb3543dd754e36af54eb5b9f218bccaa7694"
    assert sha(showcase_path) == explanation["instance_sha256"]
    assert sha(prefix_path) == explanation["plan_sha256"]
    prefix = read(prefix_path); instance = read(showcase_path)
    assert prefix["instance_id"] == instance["id"]
    private_prefix = destination.parent / "plans/showcase_feeder800_prefix.json"
    save(private_prefix, prefix)
    showcase = dict(key="showcase_feeder800_prefix", cohort="showcase_prefix", label="17/18 prefix diagnostic; never certified full plan",
        input=dict(path=str(showcase_path), relative_path="showcase/feeder_800-small.json", sha256=sha(showcase_path),
            instance_id=instance["id"], rung="small", ship="feeder_800", family="random_yard", load_count=len(instance["load_list"])),
        plan_path=str(private_prefix), plan_sha256=sha(private_prefix), plan_object_sha256=object_sha(prefix),
        original_plan_path=str(prefix_path), original_plan_sha256=sha(prefix_path),
        diagnostic_binding_path=str(explanation_path), diagnostic_binding_sha256=sha(explanation_path),
        role="incomplete showcase diagnostic", certified_full_plan=False, saved_checker_passed=False)
    plans.append(showcase)
    coverage = []
    for item in inputs:
        matching = [p for p in plans if p["input"]["sha256"] == item["sha256"]]
        official = [p for p in matching if p["cohort"] in {"tiny_exact", "tiny_solver", "tiny_twostep", "small_solver_screen", "small_solver_screen_tail"}]
        coverage.append(dict(input=item, measurable_reference_plans=len(matching),
            measurable_official_plans=len(official),
            blocker=None if matching else "No complete eligible saved reference plan in approved cohorts",
            official_blocker=None if official else "Official reference unavailable; labeled ablation does not replace it"))
    inventory = dict(created_utc=datetime.now(timezone.utc).isoformat(),
        inputs=inputs, plans=plans, slots=slots, coverage=coverage, exclusions=exclusions,
        manifest=dict(path=str(manifest_path), sha256=sha(manifest_path)),
        small_release=dict(path=str(release_path), sha256=sha(release_path)),
        test_hash_list=dict(path=str(test_hash_list), sha256=sha(test_hash_list)),
        counts=dict(inputs=len(inputs), tiny_inputs=15, small_inputs=15,
            plans=len(plans), plans_by_cohort=dict(Counter(p["cohort"] for p in plans)),
            inputs_with_any_reference=sum(bool(r["measurable_reference_plans"]) for r in coverage),
            inputs_missing_official=sum(not r["measurable_official_plans"] for r in coverage)),
        assumptions=["All approved matching cohorts are separate; no ablation replaces a missing official reference.",
            "Tiny v1-preserved hashes are active in the current v2.1 release; small uses the explicit fifteen-case v2.1 release pins.",
            "Showcase is the exact prefix pinned by the saved heat explanation; full checker failure is expected for incomplete cargo/ballast."])
    save(destination, inventory)
    print(json.dumps(inventory["counts"], indent=2))


@contextmanager
def deadline(seconds):
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*unused):
        raise TimeoutError(f"measurement checker exceeded {seconds}s")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def source_pins():
    pins = {}
    for folder, pattern in (("src/stowage", "*.py"), ("src/stowage/rules", "*.toml"),
            ("src/stowage/rules/imdg/data", "*.json"), ("src/stowage/checker", "*.json"),
            ("params", "*.toml")):
        for path in sorted((ROOT / folder).rglob(pattern)):
            pins[str(path.relative_to(ROOT))] = sha(path)
    return pins


def comparison(baseline, patched):
    if "report" not in baseline or "report" not in patched:
        return dict(direction="unavailable", baseline_passed=baseline.get("report", {}).get("passed"),
            patched_passed=patched.get("report", {}).get("passed"), first_removed_rule=None,
            first_added_rule=None, baseline_error=baseline.get("error"), patched_error=patched.get("error"),
            caveat="A checker phase errored/timed out; no directional verdict is inferred")
    before = baseline["report"]; after = patched["report"]
    left = before["violations"]; right = after["violations"]
    lc = Counter(json.dumps(v, sort_keys=True) for v in left)
    rc = Counter(json.dumps(v, sort_keys=True) for v in right)
    removed = [json.loads(s) for s, n in (lc-rc).items() for _ in range(n)]
    added = [json.loads(s) for s, n in (rc-lc).items() for _ in range(n)]
    def e8(v):
        return v["rule_id"].startswith("expert.e8") or "expert.e8" in v.get("message", "")
    direction = ("fail_to_pass" if not before["passed"] and after["passed"] else
        "pass_to_fail" if before["passed"] and not after["passed"] else
        "changed_same_verdict" if removed or added else "unchanged")
    return dict(direction=direction, baseline_passed=before["passed"], patched_passed=after["passed"],
        first_removed_rule=removed[0]["rule_id"] if removed else None,
        first_added_rule=added[0]["rule_id"] if added else None,
        removed_violations=removed, added_violations=added,
        removed_rule_counts=dict(Counter(v["rule_id"] for v in removed)),
        added_rule_counts=dict(Counter(v["rule_id"] for v in added)),
        baseline_e8=[v for v in left if e8(v)], patched_e8=[v for v in right if e8(v)],
        changed_non_e8=[v for v in removed+added if not e8(v)])


def summarize(output):
    rows = [read(p) for p in sorted(output.glob("cases/*/comparison.json"))]
    cohorts = defaultdict(Counter); instances = defaultdict(list)
    for row in rows:
        cohorts[row["cohort"]][row["comparison"]["direction"]] += 1
        instances[row["input_sha256"]].append(dict(key=row["key"], cohort=row["cohort"],
            direction=row["comparison"]["direction"],
            first_removed_rule=row["comparison"]["first_removed_rule"], first_added_rule=row["comparison"]["first_added_rule"]))
    result = dict(comparisons=len(rows), per_cohort={k: dict(v) for k,v in cohorts.items()},
        per_instance=dict(instances), generated_utc=datetime.now(timezone.utc).isoformat(),
        caveat="Current live baseline checker versus the proposed E8-only function override on frozen saved reference plans; no solving or release certification.")
    save(output / "summary.json", result)
    return result


def assert_snapshot_imports():
    origins = {}
    for name, module in list(sys.modules.items()):
        if name == "stowage" or name.startswith("stowage."):
            filename = getattr(module, "__file__", None)
            if filename:
                path = Path(filename).resolve()
                if not path.is_relative_to(ROOT / "src/stowage"):
                    raise RuntimeError(f"Non-snapshot stowage import: {name}: {path}")
                origins[name] = str(path)
    return origins


def save_live_disclosure(output):
    config_path = output / "config.json"
    if not config_path.is_file():
        return
    config = read(config_path)
    changes = [dict(path=name, snapshot=digest,
        live=sha(LIVE_ROOT/name) if (LIVE_ROOT/name).is_file() else None)
        for name, digest in config["source_pins"].items()
        if not (LIVE_ROOT/name).is_file() or sha(LIVE_ROOT/name) != digest]
    critical = [r for r in changes if not r["path"].startswith(
        ("src/stowage/solver/", "src/stowage/generate/"))]
    save(output / "live-source-disclosure.json", dict(
        runtime_root=str(ROOT), live_root=str(LIVE_ROOT),
        snapshot_source_pins_unchanged=source_pins() == config["source_pins"],
        current_live_changes=changes, checker_dependency_changes=critical,
        caveat="Live changes are disclosed; the frozen runtime and its full source pins are never rewritten or weakened."))
    save(output / "imported-snapshot-modules.json", assert_snapshot_imports())


def run(args):
    output = within_fence(args.output)
    inventory = read(args.inventory)
    candidate = Path(args.candidate).resolve()
    candidate = within_fence(candidate)
    if not candidate.is_file():
        raise ValueError("Candidate scratch source is not ready")
    original_path = ROOT / "src/stowage/checker/rules_special.py"
    original_ast = ast.parse(original_path.read_text())
    candidate_ast = ast.parse(candidate.read_text())
    # Only the special_violations body may differ; refuse unrelated scratch edits.
    strip = lambda tree: [ast.dump(node, include_attributes=False) for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != "special_violations"]
    if strip(original_ast) != strip(candidate_ast):
        raise ValueError("Candidate changes code outside special_violations")
    denied = install_write_guard()
    from stowage.checker import check
    from stowage.checker import rules_special
    from stowage.io import load_instance, load_plan
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    assert_snapshot_imports()
    if rule_freeze_v1_enabled():
        raise ValueError("Measurement requires rule freeze OFF")
    namespace = dict(rules_special.__dict__)
    node = next(n for n in candidate_ast.body if isinstance(n, ast.FunctionDef) and n.name == "special_violations")
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(candidate), "exec"), namespace)
    original_function = rules_special.special_violations
    candidate_function = namespace["special_violations"]
    pins = source_pins()
    config = dict(inventory_sha256=sha(args.inventory), candidate_sha256=sha(candidate),
        baseline_special_sha256=sha(original_path), source_pins=pins,
        driver_sha256=sha(__file__), per_check_deadline_s=args.checker_s,
        runtime_root=str(ROOT), live_root=str(LIVE_ROOT),
        snapshot_manifest_sha256=sha(FENCE / "heat_snapshot_manifest.json"),
        native_threads=1, gpu=False, rule_freeze_v1=False,
        method="full checker twice, function override only; shared caches read-only under audit guard")
    config_sha = object_sha(config)
    config_path = output / "config.json"
    if config_path.exists() and read(config_path) != config:
        raise ValueError("Measurement configuration/source changed; use fresh output")
    save(config_path, config)
    selected = [p for p in inventory["plans"] if (not args.cohort or p["cohort"] in args.cohort)
        and (not args.ship or p["input"]["ship"] == args.ship)
        and (not args.rung or p["input"]["rung"] == args.rung)]
    if args.limit:
        selected = selected[:args.limit]
    if not selected:
        raise ValueError("Selection contains no measured references")
    for item in selected:
        assert_snapshot_imports()
        case = output / "cases" / item["key"]
        combined = case / "comparison.json"
        if combined.exists():
            if read(combined)["config_sha256"] != config_sha:
                raise ValueError("Stale comparison configuration")
            continue
        if source_pins() != pins or sha(candidate) != config["candidate_sha256"]:
            raise ValueError("Live/scratch source drift; stop and use a fresh output")
        assert sha(item["input"]["path"]) == item["input"]["sha256"]
        assert sha(item["plan_path"]) == item["plan_sha256"]
        instance = load_instance(item["input"]["path"])
        plan = load_plan(item["plan_path"])
        phases = {}
        for phase, function in (("baseline", original_function), ("patched", candidate_function)):
            phase_path = case / (phase + ".json")
            if phase_path.exists():
                data = read(phase_path)
                if data.get("config_sha256") != config_sha or not data.get("completed"):
                    raise ValueError("Incomplete/stale phase; use a fresh output")
                phases[phase] = data
                continue
            rules_special.special_violations = function
            data = dict(phase=phase, key=item["key"], cohort=item["cohort"],
                config_sha256=config_sha, input_sha256=item["input"]["sha256"],
                plan_sha256=item["plan_sha256"], pid=os.getpid(), completed=False,
                started_utc=datetime.now(timezone.utc).isoformat())
            save(phase_path, data)
            start = time.perf_counter()
            try:
                with deadline(args.checker_s):
                    report = check(instance, plan)
                origins = assert_snapshot_imports()
                data.update(report=report.model_dump(mode="json"), completed=True,
                    imported_stowage_module_count=len(origins), all_stowage_imports_from_snapshot=True)
            except Exception as exc:
                # A bounded/errored measurement is a retained blocker, not a
                # physical checker verdict; continue to the other frozen plans.
                data.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(),
                    completed=True)
            except BaseException as exc:
                data.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(),
                    interrupted=True)
                raise
            finally:
                data.update(wall_s=time.perf_counter()-start,
                    process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    blocked_outside_writes=list(denied), source_pins_valid=source_pins()==pins)
                save(phase_path, data)
                rules_special.special_violations = original_function
            phases[phase] = data
        if not all(v["source_pins_valid"] for v in phases.values()):
            raise ValueError("Source drift during full checker; evidence diagnostic only")
        save(combined, dict(key=item["key"], cohort=item["cohort"], role=item["role"],
            input_sha256=item["input"]["sha256"], input_path=item["input"]["path"],
            config_sha256=config_sha, comparison=comparison(phases["baseline"], phases["patched"]),
            baseline_wall_s=phases["baseline"]["wall_s"], patched_wall_s=phases["patched"]["wall_s"]))
        summary = summarize(output)
        print(json.dumps(dict(case=item["key"], completed_comparisons=summary["comparisons"],
            baseline_s=phases["baseline"]["wall_s"], patched_s=phases["patched"]["wall_s"])), flush=True)
    print(json.dumps(summarize(output), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "summarize"))
    parser.add_argument("--inventory", type=Path, default=FENCE / "heat_inventory/inventory.json")
    parser.add_argument("--candidate", type=Path, default=FENCE / "heat/src/stowage/checker/rules_special.py")
    parser.add_argument("--output", type=Path, default=FENCE / "heat_snapshot_results")
    parser.add_argument("--cohort", nargs="+", choices=tuple(COHORTS) + ("showcase_prefix",))
    parser.add_argument("--ship", choices=("feeder_500", "feeder_800", "feeder_1700"))
    parser.add_argument("--rung", choices=("tiny", "small"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--checker-s", type=float, default=120)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args)
    elif args.mode == "run":
        try:
            run(args)
        finally:
            save_live_disclosure(within_fence(args.output))
    else:
        print(json.dumps(summarize(within_fence(args.output)), indent=2))


if __name__ == "__main__":
    main()
