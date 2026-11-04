"""D157 CPU-only two-step replay, global equality gate, then 15 s hybrid polish.

Run detached with the project Python. All outcome-bearing data and logs remain
under the sealed run directory. The external status file contains only progress,
match/mismatch counts, mismatch IDs, error types, PIDs and elapsed time. This
script never prints individual outcomes, objectives, plans or checker reports.
The original ablation/twostep_val.py is unchanged; rerun_one copies its pipeline.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DEFAULT_OUT = ROOT / "experiments/mvp/runs/test-91"
PROTOCOL = "twostep-polish91-v1"
THREADS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")
FIELDS = ("outcome", "passed", "weighted_nonproductive")


def read_json(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def cpu_only():
    for key in THREADS:
        os.environ[key] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    if any(name == "mlx" or name.startswith("mlx.") for name in sys.modules):
        raise RuntimeError("CPU-only process imported MLX")


@contextlib.contextmanager
def sealed_output(path):
    """Redirect Python and native fd output, including imports and solvers."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1), os.dup(2)
    with path.open("a") as log:
        try:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])


def same_outcome(left, right):
    return all(left.get(field) == right.get(field) for field in FIELDS)


def safe_worker_record(record):
    safe = {key: record[key] for key in ("instance_id", "status", "pid", "wall_s")}
    if "matched" in record:
        safe["matched"] = record["matched"]
    if "error_type" in record:
        safe["error_type"] = record["error_type"]
    return safe


def rerun_one(job):
    """Same seed, config, time limits, finalization and outcome as twostep_val."""
    with sealed_output(job["log"]):
        started = time.perf_counter()
        try:
            cpu_only()
            from llm.stowlm.sft_data import d79_engine_config
            from stowage.checker import check
            from stowage.io import load_instance
            from stowage.planner import finalize
            from stowage.solver.twostep import TwoStepPlanner
            cpu_only()  # importing the CPU pipeline must not import MLX
            if os.environ.get("STOWAGE_BALLAST_V4") is not None:
                raise RuntimeError("unexpected ambient V4 override")
            instance = load_instance(job["input"])
            if instance.id != job["instance_id"]:
                raise ValueError("input ID changed")
            t = time.perf_counter()
            result = TwoStepPlanner(feasibility_config=d79_engine_config()).plan(instance, seed=0, time_limit_s=120)
            row = {"instance_id": instance.id, "input": job["input"], "twostep_py_sha256": job["twostep_py_sha256"],
                   "plan_status": result.status, "finalize_s_budget": 120.}
            if result.plan is not None:
                os.environ["STOWAGE_BALLAST_V4"] = "1"
                t_fin = time.perf_counter()
                try:
                    result = finalize(instance, result, time_limit_s=120.)
                finally:
                    os.environ.pop("STOWAGE_BALLAST_V4", None)
                row["finalize_status"] = result.status
                row["finalize_s"] = round(time.perf_counter() - t_fin, 2)
                if result.status == "ok" and result.plan is not None:
                    report = check(instance, result.plan)
                    row.update(passed=report.passed, weighted_nonproductive=report.count.weighted_nonproductive
                               if report.passed else None)
            row.setdefault("passed", False)
            row["outcome"] = ("pass" if row["passed"] else "dead end" if result.plan is None and row.get("finalize_status") is None
                              else "finisher time limit" if row.get("finalize_status") not in (None, "ok")
                              and row.get("finalize_s", 0) >= 0.9 * 120.
                              else "finisher error" if row.get("finalize_status") not in (None, "ok") else "checker fail")
            row["s"] = round(time.perf_counter() - t, 2)
            original = read_json(job["reference"])
            matched = same_outcome(row, original)
            record = dict(protocol=PROTOCOL, status="completed", instance_id=instance.id,
                fingerprint=job["fingerprint"], reference_sha256=job["reference_sha256"],
                matched=matched, row=row, result=result.model_dump(mode="json"),
                pid=os.getpid(), wall_s=time.perf_counter() - started)
        except Exception as error:
            record = dict(protocol=PROTOCOL, status="error", instance_id=job["instance_id"],
                fingerprint=job["fingerprint"], matched=False, error_type=type(error).__name__,
                pid=os.getpid(), wall_s=time.perf_counter() - started)
        write_json(job["rerun_output"], record)
        return safe_worker_record(record)


