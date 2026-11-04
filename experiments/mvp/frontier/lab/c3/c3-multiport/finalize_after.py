"""Idle until the bound cohort controller exits, then verify and document results.

This file is preparation only until explicitly launched. It imports only the
standard library; all checker/test work occurs in sequential recorded children.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from time import sleep, time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
OUTPUT = HERE / "results/main"
ARMS = ("frontier09", "engine", "graph")
sys.dont_write_bytecode = True


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".finalize-tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def controller_absent(pid):
    try:
        os.kill(pid, 0)  # Only probe the controller recorded for this experiment.
    except ProcessLookupError:
        return True
    except PermissionError:
        # A denied probe is not evidence that the process has exited.
        return False
    return False


def process_identity(expected_pid, expected_started):
    value = read_json(OUTPUT / "parallel-process.json")
    if (value.get("pid"), value.get("started")) != (expected_pid, expected_started):
        raise RuntimeError("Controller identity changed; refusing a different/stale run")
    return value


def await_completion(expected_pid, expected_started, interval):
    while True:
        process = process_identity(expected_pid, expected_started)
        path = OUTPUT / "parallel-complete.json"
        if path.exists():
            complete = read_json(path)
            state = read_json(OUTPUT / "parallel-state.json")
            if (complete.get("jobs"), complete.get("maps")) != (117, 39):
                raise RuntimeError("Completion record does not contain all 117 jobs/39 maps")
            if (state.get("pid"), state.get("completed_jobs"),
                    state.get("interrupted"), state.get("pending_jobs")) != (
                    expected_pid, 117, False, 0):
                raise RuntimeError("Controller state is stale, interrupted or incomplete")
            if not (expected_started <= state.get("finished", 0)
                    <= complete.get("finished", 0)):
                raise RuntimeError("Completion timestamps predate or disagree with controller")
            if path.stat().st_mtime < expected_started:
                raise RuntimeError("Stale completion file predates this controller")
            if controller_absent(expected_pid):
                # Re-read identity after the absence probe, before any checks.
                process_identity(expected_pid, expected_started)
                return dict(process=process, state=state, complete=complete,
                            controller_absence_confirmed=time())
        sleep(interval)


def complete_rows():
    manifest = read_json(HERE / "cohort-manifest.json")["maps"]
    if len(manifest) != 39 or len({r["instance_id"] for r in manifest}) != 39:
        raise RuntimeError("Frozen cohort is not exactly 39 unique maps")
    expected = {r["instance_id"]: r for r in manifest}
    for source in manifest:
        path = (ROOT / source["path"]).resolve()
        path.relative_to(HERE / "cohort")
        if digest(path) != source["sha256"]:
            raise RuntimeError("A frozen cohort input changed")
    rows = {}
    for arm in ARMS:
        files = sorted((OUTPUT / arm).glob("*.json"))
        if len(files) != 39:
            raise RuntimeError(f"{arm}: expected exactly 39 rows, found {len(files)}")
        values = [read_json(path) for path in files]
        if Counter(r.get("instance_id") for r in values) != Counter(expected.keys()):
            raise RuntimeError(f"{arm}: row identities differ from frozen cohort")
        for row in values:
            source = expected[row["instance_id"]]
            if row.get("complete") is not True or row.get("arm") != arm:
                raise RuntimeError(f"{arm}: incomplete/mislabelled row")
            if (row.get("input_sha256"), row.get("input"), row.get("size"), row.get("ports")) != (
                    source["sha256"], source["path"], source["size"], source["ports"]):
                raise RuntimeError(f"{arm}: stale cohort binding")
            if row.get("passed") and (row.get("plan") is None or row.get("lifts") is None):
                raise RuntimeError(f"{arm}: passing row lacks a checked plan/objective")
        rows[arm] = values
    return rows


def validate_current_bindings(rows):
    """Match run.py's exact common and per-arm source fingerprints, after exit."""
    lab = ROOT / "experiments/mvp/frontier/lab"
    sys.path.insert(0, str(lab))
    import score  # Standard-library-only module; no planner/checker call here.
    if Path(score.__file__).resolve() != lab / "score.py":
        raise RuntimeError("Unexpected shared score module while binding sources")
    params = hashlib.sha256()
    for parameter in sorted(p for p in (ROOT / "params").rglob("*") if p.is_file()):
        params.update(str(parameter.relative_to(ROOT)).encode())
        params.update(parameter.read_bytes())
    common = dict(native_sha256=score.source_sha256(), params_sha256=params.hexdigest(),
                  runner_sha256=digest(HERE / "run.py"),
                  cohort_sha256=digest(HERE / "cohort-manifest.json"))
    implementations = {}
    for arm in ARMS:
        method_path = (lab / "ref/frontier09.py" if arm == "frontier09" else
                       HERE / "frozen_engine/lab_method.py" if arm == "engine" else
                       HERE / "graph_method.py")
        files = [method_path]
        if arm in ("engine", "graph"):
            files += sorted((HERE / "frozen_engine").rglob("*.py"))
        if arm == "graph":
            files += [HERE / "graph_model.py", HERE / "graph_finish.py"]
        implementations[arm] = hashlib.sha256(
            "".join(digest(path) for path in files).encode()).hexdigest()
        expected = dict(common, implementation_sha256=implementations[arm])
        for row in rows[arm]:
            for key, value in expected.items():
                if row.get("config", {}).get(key) != value:
                    raise RuntimeError(f"{arm} {row['instance_id']}: current {key} differs from measurement")
    return dict(common=common, implementation_by_arm=implementations,
                rows_verified=sum(len(values) for values in rows.values()))


