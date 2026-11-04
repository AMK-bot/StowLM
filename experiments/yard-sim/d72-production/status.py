"""Cheap, read-only status of the two cooperative study executors."""
from collections import Counter
import json
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
rows, delivered = {}, {}
for name in ("prefetch.jsonl", "results.jsonl"):
    path = HERE / name
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()
    if text and not text.endswith("\n"):
        lines = lines[:-1]  # a driver is currently appending its last record
    delivered[name] = len(lines)
    for line in lines:
        row = json.loads(line)
        rows[json.dumps(row["spec"], sort_keys=True)] = row
values = list(rows.values())
active = []
for folder in ("shards", "prefetch-shards"):
    jobs = list((HERE / folder).glob("*.job.json"))
    if jobs:
        latest = max(jobs, key=lambda path: path.stat().st_mtime)
        output = latest.with_name(latest.name.removesuffix(".job.json") + ".json")
        if not output.exists():
            active.append({"executor": folder, "spec": json.loads(latest.read_text()),
                           "age_s": round(time.time() - latest.stat().st_mtime, 1)})
print(json.dumps({"delivered": delivered, "unique_cases": len(values), "target": 768,
    "statuses": dict(Counter(r["status"] for r in values)),
    "a8_over_limit": sum(b["over_limit_stacks"] for r in values for a in r.get("arms", {}).values() for b in a["blocks"]),
    "peak_worker_gib": max((r.get("peak_rss_gib", 0) for r in values), default=0),
    "latest_pending_jobs": active,
    "measured_1700_full": sum(r["status"] == "measured" and r["spec"]["ship"] == "feeder_1700"
                              and r["spec"]["rung"] == "full" for r in values)}))
