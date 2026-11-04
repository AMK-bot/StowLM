"""Admission-controlled supervisor for fixed campaign partitions 3 through 7.

Persisted from the running 2026-10-29 11:23 EDT heredoc supervisor; do not launch
a duplicate while that session is active. The running prototype's original
three-hour estimate was revised in the ledger/history to four hours; this saved
version incorporates that revision. No inventory or allocation is modified.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time
from zoneinfo import ZoneInfo

LEDGER = Path("experiments/fleet/ledger.md")
HISTORY = Path("experiments/fleet/history.md")
ZONE = ZoneInfo("America/New_York")
ESTIMATE = "<=4h under contention; measured35.74-37.62s/case x375; cutoff16EDT"


def stamp():
    return datetime.now(ZONE).isoformat(timespec="seconds")


def emit(event, **data):
    print(json.dumps(dict(event=event, time=stamp(), **data)), flush=True)


def resources():
    rows = []
    for line in LEDGER.read_text().splitlines():
        columns = [value.strip() for value in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(columns) >= 7:
            try:
                rows.append((columns[0], float(columns[2]), float(columns[3])))
            except ValueError:
                pass
    uptime = subprocess.check_output(["uptime"], text=True)
    load = float(re.search(r"load averages?:\s*([0-9.]+)", uptime).group(1))
    memory = subprocess.check_output(["memory_pressure"], text=True)
    free = float(re.search(r"System-wide memory free percentage:\s*([0-9.]+)%", memory).group(1))
    return sum(row[1] for row in rows), sum(row[2] for row in rows), load, free


def remove_own(index):
    target = f"| mvp-corpus/trainval-{index} |"
    for _ in range(10):
        raw = LEDGER.read_text()
        replacement = "".join(line for line in raw.splitlines(keepends=True)
                              if not line.startswith(target))
        if LEDGER.read_text() != raw:
            continue
        LEDGER.write_text(replacement)
        return
    raise RuntimeError("ledger changed repeatedly while releasing own row")


def log_records(path):
    statuses, errors, child_pid = {}, [], None
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if "worker_python_pid" in row:
            child_pid = row["worker_python_pid"]
        status = row.get("status")
        if status:
            statuses[status] = statuses.get(status, 0) + 1
        if status == "failed":
            errors.append(row)
    return statuses, errors, child_pid


def main():
    queue, running, completed, last_report = list(range(3, 8)), {}, {}, 0
    environment = dict(os.environ)
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[key] = "1"
    environment["UV_CACHE_DIR"] = str(Path.cwd() / ".uv-cache")
    environment["PYTHONUNBUFFERED"] = "1"
    emit("queue_started", workers=queue, estimate_per_worker=ESTIMATE, ps="denied by sandbox")
    while queue or running:
        now = datetime.now(ZONE)
        for index, record in list(running.items()):
            process = record["proc"]
            exit_code = process.poll()
            if exit_code is None:
                continue
            record["log"].close()
            elapsed = time.monotonic() - record["monotonic"]
            remove_own(index)
            statuses, errors, child_pid = log_records(record["path"])
            with HISTORY.open("a") as stream:
                stream.write(f'\n- mvp-corpus/trainval-{index}, start{record["started"]}, end{stamp()}: '
                             f'estimate1CPU/2GB/noGPU/{ESTIMATE}; actual{elapsed:.2f}s wall, '
                             f'exit{exit_code}, statuses{json.dumps(statuses, sort_keys=True)}; native1, '
                             f'reservation released; supervisor-recorded uv PID{process.pid}, Python PID{child_pid}.\n')
            completed[index] = dict(exit_code=exit_code, seconds=elapsed, statuses=statuses, errors=errors)
            emit("worker_finished", worker=index, pid=process.pid, **completed[index])
            del running[index]
        if now.hour >= 16:
            for index, record in running.items():
                _, _, child_pid = log_records(record["path"])
                # Signal only PIDs launched and recorded by this supervisor.
                if child_pid:
                    try:
                        os.kill(child_pid, 15)
                    except ProcessLookupError:
                        pass
                if record["proc"].poll() is None:
                    record["proc"].terminate()
                emit("cutoff_terminated_own", worker=index, uv_pid=record["proc"].pid, python_pid=child_pid)
            queue = []
        while queue and now.hour < 16:
            cores, ram, load, free = resources()
            if cores + 1 > 16 or ram + 2 > 112 or load >= 16 or free < 15:
                break
            index, started = queue.pop(0), stamp()
            with LEDGER.open("a") as stream:
                stream.write(f'| mvp-corpus/trainval-{index} | 0.2 fixed shard{index}/8, 375 resumable cases; '
                             f'native1 | 1 | 2 | no | {ESTIMATE}; admission ledger{cores:g}CPU/{ram:g}GB '
                             f'load{load:.2f}/free{free:g}%; ps sandbox denied | {started} |\n')
            path = Path(f"experiments/mvp/corpus_tools/trainval-{index}.log")
            log = path.open("w")
            # Equivalent to campaign.py --worker-index N, with a recorded child
            # PID so the cutoff never relies on process-name matching.
            code = (f'import os,json,runpy,sys; print(json.dumps({{"worker_python_pid":os.getpid(),'
                    f'"worker_index":{index}}}),flush=True); '
                    f'sys.argv=["experiments/mvp/corpus_tools/campaign.py","--worker-index","{index}"]; '
                    'sys.path.insert(0,"experiments/mvp/corpus_tools"); '
                    'runpy.run_path(sys.argv[0],run_name="__main__")')
            try:
                process = subprocess.Popen(["uv", "run", "python", "-u", "-c", code],
                    stdout=log, stderr=subprocess.STDOUT, env=environment)
            except BaseException:
                log.close()
                remove_own(index)
                raise
            running[index] = dict(proc=process, log=log, path=path, started=started,
                                  monotonic=time.monotonic())
            emit("worker_started", worker=index, uv_pid=process.pid, log=str(path),
                 admission=dict(cores=cores, ram_gb=ram, load=load, free_percent=free))
        if time.monotonic() - last_report >= 120:
            cores, ram, load, free = resources()
            emit("queue_progress", queued=queue, running={n: r["proc"].pid for n, r in running.items()},
                 completed=completed, ledger_cpu=cores, ledger_ram_gb=ram, load=load, free_percent=free)
            last_report = time.monotonic()
        if queue or running:
            time.sleep(30)
    emit("all_finished", completed=completed)


if __name__ == "__main__":
    raise SystemExit("Retired at D79 restart: use tiny_batch.py now; regenerate.py only after adopted-slack GO")