def select_polished(instance, own, polished, before, check, apply_plan):
    """Independent final checker and fixed-stow/ballast nonworse fallback."""
    t = time.perf_counter()
    report = check(instance, polished.plan) if polished.plan is not None else None
    final_check_s = time.perf_counter() - t
    after = report.count.weighted_nonproductive if report is not None and report.passed else None
    original = apply_plan(instance, own.plan)
    candidate = apply_plan(instance, polished.plan) if polished.plan is not None else None
    mapping = lambda replay: {p.container_id: p.slot for p in replay.final_state.ship.placements}
    fixed = candidate is not None and candidate.legal and original.legal and mapping(candidate) == mapping(original)
    ballast = polished.plan is not None and polished.plan.ballast == own.plan.ballast
    kept = after is None or after > before or not fixed or not ballast
    return own if kept else polished, before if kept else after, kept, final_check_s


def polish_one(job):
    with sealed_output(job["log"]):
        started = time.perf_counter()
        try:
            cpu_only()
            from stowage.checker import check
            from stowage.io import load_instance
            from stowage.model import PlannerResult
            from stowage.moves import apply_plan
            from stowage.solver.gold_warmstart import improve_checked
            cpu_only()
            saved = read_json(job["rerun_output"])
            if saved.get("row", {}).get("passed") is not True:
                row = dict(protocol=PROTOCOL, status="skipped_not_passing", instance_id=job["instance_id"],
                    fingerprint=job["fingerprint"], run_fingerprint=job["run_fingerprint"], pid=os.getpid(),
                    wall_s=time.perf_counter() - started)
                write_json(job["polish_output"], row)
                return safe_worker_record(row)
            if saved.get("fingerprint") != job["fingerprint"] or saved.get("matched") is not True:
                raise ValueError("rerun record failed equality or fingerprint guard")
            instance = load_instance(job["input"])
            own = PlannerResult.model_validate(saved["result"])
            if own.plan is None or own.plan.instance_id != instance.id:
                raise ValueError("missing or misbound incumbent")
            t = time.perf_counter()
            own_report = check(instance, own.plan)
            own_check_s = time.perf_counter() - t
            if not own_report.passed:
                raise ValueError("rechecked incumbent did not pass")
            before = own_report.count.weighted_nonproductive
            if before != saved["row"].get("weighted_nonproductive"):
                raise ValueError("rechecked incumbent objective changed")
            t = time.perf_counter()
            polished = improve_checked(instance, own, time_limit_s=job["seconds"], seed=0)
            polish_s = time.perf_counter() - t
            chosen, after, kept, candidate_check_s = select_polished(instance, own, polished, before, check, apply_plan)
            row = dict(protocol=PROTOCOL, status="completed", instance_id=instance.id, rung=instance.size_class,
                fingerprint=job["fingerprint"], run_fingerprint=job["run_fingerprint"], requested_polish_s=job["seconds"],
                before_moves=before, after_moves=after, twostep_lifts=before, polished_lifts=after,
                improved=after < before, kept_incumbent=kept, own_report_passed=True, final_report_passed=True,
                assignments_preserved=True, ballast_preserved=True, polish_s=polish_s,
                own_check_s=own_check_s, candidate_check_s=candidate_check_s,
                qualification_check_s=polished.metrics.get("baseline_check_s", 0.),
                acceptance_check_s=polished.metrics.get("acceptance_check_s", 0.), metrics=polished.metrics,
                result=chosen.model_dump(mode="json"), pid=os.getpid(), wall_s=time.perf_counter() - started)
            row["check_s_total"] = sum(row[k] for k in ("own_check_s", "candidate_check_s", "qualification_check_s", "acceptance_check_s"))
        except Exception as error:
            row = dict(protocol=PROTOCOL, status="error", instance_id=job["instance_id"],
                fingerprint=job["fingerprint"], error_type=type(error).__name__, pid=os.getpid(),
                wall_s=time.perf_counter() - started)
        write_json(job["polish_output"], row)
        return safe_worker_record(row)


def cached(path, fingerprint, run_fingerprint=None):
    if not Path(path).exists():
        return False
    try:
        row = read_json(path)
        return (row.get("status") == "completed" and row.get("fingerprint") == fingerprint
                and (run_fingerprint is None or row.get("run_fingerprint") == run_fingerprint))
    except (OSError, ValueError):
        return False


def worker_initialize(pid_dir):
    cpu_only()
    write_json(Path(pid_dir) / f"{os.getpid()}.json", dict(pid=os.getpid(), supervisor_pid=os.getppid()))


