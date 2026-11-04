"""Detached, resumable CPU map scheduling with a strict base-before-x4 barrier."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ROW_KEY = "| frontier-scale/run |"
REFRESH_SECONDS = 20
POLICY = HERE / "policy.json"
SCHEDULING = HERE / "scheduling.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preflight():
    """Verify the frozen runtime and register the experiment harness, planner-free."""
    from snapshot import source_sha256, verify_snapshot
    snapshot = verify_snapshot()
    manifest = json.loads((HERE / "manifest.json").read_text())
    unrelated_drift = []
    for relative, expected in manifest["frontier_python_sha256"].items():
        if digest(ROOT / relative) != expected:
            if relative in snapshot["sources"]:
                raise RuntimeError(f"Unedited executed Frontier requirement failed: {relative}")
            unrelated_drift.append(dict(path=relative, registered_sha256=expected,
                                        observed_sha256=digest(ROOT / relative)))
    if source_sha256(ROOT) != manifest["source_sha256"]:
        raise RuntimeError("Current Frontier source_sha256 differs from registration")
    harness = {str(path.relative_to(ROOT)): digest(path) for path in sorted(HERE.glob("*.py"))}
    target = HERE / "harness-snapshot.json"
    record = {"created_at": now(), "sources": harness, "manifest_sha256": digest(HERE / "manifest.json"),
              "runtime_snapshot_sha256": digest(HERE / "runtime-snapshot.json"),
              "source_sha256": snapshot["source_sha256"], "scheduling_sha256": digest(SCHEDULING)}
    if target.exists():
        old = json.loads(target.read_text())
        immutable = ("sources", "manifest_sha256", "runtime_snapshot_sha256", "source_sha256", "scheduling_sha256")
        changed = [key for key in immutable if old.get(key) != record[key]]
        if changed:
            has_planner_artifacts = any((HERE / "runs").glob("*/*/raw.json")) or any(
                (HERE / "runs").glob("*/*/progress.json")) or any((HERE / "runs").glob("*/*/result.json"))
            if has_planner_artifacts:
                raise RuntimeError(f"Harness/freeze changed after planner execution; refusing resume: {changed}")
            record["preplanning_revision_of_sha256"] = digest(target)
            write(target, record)
    else:
        write(target, record)
    return {"verified_at": now(), "harness_snapshot": str(target.relative_to(ROOT)),
            "harness_snapshot_sha256": digest(target), "harness_sources": harness,
            "manifest_sha256": record["manifest_sha256"],
            "runtime_snapshot_sha256": record["runtime_snapshot_sha256"],
            "scheduling_sha256": record["scheduling_sha256"],
            "source_sha256": record["source_sha256"], "frozen_runtime_sources": len(snapshot["sources"]),
            "unexecuted_frontier_drift": unrelated_drift,
            "drift_policy": "Shared sessions may edit unused public/dev research. Executed original files and official source_sha256 must match; frozen runtime always matches. This executive edits no original Frontier files."}


def environment():
    env = os.environ.copy()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "HIGHS_THREADS",
                 "BLIS_NUM_THREADS", "GOTO_NUM_THREADS", "STOWAGE_IN_PORT_WORKERS"):
        env[name] = "1"
    env.update(PYTHONDONTWRITEBYTECODE="1", UV_CACHE_DIR=str(ROOT / ".uv-cache"),
               TMPDIR=str(HERE / "tmp"), CUDA_VISIBLE_DEVICES="", NVIDIA_VISIBLE_DEVICES="none")
    return env


def ledger(start, *, active_maps=0, remove=False):
    """Explicit scope exception: update only our live fleet row under its lock."""
    fleet = ROOT / "experiments/fleet"
    with (fleet / "ledger.md.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = fleet / "ledger.md"
        lines = [line for line in path.read_text().splitlines(keepends=True) if not line.startswith(ROW_KEY)]
        if not remove:
            cores, ram = 3 * active_maps, 8 * active_maps
            state = f"{active_maps} active maps; ≤3 solver workers/map" if active_maps else "queued; no compute reserved"
            row = (f"{ROW_KEY} Frontier0.9 scale12+3; {state}; dynamic2→3 map cap after HYBRID FINAL; CPU/native1; PID {os.getpid()} "
                   f"| {cores} | {ram} | no | parallel budget projection + audits/checkers/queue in supervisor-status.json | {start} |\n")
            index = next(i for i, line in enumerate(lines) if line.startswith("|---|")) + 1
            lines.insert(index, row)
        path.write_text("".join(lines))


def hybrid_final_text(text):
    """Require an explicit status line, never a FINAL substring or title."""
    for line in text.splitlines():
        normalized = re.sub(r"[*_`]", "", line).strip()
        match = re.fullmatch(r"(?:[-+]\s*)?(?:status|report status|study status)\s*:\s*(.*)", normalized, re.I)
        if match and re.fullmatch(r"FINAL(?:\s*(?:[.;:]|\(|—|–|-).*)?", match.group(1).strip(), re.I):
            return True, line.strip()
    return False, None


def policy_state():
    policy = json.loads(POLICY.read_text())
    limit = policy["load_limit"]
    if not isinstance(limit, (int, float)) or isinstance(limit, bool) or not math.isfinite(limit) or limit <= 0:
        raise ValueError("policy.load_limit must be a positive finite number")
    for name in ("workers_until_hybrid_final", "workers_after_hybrid_final"):
        if type(policy[name]) is not int or not 1 <= policy[name] <= 3:
            raise ValueError(f"policy.{name} must be an integer from 1 to 3")
    relative = Path(policy["hybrid_report_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("policy.hybrid_report_path must stay within the repository")
    path = ROOT / relative
    text = path.read_text() if path.is_file() else ""
    final, evidence = hybrid_final_text(text)
    target = policy["workers_after_hybrid_final" if final else "workers_until_hybrid_final"]
    return {"policy": policy, "policy_sha256": digest(POLICY), "checked_at": now(),
            "hybrid_report_exists": path.is_file(), "hybrid_report_sha256": digest(path) if path.is_file() else None,
            "hybrid_explicit_final": final, "hybrid_final_evidence": evidence,
            "worker_target": min(3, target), "load_limit": limit,
            "detection": "Explicit Status:, Report status:, or Study status: line with exact FINAL token; markdown emphasis ignored; titles and NOT FINAL do not qualify"}


def admission(policy):
    load = os.getloadavg()[0]
    memory = None
    memory_note = None
    executable = shutil.which("memory_pressure")
    if executable:
        try:
            output = subprocess.run([executable, "-Q"], capture_output=True, text=True, check=False)
            match = re.search(r"System-wide memory free percentage:\s*(\d+)%", output.stdout + output.stderr)
            if match:
                memory = int(match.group(1))
            else:
                memory_note = f"memory_pressure did not report percentage (exit {output.returncode})"
        except OSError as error:
            memory_note = str(error)
    else:
        memory_note = "memory_pressure unavailable; admission uses live load"
    reasons = []
    limit = policy["load_limit"]
    if load > limit:
        reasons.append(f"load1={load:.2f} exceeds {limit:g}")
    if memory is not None and memory < 15:
        reasons.append(f"free memory={memory}% below 15%")
    return {"admitted": not reasons, "load1": load, "load_limit": limit,
            "free_memory_percent": memory, "memory_note": memory_note,
            "reason": "; ".join(reasons) if reasons else "live admission passed", "checked_at": now()}


def tasks(manifest):
    by_id = {job["map_id"]: job for job in manifest["jobs"]}
    order = lambda job: (job["n_boxes"], job["map_id"])
    return [("base", job) for job in sorted((by_id[map_id] for map_id in manifest["map_ids"]), key=order)] + [
        ("x4", job) for job in sorted((by_id[map_id] for map_id in manifest["budget_x4_map_ids"]), key=order)]


def freeze_scheduling(manifest):
    schedule = {"schema": 1, "manifest_sha256": digest(HERE / "manifest.json"),
                "within_budget_order": ["n_boxes", "map_id"], "strict_base_before_x4_barrier": True,
                "tasks": [{"budget": budget, "map_id": job["map_id"], "n_boxes": job["n_boxes"]}
                          for budget, job in tasks(manifest)]}
    if SCHEDULING.exists():
        assert json.loads(SCHEDULING.read_text()) == schedule, "Frozen scheduling changed"
    else:
        with SCHEDULING.open("x") as stream:
            json.dump(schedule, stream, indent=2, sort_keys=True)
            stream.write("\n")
    return {"path": str(SCHEDULING.relative_to(ROOT)), "sha256": digest(SCHEDULING), **schedule}


def scheduling_phase(pending, active_keys):
    # A base worker must also exit successfully, even if it has just published
    # result.json, before any x4 worker may be admitted.
    return "base" if any(budget == "base" for budget, _ in pending) or any(
        budget == "base" for budget, _ in active_keys) else "x4"


def control_state():
    path = HERE / "control.json"
    control = json.loads(path.read_text()) if path.is_file() else {"action": "run"}
    if control.get("action") not in {"run", "stop", "restart"}:
        raise ValueError("control.action must be run, stop, or restart")
    return {"action": control["action"], "checked_at": now(),
            "path": str(path.relative_to(ROOT)), "sha256": digest(path) if path.is_file() else None,
            "policy": "stop/restart stops admission, drains exact owned active workers, then exits/re-execs without signals"}


def result_path(budget, job):
    return HERE / "runs" / budget / job["map_id"] / "result.json"


def complete(budget, job):
    return result_path(budget, job).is_file()


def phase_duration(queued, active, workers):
    """List scheduling projection, retaining active workers above a reduced cap."""
    waiting = list(queued)
    running = list(active)
    elapsed = 0.0
    while waiting or running:
        while waiting and len(running) < workers:
            running.append(elapsed + waiting.pop(0))
        if running:
            elapsed = min(running)
            running = [end for end in running if end > elapsed]
    return elapsed


def estimate(pending, active=None, workers=2):
    # active maps task keys to elapsed worker seconds, including audits.
    active = active or {}
    allocations = []
    for budget, job in pending:
        search = (24 if budget == "x4" else 6) * job["n_boxes"]
        finish = 2400 if budget == "x4" else 600
        if job["route"] == "plan_big":
            components = {"search": search, "twostep_constructor": 240,
                          "column_master_up_to_four": 4 * 240,
                          "column_completion_up_to_four": 4 * 120,
                          "finishers_up_to_three": 3 * finish,
                          "supplemental_block_constructor": 240,
                          "supplemental_block_finisher": finish}
        else:
            components = {"search": search, "two_constructors": 2 * 120,
                          "yard_first": max(5, .15 * search),
                          "slot_master_up_to_four": 4 * max(8, .12 * search),
                          "finishers_up_to_five": 5 * finish}
        total = sum(components.values())
        key = (budget, job["map_id"])
        remaining = max(0, total - active.get(key, 0))
        allocations.append({"budget": budget, "map_id": job["map_id"], "route": job["route"],
                            "active": key in active,
                            "components_s": components, "full_budget_component_seconds": total,
                            "remaining_budget_component_seconds": remaining})
    seconds = sum(a["remaining_budget_component_seconds"] for a in allocations)
    def projection(target):
        return sum(phase_duration(
            [a["remaining_budget_component_seconds"] for a in allocations if a["budget"] == phase and not a["active"]],
            [a["remaining_budget_component_seconds"] for a in allocations if a["budget"] == phase and a["active"]], target)
                   for phase in ("base", "x4"))
    return {"remaining_tasks": len(pending), "active_map_workers": len(active),
            "worker_target": workers, "analytic_remaining_budget_seconds": seconds,
            "parallel_budget_projection_seconds": projection(workers),
            "parallel_budget_projection_at_2_maps_seconds": projection(2),
            "parallel_budget_projection_at_3_maps_seconds": projection(3),
            "budget_allocations": allocations,
            "analytic_assumption": "Caller budget components are projected through sorted list scheduling with the strict base barrier and current map-worker target; active elapsed is subtracted per worker. The 2/3-worker alternatives assume that capacity for the remainder. Early returns may shorten work; contention, full checkers, audits, compaction, scoring and admission waits add unbudgeted time. This is not a wall-time ceiling or measured scaling curve.",
            "queue_time_unbounded": True}


def refresh(status, env, *, require_final=False):
    report = HERE / "report.py"
    if not report.exists():
        status["report_refresh"] = {"at": now(), "status": "report.py pending"}
        return False
    with (HERE / "report-refresh.log").open("a") as stream:
        try:
            command = [sys.executable, str(report)] + (["--require-final"] if require_final else [])
            result = subprocess.run(command, cwd=ROOT, env=env,
                                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                    check=False)
            record = {"at": now(), "exit_code": result.returncode, "command": command}
            status["report_refresh"] = record
            if require_final:
                status["final_report_verification"] = record
            return result.returncode == 0
        except OSError as error:
            status["report_refresh"] = {"at": now(), "error": str(error)}
            if require_final:
                status["final_report_verification"] = dict(status["report_refresh"])
            return False


def supervise(lock):
    env = environment()
    (HERE / "tmp").mkdir(exist_ok=True)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv executable unavailable")
    manifest = json.loads((HERE / "manifest.json").read_text())
    scheduling = freeze_scheduling(manifest)
    frozen = preflight()
    all_tasks = tasks(manifest)
    started = time.time()
    start = now()
    status = {"pid": os.getpid(), "start": start, "phase": "initializing",
              "command": [sys.executable, str(HERE / "launch.py")],
              "manifest_sha256": hashlib.sha256((HERE / "manifest.json").read_bytes()).hexdigest(),
              "preflight": frozen, "scheduling": scheduling,
              "owned_children": [], "active_children": [], "cpu_only": True,
              "max_map_workers": 3, "internal_solver_workers_per_map_max": 3,
              "max_solver_workers": 9,
              "native_threads_per_library": 1, "queue_elapsed_seconds": 0,
              "scope_exception": "only own row in experiments/fleet/ledger.md, protected by ledger.md.lock; no history.md write"}
    active = {}
    errors = []
    restart = False
    target = 2
    write(HERE / "supervisor-status.json", status)
    ledger(start)
    try:
        while True:
            for key, owner in list(active.items()):
                child = owner["process"]
                if child.poll() is None:
                    continue
                budget, job = owner["task"]
                owner["record"].update(exit_code=child.returncode, ended_at=now())
                write(owner["folder"] / "launch.json", owner["record"])
                del active[key]
                if child.returncode or not complete(budget, job):
                    errors.append(f"Worker {budget}/{job['map_id']} exited {child.returncode}; "
                                  f"result_present={complete(budget, job)}; raw/checkpoints preserved for resume")
            pending = [(b, j) for b, j in all_tasks if not complete(b, j)]
            try:
                policy = policy_state()
                control = control_state()
                status.update(policy=policy, control=control)
                target = policy["worker_target"]
            except Exception as error:
                if not errors:
                    errors.append(f"Policy/control error: {type(error).__name__}: {error}")
                control = {"action": "stop"}
            if errors and not active:
                raise RuntimeError("; ".join(errors))
            if control["action"] in {"stop", "restart"} and not active:
                restart = control["action"] == "restart"
                status.update(phase="restarting" if restart else "stopped", ended_at=now(),
                              elapsed_s=time.time()-started, active_children=[], active_child_pid=None,
                              active_map_workers=0, conservative_active_solver_workers=0,
                              eta=estimate(pending, workers=target))
                break
            if not pending and not active:
                status.update(phase="complete", ended_at=now(), elapsed_s=time.time()-started,
                              eta=estimate([], workers=target), completed_tasks=len(all_tasks),
                              active_children=[], active_child_pid=None, active_map_workers=0,
                              conservative_active_solver_workers=0)
                write(HERE / "supervisor-status.json", status)
                # Valid failed plans remain complete result rows; this requires
                # all claimed passes to reproduce in the fresh-process audits.
                if not refresh(status, env, require_final=True):
                    status.update(phase="complete_with_issues",
                                  errors="Final report validation failed; inspect result/audit rows and report-refresh.log")
                break
            phase = scheduling_phase(pending, active)
            check = None
            while not errors and control["action"] == "run":
                # Policy, HYBRID status, load, and control are read before each
                # new map admission. Existing workers are never stopped when
                # load rises or the configured target becomes smaller.
                try:
                    policy, control = policy_state(), control_state()
                    status.update(policy=policy, control=control)
                    target = policy["worker_target"]
                    if len(active) >= target or control["action"] != "run":
                        break
                    candidates = [(b, j) for b, j in pending if b == phase and (b, j["map_id"]) not in active]
                    if not candidates:
                        break
                    check = admission(policy)
                    status["admission"] = check
                    if not check["admitted"]:
                        break
                    budget, job = candidates[0]
                    status["preflight"] = preflight()
                    command = [uv, "run", "--offline", "--no-sync", "python", str(HERE / "run.py"),
                               "work", "--budget", budget, "--map-id", job["map_id"]]
                    folder = HERE / "runs" / budget / job["map_id"]
                    folder.mkdir(parents=True, exist_ok=True)
                    ledger(start, active_maps=len(active)+1)
                    with (folder / "worker.log").open("a") as stream:
                        # Each exact owned uv child inherits the supervisor
                        # lock; a supervisor crash cannot duplicate live work.
                        child = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                                 stdout=stream, stderr=subprocess.STDOUT, pass_fds=(lock.fileno(),))
                    record = {"pid": child.pid, "command": command, "cwd": str(ROOT),
                              "harness_snapshot_sha256": status["preflight"]["harness_snapshot_sha256"],
                              "harness_sources": status["preflight"]["harness_sources"],
                              "scheduling_sha256": scheduling["sha256"],
                              "admission": check, "policy": policy,
                              "budget": budget, "map_id": job["map_id"], "launched_at": now()}
                    status["owned_children"].append(record)
                    active[(budget, job["map_id"])] = {"process": child, "record": record,
                                                        "task": (budget, job), "folder": folder,
                                                        "began": time.monotonic()}
                    write(folder / "launch.json", record)
                    status.update(phase="running", active_children=[o["record"] for o in active.values()],
                                  active_child_pid=next(iter(active.values()))["process"].pid, updated_at=now())
                    write(HERE / "supervisor-status.json", status)
                except Exception as error:
                    errors.append(f"Admission error: {type(error).__name__}: {error}")
                    break
            elapsed = {key: time.monotonic()-owner["began"] for key, owner in active.items()}
            status.update(phase="draining_after_error" if errors else "draining_for_"+control["action"]
                          if control["action"] != "run" else "running" if active else "queued",
                          current_budget=phase, active_children=[o["record"] for o in active.values()],
                          active_child_pid=next(iter(active.values()))["process"].pid if active else None,
                          active_map_workers=len(active), worker_target=target,
                          conservative_active_solver_workers=3*len(active),
                          base_barrier_remaining=sum(b == "base" for b, _ in pending),
                          pending_tasks=len(pending), updated_at=now(), elapsed_s=time.time()-started,
                          eta=estimate(pending, active=elapsed, workers=target))
            if errors:
                status["errors"] = errors
            ledger(start, active_maps=len(active))
            write(HERE / "supervisor-status.json", status)
            refresh(status, env)
            write(HERE / "supervisor-status.json", status)
            pause = time.monotonic()
            time.sleep(REFRESH_SECONDS)
            if not active and check and not check["admitted"]:
                status["queue_elapsed_seconds"] += time.monotonic()-pause
        ledger(start, remove=True)
    except Exception as error:
        status.update(phase="error", error=f"{type(error).__name__}: {error}",
                      traceback=traceback.format_exc(), ended_at=now(), elapsed_s=time.time() - started)
        # An exceptional supervisor exit never signals its children. Retain a
        # conservative reservation if an owned child is still live; its lock
        # prevents duplicate work until it exits naturally.
        ledger(start, active_maps=len(active), remove=not active)
        raise
    finally:
        write(HERE / "supervisor-status.json", status)
        refresh(status, env)
        write(HERE / "supervisor-status.json", status)
    if restart:
        control_path = HERE / "control.json"
        write(control_path, {"action": "run", "restart_consumed_at": now(), "supervisor_pid": os.getpid()})
        command = [sys.executable, str(HERE / "launch.py"), "--lock-fd", str(lock.fileno())]
        write(HERE / "restart-launch.json", {"pid": os.getpid(), "command": command, "at": now(),
                                             "policy": "owned workers drained; same supervisor PID and lock re-exec"})
        os.set_inheritable(lock.fileno(), True)
        os.execve(sys.executable, command, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detach", action="store_true", help="start a detached supervisor and return its exact PID")
    parser.add_argument("--lock-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.lock_fd is not None:
        with os.fdopen(args.lock_fd, "a") as lock:
            supervise(lock)
        return
    with (HERE / "supervisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("An existing supervisor or owned worker still holds supervisor.lock; no duplicate launched")
        if args.detach:
            (HERE / "tmp").mkdir(exist_ok=True)
            manifest = json.loads((HERE / "manifest.json").read_text())
            scheduling = freeze_scheduling(manifest)
            policy = policy_state()
            frozen = preflight()
            command = [sys.executable, str(HERE / "launch.py"), "--lock-fd", str(lock.fileno())]
            with (HERE / "supervisor.log").open("a") as stream:
                child = subprocess.Popen(command, cwd=ROOT, env=environment(), stdin=subprocess.DEVNULL,
                                         stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                         pass_fds=(lock.fileno(),))
            record = {"pid": child.pid, "command": command, "cwd": str(ROOT), "launched_at": now(),
                      "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      "preflight": frozen, "scheduling": scheduling, "policy": policy,
                      "cpu_only": True, "max_map_workers": 3,
                      "internal_solver_workers_per_map_max": 3, "max_solver_workers": 9}
            write(HERE / "supervisor-launch.json", record)
            print(json.dumps(record))
        else:
            supervise(lock)


if __name__ == "__main__":
    main()
