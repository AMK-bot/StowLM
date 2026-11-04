"""Resequence the T4 full-size C1 plans (one-side-first and alternating) and replay D64 in-port before/after.

The T4 builders live in tests/checker/test_in_port_acceptance.py (production vessel, port, yard, berth arrival with
50% HEEL). Usage: uv run python experiments/inport-order/t4_reseq.py <ship> <one-side|alternating> [--no-replay]
Writes experiments/inport-order/t4/<ship>.<variant>.json.
"""
import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "checker"))

from test_in_port_acceptance import _prepared_manual_plan  # noqa: E402
from stowage.checker import full  # noqa: E402
from stowage.planner_resequence import resequence  # noqa: E402
from stowage.rules.catalogue import rule_freeze_v1  # noqa: E402


def fingerprint(plan):
    return sorted(m.model_dump_json() for m in plan.moves)


def summary(report):
    lists = {k: v for k, v in report.margins.items() if k.endswith(".list_rad")}
    negative = sorted((int(k.split(":")[1].split(".")[0]), v) for k, v in lists.items() if v < 0)
    first = report.violations[0] if report.violations else None
    return dict(ran=report.ran, error=report.error, passed=report.ran and not report.violations,
                k=report.metrics.get("k"), state_count=report.metrics.get("state_count"),
                n_violations=len(report.violations),
                rules=sorted({v.rule_id for v in report.violations}),
                first_failure=None if first is None else dict(rule=first.rule_id, message=first.message[:300],
                    state=report.metrics.get("failure.state_index")),
                min_list_margin_rad=min(lists.values()) if lists else None,
                negative_list_states=len(negative), first_negative_list=negative[0] if negative else None)


def main():
    ship, variant = sys.argv[1], sys.argv[2]
    replay = "--no-replay" not in sys.argv
    out = ROOT / "experiments/inport-order" / os.environ.get("T4_OUT", "t4") / f"{ship}.{variant}.json"
    instance, plan = _prepared_manual_plan(ship, one_side_first=variant == "one-side")
    record = dict(ship=ship, variant=variant, n_moves=len(plan.moves))
    with rule_freeze_v1(enabled=True):
        t = time.monotonic()
        outcome = resequence(instance, plan, time_limit_s=120.0)
        record["resequence_wall_s"] = time.monotonic() - t
        record["resequence"] = {k: v for k, v in outcome.report.items() if not k.startswith("APPROX")}
        record["moves_unchanged"] = fingerprint(outcome.plan) == fingerprint(plan)
        assert record["moves_unchanged"]
        print(json.dumps(record, indent=1), flush=True)
        if replay:
            for label, candidate in (("before", plan), ("after", outcome.plan)):
                if label == "after" and not outcome.applied:
                    record["after"] = record["before"]
                    continue
                t = time.monotonic()
                report = full._in_port(instance, candidate)
                record[label] = dict(summary(report), wall_s=time.monotonic() - t)
                print(label, json.dumps(record[label]), flush=True)
    out.write_text(json.dumps(record, indent=1, default=str))


if __name__ == "__main__":
    main()