def run_pool(stage, jobs, workers, status, status_path, started):
    if not jobs:
        return
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
            initializer=worker_initialize, initargs=(str(status_path.with_suffix(".pids")),)) as pool:
        futures = [pool.submit(rerun_one if stage == "rerun" else polish_one, job) for job in jobs]
        for future in as_completed(futures):
            item = future.result()
            status["worker_pids"] = sorted(set(status.get("worker_pids", [])) | {item["pid"]})
            status[f"n_{stage}_finished_this_launch"] = status.get(f"n_{stage}_finished_this_launch", 0) + 1
            if item["status"] == "error":
                status["n_errors"] = status.get("n_errors", 0) + 1
                status["error_types"] = sorted(set(status.get("error_types", [])) | {item.get("error_type", "WorkerError")})
            status["wall_s"] = time.perf_counter() - started
            write_json(status_path, status)


def clear_ledger(ledger, history, key, started, state):
    """Remove only this supervisor's exact reservation; no process signalling."""
    import fcntl
    lock = Path(str(ledger) + ".lock")
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if not ledger.exists():
            return
        lines = ledger.read_text().splitlines(keepends=True)
        owned = [line for line in lines if line.startswith(f"| {key} |")]
        if owned:
            ledger.write_text("".join(line for line in lines if not line.startswith(f"| {key} |")))
            with history.open("a") as out:
                out.write(f"\n- {datetime.now(timezone.utc).isoformat()} {key}: reserved <=6 CPU / CPU only; actual supervisor wall {time.perf_counter()-started:.1f}s; state {state}; PID {os.getpid()}.\n")


def sources_intact(jobs, sources):
    return (all(sha(j["reference"]) == j["reference_sha256"] and sha(j["input"]) == j["input_sha256"] for j in jobs)
            and all(sha(ROOT / name) == value for name, value in sources.items() if name not in ("harness", "twostep_val.py"))
            and sha(__file__) == sources["harness"]
            and sha(HERE.parent / "ablation/twostep_val.py") == sources["twostep_val.py"])


