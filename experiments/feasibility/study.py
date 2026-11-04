"""Completion-strength study (4.4, 27 Oct 2026): before/after completion rates, latency, soundness checks.

Engines: ``--engine current`` (``stowage.feasibility``) or ``--engine v21`` (frozen copy of v2.1 in
``experiments/feasibility/baseline_v21``). Instances: ``experiments/feasibility/fresh/<generator>/<rung>/*.json``
(``gen_fresh.py``: train/val specs only). C2 off unless ``--c2``.

Per instance: ``--random k`` uniform-random rollouts and one greedy rollout (no forced restow first, then heaviest
box, then lowest tier, then canonical order). Every cold ``legal_actions`` (first call on a new state) and every
``push`` is timed. Every completed plan is replayed (``apply_plan``) and checked with 2.4 ``check_static_rules``
(hard violations counted). Output: one JSON per run.

Usage: ``nice uv run python experiments/feasibility/study.py --engine current --rungs small --random 5 --out f.json``
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments/feasibility"))

from stowage.actions import get_action_space  # noqa: E402
from stowage.actions.space import Place  # noqa: E402
from stowage.checker.rules import check_static_rules  # noqa: E402
from stowage.conventions import parse_slot  # noqa: E402
from stowage.feasibility import FeasibilityConfig  # noqa: E402
from stowage.io import load_instance  # noqa: E402
from stowage.moves import apply_plan  # noqa: E402

SPACE = get_action_space()


def factory(name, c2):
    cfg = FeasibilityConfig(extra={} if c2 else {"global_screen": 0})
    if name == "v21":
        from baseline_v21._engine import IncrementalEngine
        return lambda inst: IncrementalEngine(inst, SPACE, cfg)
    from stowage.feasibility import make_feasibility_engine
    return lambda inst: make_feasibility_engine(inst, SPACE, config=cfg)


def greedy_key(eng, mass):
    def key(a):
        if not isinstance(a, Place):
            return (0, 0, 0, a.box)
        return (int(eng.forces_restow(a)), -mass[a.box], parse_slot(a.slot).tier, a.box, a.slot)
    return key


def rollout(eng, policy, rng, mass, lat, push_lat, max_depth):
    while not eng.is_complete() and eng.depth < max_depth:
        t = time.perf_counter()
        acts = eng.legal_actions()
        lat.append(time.perf_counter() - t)
        if not acts:
            d = eng.dead_end()
            return "dead", eng.depth, (d.reason if d else ""), tuple(d.rule_ids) if d else ()
        if policy == "greedy":
            cheap = [a for a in acts if not (isinstance(a, Place) and eng.forces_restow(a))] or acts
            a = min(cheap, key=greedy_key(eng, mass))
        else:
            a = rng.choice(acts)
        t = time.perf_counter()
        eng.push(a)
        push_lat.append(time.perf_counter() - t)
    if not eng.is_complete():
        return "capped", eng.depth, "", ()
    return "complete", eng.depth, "", ()


def verify(inst, eng):
    res = apply_plan(inst, eng.to_plan())
    hard = [v.rule_id for v in check_static_rules(inst, res.final_state.ship, call_index=inst.call_index).violations
            if v.severity == "hard"]
    return bool(res.legal), hard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="current", choices=["current", "v21"])
    ap.add_argument("--rungs", nargs="+", default=["tiny", "small", "medium", "full"])
    ap.add_argument("--generators", nargs="+", default=["random_yard", "simulated"])
    ap.add_argument("--per-rung", type=int, default=30)
    ap.add_argument("--random", type=int, default=5)
    ap.add_argument("--c2", action="store_true")
    ap.add_argument("--max-wall", type=float, default=900.0, help="per rung × generator, s")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    make = factory(args.engine, args.c2)
    out = dict(engine=args.engine, c2=args.c2, argv=sys.argv[1:], date=time.strftime("%Y-%m-%d %H:%M %Z"),
               blocks={})
    for gen in args.generators:
        for rung in args.rungs:
            paths = sorted((ROOT / "experiments/feasibility/fresh" / gen / rung).glob("seed-*.json"))[:args.per_rung]
            blk = dict(instances=0, root_dead=0, construct_s=[], random=Counter(), greedy=Counter(),
                       dead_reasons=Counter(), verify_fail=[], lat=[], push=[], depth_dead=[], n_random=0)
            t_blk = time.perf_counter()
            for path in paths:
                if time.perf_counter() - t_blk > args.max_wall:
                    blk["wall_capped"] = True
                    break
                inst = load_instance(path)
                mass = {b.id: b.gross_mass_kg for b in inst.containers}
                t = time.perf_counter()
                root = make(inst)
                blk["construct_s"].append(time.perf_counter() - t)
                blk["instances"] += 1
                if not root.legal_actions() and not root.is_complete():
                    blk["root_dead"] += 1
                    blk["dead_reasons"][f"root: {root.dead_end().reason[:120]}"] += 1
                    continue
                maxd = 4 * len(inst.load_list) + 20
                for policy, k in (("greedy", 1), ("random", args.random)):
                    for r in range(k):
                        eng = root.copy()
                        status, depth, reason, ids = rollout(eng, policy, random.Random(r), mass, blk["lat"],
                                                             blk["push"], maxd)
                        blk[policy][status] += 1
                        if status == "dead":
                            blk["depth_dead"].append(depth / max(1, len(inst.load_list)))
                            blk["dead_reasons"][reason.split("(")[-1][:80]] += 1
                        elif status == "complete":
                            legal, hard = verify(inst, eng)
                            if not legal or hard:
                                blk["verify_fail"].append(dict(path=str(path), policy=policy, seed=r, legal=legal,
                                                               hard=hard[:5]))
            lat = np.array(blk.pop("lat")) * 1e3
            push = np.array(blk.pop("push")) * 1e3
            blk["legal_ms"] = (np.percentile(lat, [50, 95, 99]).round(2).tolist() if len(lat) else None)
            blk["legal_max_ms"] = float(lat.max().round(1)) if len(lat) else None
            blk["push_ms"] = (np.percentile(push, [50, 95, 99]).round(3).tolist() if len(push) else None)
            blk["push_max_ms"] = float(push.max().round(1)) if len(push) else None
            blk["n_states"] = int(len(lat))
            blk["construct_s"] = [round(x, 3) for x in blk["construct_s"]]
            blk["mean_dead_depth_frac"] = round(float(np.mean(blk.pop("depth_dead"))), 3) if blk["random"]["dead"] \
                or blk["greedy"]["dead"] else None
            blk["wall_s"] = round(time.perf_counter() - t_blk, 1)
            for k in ("random", "greedy", "dead_reasons"):
                blk[k] = dict(blk[k])
            out["blocks"][f"{gen}/{rung}"] = blk
            print(json.dumps({f"{gen}/{rung}": {k: blk[k] for k in ("instances", "root_dead", "random", "greedy",
                                                                    "legal_ms", "push_ms", "wall_s")}}), flush=True)
            Path(args.out).write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
