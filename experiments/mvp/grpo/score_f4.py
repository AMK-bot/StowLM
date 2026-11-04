# Score falsifier-4 rollouts with the production GRPO reward and measure the mixed-group share (D26 item 4).
#
# Each rollout goes through `modal_grpo.score_rollout` with the MVP engine (`mvp_engine`: D79 C2 off + D95 securing,
# the same mask the worker sampled under) and `mvp_reward_config()` (V4 finisher scoped to finalize, D88; then the
# full checker). Reward = fixed -10,000 penalty for any hard failure, otherwise minus the weighted moves (reward.py).
# A group is MIXED when the variance of its available (non-dropped) rewards is > 0: only then does GRPO's
# group-normalised advantage give a gradient (Shao et al. 2024 §4).
#
# RUN: OPENBLAS_NUM_THREADS=1 uv run python experiments/mvp/grpo/score_f4.py <f4-rollouts-*.json> [--workers 4]
import argparse
import json
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _score(job):
    name, tokens = job
    import stowage.interfaces  # noqa: F401
    from llm.rl import modal_grpo as grpo
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.sft_data import codec_for
    from stowage.model import Instance
    case = name.replace("-", "/", 4)  # train-tiny-<vessel>-<gen>-seed-N -> train/tiny/<vessel>/<gen>/seed-N
    instance = Instance.model_validate_json((ROOT / f"experiments/mvp/corpus/{case}.json").read_text())
    t = time.perf_counter()
    engine = grpo.mvp_engine(instance)
    codec = codec_for(instance, load_tokenizer())
    actual, _, outcome = grpo.score_rollout(instance, engine, codec, tokens, seed=0, config=grpo.mvp_reward_config())
    return {"name": name, "value": outcome.value, "passed": bool(outcome.passed), "reason": outcome.reason,
            "moves": outcome.weighted_nonproductive, "policy_tokens": len(actual),
            "score_s": round(time.perf_counter() - t, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rollouts", type=Path)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    run = json.loads(a.rollouts.read_text())
    steps = run["gpu"]["steps"]
    jobs = [(n, t) for s in steps for n, t in zip(s["names"], s["tokens"])]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(a.workers) as pool:
        scored = list(pool.map(_score, jobs))
    wall = time.perf_counter() - t0
    groups = {}
    for r in scored:
        groups.setdefault(r["name"], []).append(r)
    rows = []
    for name, rs in groups.items():
        vals = [r["value"] for r in rs if r["value"] is not None]
        var = statistics.pvariance(vals) if len(vals) >= 2 else 0.0
        rows.append({"name": name, "n": len(rs), "available": len(vals), "passed": sum(r["passed"] for r in rs),
                     "reasons": {k: sum(r["reason"] == k for r in rs) for k in sorted({r["reason"] for r in rs})},
                     "moves_passing": sorted(r["moves"] for r in rs if r["passed"]),
                     "reward_variance": var, "mixed": var > 0})
    n_mixed = sum(r["mixed"] for r in rows)
    per_step = []
    for k, s in enumerate(steps):
        ss = [r for r in scored if r["name"] in set(s["names"])]
        per_step.append({"step": k, "rollout_wall_s": s["wall_s"], "rollouts": len(ss),
                         "score_cpu_s_sum": round(sum(r["score_s"] for r in ss), 1),
                         "score_s_max": max(r["score_s"] for r in ss),
                         "mixed_groups": sum(r["mixed"] for r in rows if r["name"] in set(s["names"]))})
    summary = {"groups": len(rows), "mixed_groups": n_mixed, "mixed_share": n_mixed / len(rows),
               "bar": 0.30, "pass": n_mixed / len(rows) >= .30,
               "rollouts": len(scored), "passed": sum(r["passed"] for r in scored),
               "dropped": sum(r["value"] is None for r in scored),
               "reasons": {k: sum(r["reason"] == k for r in scored) for k in sorted({r["reason"] for r in scored})},
               "score_s_mean": round(statistics.mean(r["score_s"] for r in scored), 2),
               "score_s_max": max(r["score_s"] for r in scored),
               "score_wall_s": round(wall, 1), "workers": a.workers, "per_step": per_step, "groups_detail": rows}
    out = a.rollouts.with_name(a.rollouts.stem.replace("rollouts", "scored") + ".json")
    out.write_text(json.dumps({"summary": summary, "rollouts": scored}, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "groups_detail"}, indent=1))
    for r in rows:
        print(r["name"], r["passed"], "/", r["n"], r["moves_passing"], r["reasons"], "mixed" if r["mixed"] else "")
    print("wrote", out)


if __name__ == "__main__":
    main()
