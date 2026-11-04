"""C2-on construction probe (plan 4.4): try ``global_screen: 1`` on one random_yard instance of tiny/small/full.

    UV_CACHE_DIR=$PWD/.uv-cache nice uv run python experiments/feasibility/c2_probe.py --tag start \
        --out experiments/feasibility/c2_probe-start.json [--rollouts 3]

Records construction time, ``diagnostics['global_screen']`` and the root dead end. If C2 builds and ``--rollouts`` > 0,
also runs that many random rollouts + 1 greedy rollout per instance with latency timing (see measure.py).
"""
from __future__ import annotations

import argparse, json, os, random, sys, time
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"

from stowage.actions import get_action_space  # noqa: E402
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine  # noqa: E402
from stowage.io import load_instance  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INSTANCES = {r: ROOT / f"experiments/instances-v2/random_yard/{r}/seed-{s}.json"
             for r, s in (("tiny", "00000000"), ("small", "00100000"), ("full", "00300000"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="start")
    ap.add_argument("--rungs", nargs="+", default=list(INSTANCES))
    ap.add_argument("--rollouts", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).parent))
    space = get_action_space()
    out = dict(tag=a.tag, date=time.strftime("%Y-%m-%d %H:%M %Z"), rungs={})
    for rung in a.rungs:
        p = INSTANCES[rung]
        inst = load_instance(p)
        t0 = time.perf_counter()
        eng = make_feasibility_engine(inst, space, config=FeasibilityConfig(extra={"global_screen": 1}))
        ct = time.perf_counter() - t0
        de = eng.dead_end()
        row = dict(instance=str(p.relative_to(ROOT)), n_load=len(inst.load_list), construct_s=ct,
                   global_screen=eng.diagnostics["global_screen"],
                   root_dead_end=None if de is None else dict(reason=de.reason, rule_ids=list(de.rule_ids)))
        print(rung, json.dumps(row)[:600], flush=True)
        if de is None and a.rollouts:
            from measure import run_policy_block  # noqa: E402
            row["rollouts"] = run_policy_block(inst, eng, rung, a.rollouts, greedy=True, timing=True)
        out["rungs"][rung] = row
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