def command_environment():
    env = dict(os.environ)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                 "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "STOWAGE_IN_PORT_WORKERS"):
        env[name] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_ADDOPTS"] = ""
    env["PYTHONPATH"] = os.pathsep.join(map(str, (
        ROOT / "src", ROOT / "experiments/mvp/frontier",
        ROOT / "experiments/mvp/frontier/lab", HERE)))
    return env


def run_check(name, arguments, state):
    process_identity(state["expected_controller_pid"], state["expected_controller_started"])
    if not controller_absent(state["expected_controller_pid"]):
        raise RuntimeError("Controller absence cannot be confirmed before verification child")
    log = OUTPUT / "final-verification-logs" / f"{name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    command = ["/usr/bin/nice", "-n", "5", sys.executable, *map(str, arguments)]
    entry = dict(name=name, command=command, log=str(log.relative_to(HERE)), started=time())
    state["checks"].append(entry)
    with log.open("w") as stream:
        child = subprocess.Popen(command, cwd=ROOT, env=command_environment(),
                                 stdout=stream, stderr=subprocess.STDOUT)
        entry["pid"] = child.pid
        write_json(OUTPUT / "final-verification.json", state)
        entry["returncode"] = child.wait()
    entry.update(finished=time(), log_sha256=digest(log))
    write_json(OUTPUT / "final-verification.json", state)
    if entry["returncode"]:
        raise RuntimeError(f"{name} failed with exit {entry['returncode']}; see {log}")
    return log.read_text()


