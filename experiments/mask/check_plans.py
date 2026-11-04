"""Check the plans the base model wrote under the mask (gpu-plans.jsonl): decode, replay through the engine and the
2.3 move engine, 2.4 static hard rules; with ``--full N`` also finalize (4.2 ballast) + full checker 4.1 on the
first N completed plans. CPU only. Usage: uv run python experiments/mask/check_plans.py [--full 8]"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", default=str(ROOT / "experiments/mask/gpu-plans.jsonl"))
    ap.add_argument("--full", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "experiments/mask/gpu-plans-check.json"))
    args = ap.parse_args()
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import allowed_sets
    from stowage.actions import get_action_space
    from stowage.checker.full import check
    from stowage.checker.rules import check_static_rules
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from stowage.moves.engine import apply_plan
    from stowage.planner import finalize

    tok = load_tokenizer()
    rows = [json.loads(x) for x in Path(args.plans).read_text().splitlines()]
    cache: dict = {}
    out, full_done = [], 0
    for r in rows:
        if r["path"] not in cache:
            if r["path"].startswith("fresh:"):
                from stowage.generate.random_yard import generate_random_instance

                _, rung, seed = r["path"].split(":")
                inst = generate_random_instance(rung, seed=int(seed))
            else:
                inst = load_instance(ROOT / r["path"])
            eng = make_feasibility_engine(inst, get_action_space("boxslot"),
                                          config=FeasibilityConfig(extra={"global_screen": 0}))
            cache[r["path"]] = (inst, eng, ActionCodec(tok, [c.id for c in inst.containers]))
        inst, eng, codec = cache[r["path"]]
        rec = dict(rung=r["rung"], instance=r["instance"], sample=r["sample"], status=r["status"])
        sets = allowed_sets(eng, codec, r["tokens"])  # raises if any token was outside the mask
        rec["all_tokens_allowed"] = all(t in s for t, s in zip(r["tokens"], sets))
        if r["status"] == "complete":
            actions = codec.decode_plan(r["tokens"])
            e = eng.copy()
            for a in actions:
                e.push(a)
            plan = e.to_plan()
            res = apply_plan(inst, plan)
            rec["apply_legal"] = bool(res.legal)
            rep = check_static_rules(inst, res.final_state.ship, call_index=inst.call_index)
            rec["static_hard"] = sorted({v.rule_id for v in rep.violations if v.severity == "hard"})
            if full_done < args.full:
                full_done += 1
                t = time.perf_counter()
                result = finalize(inst, PlannerResult(status="ok", plan=plan, planner="stowlm.base-masked",
                                                      planner_version="glimmer-30b-4bit", seed=7))
                report = check(inst, result.plan)
                rec.update(finalize_status=result.status, ballast_status=result.metrics.get("ballast_status"),
                           check_passed=bool(report.passed), check_s=round(time.perf_counter() - t, 1),
                           check_hard=sorted({v.rule_id for p in report.parts.values() for v in p.violations
                                              if v.severity == "hard"}))
        out.append(rec)
    summary: dict = {}
    for rec in out:
        s = summary.setdefault(rec["rung"], dict(plans=0, all_tokens_allowed=0, complete=0, replay_legal_static_ok=0,
                                                 full_checked=0, full_passed=0))
        s["plans"] += 1
        s["all_tokens_allowed"] += rec["all_tokens_allowed"]
        if rec["status"] == "complete":
            s["complete"] += 1
            s["replay_legal_static_ok"] += bool(rec["apply_legal"] and not rec["static_hard"])
            if "check_passed" in rec:
                s["full_checked"] += 1
                s["full_passed"] += rec["check_passed"]
    Path(args.out).write_text(json.dumps(dict(summary=summary, plans=out), indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
