"""Row e: "+ 15 s LNS polish" on a run folder's own checker-passing plans (stowage.solver.gold_warmstart.improve_checked;
yard-order LNS that keeps every box-to-slot assignment; mask replay + securing + full checker gate every candidate).
Re-checks the result and never reports worse than the input plan. Resumable (one JSON per instance).
Usage: uv run python experiments/mvp/sft/ablation/polish_row.py <run_dir> <out_dir> [seconds=15]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from stowage.solver.gold_warmstart import improve_checked

    run, out = Path(sys.argv[1]), Path(sys.argv[2])
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
    out.mkdir(parents=True, exist_ok=True)
    for rec_path in sorted(run.glob("instances/*.json")):
        rec = json.loads(rec_path.read_text())
        folder = run / "instances" / rec["instance_id"]
        target = out / f"{rec['instance_id']}.json"
        if target.exists() or json.loads((folder / "outcome.json").read_text())["outcome"] != "own pass":
            continue
        instance = load_instance(ROOT / rec["input"])
        own = PlannerResult.model_validate(json.loads((folder / "result.json").read_text()))
        own_report = check(instance, own.plan)
        t = time.perf_counter()
        polished = improve_checked(instance, own, time_limit_s=seconds)
        report = check(instance, polished.plan) if polished.plan is not None else None
        before = own_report.count.weighted_nonproductive if own_report.passed else None
        after = report.count.weighted_nonproductive if report is not None and report.passed else None
        improved = before is not None and after is not None and after < before
        row = {"instance_id": rec["instance_id"], "rung": rec["rung"], "input": rec["input"],
               "own_lifts": before, "polished_lifts": after if improved else before, "improved": improved,
               "polish_s": round(time.perf_counter() - t, 2), "message": polished.message}
        target.write_text(json.dumps(row, indent=1))
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
