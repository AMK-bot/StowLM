"""Task 2.2 v6 stand-in hero: replan the 190-box package map under one real radio-clip rule, through binding.

    PYTHONPATH=.:src:experiments/mvp/frontier:experiments/mvp/frontier/lab uv run --offline --no-sync python experiments/mvp/ui/v6_standin.py

Writes experiments/mvp/ui/v6-hero/{after.plan.json, evidence.json, events.jsonl}. It replaces nothing: the demo's hero
slot takes task 1.10's plan when that lands (app/v6_hero.json). Engine 0.13.0 through the same binding as the pipeline.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
OUT = ROOT / "experiments/mvp/ui/v6-hero"
PACKAGE = ROOT / "experiments/mvp/pipeline/package/fixtures/complete"
ANSWER = {"constraint": {"bay": 27, "max_tiers": 1, "type": "max_tiers", "zone": "hold"}, "schema_version": "stowlm.reader/2"}


def main() -> int:
    from stowage.model.instance import Instance
    from experiments.mvp.pipeline.binding import plan_with_rules
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    instance = Instance.model_validate_json((PACKAGE / "instance.json").read_text())
    answer = validate_answer(json.dumps(ANSWER), context_from_instance(instance))
    OUT.mkdir(exist_ok=True)
    began = time.perf_counter()
    events = (OUT / "events.jsonl").open("w")

    def on_incumbent(*args, **kwargs):
        row = {"t_s": round(time.perf_counter() - began, 2), "args": repr(args)[:300], "kwargs": repr(kwargs)[:300]}
        events.write(json.dumps(row) + "\n"); events.flush()

    result, evidence = plan_with_rules(instance, [answer], budget_s=180, finalize_s=60, on_incumbent=on_incumbent)
    wall = time.perf_counter() - began
    (OUT / "evidence.json").write_text(json.dumps({"wall_s": wall, "computed_utc": datetime.now(timezone.utc).isoformat(), "answer": ANSWER,
                                                   "evidence": evidence}, indent=1, default=str))
    if result is not None and getattr(result, "plan", None) is not None:
        (OUT / "after.plan.json").write_text(result.plan.model_dump_json())
    print(json.dumps({"wall_s": wall, "status": evidence.get("status"), "passed": evidence.get("passed"), "lifts": evidence.get("lifts")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
