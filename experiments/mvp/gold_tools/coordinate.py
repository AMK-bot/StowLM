"""Set gold worker ceilings; supervisors admit against live load and memory.

Leader 15:30: small is the critical chain. Stale ledger reservations remain
visible as diagnostics, but cannot block live-load-first admission.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from driver import atomic_json
from release_gate import release_ready

TINY_TRAIN_TARGET = 800


def training_stats(output):
    # APPROXIMATION: recent measured wall effort per gold is a proxy for the
    # remaining CPU work. This changes scheduling only, never acceptance gates.
    counts = Counter()
    samples = {r: deque(maxlen=30) for r in ("tiny", "small")}
    paths = sorted(output.glob("train/*/**/result.json"), key=lambda p: p.stat().st_mtime_ns)
    for path in paths:
        row = json.loads(path.read_text())
        rung = row.get("rung")
        if rung not in samples or not row.get("completed"):
            continue
        good = bool(row.get("passed"))
        counts[rung] += good
        samples[rung].append((float(row.get("wall_s", 0)), good))
    costs = {"tiny": 60.0, "small": 150.0 / .75}
    basis = {"tiny": "planning assumption: 60 wall-seconds per gold",
             "small": "leader planning assumption: 150 seconds per attempt / 75% yield"}
    for rung, rows in samples.items():
        successes = sum(good for _, good in rows)
        if successes:
            costs[rung] = sum(wall for wall, _ in rows) / successes
            basis[rung] = f"measured wall effort: {len(rows)} recent train attempts / {successes} gold"
    return counts, costs, basis


def gold_capacity(ledger):
    reserved, unknown = 0.0, []
    for line in ledger.read_text().splitlines():
        fields = [s.strip() for s in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(fields) < 7 or fields[0] == "executive/job" or set(fields[0]) <= {"-"}:
            continue
        if fields[0] in {"mvp-gold/tiny", "mvp-gold/small"}:
            continue
        match = re.search(r"\d+(?:\.\d+)?", fields[2])
        if not match:
            unknown.append(fields[0])
        else:
            reserved += float(match.group())
    # Admission in each supervisor also verifies RAM and live memory pressure.
    return (0 if unknown else min(12, max(0, math.floor(16-reserved)))), reserved, unknown


def allocate(pool, counts, costs, small_ready, work_available=None):
    work = work_available or {"tiny": True, "small": True}
    if not small_ready:
        return (min(5, pool) if work["tiny"] else 0), 0
    # D100: release tiny slots at 800 published training gold even if unused
    # corpus/validation work remains. In-flight attempts drain normally.
    tiny_need = counts.get("tiny", 0) < TINY_TRAIN_TARGET and work["tiny"]
    small_need = work["small"]
    if not small_need:
        return min(2, pool) if tiny_need else 0, 0
    if not tiny_need:
        return 0, min(10, pool)
    tiny = min(2, max(0, pool-1))
    return tiny, min(10, pool-tiny)


def priority_ceilings(pool, counts, costs, ready, work, small_paused=False):
    if small_paused:
        return (10 if work["tiny"] else 0), 0
    return allocate(pool, counts, costs, ready, work)


def d97_ceilings(work, ready, counts=None):
    # Leader 18:05: target ten small workers alongside two tiny; measured-CPU
    # admission controls actual starts. ASSUMPTION: "then all small" transfers
    # those two slots to small within the same twelve-worker gold ceiling.
    # Validation inventory cannot keep tiny slots reserved after its target.
    counts = counts or {}
    tiny = 2 if work["tiny"] and counts.get("tiny", 0) < TINY_TRAIN_TARGET else 0
    small = (10 if tiny else 12) if ready and work["small"] else 0
    return tiny, small


def reserve_probe_slot(tiny_workers, small_workers, probe_active):
    # D100: keep one of the eight authorized CPU-probe-failure slots for the
    # sequential ballast diagnostic. Existing workers finish normally.
    if probe_active:
        small_workers = min(small_workers, max(0, 7-tiny_workers))
    return tiny_workers, small_workers


def remaining_inventory_work(corpus, output, rung, settings, val_cap):
    val_count = sum(bool(row.get("completed") and row.get("passed"))
                    for path in (output / "val" / rung).rglob("result.json")
                    for row in [json.loads(path.read_text())])
    for split in ("train", "val"):
        if split == "val" and val_count >= val_cap:
            continue
        request = corpus / f"{split}.requested.json"
        if not request.exists():
            return True
        for spec in json.loads(request.read_text()).get("specs", []):
            if spec.get("rung") != rung:
                continue
            expected = settings.get(spec.get("vessel")) if settings else {}
            if expected is None or any(spec.get(k) != value for k, value in expected.items()):
                continue
            result = (output / split / rung / spec["vessel"] / spec["generator"] /
                      f"seed-{spec['seed']:09d}" / "result.json")
            if not result.exists() or not json.loads(result.read_text()).get("completed"):
                return True
    return False


def has_small_shard(corpus, settings):
    for split in ("train", "val"):
        for path in (corpus / split / "small").rglob("*.audit.json"):
            audit = json.loads(path.read_text())
            config = audit.get("config", {})
            expected = settings.get(config.get("vessel"))
            if expected and all(config.get(k) == v for k, v in expected.items()) and audit.get("accepted") is True:
                return True
    return False


def update_policy(path, workers, small=False, admission_mode="live_load_first"):
    previous = json.loads(path.read_text()) if path.exists() else {}
    # The coordinator owns scheduling fields only. Preserve runtime ballast
    # budgets (and other independently managed solver policy fields).
    value = dict(previous)
    value.update(max_load=16, train_first=True, max_workers=workers,
                 admission_mode=admission_mode,
                 val_gold_caps={"tiny": 50, "small": 56})
    if previous != value:
        atomic_json(path, value)


def start_small(root, output, ledger):
    command = [sys.executable, str(Path(__file__).with_name("launch.py")), "--name", "small-v1", "--",
        "--rung", "small", "--workers", "12", "--adaptive-workers", "--max-load", "16",
        "--policy-file", str(output / "small-policy.json"), "--solver-s", "120", "--ballast-s", "120",
        "--ballast-v4", "--follow", "--poll-s", "10", "--resume-finished",
        "--input-settings", str(output / "small-input-settings.json"),
        "--scalable-config", str(output / "small-scalable.json"),
        "--admit-ledger", str(ledger), "--ledger-owner", "mvp-gold/small",
        "--stop-file", str(output / "STOP-small"), "--output", str(output),
        "--corpus-root", str(root / "experiments/mvp/corpus")]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def active_gold_workers(output):
    counts = Counter()
    for path in (output / "runtime").glob("*.json"):
        runtime = json.loads(path.read_text())
        if runtime.get("status") not in {"running", "starting", "queued_admission"}:
            continue
        process = next((p for p in output.glob("*.process.json")
                        if json.loads(p.read_text()).get("pid") == runtime.get("pid")), None)
        if process:
            command = json.loads(process.read_text()).get("command", [])
            if "--rung" in command:
                rung = command[command.index("--rung")+1]
                counts[rung] += len(runtime.get("active_worker_pids", []))
    return counts


def deadline_projection(counts, costs, workers, now):
    deadline = datetime(2026, 10, 30, 6, tzinfo=timezone.utc)
    hours = max(0, (deadline-now).total_seconds()/3600)
    rate = 3600/costs["small"]
    return dict(deadline_utc=deadline.isoformat(), hours_remaining=hours,
                measured_small_gold_per_worker_hour=rate,
                active_small_workers=workers.get("small", 0),
                small_train_now=counts["small"],
                projected_small_train_at_current_workers=counts["small"]+hours*rate*workers.get("small", 0),
                projected_small_train_at_ten_workers=counts["small"]+hours*rate*10,
                approximation="Linear recent measured attempt-wall effort per gold; assumes unchanged yield, ample corpus and continuous utilisation; excludes admission waits and ramp time.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiments/mvp/gold"))
    parser.add_argument("--poll-s", type=float, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    output = args.output.resolve()
    corpus, ledger = root / "experiments/mvp/corpus", root / "experiments/fleet/ledger.md"
    while True:
        try:
            gate = release_ready(corpus)
            settings = json.loads((output / "small-input-settings.json").read_text())
            ready = gate["ready"] and has_small_shard(corpus, settings)
            counts, costs, basis = training_stats(output)
            _, other, unknown = gold_capacity(ledger)
            pool = 12  # Policy ceilings; live admission controls actual starts.
            load = os.getloadavg()[0]
            work = dict(tiny=remaining_inventory_work(corpus, output, "tiny", {}, 50),
                        small=remaining_inventory_work(corpus, output, "small", settings, 56))
            d97_go = (output / "D97-GO").exists()
            small_paused = (output / "D96-PAUSE-SMALL").exists() and not d97_go
            tiny_workers, small_workers = priority_ceilings(pool, counts, costs, ready, work, small_paused)
            if d97_go:
                tiny_workers, small_workers = d97_ceilings(work, ready, counts)
            ballast_probe = (output / "D100-BALLAST-PROBE").exists()
            tiny_workers, small_workers = reserve_probe_slot(tiny_workers, small_workers, ballast_probe)
            admission_mode = "measured_cpu" if (output / "CPU-ADMISSION").exists() else "live_load_first"
            if admission_mode == "measured_cpu":
                tiny_workers = min(8, tiny_workers)  # Leader-authorized fallback when CPU probes are denied.
            update_policy(output / "tiny-policy.json", tiny_workers, admission_mode=admission_mode)
            update_policy(output / "small-policy.json", small_workers, small=True,
                          admission_mode="measured_cpu" if d97_go else "live_load_first")
            small_record = output / "small-v1.process.json"
            launch = None
            if ready and not small_paused and not small_record.exists():
                launch = start_small(root, output, ledger)
            active_workers = active_gold_workers(output)
            projection = deadline_projection(counts, costs, active_workers, datetime.now(timezone.utc))
            state = dict(active_workers=dict(active_workers), projection_0200=projection, updated_utc=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
                status="D97_small_warmstart_priority" if d97_go else ("D96_small_paused_tiny_priority" if small_paused else ("sharing" if ready else "waiting_frozen_small")),
                d97_go=d97_go, tiny_train_target=TINY_TRAIN_TARGET,
                tiny_train_target_reached=counts.get("tiny", 0) >= TINY_TRAIN_TARGET,
                ballast_probe_reservation=ballast_probe,
                small_paused=small_paused, release_gate=gate,
                matching_small_shard=ready, published_train=dict(counts), seconds_per_gold=costs,
                cost_basis=basis,
                gold_pool_ceiling=pool, admission_mode=admission_mode,
                other_reserved_cores=other, load_1m=load,
                unknown_reservations=unknown, remaining_inventory_work=work, tiny_worker_ceiling=tiny_workers,
                small_worker_ceiling=small_workers, started_small=launch)
            atomic_json(output / "coordination.json", state)
        except Exception as exc:
            atomic_json(output / "coordination.json", dict(status="retry", pid=os.getpid(),
                error=f"{type(exc).__name__}: {exc}", updated_utc=datetime.now(timezone.utc).isoformat()))
        if args.once or (output / "STOP-coordinator").exists():
            break
        time.sleep(args.poll_s)


if __name__ == "__main__":
    main()
