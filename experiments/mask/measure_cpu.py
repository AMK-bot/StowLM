"""Task 5.1, CPU part: uniform random sampling under the feasibility mask (no model).

At every token the next token is drawn uniformly from the mask's allowed set. Each sample ends complete (end of
plan written) or with a reported dead end. Completed plans are replayed (2.3 ``apply_plan``) and checked for hard
2.4 static violations; with ``--check`` they also go through ``finalize`` (4.2 ballast) and the full checker 4.1.
Mask timing separates the first ``allowed_token_ids`` call on a new engine state (legal actions + head trie +
lookahead) from the within-action calls (trie walk), and reports the engine's own ``legal_actions`` time so the
mask's overhead on top of the engine can be read off.

APPROXIMATION (diagnostic): the engine runs with C2 off (``global_screen: 0``): the 3.7 linear model currently
can't be built for the v2 KCS hulls (construction takes minutes and then fails closed at depth 0).

Usage: uv run python experiments/mask/measure_cpu.py --rung tiny --out experiments/mask/cpu-tiny.jsonl
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import resource
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    "v2-random": "experiments/instances-v2/random_yard/{rung}",
    "v2-sim": "experiments/instances-v2/simulated_yard/{rung}",
    "sim": "experiments/instances-sim/{rung}",
}
NO_C2 = {"global_screen": 0}


def instance_paths(source: str, rung: str, n: int) -> list[str]:
    if source == "fresh":  # current 3.5 generator, seeds 1..n (generated in the worker)
        return [f"fresh:{rung}:{seed}" for seed in range(1, n + 1)]
    return sorted(glob.glob(str(ROOT / SOURCES[source].format(rung=rung) / "seed-*[0-9].json")))[:n]


def _pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def run_one(job: dict) -> dict:
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskConfig, MaskedDecoder
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance

    if job["path"].startswith("fresh:"):
        from stowage.generate.random_yard import generate_random_instance

        _, rung, seed = job["path"].split(":")
        inst = generate_random_instance(rung, seed=int(seed))
    else:
        inst = load_instance(job["path"])
    t0 = time.perf_counter()
    root = make_feasibility_engine(inst, get_action_space("boxslot"), config=FeasibilityConfig(extra=NO_C2))
    construct = time.perf_counter() - t0
    codec = ActionCodec(load_tokenizer(), [c.id for c in inst.containers])
    eng = root.copy()
    dec = MaskedDecoder(eng, codec, MaskConfig(lookahead=job["lookahead"], index_arrays=not job.get("action_path", False)))
    rng = random.Random(job["seed"])
    boundary, within, sizes, advance_ms = [], [], [], []
    t_start = time.perf_counter()
    timed_out = False
    while True:
        if time.perf_counter() - t_start > job["max_wall"]:
            timed_out = True
            break
        at_boundary = not dec._prefix
        t = time.perf_counter()
        allowed = dec.allowed_token_ids()
        dt = (time.perf_counter() - t) * 1e3
        (boundary if at_boundary else within).append(dt)
        if at_boundary:
            sizes.append(dec.n_legal)
        if not allowed:
            break
        t = time.perf_counter()
        dec.advance(allowed[rng.randrange(len(allowed))])
        advance_ms.append((time.perf_counter() - t) * 1e3)
    wall = time.perf_counter() - t_start
    # engine legal_actions time: the chosen chain (copies inherit counters) + lookahead successors not taken
    chain = {id(v.engine) for v in dec._stack}
    legal_s = dec.engine.stats().legal_time_s + sum(
        e.stats().legal_time_s - v.engine.stats().legal_time_s
        for v in dec._stack for e in v.succ.values() if id(e) not in chain)
    out = dict(job, instance=inst.id, n_load=len(inst.load_list), construct_s=round(construct, 3),
               status="timeout" if timed_out else dec.status, depth=dec.engine.depth, tokens=len(dec.tokens),
               dead_end=None if dec.dead_end is None else dict(depth=dec.dead_end.depth, reason=dec.dead_end.reason,
                                                               rule_ids=list(dec.dead_end.rule_ids)),
               lookahead_evals=dec.lookahead_evals, wall_s=round(wall, 3),
               mask_boundary_ms=dict(n=len(boundary), p50=_pct(boundary, .5), p95=_pct(boundary, .95),
                                     max=max(boundary) if boundary else None, total=sum(boundary)),
               mask_within_ms=dict(n=len(within), p50=_pct(within, .5), p95=_pct(within, .95),
                                   total=sum(within)),
               advance_ms=dict(p50=_pct(advance_ms, .5), p95=_pct(advance_ms, .95), total=sum(advance_ms)),
               legal_sizes=dict(max=max(sizes) if sizes else 0, p50=_pct(sizes, .5)),
               engine_legal_s=round(legal_s, 4),
               mask_total_s=round((sum(boundary) + sum(within) + sum(advance_ms)) / 1e3, 4))
    if dec.status == "complete":
        from stowage.checker.rules import check_static_rules
        from stowage.moves.engine import apply_plan

        assert codec.decode_plan(dec.tokens) == dec.actions
        plan = dec.engine.to_plan()
        res = apply_plan(inst, plan)
        out["apply_legal"] = bool(res.legal)
        if res.legal:
            rep = check_static_rules(inst, res.final_state.ship, call_index=inst.call_index)
            out["static_hard"] = sorted({v.rule_id for v in rep.violations if v.severity == "hard"})
        out["n_moves"] = len(plan.moves)
        if job["check"]:
            from stowage.checker.full import check
            from stowage.model import PlannerResult
            from stowage.planner import finalize

            t = time.perf_counter()
            result = finalize(inst, PlannerResult(status="ok", plan=plan, planner="stowlm.mask-random",
                                                  planner_version="mask@1.0", seed=job["seed"]))
            out["finalize_s"] = round(time.perf_counter() - t, 2)
            out["finalize_status"] = result.status
            out["ballast_status"] = result.metrics.get("ballast_status")
            t = time.perf_counter()
            rep = check(inst, result.plan)
            out["check_s"] = round(time.perf_counter() - t, 2)
            out["check_passed"] = bool(rep.passed)
            out["check_hard"] = sorted({v.rule_id for p in rep.parts.values() for v in p.violations
                                        if v.severity == "hard"})
    out["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1)
    return out


def census(rung: str, sources: list[str]) -> dict:
    """How many instances already dead-end at depth 0 (engine, C2 off)."""
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance

    out = {}
    for src in sources:
        dead = ok = 0
        rules: dict[str, int] = {}
        for p in instance_paths(src, rung, 10**6):
            e = make_feasibility_engine(load_instance(p), get_action_space("boxslot"),
                                        config=FeasibilityConfig(extra=NO_C2))
            e.legal_actions()
            d = e.dead_end()
            if d is None:
                ok += 1
            else:
                dead += 1
                for r in d.rule_ids:
                    rules[r] = rules.get(r, 0) + 1
        out[src] = dict(root_ok=ok, root_dead_end=dead, rule_ids=rules)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rung", required=True)
    ap.add_argument("--sources", nargs="+", default=["v2-random", "v2-sim"])
    ap.add_argument("--instances", type=int, default=50, help="per source")
    ap.add_argument("--samples", type=int, default=1, help="per instance and lookahead")
    ap.add_argument("--lookahead", type=int, nargs="+", default=[8, 0])
    ap.add_argument("--check", action="store_true", help="finalize + full checker on completed plans")
    ap.add_argument("--check-lookahead", type=int, default=8, help="only check plans of this lookahead")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--action-path", action="store_true", help="build the mask from legal_actions() (old path)")
    ap.add_argument("--max-wall", type=float, default=120.0, help="per-sample cap, s (status 'timeout')")
    ap.add_argument("--census", nargs="*", default=None, help="also count root dead ends for these sources")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    jobs = []
    for src in args.sources:
        for path in instance_paths(src, args.rung, args.instances):
            for la in args.lookahead:
                for s in range(args.samples):
                    jobs.append(dict(rung=args.rung, source=src, path=path if path.startswith("fresh:") else str(Path(path).relative_to(ROOT)),
                                     action_path=args.action_path,
                                     lookahead=la, seed=1000 * s + 17, max_wall=args.max_wall,
                                     check=args.check and la == args.check_lookahead))
    t0 = time.perf_counter()
    out = Path(args.out)
    with out.open("w") as f:
        if args.census is not None:
            f.write(json.dumps(dict(census=census(args.rung, args.census or args.sources), rung=args.rung)) + "\n")
        if args.workers > 1:
            with ProcessPoolExecutor(args.workers) as pool:
                for r in pool.map(run_one, jobs, chunksize=1):
                    f.write(json.dumps(r) + "\n")
                    f.flush()
        else:
            for job in jobs:
                f.write(json.dumps(run_one(job)) + "\n")
                f.flush()
    print(f"{len(jobs)} samples in {time.perf_counter() - t0:.1f} s -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
