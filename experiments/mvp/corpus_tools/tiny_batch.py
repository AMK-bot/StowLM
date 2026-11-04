"""D79 tiny-only restart, independently resumable across live source epochs.

Three disjoint single-core shards may be admitted separately. Source edits stop
the current child; the next child imports the new epoch and retains completed
tiny bytes with their original audits. No small/medium job is selected.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import build
import campaign


def selection(root, index, count):
    tiny = [s for s in campaign.jobs(root) if s["rung"] == "tiny"]
    return tiny[index::count]


def pending(specs, root):
    result = []
    for spec in specs:
        path = build.shard_path(spec, root)
        sidecar = path.with_suffix(".audit.json")
        if not sidecar.exists():
            result.append(spec)
            continue
        audit = json.loads(sidecar.read_text())
        if (audit.get("config") != spec or audit.get("accepted") is not True
                or audit.get("rule_freeze_v1_enabled") is not False
                or not path.exists() or build.sha256(path) != audit["instance_sha256"]):
            raise ValueError(f"invalid completed tiny shard: {path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=build.CORPUS)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    assert 0 <= args.shard_index < args.shard_count <= 3
    specs = selection(args.root, args.shard_index, args.shard_count)
    if args.limit is not None:
        # Limit newly generated jobs, preserving all completed jobs in the shard.
        missing = pending(specs, args.root)[:args.limit]
        specs = [s for s in specs if s not in pending(specs, args.root)] + missing
    todo = pending(specs, args.root)
    if args.worker:
        build.run(todo, workers=1, root=args.root)
        return
    runtime = Path(__file__).with_name(f"tiny-{args.shard_index}.runtime.json")
    log = Path(__file__).with_name(f"tiny-{args.shard_index}.log")
    started = time.monotonic()
    state = dict(parent_pid=os.getpid(), child_pids=[], shard_index=args.shard_index,
                 requested=len(specs), initially_complete=len(specs)-len(todo),
                 started_at_utc=datetime.now(timezone.utc).isoformat(), requested_nice=10)
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    except OSError as error:
        state["nice_error"] = str(error)
    state["effective_nice"] = os.getpriority(os.PRIO_PROCESS, 0)
    for attempt in range(30):
        todo = pending(specs, args.root)
        if not todo:
            break
        state.update(attempt=attempt, remaining=len(todo))
        argv = [sys.executable, str(Path(__file__).resolve()), "--root", str(args.root),
                "--shard-index", str(args.shard_index), "--shard-count", str(args.shard_count), "--worker"]
        if args.limit is not None:
            argv += ["--limit", str(max(0, args.limit - (len(specs)-len(todo)-state["initially_complete"])))]
        with log.open("a") as output:
            offset = output.tell()
            child = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT)
            state["child_pids"].append(child.pid)
            build.atomic_json(runtime, state)
            code = child.wait()
        tail = log.read_text()[offset:]
        state["last_exit_code"] = code
        if code == 0:
            break
        if not any(message in tail for message in ("source epoch changed", "source changed during generation")):
            break
        time.sleep(2)
    remaining = pending(specs, args.root)
    state.update(complete=not remaining, remaining=len(remaining),
                 elapsed_wall_s=time.monotonic()-started, ended_at_utc=datetime.now(timezone.utc).isoformat())
    build.atomic_json(runtime, state)
    print(json.dumps(state, sort_keys=True), flush=True)
    if remaining:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
