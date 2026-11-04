"""0.5 length probe: real tiny/small/medium token lengths under format v2 serialisation (switch OFF).

One random_yard instance per ship x rung, a constructor plan (ScalableSolver without LNS), serialised with
``sft_data.build_example`` (prompt + completion, the exact training sequence). Timing input only: plans are
unchecked here (APPROXIMATION: completion length of a constructor plan stands in for gold-plan length; both
load every box once, so the action count matches; yard-rehandle actions may differ slightly).
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

OUT = Path(__file__).resolve().parent / "probe"


def one(job):
    rung, ship, seed = job
    from llm.stowlm.sft_data import build_example
    from stowage.feasibility import FeasibilityConfig
    from stowage.generate.random_yard import generate_random_instance
    from stowage.io import save_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.solver.scalable import ScalableConfig, ScalableSolver
    assert not rule_freeze_v1_enabled()
    t = time.perf_counter()
    try:
        inst = generate_random_instance(rung, seed=seed, vessel_name=ship, port_name="laem_chabang_like",
                                        max_attempts=60)
        tg = time.perf_counter() - t
        res = ScalableSolver(ScalableConfig(max_neighborhoods=0, feasibility_config=FeasibilityConfig(
            extra={"global_screen": 0}))).plan(inst, seed=seed, time_limit_s=240)
        if res.plan is None:
            return {"rung": rung, "ship": ship, "error": f"solver {res.status}: {res.message}"}
        ex = build_example(inst, res.plan, split="train", allow_stubs=True)
        OUT.mkdir(parents=True, exist_ok=True)
        save_instance(inst, OUT / f"{rung}-{ship}.instance.json")
        (OUT / f"{rung}-{ship}.example.json").write_text(json.dumps(
            {"input_ids": ex.input_ids, "prompt_len": ex.prompt_len}))
        return {"rung": rung, "ship": ship, "boxes": len(inst.load_list), "n_actions": ex.n_actions,
                "prompt": ex.prompt_len, "completion": len(ex.input_ids) - ex.prompt_len,
                "total": len(ex.input_ids), "gen_s": round(tg, 1), "wall_s": round(time.perf_counter() - t, 1)}
    except Exception as e:
        return {"rung": rung, "ship": ship, "error": f"{type(e).__name__}: {e}"[:300]}


if __name__ == "__main__":
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    jobs = [(r, s, 29_500 + i) for i, (r, s) in enumerate(
        (r, s) for r in ("tiny", "small", "medium") for s in ("feeder_500", "feeder_800", "feeder_1700"))]
    rows = []
    with ProcessPoolExecutor(workers) as pool:
        for row in pool.map(one, jobs):
            print(json.dumps(row), flush=True)
            rows.append(row)
    (OUT / "lengths.json").write_text(json.dumps(rows, indent=1))
