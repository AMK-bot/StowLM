"""Two-step references on the val15 ablation maps (D110: the ablation runs on validation, never test). Same pipeline
as the test v4.4 references: TwoStepPlanner (D79 C2 off), STOWAGE_BALLAST_V4=1 scoped to finalize only, full checker,
no repair. Uses the live two-step source (the test references used the frozen v4.4 runtime; the hash of the live
twostep.py is recorded so the two can be compared). Resumable; one JSON per map. CPU, one core.
Usage: uv run python experiments/mvp/sft/ablation/twostep_val.py <list.txt> <out_dir> [finalize_s=120]
The finisher budget is the same number StowLM gets (D128.5: equal budgets); "outcome" separates finisher time limits
from planner dead ends and checker failures.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    from llm.stowlm.sft_data import d79_engine_config
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.planner import finalize
    from stowage.solver.twostep import TwoStepPlanner

    paths = [p for p in Path(sys.argv[1]).read_text().split() if p]
    out = Path(sys.argv[2])
    finalize_s = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
    out.mkdir(parents=True, exist_ok=True)
    src_sha = hashlib.sha256((ROOT / "src/stowage/solver/twostep.py").read_bytes()).hexdigest()
    assert os.environ.get("STOWAGE_BALLAST_V4") is None
    for rel in paths:
        instance = load_instance(ROOT / rel)
        target = out / f"{instance.id}.json"
        if target.exists():
            continue
        t = time.perf_counter()
        result = TwoStepPlanner(feasibility_config=d79_engine_config()).plan(instance, seed=0, time_limit_s=120)
        row = {"instance_id": instance.id, "input": rel, "twostep_py_sha256": src_sha, "plan_status": result.status,
               "finalize_s_budget": finalize_s}
        if result.plan is not None:
            os.environ["STOWAGE_BALLAST_V4"] = "1"
            t_fin = time.perf_counter()
            try:
                result = finalize(instance, result, time_limit_s=finalize_s)
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
                          and row.get("finalize_s", 0) >= 0.9 * finalize_s
                          else "finisher error" if row.get("finalize_status") not in (None, "ok") else "checker fail")
        row["s"] = round(time.perf_counter() - t, 2)
        target.write_text(json.dumps(row, indent=1))
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
