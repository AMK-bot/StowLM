"""Supplemental replay of archived LP arrays; not production-mask qualification.

The input is a previously qualified production profile's saved rows and root
state. Running these arrays does not qualify the current production policy and
cannot establish mask equivalence. This isolates numerical LP costs while other
owners update production source fingerprints.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import time

import numpy as np

from bench import instrument, pins, runtime, sha, write

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=HERE.parent / "c2-production/final-profile-full-lp-inputs.npz")
    ap.add_argument("--output", type=Path, default=HERE / "bench-lp.json")
    ap.add_argument("--cells", nargs="+", type=int, default=list(range(0, 32, 4)))
    ap.add_argument("--random-queries", type=int, default=0,
                    help="Seeded singleton/pair queries with full, narrow and point windows.")
    args = ap.parse_args()
    from candidate import get_classes
    from stowage.io import load_instance
    inst = load_instance(HERE.parent / "fix-t4-measure/full-current.json")
    load_ids = set(inst.load_list)
    discrete_masses = np.array(sorted({c.gross_mass_kg for c in inst.containers if c.id in load_ids}))
    out = dict(kind="supplemental frozen LP replay; not production-mask qualification",
               input=str(args.input), input_sha256=sha(args.input), sources=pins(), variants={},
               environment=runtime(), candidate_status="quarantined, unshipped")
    counts = instrument()
    started = time.perf_counter()
    with np.load(args.input) as z:
        queries = []
        if args.random_queries:
            rng = np.random.default_rng(1704)
            free = np.flatnonzero(z["root_free"])
            for index in range(args.random_queries):
                cells = rng.choice(free, 1 + index % 2, replace=False).tolist()
                cap = min(float(z["root_R"]), float(z["u0"]) * len(cells))
                mode = (index // 2) % 4
                if mode == 0:
                    lo, hi = 0., cap
                elif mode == 1:
                    lo, hi = .5 * cap, .75 * cap
                elif mode == 2:
                    lo, hi = .9 * cap, cap
                else:
                    mass = float(rng.choice(discrete_masses[discrete_masses <= cap]))
                    lo = hi = mass
                queries.append((cells, lo, hi))
        else:
            queries = [([cell], 0., min(float(z["root_R"]), float(z["u0"]))) for cell in args.cells]
        out["seed"] = 1704
        out["discrete_masses"] = discrete_masses.tolist()
        for optimized in (0, 1):
            classes = get_classes(bool(optimized))
            GlobalScreen, UNKNOWN_INTERVAL = classes.GlobalScreen, classes.UNKNOWN_INTERVAL
            counts.clear()
            t0 = time.perf_counter()
            g = GlobalScreen(*[z[k] for k in ("A", "At", "const", "lo", "hi", "tank_lo", "tank_hi", "u0")])
            g.optimized = bool(optimized)
            ref = g.reference(z["root_fixed"], z["root_free"], float(z["root_R"]))
            rec = dict(center_s=time.perf_counter() - t0, center_counts=dict(counts), queries=[],
                       global_screen_module=GlobalScreen.__module__)
            out["variants"][str(optimized)] = rec
            for cells, lo, hi in queries:
                counts.clear()
                t0 = time.perf_counter()
                value = g.slot_interval(ref, cells, lo, hi)
                row = dict(cells=cells, window=[lo, hi], wall_s=time.perf_counter() - t0, counts=dict(counts))
                row["status"] = "infeasible" if value is None else "undecided" if value is UNKNOWN_INTERVAL else "feasible"
                if row["status"] == "feasible":
                    row["endpoints"] = [float(value[0][0]), float(value[1][0])]
                    row["discrete_allowed"] = ((discrete_masses >= value[0][0] - 1e-6)
                                                & (discrete_masses <= value[1][0] + 1e-6)).tolist()
                else:
                    row["discrete_allowed"] = [row["status"] == "undecided"] * len(discrete_masses)
                rec["queries"].append(row)
                write(args.output, out)
            rec["query_total_s"] = sum(q["wall_s"] for q in rec["queries"])
            rec["query_counts"] = {k: sum(q["counts"].get(k, 0) for q in rec["queries"])
                                   for k in ("native", "cold", "warm", "native_s", "cold_s")}
            print(optimized, rec["center_s"], rec["query_total_s"], rec["query_counts"], flush=True)
    comparisons = []
    for old, new in zip(out["variants"]["0"]["queries"], out["variants"]["1"]["queries"]):
        row = dict(cells=old["cells"], same_status=old["status"] == new["status"])
        row["same_discrete_allowed"] = old["discrete_allowed"] == new["discrete_allowed"]
        if "endpoints" in old and "endpoints" in new:
            row["endpoint_abs_differences"] = np.abs(np.array(old["endpoints"]) - new["endpoints"]).tolist()
        comparisons.append(row)
    out.update(comparisons=comparisons, sources_after=pins(), wall_s=time.perf_counter() - started,
               maxrss_bytes_macos=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    out["changed_sources"] = {k: v for k, v in out["sources_after"].items() if out["sources"].get(k) != v}
    write(args.output, out)
    assert all(c["same_status"] and c["same_discrete_allowed"] for c in comparisons), "Frozen LP equivalence failed"


if __name__ == "__main__":
    main()
