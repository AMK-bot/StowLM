"""Bounded reproduction of v3 depth-0 rejection; never bypasses screens."""
import json
import argparse
from pathlib import Path
from time import perf_counter

from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.generate import random_yard
from stowage.generate.instances import GenerationReport
from stowage.generate import instances
from stowage.generate.instances_v2_random import generate_random_v2
from stowage.io import save_instance
from stowage.vessel.fleet_conditions import slice_condition

root = Path(__file__).parent
parser = argparse.ArgumentParser()
parser.add_argument("--generator", choices=["random_yard", "simulated"], default="random_yard")
parser.add_argument("--size", default="tiny")
parser.add_argument("--seed", type=int, default=100000000)
parser.add_argument("--boxes", type=int)
parser.add_argument("--attempts", type=int, default=20)
parser.add_argument("--prefix", default="v3-tiny")
args = parser.parse_args()
records = []
start = perf_counter()
captured = False


def completion(instance):
    global captured
    engine = make_feasibility_engine(instance, get_action_space("boxslot"),
        config=FeasibilityConfig(lookahead=0, extra={"global_screen": 1}))
    result = engine.completion_check()
    c1 = tuple(sorted(k for k, ok in result.items() if k.startswith("c1") and not ok))
    failures = c1 if c1 or len(instance.load_list) == 1 else (() if result["c2"] else ("c2",))
    if len(instance.load_list) > 1:
        fixed, free, remaining = engine._global_state(engine._cur.ex)
        row = dict(check=result, diagnostics=engine.diagnostics,
                   cells=len(instance.vessel.cells), placements=len(instance.onboard.placements),
                   free_cells=int(free.sum()), fixed_cell_mass_kg=float(fixed.sum()),
                   remaining_mass_kg=remaining,
                   targets=len(instance.load_list),
                   background=slice_condition(instance.vessel))
        records.append(row)
        if not c1 and not captured:
            save_instance(instance, root / f"{args.prefix}-c2-rejected.json")
            captured = True
            g = engine._global
            if g is not None:
                import numpy as np
                np.savez(root / f"{args.prefix}-c2-lp.npz", A=g.A, At=g.At, const=g.const,
                         lo=g.lo, hi=g.hi, tank_lo=g.tank_lo, tank_hi=g.tank_hi,
                         u0=g.u0, names=np.array(g.names),
                         remaining_mass=sum(b.gross_mass_kg for b in instance.containers
                                            if b.id in instance.load_list))
    return failures


module = random_yard if args.generator == "random_yard" else instances
original = module.completion_failures
module.completion_failures = completion
audit = GenerationReport()
try:
    producer = generate_random_v2 if args.generator == "random_yard" else instances.generate_simulated_instance
    producer(args.size, seed=args.seed, noise=0.0, n_boxes=args.boxes,
        vessel_name="feeder_500", port_name="laem_chabang_like",
        report=audit, max_attempts=args.attempts, tiny_dg_enabled=True)
except ValueError as exc:
    print(str(exc), flush=True)
finally:
    module.completion_failures = original
    from dataclasses import asdict
    output = dict(seconds=perf_counter()-start, diagnostic_c2_enabled=True,
                  config=vars(args), report=asdict(audit), candidates=records)
    (root / f"{args.prefix}-diagnostic.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(dict(seconds=output["seconds"], candidates=len(records),
                          completion_rejections=audit.completion_rejections)), flush=True)
