# Tiny chain step 4: masked eval on the tiny VAL split.
# Phase 1 (GPU): StowLMPlanner (5.1 mask over the 4.4 engine, greedy) plans every val instance, with the SFT adapter
#   and, for contrast, without it (stowlm.base). The engine runs with C2 off (stub, as in gold.py), so every result
#   is stub-tainted.
# Phase 2 (CPU, workers): planner.finalize (4.2 ballast) + checker.check on every complete plan; moves via
#   moves.count_moves, compared with the stand-in gold plan where one exists.
# Usage: uv run python experiments/tiny-chain/eval.py --adapter experiments/tiny-chain/adapters/quick [--workers 4]
#        [--instances DIR --groups G.. --split val --gold-dir DIR --out DIR --no-stub]   (see NEXT.md)
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).parent))
import gold  # noqa: E402
from gold import STUB_REASON, c2_off_engine, tiny_instances  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# set by --out / --gold-dir / --no-stub; environment variables so the judge workers (spawned) see them
OUT = Path(os.environ.get("TINY_CHAIN_EVAL_OUT", ROOT / "experiments/tiny-chain/eval"))
GOLD_DIR = Path(os.environ.get("TINY_CHAIN_EVAL_GOLD", ROOT / "experiments/tiny-chain/gold/standin"))
NO_STUB = os.environ.get("TINY_CHAIN_NO_STUB") == "1"


def generate(adapter: str | None, instances: list[tuple[str, str]]) -> list[dict]:
    import stowage.interfaces  # noqa: F401
    from llm.stowlm.planner import StowLMPlanner
    from stowage.frozen import stub_override
    from stowage.io import load_instance, save_result

    import contextlib

    planner = StowLMPlanner(adapter_path=adapter)
    rows = []
    ctx = contextlib.nullcontext() if NO_STUB else stub_override("feasibility.make_engine", c2_off_engine,
                                                                  reason=STUB_REASON)
    with ctx:
        for split, path in instances:
            inst = load_instance(path)
            t = time.perf_counter()
            res = planner.plan(inst, seed=0)
            f = save_result(res, OUT / planner.name / f"{inst.id}.result.json", allow_stubs=not NO_STUB)
            rows.append({"instance": inst.id, "path": os.path.relpath(path, ROOT), "planner": planner.name,
                         "diagnostic": True, "shippable": False,
                         "status": res.status, "message": (res.message or "")[:200], "plan_s": round(time.perf_counter() - t, 2),
                         "result_file": str(f.relative_to(ROOT)), "split": split,
                         **{k: res.metrics.get(k) for k in ("tokens_in", "tokens_out", "n_actions", "masked_logprob",
                                                            "gen_tok_s", "gen_prefill_s", "gen_host_mask_s")}})
            print(rows[-1], flush=True)
    return rows


def judge(row: dict) -> dict:
    import stowage.interfaces  # noqa: F401
    from stowage.checker import check
    from stowage.io import load_instance, load_plan, load_result
    from stowage.moves import count_moves
    from stowage.planner import finalize

    inst, res = load_instance(ROOT / row["path"]), load_result(ROOT / row["result_file"])
    if res.status != "ok" or res.plan is None:
        return row
    c = count_moves(inst, res.plan)
    row.update(lifts_now=c.total_lifts_now, weighted_nonproductive=c.weighted_nonproductive)
    gold = GOLD_DIR / row["split"] / f"{inst.id}.plan.json"
    if gold.exists():
        g = count_moves(inst, load_plan(gold))
        row.update(gold_weighted=g.weighted_nonproductive, gap_vs_gold=c.weighted_nonproductive - g.weighted_nonproductive)
    t = time.perf_counter()
    fin = finalize(inst, res, time_limit_s=30.0)
    row.update(finalize_status=fin.status, ballast_status=fin.metrics.get("ballast_status"),
               finalize_s=round(time.perf_counter() - t, 1))
    if fin.plan is not None and fin.plan.ballast is not None:
        rep = check(inst, fin.plan)
        row.update(checker_passed=rep.passed, violations=sorted({v.rule_id for v in rep.violations if v.severity == "hard"}))
    else:
        row.update(checker_passed=False, violations=["no ballast: " + (fin.message or "")[:150]])
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-base", action="store_true")
    ap.add_argument("--instances", help="campaign folder with manifest.json (default experiments/instances-v2)")
    ap.add_argument("--groups", nargs="+", help="manifest groups (default random_yard/tiny simulated_yard/tiny)")
    ap.add_argument("--split", default="val")
    ap.add_argument("--gold-dir", help="gold plans to compare moves with (<dir>/<split>/<id>.plan.json)")
    ap.add_argument("--out", help="output folder (default experiments/tiny-chain/eval)")
    ap.add_argument("--no-stub", action="store_true", help="real engine (C2 on); outputs remain diagnostic, never shippable")
    args = ap.parse_args()
    global OUT, GOLD_DIR, NO_STUB
    gold.configure(args.instances, args.groups)
    if args.out:
        os.environ["TINY_CHAIN_EVAL_OUT"] = str((ROOT / args.out).resolve())
        OUT = Path(os.environ["TINY_CHAIN_EVAL_OUT"])
    if args.gold_dir:
        os.environ["TINY_CHAIN_EVAL_GOLD"] = str((ROOT / args.gold_dir).resolve())
        GOLD_DIR = Path(os.environ["TINY_CHAIN_EVAL_GOLD"])
    if args.no_stub:
        os.environ["TINY_CHAIN_NO_STUB"] = "1"
        NO_STUB = True
    paths = tiny_instances([args.split])
    OUT.mkdir(parents=True, exist_ok=True)
    rows = generate(str(ROOT / args.adapter), paths)
    if not args.no_base:
        rows += generate(None, paths)
    import gc

    import mlx.core as mx

    gc.collect()
    mx.clear_cache()
    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(judge, rows))
    (OUT / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = {}
    for name in sorted({r["planner"] for r in rows}):
        rs = [r for r in rows if r["planner"] == name]
        ok = [r for r in rs if r["status"] == "ok"]
        summary[name] = {
            "diagnostic": True, "shippable": False,
            "instances": len(rs), "complete": len(ok), "dead_end": sum(r["status"] == "dead_end" for r in rs),
            "checker_passed": sum(bool(r.get("checker_passed")) for r in rs),
            "mean_plan_s": round(sum(r["plan_s"] for r in rs) / len(rs), 2),
            "mean_gen_tok_s": round(sum(r["gen_tok_s"] or 0 for r in ok) / max(1, len(ok)), 1),
            "weighted_nonproductive": [r.get("weighted_nonproductive") for r in rs],
            "gap_vs_gold": [r.get("gap_vs_gold") for r in rs],
            "stub_tainted": not NO_STUB,
        }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
