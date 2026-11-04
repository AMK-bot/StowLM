"""Driver: generate cases, run planners, measure every saved plan, collect a JSONL.

    uv run python experiments/inport-order/run_all.py --stage all --workers 3 --out results/part1.jsonl
    uv run python experiments/inport-order/run_all.py --stage measure --resequence --workers 3 --out results/part1-reseq.jsonl

Each job is a separate one-core subprocess (thread env pinned to 1) with its PID written to runs/<job>.pid and
its output to logs/<job>.log. A job exceeding its wall cap is terminated by its recorded PID only. Stages are
idempotent: an existing output is skipped unless --force. Run the driver itself detached, e.g.
    nohup .venv/bin/python experiments/inport-order/run_all.py ... > experiments/inport-order/logs/run_all.log 2>&1 &
    echo $! > experiments/inport-order/runs/run_all.pid
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

REPO = common.ROOT.parent.parent
SNAPSHOT = common.ROOT / "snapshot"
# The live tree is edited by other agents while these jobs run; instances_v3.generate aborts with "physics
# implementation changed during generation" whenever a physics source changes mid-run. Jobs therefore run against
# an APFS-cloned snapshot (src/, params/, data/, needed experiments/ inputs and caches); part 1 used a clone of
# the w2-freeze-prep 22:35 EDT devtree (see SNAPSHOT.json). The interpreter is the repo .venv; the snapshot
# src wins via PYTHONPATH and repo_root() resolves to the snapshot (it holds pyproject.toml).
TREE = {"snapshot": SNAPSHOT, "live": REPO}
PY = [str(REPO / ".venv/bin/python")]
_tree = "snapshot"


def _env():
    env = dict(os.environ)
    env.update(common.THREAD_ENV)
    env.setdefault("UV_CACHE_DIR", str(REPO / ".uv-cache"))
    env["PYTHONPATH"] = str(TREE[_tree] / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_job(name, argv, cap_s):
    common.RUNS.mkdir(parents=True, exist_ok=True)
    common.LOGS.mkdir(parents=True, exist_ok=True)
    log = open(common.LOGS / f"{name}.log", "w")
    started = time.monotonic()
    proc = subprocess.Popen(PY + argv, cwd=TREE[_tree], env=_env(), stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    pidfile = common.RUNS / f"{name}.pid"
    pidfile.write_text(str(proc.pid))
    timed_out = False
    try:
        rc = proc.wait(timeout=cap_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGTERM)  # our own child's session only
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            rc = proc.wait()
    log.close()
    pidfile.unlink(missing_ok=True)
    wall = time.monotonic() - started
    print(json.dumps(dict(job=name, rc=rc, timed_out=timed_out, wall_s=round(wall, 1))), flush=True)
    return dict(job=name, rc=rc, timed_out=timed_out, wall_s=wall)


def gen_jobs(roster, force):
    for case in roster:
        out = common.CASES / case["case_id"] / "instance.json"
        status = common.CASES / case["case_id"] / "generation.json"
        if not force and (out.exists() or status.exists()):
            continue
        cap = {"tiny": 600, "small": 900, "medium": 1800, "full": 3600}[case["rung"]]
        yield (f"gen.{case['case_id']}", [str(common.ROOT / "build_cases.py"), "gen", case["case_id"]], cap)


def plan_jobs(roster, force):
    for case in roster:
        d = common.CASES / case["case_id"]
        if not (d / "instance.json").exists():
            continue
        for method in common.METHODS[case["rung"]]:
            if not force and (d / f"plan.{method}.status.json").exists():
                continue
            limit = common.TIME_LIMITS[method][case["rung"]]
            yield (f"plan.{case['case_id']}.{method}",
                   [str(common.ROOT / "build_cases.py"), "plan", case["case_id"], method],
                   limit * 2 + 600)


def measure_jobs(roster, force, resequence):
    suffix = ".reseq" if resequence else ""
    for case in roster:
        d = common.CASES / case["case_id"]
        for method in common.METHODS[case["rung"]]:
            if not (d / f"plan.{method}.json").exists():
                continue
            out = d / f"measure.{method}{suffix}.json"
            if not force and out.exists():
                continue
            argv = [str(common.ROOT / "measure.py"), str(d), method, "--out", str(out)]
            if resequence:
                argv.append("--resequence")
            cap = {"tiny": 900, "small": 1800, "medium": 2700, "full": 5400}[case["rung"]]
            yield (f"measure{suffix}.{case['case_id']}.{method}", argv, cap)


def _order(jobs):
    # Longest first (full, then medium, then small) so the tail is short.
    rank = {"full": 0, "medium": 1, "small": 2, "tiny": 3}
    return sorted(jobs, key=lambda j: rank.get(j[0].split(".")[1].split("-")[0], 3))


def run_stage(jobs, workers):
    jobs = _order(list(jobs))
    print(json.dumps(dict(stage_jobs=len(jobs))), flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda j: run_job(*j), jobs))


def run_pipeline(roster, workers, force, resequence):
    """Per case: gen -> each planner -> each measure, sequential in one worker; cases small->medium->full."""
    rank = {"tiny": -1, "small": 0, "medium": 1, "full": 2}
    cases = sorted(roster, key=lambda c: rank[c["rung"]])

    def one(case):
        for job in gen_jobs([case], force):
            run_job(*job)
        for job in plan_jobs([case], force):
            run_job(*job)
        for job in measure_jobs([case], force, resequence):
            run_job(*job)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, cases))


def collect(roster, out, resequence):
    suffix = ".reseq" if resequence else ""
    lines = []
    for case in roster:
        d = common.CASES / case["case_id"]
        for method in common.METHODS[case["rung"]]:
            status_path = d / f"plan.{method}.status.json"
            meas = d / f"measure.{method}{suffix}.json"
            row = {"case_id": case["case_id"], "ship": case["ship"], "rung": case["rung"], "method": method,
                   "port": case["port"], "generator": case["generator"]}
            gen = d / "generation.json"
            if gen.exists():
                g = json.loads(gen.read_text())
                row["generation_status"] = g.get("status")
                if g.get("status") != "ok":
                    row["generation_attempts"] = g.get("attempts")
            else:
                row["generation_status"] = "not_run"
            if status_path.exists():
                st = json.loads(status_path.read_text())
                row["planner"] = {k: st.get(k) for k in ("planner", "status", "message", "wall_s", "time_limit_s",
                                                          "n_moves", "has_plan", "has_ballast")}
            elif row["generation_status"] == "ok":
                row["planner"] = {"status": "not_run_or_killed"}
            if meas.exists():
                row["measure"] = json.loads(meas.read_text())
            lines.append(row)
    out = Path(out)
    if not out.is_absolute():
        out = common.ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in lines))
    print(json.dumps(dict(collected=len(lines), out=str(out))), flush=True)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("gen", "plan", "measure", "collect", "all", "pipeline"), default="measure")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--resequence", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--only", default=None, help="substring filter on case_id")
    p.add_argument("--out", default="results/part1.jsonl")
    p.add_argument("--tree", choices=("snapshot", "live"), default="snapshot",
                   help="code/params tree the jobs import (default: the frozen snapshot used for part 1)")
    args = p.parse_args(argv)
    global _tree
    _tree = args.tree
    if args.workers > 3:
        raise SystemExit("at most 3 one-core workers")
    roster = common.load_roster()["cases"]
    if args.only:
        roster = [c for c in roster if args.only in c["case_id"]]
    if args.stage == "pipeline":
        run_pipeline(roster, args.workers, args.force, args.resequence)
    if args.stage in ("gen", "all"):
        run_stage(gen_jobs(roster, args.force), args.workers)
    if args.stage in ("plan", "all"):
        run_stage(plan_jobs(roster, args.force), args.workers)
    if args.stage in ("measure", "all"):
        run_stage(measure_jobs(roster, args.force, args.resequence), args.workers)
    collect(roster, args.out, args.resequence)


if __name__ == "__main__":
    main()
