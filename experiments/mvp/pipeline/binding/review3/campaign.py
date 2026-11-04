"""Boundary-only migration of the existing detached three-worker campaign.

Nothing here creates a planning pool. Catchup, recorded-reader cases and final
validation run synchronously inside an already-owned map-worker slot.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import inspect
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid

from .. import live_campaign as live, measure, measurement_identity as identity
from ..measurement_lifecycle import durable_json
from . import measurement

HERE = live.HERE
_INSTALLED = False
_DRAINING = False
_WORKER_DEPTH = 0
_RUN_CODES = set()


def _compatible_shim(row, expected):
    return _review3_compatible_handler(row, expected)


def _implementation_shim(binding_root):
    return _review3_implementation_handler(binding_root)


def _build_shim(*, input_path, cell, profile, rule, settings, engine_identity, implementation, seed=0):
    return _review3_build_handler(input_path=input_path, cell=cell, profile=profile, rule=rule,
        settings=settings, engine_identity=engine_identity, implementation=implementation, seed=seed)


def _rows_shim(out, *, persist=True):
    return _review3_rows_handler(out, persist=persist)


def _refresh_shim(output):
    return _review3_refresh_handler(output)


def _run_map_shim(job, output):
    return _review3_run_map_handler(job, output, _review3_original_run_map)


def replace_code(function, shim, handler_name, handler):
    """Preserve object identity for captured locals and pickle references."""
    if getattr(function, "_review3_dispatch", False):
        return
    function.__globals__[handler_name] = handler
    function.__code__ = shim.__code__
    function._review3_dispatch = True


def install():
    global _INSTALLED
    if _INSTALLED:
        return dict(installed=True, pid=os.getpid(), numerical_processes_created=0)
    replace_code(identity.compatibility, _compatible_shim, "_review3_compatible_handler", measurement.compatibility)
    replace_code(identity.implementation_identity, _implementation_shim, "_review3_implementation_handler", measurement.implementation_identity)
    replace_code(identity.build_identity, _build_shim, "_review3_build_handler", measurement.build_identity)
    replace_code(measure.current_rows, _rows_shim, "_review3_rows_handler", measurement.current_rows)
    # -m supervisor and spawn workers may expose the module under __main__ or
    # __mp_main__; patch all same-file aliases without changing pickle identity.
    seen = set()
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if not filename or Path(filename).resolve() != (HERE / "live_campaign.py").resolve():
            continue
        original = getattr(module, "run_map", None)
        if original is not None and id(original) not in seen:
            seen.add(id(original))
            _RUN_CODES.add(original.__code__)
            original.__globals__["_review3_original_run_map"] = measurement.clone(original)
            replace_code(original, _run_map_shim, "_review3_run_map_handler", run_map)
        refresh_function = getattr(module, "refresh", None)
        if refresh_function is not None:
            replace_code(refresh_function, _refresh_shim, "_review3_refresh_handler", refresh)
    _INSTALLED = True
    return dict(installed=True, pid=os.getpid(), numerical_processes_created=0,
                core_approved=measurement.core_scope()["approved_core"])


def in_worker_boundary():
    if _WORKER_DEPTH:
        return True
    frame = inspect.currentframe()
    try:
        while frame is not None:
            if frame.f_code in _RUN_CODES:
                return True
            frame = frame.f_back
    finally:
        del frame
    return False


def real_manifest(output):
    path = Path(output) / "campaign.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    return value if value.get("selected_medium") and value.get("workers") == 3 else None


def claim_available(output, key):
    path = Path(output) / "review3/claims" / (key + ".json")
    if not path.exists():
        return True
    value = json.loads(path.read_text())
    request = Path(output) / "review3/retry-unknown.json"
    keys = json.loads(request.read_text()).get("keys", []) if request.exists() else []
    # Explicitly reviewed retry only, bounded to one extra attempt. Active
    # claimed work is never reclaimed; unknown means the old call already left.
    if key not in keys or value.get("attempt", 1) >= 2:
        return False
    return value.get("state") == "unknown" or (value.get("state") == "claimed" and pid_present(value.get("pid")) is False)


def pid_present(pid):
    """Read-only existence probe for one recorded owner; never sends signals."""
    import ctypes
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        function = library.proc_pid_rusage
        function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        function.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        result = function(pid, 2, ctypes.byref(buffer))
        return True if result == 0 else (False if ctypes.get_errno() == 3 else None)
    except (OSError, AttributeError):
        return None


def next_hatch_work(output):
    manifest = real_manifest(output)
    if manifest is None:
        return None
    allowed = {row["path"] for row in manifest["selected_medium"] + manifest.get("opportunistic", [])}
    current, _ = measurement.current_rows(live.ROWS, persist=False)
    done = {(row["map"], row["cell"], row["profile"]) for row in current if row["rule_type"] == "one_port_per_hatch"}
    candidates = []
    for path in sorted(live.ROWS.glob("*.json")):
        row = json.loads(path.read_text())
        case = (row["map"], row["cell"], row["profile"])
        if row["rule_type"] != "one_port_per_hatch" or row["map"] not in allowed or row["profile"] != live.PROFILE or case in done:
            continue
        if measurement.annotate(row)["not_a_test"]:
            continue
        key = "hatch-" + hashlib.sha256(json.dumps(case).encode()).hexdigest()[:24]
        if not claim_available(output, key):
            continue
        candidates.append(dict(kind="hatch", key=key, row=row))
    return candidates[0] if candidates else None


def next_work(output):
    if real_manifest(output) is None:
        return None
    hatch = next_hatch_work(output)
    if hatch:
        return hatch
    request = Path(output) / "review3/validation-request.json"
    if request.exists() and json.loads(request.read_text()).get("enabled"):
        key = "full-pytest"
        if claim_available(output, key):
            return dict(kind="validation", key=key)
    for kind in live.KINDS:
        key = "recorded-" + kind
        if claim_available(output, key):
            return dict(kind="recorded", key=key, case_id=kind)
    return None


def claim(output, work):
    path = Path(output) / "review3/claims" / (work["key"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    attempt = 1
    if path.exists() and claim_available(output, work["key"]):
        guard = path.with_suffix(".recovery-lock")
        try:
            lock = os.open(guard, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return None
        os.close(lock)
        try:
            if not claim_available(output, work["key"]):
                return None
            previous = json.loads(path.read_text())
            history = path.parent / "history" / (work["key"] + "." + uuid.uuid4().hex + ".json")
            history.parent.mkdir(parents=True, exist_ok=True)
            path.replace(history)
            attempt = previous.get("attempt", 1) + 1
        finally:
            guard.unlink(missing_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w") as stream:
        stream.write(json.dumps(dict(kind=work["kind"], key=work["key"], pid=os.getpid(),
                                     state="claimed", attempt=attempt, started_at=time.time())) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def run_work(output, work):
    output = Path(output)
    if work["kind"] == "hatch":
        from stowage.io import load_instance
        from experiments.mvp.reader.schema import context_from_instance, validate_answer
        old = work["row"]
        current, _ = measurement.current_rows(live.ROWS)
        base = next((row for row in current if row["rule_type"] == "baseline"
                     and (row["map"], row["cell"], row["profile"]) == (old["map"], old["cell"], old["profile"])), None)
        instance = load_instance(measure.ROOT / old["map"])
        answer = validate_answer(json.dumps(old["canonical_rule"]), context_from_instance(instance))
        row = measure.run_one(old["cell"], old["map"], old["profile"], answer, base, live.ROWS)
        if base is not None:
            durable_json(output / "comparisons" / (row["key"] + ".json"),
                         live.comparison(instance, base, row, answer, dict(selection="logic3 hatch portfolio catchup")))
        return dict(row_key=row["key"], passed=bool(row.get("passed")))
    if work["kind"] == "recorded":
        from .recorded import run_case
        campaign_id = hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:16]
        destination = HERE / "review3/executions" / campaign_id / (work["case_id"] + ".json")
        if destination.exists():
            return dict(existing=True, output=str(destination))
        result = run_case(work["case_id"], destination, budget_s=90., finalize_s=30.,
                          config=measure.profile(live.PROFILE)[2], seed=0)
        return dict(output=str(destination), status=result.get("status"), passed=result.get("passed"))
    measure.admit()
    log_path = HERE / "verification-logic3.log"
    command = [sys.executable, "-m", "pytest", "experiments/mvp/pipeline/binding", "-q",
               "--basetemp=" + str(HERE / "pytest-logic3-tmp"),
               "-o", "cache_dir=" + str(HERE / "pytest-logic3-cache"),
               "--junitxml=" + str(HERE / "verification-logic3.xml")]
    with log_path.open("ab", buffering=0) as log:
        result = subprocess.run(command, cwd=measure.ROOT, env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT, check=False)
    report = dict(returncode=result.returncode, passed=result.returncode == 0, command=command,
                  finished_at=time.time(), worker_pid=os.getpid(), numerical_slot="existing blocked map worker; no fourth worker")
    durable_json(HERE / "verification-logic3.json", report)
    return report


def drain(output):
    global _DRAINING
    if _DRAINING or not in_worker_boundary() or real_manifest(output) is None:
        return
    _DRAINING = True
    try:
        while work := next_work(output):
            handle = claim(output, work)
            if handle is None:
                continue
            try:
                result = run_work(output, work)
                attempt = json.loads(handle.read_text()).get("attempt", 1)
                durable_json(handle, dict(key=work["key"], kind=work["kind"], pid=os.getpid(), attempt=attempt,
                                          state="finished", result=result, finished_at=time.time()))
            except BaseException as error:
                attempt = json.loads(handle.read_text()).get("attempt", 1)
                durable_json(handle, dict(key=work["key"], kind=work["kind"], pid=os.getpid(), attempt=attempt,
                                          state="unknown", reason=repr(error), interrupted_at=time.time()))
                if not isinstance(error, Exception):
                    raise
    finally:
        _DRAINING = False


def run_map(job, output, original):
    global _WORKER_DEPTH
    _WORKER_DEPTH += 1
    try:
        if real_manifest(output) is not None:
            drain(output)
        return original(job, output)
    finally:
        _WORKER_DEPTH -= 1


def refresh(output):
    output = Path(output)
    if real_manifest(output) is None:
        return {}  # seam tests/no real campaign never acquire numerical work
    if in_worker_boundary() and not _DRAINING:
        drain(output)
    manifest = real_manifest(output)
    allowed = {row["path"]: row["cell"] for row in manifest["selected_medium"] + manifest.get("opportunistic", [])}
    all_current, compat = measurement.current_rows(live.ROWS)
    rows = [row for row in all_current if row["map"] in allowed and row["profile"] == live.PROFILE]
    by_key = {row["key"]: row for row in rows}
    bases = {(row["map"], row["cell"], row["profile"]): row for row in rows if row["rule_type"] == "baseline"}
    comparisons = {value["row_key"]: value for path in (output / "comparisons").glob("*.json")
                   if (value := json.loads(path.read_text()))["row_key"] in by_key}
    annotations = {}
    for row in rows:
        base = bases.get((row["map"], row["cell"], row["profile"])) if row["rule_type"] != "baseline" else None
        annotations[row["key"]] = measurement.annotate(row, base)
    groups = defaultdict(list)
    for row in rows:
        groups[row["cell"], row["rule_type"]].append(row)
    lines = ["# Current live diagnostic campaign — scoped logic3 evidence", "",
        "Eight public r0 strata; three existing map-worker slots. Only hatch allocation policy changed: "
        "approved baseline/close-bay/tier/DG rows remain valid. Old hatch rows are historical until the new portfolio policy is executed.", "",
        "Structured rules were chosen to probe baseline violations; recorded Reader cases are reported separately. "
        "Lift spread is search variation, not a causal instruction cost or saving. Best means best found at this work budget. "
        "First checked time and final return time are separate. No sealed evaluation is implied.", "",
        "| Cell | Type | Final | Tested | Not a test | Pass | Binding | Already obeyed | Unknown | Baseline also failed | Refusals | First checked median s | Final median/max s | Paired lifts / baseline |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    median = lambda values: f"{statistics.median(values):.1f}" if values else "—"
    for cell in ("medium", "tiny", "small"):
        for kind in ("baseline",) + live.KINDS:
            group = groups[cell, kind]
            tested = [row for row in group if not annotations[row["key"]]["not_a_test"]]
            passed = [row for row in tested if row.get("passed")]
            classifications = Counter(comparisons.get(row["key"], {}).get("binding_classification", "unknown") for row in passed)
            failed_bases = sum(annotations[row["key"]]["baseline_also_failed"] for row in tested)
            refused = sum(row.get("evidence", {}).get("status") == "constraint_unavailable" for row in tested)
            first = [annotations[row["key"]]["first_checked_wall_s"] for row in tested
                     if annotations[row["key"]]["first_checked_wall_s"] is not None]
            times = [row["wall_s"] for row in tested]
            pairs = [(row.get("lifts"), bases[(row["map"], row["cell"], row["profile"])].get("lifts"))
                     for row in passed if kind != "baseline" and (row["map"], row["cell"], row["profile"]) in bases
                     and bases[(row["map"], row["cell"], row["profile"])].get("passed")]
            pairs = [(left, right) for left, right in pairs if left is not None and right is not None]
            lifts = f"{median([pair[0] for pair in pairs])} / {median([pair[1] for pair in pairs])} (n={len(pairs)})"
            maximum = f"{max(times):.1f}" if times else "—"
            lines.append(f"|{cell}|{kind}|{len(group)}|{len(tested)}|{len(group)-len(tested)}|{len(passed)}|{classifications['binding']}|{classifications['already_obeyed']}|{classifications['unknown']}|{failed_bases}|{refused}|{median(first)}|{median(times)}/{maximum}|{lifts}|")
    lines += ["", "Tiny hatch cells with one physical hatch and multiple discharge ports are 'not a test: section has one hatch' and excluded from refusal counts/denominators.", "",
              "Relaxed row ladder evidence (classified level is not proof that milder levels were searched):"]
    for row in rows:
        annotation = annotations[row["key"]]
        if row.get("evidence", {}).get("status") == "relaxed_plan":
            lines.append(f"- `{row['key']}`: ladder_work={annotation['ladder_work']}; searched levels={annotation['ladder_levels_searched']}; classified levels={annotation['ladder_levels_classified']}.")
    campaign_id = hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:16]
    executions = []
    lines += ["", "## Recorded Reader → execute_brief → current engine", "",
              "Authenticated recorded model responses are reused; this is not fresh Reader inference or an accuracy estimate.", "",
              "| Type | Status | Full pass | First checked s | Final s |", "|---|---|---:|---:|---:|"]
    for kind in live.KINDS:
        destination = HERE / "review3/executions" / campaign_id / (kind + ".json")
        if not destination.exists():
            lines.append(f"|{kind}|pending|—|—|—|")
            continue
        execution = json.loads(destination.read_text())
        executions.append(dict(execution, output=str(destination)))
        evidence = execution.get("evidence", {})
        first = evidence.get("first_checked_wall_s")
        wall = evidence.get("wall_s")
        lines.append(f"|{kind}|{execution.get('status')}|{execution.get('passed')}|{first if first is not None else '—'}|{wall if wall is not None else '—'}|")
    medium = [row for row in rows if row["cell"] == "medium"]
    geometry_exclusions = []
    for job in manifest.get("opportunistic", []):
        if job["cell"] == "tiny":
            diagnostic = measurement.annotate(dict(key="geometry-" + job["path"], map=job["path"],
                cell="tiny", rule_type="one_port_per_hatch", evidence={}))
            if diagnostic["not_a_test"]:
                geometry_exclusions.append(dict(map=job["path"], status=diagnostic["report_status"],
                                                 excluded_from_refusal_rates=True))
    coverage = {job["path"]: sorted(row["rule_type"] for row in medium if row["map"] == job["path"])
                for job in manifest["selected_medium"]}
    complete = all(set(values) == set(("baseline",) + live.KINDS) for values in coverage.values())
    state = dict(observed_at=time.time(), supervisor_pid=manifest["supervisor_pid"],
        current_finalized_rows=len(rows), medium_finalized_rows=len(medium), medium_target=40,
        medium_complete=complete, medium_coverage=coverage, annotations=annotations,
        historical_stale_rows=len(compat["stale_rows"]), source_identity=compat["implementation"],
        engine_identity=compat["engine_identity"], eta="native work and admission determine completion; not a deadline",
        jobs=[json.loads(path.read_text()) for path in sorted((output / "jobs").glob("*.json"))],
        review3_work=[json.loads(path.read_text()) for path in sorted((output / "review3/claims").glob("*.json"))],
        recorded_reader_executions=executions,
        geometry_exclusions=geometry_exclusions,
        lift_comparison_scope="search spread; no causal instruction cost claim")
    durable_json(output / "status.json", state)
    durable_json(output / "review3/row-annotations.json", annotations)
    for path in (output / "RESULTS.md", HERE / "RESULTS-LIVE.md", HERE / "RESULTS.md"):
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text("\n".join(lines) + "\n")
        temporary.replace(path)
    return state
