"""Rebuild the four masked plans of experiments/mask/c2-on-current (deterministic), then for
background-only and each plan: unballasted B 2.3, symmetric-DB sweep, and the ballast finisher
at a generous budget, then full check of the finisher result. One thread."""
import json, random, sys, time
from pathlib import Path
from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import random_masked_plan
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance, MovePlan
from stowage.solver.ballast import finish_ballast
from stowage.checker import check
sys.path.insert(0, str(Path(__file__).parent))
from sweep import sweep, b23, evaluate, pairs



def main():
    ROOT = Path(__file__).resolve().parents[3]
    OUT = Path(__file__).with_name("plans.jsonl")
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 120
    tok = load_tokenizer()
    for name in ("random-tiny-1", "simulated-tiny-1"):
        inst = Instance.model_validate_json((ROOT / f"experiments/mask/c2-on-current/{name}.instance.json").read_text())
        plans = {"empty": MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[])}
        for seed in (17, 18):
            cfg = FeasibilityConfig()
            eng = make_feasibility_engine(inst, get_action_space("boxslot"), config=cfg)
            codec = ActionCodec(tok, [b.id for b in inst.containers])
            dec = random_masked_plan(eng, codec, random.Random(seed))
            if dec.status == "complete":
                plans[f"seed{seed}"] = dec.engine.to_plan()
        for label, plan in plans.items():
            t = time.time()
            base = b23(evaluate(inst, plan, {}))
            rows = sweep(inst, plan, ["DB-A", "DB-M", "DB-F"], [0, 1])
            ok = [r for r in rows if not r.get("rules")]
            fb = finish_ballast(inst, plan, time_limit_s=budget)
            full = None
            if fb.plan is not None:
                rep = check(inst, fb.plan)
                full = dict(passed=rep.passed, rules=sorted({v.rule_id for v in rep.violations}),
                            ballast_t={k: round(v / 1e3, 1) for k, v in fb.plan.ballast.tank_masses_kg.items() if v})
            placed = [m.model_dump(mode="json", exclude_none=True) for m in plan.moves if type(m).__name__ == "Load"]
            rec = dict(instance=name, plan=label, moves=len(plan.moves), placed=placed, unballasted=base,
                       sweep_stab_pass=[r["combo"] for r in ok], sweep_best=max(rows, key=lambda r: r.get("worst_ratio", 0)),
                       finisher=dict(status=fb.status, proof=fb.proof, wall_s=fb.wall_time_s, budget_s=budget), full=full,
                       wall_s=time.time() - t)
            with OUT.open("a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
            print(name, label, len(plan.moves), "unballasted", round(base["worst_ratio"], 4), base["worst"],
                  "sweep-ok", len(ok), "finisher", fb.status, round(fb.wall_time_s, 1), "full", full, flush=True)


if __name__ == '__main__':
    main()
