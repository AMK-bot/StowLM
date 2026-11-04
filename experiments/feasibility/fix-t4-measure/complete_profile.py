"""Bounded 4.7 complete profile and repeated completion-check microbenchmark."""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import time

from measure import HERE, factory, pins, write


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("baseline", "after"), required=True)
    ap.add_argument("--seconds", type=float, default=3.)
    args = ap.parse_args()
    from stowage.io import load_instance
    from stowage.feasibility import FeasibilityConfig
    import stowage.solver.scalable as scalable
    import stowage.solver.scalable_oracle as oracle
    make = factory(args.phase)
    def adapter(instance, space, *, config=None):
        return make(instance, (config.extra.get("global_screen", 1) if config else 1) != 0)
    scalable.make_feasibility_engine = oracle.make_feasibility_engine = adapter
    out = {"phase": args.phase, "pins": pins(), "seconds": args.seconds, "rungs": {}}
    started = time.perf_counter()
    for rung in ("tiny", "small", "medium", "full"):
        inst = load_instance(HERE / (rung + "-current.json"))
        eng = make(inst, False)
        checks = []
        for _ in range(16):
            t = time.perf_counter()
            result = eng.completion_check()
            checks.append((time.perf_counter() - t) * 1000)
        row = {"completion_check_ms": checks, "checks": result}
        if rung in ("tiny", "small"):
            profile = cProfile.Profile()
            t = time.perf_counter()
            profile.enable()
            result = scalable.complete(inst, (), seed=1704, time_limit_s=args.seconds,
                config=scalable.ScalableConfig(feasibility_config=FeasibilityConfig(extra={"global_screen": 0}),
                                              max_neighborhoods=0))
            profile.disable()
            row.update(solver_status=result.status, solver_wall_s=time.perf_counter()-t)
            stream = io.StringIO()
            stats = pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative")
            stats.print_stats(40)
            (HERE / f"{args.phase}-complete-{rung}.profile.txt").write_text(stream.getvalue())
            row["profile_total_s"] = stats.total_tt
            row["c1_profile"] = {str(k): {"primitive_calls": v[0], "calls": v[1], "own_s": v[2], "cumulative_s": v[3]}
                                 for k, v in stats.stats.items() if k[2] in ("_completion", "completion_check", "_c1", "_c1f_state")}
        out["rungs"][rung] = row
        write(HERE / (args.phase + "-complete.json"), out)
        print(rung, "check first/repeat ms", checks[0], checks[-1], row.get("solver_status"), flush=True)
    out["wall_s"] = time.perf_counter()-started
    out["pins_after"] = pins()
    write(HERE / (args.phase + "-complete.json"), out)


if __name__ == "__main__":
    main()
