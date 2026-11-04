"""CLI demo: a planner's sentence -> Muse Spark constraint -> replan -> full checker -> instruction honoured.

    set -a; . /path/outside/repo/.env; set +a; export SPARK_CREDIT_READY=1
    uv run python -m llm.instructions.demo --instance <instance.json> "Crane 1 is down, no loading in bay 01"

``--answer-json '<envelope>'`` skips Spark (offline rehearsal; labelled as such). The unconstrained "before" plan is
cached per instance under ``experiments/mvp/instruct/cache/``; ``--refresh-baseline`` recomputes it.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "experiments/mvp/instruct/cache"
# Job allocation for task 5.10 (plan v4): $3 of Spark on the shared ledger, counted from this job's start (UTC).
JOB_START = "2026-10-29T16:00:00"
ALLOCATION_USD = Decimal("3")


def _counts(report) -> dict | None:
    c = report.count if report is not None else None
    if c is None:
        return None
    return {"loads": c.loads, "yard_rehandles": c.relocations, "restow_lifts": c.restow_lifts,
            "hatch_cover_lifts": c.hatch_cover_lifts, "crane_lifts_now": c.total_lifts_now,
            "later_port_lifts": sum(f.restow_lifts + f.hatch_cover_lifts for f in c.future),
            "weighted_D11": c.weighted_nonproductive}


def _summary(run, constrained) -> dict:
    from llm.instructions.apply import ConstrainedInstance

    plan = run.plan
    return {
        "status": run.result.status if run.result is not None else "not run",
        "message": run.precheck or (run.result.message if run.result is not None else None),
        "checker_passed": bool(run.report is not None and run.report.passed),
        "shippable": bool(run.result is not None and run.result.shippable),
        "counts": _counts(run.report),
        "placements": [(m.container_id, m.dst) for m in plan.moves if m.kind == "load"] if plan else [],
        "wall_s": round(run.wall_s, 1),
        "plan": plan.model_dump(mode="json") if plan is not None else None,
    }


def _fingerprint(instance) -> str:
    import hashlib

    return hashlib.sha256(instance.model_dump_json().encode()).hexdigest()[:16]


def verify_cached(instance, cached: dict) -> dict | None:
    """Re-check a cached before-plan against *this* instance with *today's* full checker (rev-v4-a P2).

    Returns a summary whose verdict and counts come from the fresh report, or None when the entry is unusable
    (no plan, wrong instance, unbound report or a failing check), so the caller recomputes it. Cached verdicts and
    counts are never shown as current verification.
    """
    from stowage.checker import check, report_matches_inputs
    from stowage.model import MovePlan

    if not cached.get("plan") or cached.get("instance_sha256_16") not in (None, _fingerprint(instance)):
        return None
    try:
        plan = MovePlan.model_validate(cached["plan"])
    except ValueError:
        return None
    if plan.instance_id != instance.id or plan.call_index != instance.call_index:
        return None
    report = check(instance, plan)
    if not report.passed or not report_matches_inputs(report, instance, plan):
        return None
    return {**cached, "checker_passed": True, "counts": _counts(report),
            "placements": [(m.container_id, m.dst) for m in plan.moves if m.kind == "load"],
            "verified": "fresh full check of the cached plan on this instance, now"}


def _baseline(instance, *, refresh: bool, time_limit_s: float) -> dict:
    from llm.instructions.apply import ConstrainedInstance
    from llm.instructions.replan import replan

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{instance.id}.{_fingerprint(instance)}.baseline.json"
    if path.exists() and not refresh:
        verified = verify_cached(instance, json.loads(path.read_text()))
        if verified is not None:
            return verified
    run = replan(ConstrainedInstance(instance), time_limit_s=time_limit_s)
    summary = {**_summary(run, None), "instance_sha256_16": _fingerprint(instance),
               "verified": "planned and fully checked in this run"}
    path.write_text(json.dumps(summary, indent=1))
    return summary


def _fmt_counts(c: dict | None) -> str:
    if not c:
        return "no move counts"
    return (f"{c['loads']} loads, {c['yard_rehandles']} yard rehandles, {c['restow_lifts']} restow lifts, "
            f"{c['hatch_cover_lifts']} hatch-cover lifts = {c['crane_lifts_now']} crane lifts now "
            f"(+{c['later_port_lifts']} projected later; D11 weighted {c['weighted_D11']:g})")


def main(argv: list[str] | None = None) -> int:
    from stowage.io import load_instance
    from stowage.model import MovePlan

    from llm.instructions.apply import NotApplicable, apply, describe
    from llm.instructions.parser import JobGuard, ParseResult, parse_detailed
    from llm.instructions.replan import replan
    from llm.instructions.schema import Refusal, validate_answer

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sentence")
    ap.add_argument("--instance", required=True)
    ap.add_argument("--answer-json", help="offline: use this envelope instead of calling Muse Spark")
    ap.add_argument("--offline-rules", action="store_true",
                    help="offline: canonical-form rule parser instead of Muse Spark (refuses anything else)")
    ap.add_argument("--time-limit", type=float, default=240.0)
    ap.add_argument("--refresh-baseline", action="store_true")
    ap.add_argument("--save", help="write the full transcript JSON here")
    args = ap.parse_args(argv)

    out: dict = {"sentence": args.sentence, "instance": args.instance}
    print(f'Planner: "{args.sentence}"')
    if args.offline_rules:
        from llm.instructions.rules import parse_rules

        result = ParseResult(parse_rules(args.sentence), None, Decimal(0))
        print("Parser: [offline rule parser, canonical forms only; Muse Spark not called]")
    elif args.answer_json:
        result = ParseResult(validate_answer(args.answer_json), args.answer_json, Decimal(0))
        print("Parser: [offline rehearsal: supplied JSON, Muse Spark not called]")
    else:
        from llm.spark.client import SparkClient

        try:
            client = SparkClient(max_retries=0)
            guard = JobGuard(client.ledger.path, since=JOB_START, allocation_usd=ALLOCATION_USD)
        except Exception as exc:  # missing key etc.: refuse, never guess
            client, guard = None, None
            print(f"Parser: Muse Spark unavailable ({type(exc).__name__}); nothing applied.")
            return 2
        result = parse_detailed(args.sentence, client=client, guard=guard)
        print(f"Parser: Muse Spark (muse-spark-1.3), measured cost ${result.cost_usd}"
              + (f" [{result.error}]" if result.error else ""))
    outcome = result.outcome
    out["parse"] = {"raw": result.raw, "cost_usd": str(result.cost_usd), "error": result.error,
                    "outcome": outcome.model_dump(mode="json")}
    if isinstance(outcome, Refusal):
        print(f"Refused ({outcome.code}): {outcome.reason}")
        print("Nothing applied; the current plan stands.")
        _save(args.save, out)
        return 0
    print("Constraint (schema " + "stowlm.instruction/1, validated): " + json.dumps(outcome.model_dump(mode="json")))

    instance = load_instance(args.instance)
    try:
        constrained = apply(instance, outcome)
    except NotApplicable as exc:
        print(f"Refused (not applicable to this ship): {exc}. Nothing applied.")
        out["not_applicable"] = str(exc)
        _save(args.save, out)
        return 0
    print(f"Applied (tighten-only): {describe(constrained)}")
    load = set(instance.load_list)
    if not constrained._all_boxes and not any(b in load for b in constrained._per_box):
        print("This instruction closes no slot for this ship and load list: any checked plan already honours it. "
              "No replan needed.")
        out["no_effect"] = True
        _save(args.save, out)
        return 0

    before = _baseline(instance, refresh=args.refresh_baseline, time_limit_s=args.time_limit)
    before_plan = MovePlan.model_validate(before["plan"]) if before.get("plan") else None
    broken_before = constrained.violations(before_plan) if before_plan is not None else []
    print(f"Before (scalable solver, no instruction): checker {'PASS' if before['checker_passed'] else 'FAIL'}, "
          f"{_fmt_counts(before['counts'])}")
    print(f"  placements breaking the instruction: {len(broken_before)}"
          + (f" ({'; '.join(broken_before)})" if broken_before else ""))

    print(f"Replanning under the instruction (scalable solver, D79 config, limit {args.time_limit:g}s)...")
    after_run = replan(constrained, time_limit_s=args.time_limit)
    after = _summary(after_run, constrained)
    if after_run.precheck:
        print(f"Cannot honour: {after_run.precheck}. No plan shown; the planner must decide.")
    else:
        verdict = "PASS" if after["checker_passed"] else "FAIL"
        print(f"After: status {after['status']}, full checker {verdict}, shippable {after['shippable']}, "
              f"{_fmt_counts(after['counts'])} [{after['wall_s']}s]")
        print(f"  placements breaking the instruction: {len(after_run.constraint_violations)}"
              + (f" ({'; '.join(after_run.constraint_violations)})" if after_run.constraint_violations else ""))
        if after_run.shown:  # placements only for a checked, instruction-honouring plan (rev-v4-a P2)
            print(f"  loads: {', '.join(f'{b}->{s}' for b, s in after['placements'])}")
            print("Instruction honoured and plan checked: shown as a proposal for the carrier's approval.")
        else:
            print("Not shown: the plan must both pass the full checker and honour the instruction.")
    if not after_run.shown:
        after = {k: v for k, v in after.items() if k != "placements"}
    out.update(before={k: v for k, v in before.items() if k != "plan"}, broken_before=broken_before,
               after={k: v for k, v in after.items() if k != "plan"},
               broken_after=after_run.constraint_violations, shown=after_run.shown)
    _save(args.save, out)
    return 0


def _save(path: str | None, out: dict) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    sys.exit(main())
