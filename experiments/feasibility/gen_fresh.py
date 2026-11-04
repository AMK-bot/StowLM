"""Fresh train/val instances for the 4.4 completion-strength study (27 Oct 2026).

Generators: 3.5 random-yard (``stowage.generate.random_yard.generate_random_instance``) and 4.3 simulated-yard
(``stowage.generate.instances.generate_simulated_instance``), at their current versions (recorded per file). Only
specs whose frozen split (``eval.splits.split_of``) is train or val are kept; held-out axes (feeder_1700 medium,
us_west_coast, noise 0.8) are never drawn. Seeds start at 70_000_000 to stay clear of the canonical campaign.

Usage: ``nice uv run python experiments/feasibility/gen_fresh.py --rungs tiny small --count 30 [--shard i/n]``
Writes ``experiments/feasibility/fresh/<generator>/<rung>/seed-<n>.json`` (resumable: existing files are skipped)
and appends one JSON line per attempt to ``fresh/log.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "experiments/feasibility/fresh"
VESSEL = {"tiny": "feeder_500", "small": "feeder_500", "medium": "feeder_800", "full": "feeder_800"}
PORTS = ("laem_chabang_like", "singapore_like")
BASE = 70_000_000


def spec_for(gen, rung, seed, port):
    from eval.splits import InstanceSpec
    return InstanceSpec(generator=gen, rung=rung, vessel=VESSEL[rung], slice=rung, port=port,
                        noise=0.2 if gen == "simulated" else None,
                        blocking_ratio=0.3 if gen == "random_yard" else None, seed=seed)


def make(gen, rung, seed, port):
    if gen == "random_yard":
        from stowage.generate.random_yard import generate_random_instance
        return generate_random_instance(rung, seed=seed, blocking_ratio=0.3, vessel_name=VESSEL[rung],
                                        port_name=port)
    from stowage.generate.instances import generate_simulated_instance
    return generate_simulated_instance(rung, seed=seed, noise=0.2, vessel_name=VESSEL[rung], port_name=port)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rungs", nargs="+", default=["tiny", "small", "medium", "full"])
    ap.add_argument("--generators", nargs="+", default=["random_yard", "simulated"])
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--max-seeds", type=int, default=200, help="candidate seeds tried per rung/generator")
    args = ap.parse_args()
    from eval.splits import split_of
    from stowage.io import save_instance
    i, n = map(int, args.shard.split("/"))
    for gen in args.generators:
        for rung in args.rungs:
            d = OUT / gen / rung
            d.mkdir(parents=True, exist_ok=True)
            have = len(list(d.glob("seed-*.json")))
            k = -1
            for off in range(args.max_seeds):
                if have >= args.count:
                    break
                seed = BASE + {"tiny": 0, "small": 1, "medium": 2, "full": 3}[rung] * 100_000 + off
                port = PORTS[off % len(PORTS)]
                spec = spec_for(gen, rung, seed, port)
                if split_of(spec) == "test":
                    continue
                k += 1
                if k % n != i:
                    continue
                path = d / f"seed-{seed}.json"
                if path.exists():
                    continue
                t = time.perf_counter()
                rec = dict(generator=gen, rung=rung, seed=seed, port=port, split=split_of(spec))
                try:
                    inst = make(gen, rung, seed, port)
                    save_instance(inst, path)
                    rec.update(ok=True, boxes=len(inst.load_list), dg=sum(b.dg is not None for b in inst.containers
                                                                        if b.id in set(inst.load_list)))
                    have += 1
                except Exception as exc:  # noqa: BLE001 - record generator failures, keep going
                    rec.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
                rec["s"] = round(time.perf_counter() - t, 2)
                with (OUT / "log.jsonl").open("a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                print(json.dumps(rec), flush=True)


if __name__ == "__main__":
    main()