def supervise(args):
    cpu_only()
    started = time.perf_counter()
    out = args.out.resolve()
    status_path = args.status.resolve()
    if status_path.is_relative_to(out):
        raise ValueError("safe status must be outside the sealed run directory")
    out.mkdir(parents=True, exist_ok=True)
    guard_path = out / "twostep-polish/GUARD.json"
    status = dict(protocol=PROTOCOL, stage="starting", supervisor_pid=os.getpid(), worker_pids=[],
                  n_expected=args.expected_maps, n_matches=0, n_mismatches=0, mismatch_ids=[], n_errors=0, wall_s=0.)
    write_json(status_path, status)
    write_json(out / "twostep-polish/logs/supervisor.json", status)
    guard = dict(protocol=PROTOCOL, stage="starting", valid=False, all_matches=False,
                 n_expected=args.expected_maps, n_matches=0, n_mismatches=0, requested_polish_s=args.seconds)
    write_json(guard_path, guard)  # Invalidate old admission before reading or doing any work.
    state = "error"
    try:
        with sealed_output(out / "twostep-polish/logs/supervisor.log"):
            from stowage.io import load_instance
            cpu_only()
            paths = (out / "inputs.txt").read_text().split()
            if len(paths) != args.expected_maps:
                raise ValueError("input count differs from the required experiment size")
            sources = {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT / "src").rglob("*.py"))}
            sources["harness"] = sha(__file__)
            sources["twostep_val.py"] = sha(HERE.parent / "ablation/twostep_val.py")
            jobs = []
            for rel in paths:
                path = Path(rel)
                path = (path if path.is_absolute() else ROOT / path).resolve()
                if out == DEFAULT_OUT.resolve():
                    path.relative_to((ROOT / "experiments/mvp/corpus/test").resolve())
                    if path.name.endswith(".audit.json"):
                        raise ValueError("input must be a test instance, not an audit sidecar")
                # The sealed input list is authoritative; it must point at tiny/small instances.
                instance = load_instance(path)
                iid = instance.id
                if instance.size_class not in ("tiny", "small"):
                    raise ValueError("input list contains an unsupported rung")
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", iid):
                    raise ValueError("unsafe instance ID")
                reference = out / "twostep" / f"{iid}.json"
                job = dict(instance_id=iid, input=str(path), input_sha256=sha(path),
                    reference=str(reference), reference_sha256=sha(reference),
                    twostep_py_sha256=sources["src/stowage/solver/twostep.py"], sources=sources, seconds=args.seconds,
                    rerun_output=str(out / "twostep-plans" / f"{iid}.json"),
                    polish_output=str(out / "twostep-polish" / f"{iid}.json"),
                    log=str(out / "twostep-polish/logs" / f"{iid}.log"))
                job["fingerprint"] = digest(job)
                jobs.append(job)
            if len({j["instance_id"] for j in jobs}) != args.expected_maps:
                raise ValueError("duplicate input IDs")
            run_fingerprint = digest([j["fingerprint"] for j in jobs])
            for job in jobs:
                job["run_fingerprint"] = run_fingerprint
            guard.update(fingerprint=run_fingerprint, reference_sha256={j["instance_id"]: j["reference_sha256"] for j in jobs})
            write_json(out / "twostep-plans/manifest.json", dict(protocol=PROTOCOL, fingerprint=run_fingerprint, jobs=jobs))
            status["stage"] = "rerun"
            write_json(status_path, status)
            if not sources_intact(jobs, sources):
                raise ValueError("sources changed before rerun")
            run_pool("rerun", [j for j in jobs if not cached(j["rerun_output"], j["fingerprint"])], args.workers, status, status_path, started)
            records = {j["instance_id"]: read_json(j["rerun_output"]) for j in jobs}
            mismatch_ids = [j["instance_id"] for j in jobs if records[j["instance_id"]].get("status") != "completed"
                            or not same_outcome(records[j["instance_id"]].get("row", {}), read_json(j["reference"]))]
            guard.update(stage="rerun_complete", n_matches=args.expected_maps-len(mismatch_ids),
                n_mismatches=len(mismatch_ids), all_matches=not mismatch_ids, mismatch_ids=mismatch_ids)
            status.update(n_matches=guard["n_matches"], n_mismatches=guard["n_mismatches"], mismatch_ids=mismatch_ids)
            write_json(guard_path, guard)
            if mismatch_ids:
                state = "nondeterministic_do_not_use"
                return 2
            # GLOBAL guard: no polish submission occurs until every map matches.
            if not sources_intact(jobs, sources):
                guard.update(stage="sources_changed", valid=False)
                write_json(guard_path, guard)
                state = "sources_changed"
                return 2
            passing = [j for j in jobs if records[j["instance_id"]]["row"].get("passed") is True]
            guard.update(stage="polishing", n_passing=len(passing), n_polished=0)
            write_json(guard_path, guard)
            status["stage"] = "polishing"
            write_json(status_path, status)
            run_pool("polish", [j for j in jobs if not cached(j["polish_output"], j["fingerprint"], run_fingerprint)], args.workers, status, status_path, started)
            completed = [j for j in passing if cached(j["polish_output"], j["fingerprint"], run_fingerprint)]
            valid = len(completed) == len(passing)
            guard.update(stage="complete" if valid else "polish_error", valid=valid, n_polished=len(completed),
                rows={j["instance_id"]: j["polish_output"] for j in completed},
                polish_sha256={j["instance_id"]: sha(j["polish_output"]) for j in completed})
            # Original references must remain unchanged throughout the whole run.
            if not sources_intact(jobs, sources):
                guard.update(stage="sources_changed", valid=False)
            write_json(guard_path, guard)
            state = "complete" if guard["valid"] else guard["stage"]
            return 0 if guard["valid"] else 2
    except Exception as error:
        status["error_type"] = type(error).__name__
        status["n_errors"] = status.get("n_errors", 0) + 1
        guard.update(stage="error", valid=False, error_type=type(error).__name__)
        write_json(guard_path, guard)
        return 2
    finally:
        status.update(stage=state, wall_s=time.perf_counter() - started)
        write_json(status_path, status)
        write_json(out / "twostep-polish/logs/supervisor.json", status)
        clear_ledger(args.ledger, args.history, args.ledger_key, started, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--status", type=Path, default=HERE / "twostep-polish-status.json")
    parser.add_argument("--workers", type=int, choices=range(1, 7), default=6)
    parser.add_argument("--seconds", type=float, default=15.)
    parser.add_argument("--expected-maps", type=int, default=30)
    parser.add_argument("--ledger", type=Path, default=ROOT / "experiments/fleet/ledger.md")
    parser.add_argument("--history", type=Path, default=ROOT / "experiments/fleet/history.md")
    parser.add_argument("--ledger-key", default="test91-polish/cpu")
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds < 0 or args.expected_maps < 1:
        parser.error("invalid budget or map count")
    return supervise(args)


if __name__ == "__main__":
    sys.exit(main())
