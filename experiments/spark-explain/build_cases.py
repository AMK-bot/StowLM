"""Build the four explainer evaluation cases (instance, plan, CheckReport) on one fresh tiny instance.

All four plans are checked by the live full checker (stowage.checker.check). On 28 Oct 2026 the shared fleet
parameters were mid-requalification, so every live report carries `checker.missing_vessel_data` hard violations and
stability does not run; no planner could produce a plan either ("production linear policy epoch is stale").
Cases A and B therefore use a *pass-modelled* report: the live report with only those stale-data violations removed
and `passed=True`, marked as a stub (`explain-eval`) so the explainer labels it "Stub-tainted ... not a validated
result". They test the explainer's pass wording; they are not checker passes. C and D are live reports as-is.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from stowage.checker import check, report_matches_inputs
from stowage.model import Instance, MovePlan

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("cases")
inst = Instance.model_validate_json((ROOT / "experiments/ui-shots/w2/fresh-tiny.json").read_text())
base = MovePlan.model_validate_json((ROOT / "experiments/ui-shots/w2/live-pilot/initial-plan.json").read_text())
moves = [m.model_dump(mode="json") for m in base.moves]

def plan(suffix, moves):
    return MovePlan.model_validate({**base.model_dump(mode="json"), "id": f"{base.id}.{suffix}", "moves": moves})

restow = [*moves[:6],
          {"call_index": 0, "kind": "restow_off", "container_id": "QZXU0000035", "src": "0030204"},
          {"call_index": 0, "kind": "restow_on", "container_id": "QZXU0000035", "dst": "0030204"}, moves[6]]
cases = {
    "A_wording_test_only_not_a_pass": (plan("A", moves), True),
    "B_wording_test_only_not_a_pass_restow": (plan("B", restow), True),
    "C_live_stale_vessel_data": (plan("C", moves), False),
    "D_live_shut_out": (plan("D", [m for m in moves if m.get("container_id") != "QZXU0000035"]), False),
}
OUT.mkdir(exist_ok=True)
(OUT / "instance.json").write_text(inst.model_dump_json())
for name, (p, model_pass) in cases.items():
    if len(sys.argv) > 1 and name not in sys.argv[1:]:
        continue
    report = check(inst, p)
    live = report.model_copy(deep=True)
    if model_pass:
        report.violations = [v for v in report.violations if v.rule_id != "checker.missing_vessel_data"]
        for part in report.parts.values():
            part.violations = [v for v in part.violations if v.rule_id != "checker.missing_vessel_data"]
        if report.violations:
            raise SystemExit(f"{name}: live report has other violations; not pass-modelling it: {report.violations}")
        report.passed = True
        report.provenance.stubs["explain-eval"] = ("pass-modelled: live report minus stale-fleet-data violations; "
                                                    "not a checker pass")
    assert report_matches_inputs(report, inst, p)
    (OUT / f"{name}.plan.json").write_text(p.model_dump_json())
    (OUT / f"{name}.report.json").write_text(report.model_dump_json())
    (OUT / f"{name}.live-report.json").write_text(live.model_dump_json())
    print(name, "live passed", live.passed, "violations", sorted({v.rule_id for v in live.violations}),
          "shut_out", report.shut_out, report.count)