def validate_artifacts(rows, pytest_output):
    audit = read_json(OUTPUT / "audit.json")
    expected_passes = sum(bool(row.get("passed")) for values in rows.values() for row in values)
    if audit.get("all_agree") is not True or len(audit.get("records", [])) != expected_passes:
        raise RuntimeError("Fresh full-check audit omitted a passing row or disagreed")
    bounds = read_json(OUTPUT / "global-tiny-bounds.json")
    if len(bounds.get("rows", [])) != 18:
        raise RuntimeError("Missing tiny global certificates")
    for row in bounds["rows"]:
        bound = row["bound"]
        if (bound.get("proved"), bound.get("enumeration_complete"),
                bound.get("assignments_enumerated")) != (True, True, 720):
            raise RuntimeError("A tiny certificate is incomplete")
    sources = read_json(HERE / "source-audit.json")
    if sources.get("passed") is not True or sources.get("changes"):
        raise RuntimeError("Source/snapshot audit disagreed")
    test_counts = re.findall(r"\b(\d+) passed\b", pytest_output)
    if not test_counts or int(test_counts[-1]) < 1:
        raise RuntimeError("Pytest did not report any passing graph/bound regressions")
    tests_passed = int(test_counts[-1])
    summary = read_json(OUTPUT / "summary.json")
    expected_groups = {("tiny", p): 6 for p in (3, 4, 5)}
    expected_groups.update({("small", p): 6 for p in (3, 4, 5)})
    expected_groups[("medium", 4)] = 3
    groups = summary.get("by_size_ports", [])
    if {(g["size"], g["ports"]) for g in groups} != set(expected_groups):
        raise RuntimeError("Final summary omits a size/port stratum")
    for group in groups:
        expected = expected_groups[(group["size"], group["ports"])]
        if any(group["arms"][arm]["measured"] != expected for arm in ARMS):
            raise RuntimeError("Final summary contains a partial stratum")
    return audit, bounds, summary, tests_passed


