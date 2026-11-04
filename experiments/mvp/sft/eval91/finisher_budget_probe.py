"""D128.5 probe: does a longer (equal) finisher budget rescue StowLM's finisher time limits? Re-finalizes saved
complete StowLM plans (val15, never test) with STOWAGE_BALLAST_V4=1 scoped to finalize, at each budget; full check.
Usage: uv run python experiments/mvp/sft/eval91/finisher_budget_probe.py <run_dir> <instance_id> <budget_s>..."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from stowage.planner import finalize

    run, iid, budgets = Path(sys.argv[1]), sys.argv[2], [float(x) for x in sys.argv[3:]]
    rec = next(json.loads(p.read_text()) for p in run.glob("instances/*.json") if json.loads(p.read_text())["instance_id"] == iid)
    instance = load_instance(ROOT / rec["input"])
    result = PlannerResult.model_validate(json.loads((run / "instances" / iid / "result.json").read_text()))
    plan = result.plan.model_copy(update={"ballast": None})
    for b in budgets:
        os.environ["STOWAGE_BALLAST_V4"] = "1"
        t = time.perf_counter()
        try:
            out = finalize(instance, result.model_copy(update={"plan": plan, "status": "ok"}), time_limit_s=b)
        finally:
            os.environ.pop("STOWAGE_BALLAST_V4", None)
        fin_s = time.perf_counter() - t
        rep = check(instance, out.plan) if out.status == "ok" and out.plan is not None else None
        print(json.dumps({"instance_id": iid, "budget_s": b, "finalize_status": out.status, "finalize_s": round(fin_s, 1),
                          "cause": (out.metrics or {}).get("not_found_cause"), "passed": rep.passed if rep else None,
                          "lifts": rep.count.weighted_nonproductive if rep and rep.passed else None}), flush=True)


if __name__ == "__main__":
    main()
