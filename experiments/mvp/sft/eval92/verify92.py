"""9.2 post-run gate: every stored ``ok`` StowLM showcase plan must pass a FRESH full checker on its bound input (the
config.json input + SHA-256); dead ends / time limits are recorded as such. Writes <run>/verify.json; exits 1 if a stored
``ok`` plan fails the fresh check (the demo would show it as REJECTED BY CHECKER, never as a result).
Usage: uv run python experiments/mvp/sft/eval92/verify92.py <run_dir>
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import PlannerResult

    run = Path(sys.argv[1])
    inputs = {}
    for item in json.loads((run / "config.json").read_text())["inputs"]:
        path = ROOT / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], f"input changed: {path}"
        inputs[json.loads(path.read_text())["id"]] = path
    rows, bad = {}, 0
    for res in sorted(run.glob("instances/*/result.json")):
        iid = res.parent.name
        result = PlannerResult.model_validate(json.loads(res.read_text()))
        outcome = json.loads((res.parent / "outcome.json").read_text())
        row = {"status": result.status, "outcome": outcome["outcome"], "repaired": result.repaired}
        if result.status == "ok":
            report = check(load_instance(inputs[iid]), result.plan)
            row.update(fresh_check_passed=report.passed,
                       weighted_lifts=report.count.weighted_nonproductive if report.passed else None,
                       first_hard_rule=next((v.rule_id for v in report.violations if v.severity == "hard"), None))
            bad += (not report.passed) or result.repaired
        rows[iid] = row
    (run / "verify.json").write_text(json.dumps({"rows": rows, "all_shipped_plans_pass_fresh_check": bad == 0}, indent=1))
    print(json.dumps(rows, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