def update_notes(rows, audit, bounds, summary, tests_passed):
    totals = {arm: dict(measured=len(rows[arm]),
        passed=sum(bool(row.get("passed")) for row in rows[arm]),
        timeouts=sum(bool(row.get("outer_timeout")) for row in rows[arm])) for arm in ARMS}
    counters = bounds["optimum_counts"]
    cap = summary.get("resource_policy", {}).get("cap_s")
    cap_description = f"{cap:g}-second" if isinstance(cap, (int, float)) else "declared"
    table = (OUTPUT / "table.md").read_text().strip()
    text = "\n".join((
        "<!-- FINALIZE_AFTER_RESULTS_BEGIN -->", "## Final verified cohort results", "",
        "All 117 arm/map rows are complete: 39 frozen TRAIN maps per arm. "
        "The unchanged full checker was rerun in a fresh process on every passing "
        f"stored plan; all {len(audit['records'])} audits agree with the stored "
        "lifts and graph top/hatch counts. Source/snapshot verification and all "
        f"{tests_passed} graph/bound regression tests pass.", "",
        table, "",
        "Arm order in the table is 0.9.0 / copied engine / graph. "
        "Paired means use only maps fully passed by all three arms, within each "
        "size and port count; no cross-size mean is used. Known replay counts "
        "are separate from full-check passes. Restows are lift counts, including "
        "the configured off/on charge. Timeouts are bounded-search failures, "
        f"not infeasibility proofs. The uniform outer {cap_description} cap censors "
        "runtime; cleanup can add time. Search, finishing and candidate limits "
        "differ across methods, so the timing columns describe these configured "
        "runs and do not isolate an algorithmic speedup.", "",
        "Across all sizes, passes/39 and outer timeouts are " + "; ".join(
            f"{arm}: {totals[arm]['passed']}/39, {totals[arm]['timeouts']} timeouts"
            for arm in ARMS) + ". These are pass counts only, not pooled lift means.", "",
        "The 18 tiny certificates enumerate 12,960 complete six-cell bijections. "
        "All have a restow-only geometric minimum of zero; this does not prove "
        "zero restows physically feasible. Checked plans attain the joint "
        "hatch/restow plus mandatory-yard total bound on " + "/".join(
            str(counters[arm]) for arm in ARMS) +
        " tiny maps for 0.9.0/engine/graph respectively. Bound attainment proves "
        "optimality for the checker's D15 departure-coordinate objective, "
        "not for arbitrary adaptive future restow policies.", "",
        "Scope: one loading call, empty arrival, no future loading calls; tiny "
        "uses six boxes in six cells, small uses the inherited equipment mix, "
        "and the three medium maps condition equipment to dry20 at 60% fill. "
        "More discharge ports alone still allow sorted crossing-free stacks. "
        "Physical restrictions and shared-hatch activations create the "
        "nontrivial projected-restow choices here.", "",
        "Verdict: keep the all-call blocker/hatch accounting and exact tiny "
        "bounds. The mixed-page constructor remains experimental; these TRAIN "
        "measurements do not establish a general solver advantage. Next, test "
        "broader page families and real multi-load rotations while retaining "
        "the full checker and independent evaluation split.",
        "<!-- FINALIZE_AFTER_RESULTS_END -->", ""))
    path = HERE / "NOTES.md"
    notes = path.read_text()
    notes = re.sub(r"<!-- FINALIZE_AFTER_RESULTS_BEGIN -->.*?<!-- FINALIZE_AFTER_RESULTS_END -->\n?",
                   "", notes, flags=re.S)
    paragraphs = notes.split("\n\n")
    for i, paragraph in enumerate(paragraphs):
        if paragraph.startswith("Status:"):
            paragraphs[i] = ("Status: all 117 fixed-cohort measurements and final verification "
                             "are complete. See the final verified results below.")
        elif paragraph.startswith("Pending."):
            paragraphs[i] = ("Final measurements and independent verification are complete; "
                             "the final results below supersede the development summaries.")
    notes = "\n\n".join(paragraphs).rstrip() + "\n\n" + text
    temporary = path.with_name("NOTES.md.finalize-tmp")
    temporary.write_text(notes)
    temporary.replace(path)
    return dict(totals=totals, tiny_optimum_counts=counters,
                fresh_full_checks=len(audit["records"]), tests_passed=tests_passed,
                notes_sha256=digest(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-controller-pid", type=int, required=True)
    parser.add_argument("--expected-controller-started", type=float, required=True)
    parser.add_argument("--poll-s", type=float, default=30.)
    args = parser.parse_args()
    if not 1 <= args.poll_s <= 60:
        raise ValueError("poll interval must be between 1 and 60 seconds")
    lock = (OUTPUT / "finalize-after.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = dict(status="waiting", passed=False, pid=os.getpid(), started=time(), checks=[],
                 expected_controller_pid=args.expected_controller_pid,
                 expected_controller_started=args.expected_controller_started)
    target = OUTPUT / "final-verification.json"
    write_json(target, state)
    try:
        state["completion"] = await_completion(args.expected_controller_pid,
            args.expected_controller_started, args.poll_s)
        rows = complete_rows()
        state["current_bindings_before"] = validate_current_bindings(rows)
        state.update(status="verifying", rows={arm: len(rows[arm]) for arm in ARMS})
        write_json(target, state)
        run_check("full-check-audit", [HERE / "audit.py", OUTPUT], state)
        run_check("tiny-global-bounds", [HERE / "measure_bounds.py"], state)
        run_check("source-bindings", [HERE / "verify_sources.py"], state)
        test_output = run_check("graph-bound-tests", ["-m", "pytest", HERE / "test_graph.py",
            HERE / "test_tiny_bound.py", "-o", "addopts=", "-q", "-p", "no:cacheprovider"], state)
        run_check("final-summary", [HERE / "report.py", OUTPUT], state)
        # Refresh row bindings and verify all artifacts before editing NOTES.
        rows = complete_rows()
        state["current_bindings_after"] = validate_current_bindings(rows)
        if state["current_bindings_before"] != state["current_bindings_after"]:
            raise RuntimeError("Current measurement bindings changed during final verification")
        audit, bounds, summary, tests_passed = validate_artifacts(rows, test_output)
        state["results"] = update_notes(rows, audit, bounds, summary, tests_passed)
        state.update(status="passed", passed=True, finished=time())
        write_json(target, state)
        print(json.dumps(dict(passed=True, results=state["results"])), flush=True)
    except Exception as error:
        state.update(status="failed", passed=False, error=f"{type(error).__name__}: {error}",
                     finished=time())
        write_json(target, state)
        raise


if __name__ == "__main__":
    main()
