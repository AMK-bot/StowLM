"""Describe observed paired-mask costs; incomplete paths are never plan estimates."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def costs(states):
    answer = {"paired_states": len(states)}
    for engine in ("reference", "candidate"):
        values = [s[f"{engine}_index_ms"] for s in states]
        answer[engine] = (
            {"median_ms": float(np.median(values)), "p95_ms": float(np.percentile(values, 95))}
            if values else None
        )
    return answer


def main():
    rows = []
    for path in sorted(HERE.glob("gate-saved-full-*.json")):
        data = json.loads(path.read_text())
        if "runs" not in data:
            continue
        for key, run in data["runs"].items():
            states = run["states"]
            rows.append({
                "shard": path.name,
                "case": key,
                "status": run["status"],
                "actions": len(run["actions"]),
                "wall_s": run.get("wall_s"),
                "source_audit_finished": "sources_after" in data,
                "usage": data.get("usage"),
                "all_observed_prefixes": costs(states),
                "initial_depth_0_to_12": costs([s for s in states if s["depth"] <= 12]),
                "deep_depth_at_least_100": costs([s for s in states if s["depth"] >= 100]),
            })
    samples = [json.loads(line) for line in (HERE / "gate-memory.jsonl").read_text().splitlines()]
    # Polls consist of one sample per live worker. A 1-second bucket groups these
    # read-only observations, whose timestamps differ by less than 10 ms.
    aggregates = {}
    for sample in samples:
        bucket = int(sample["time"])
        pid = sample.get("pid", sample.get("owned_pid"))
        aggregates.setdefault(bucket, {})[pid] = sample["rss_bytes"] or 0
    observed_peak = max((sum(rss.values()) for rss in aggregates.values()), default=0)
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "interpretation": "Observed partial-path timings only; no complete-plan estimate or causal speedup claim.",
        "memory": {"observed_max_aggregate_rss_bytes": observed_peak,
                   "soft_guard_bytes": 7_300_000_000, "hard_budget_bytes": 8_000_000_000},
        "full_cases": rows,
    }
    (HERE / "gate-partial-stats.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
