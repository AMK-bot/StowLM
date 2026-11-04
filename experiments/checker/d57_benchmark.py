"""D57-valid identities around the unchanged production full-check benchmark.

ASSUMPTION: both synthetic benchmark calls use 1025 kg/m3 water and declared
synthetic environments. No cargo, tank, ballast, hull or acceptance limits change.
Run with the same CLI as benchmark_full.py. One numerical CPU; no cache warming.
"""
from __future__ import annotations

import os
from pathlib import Path

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"


def declare_voyage(instance):
    from stowage.model import Instance, VoyageSpec
    from stowage.model.provenance import Provenance

    density = instance.port.water_density_kg_m3
    identities = {}
    for phase, index in (("departure", instance.call_index), ("arrival", instance.call_index + 1)):
        identities[phase] = dict(
            id=f"{instance.id}:call-{index}:{phase}", phase=phase, call_index=index,
            locode=instance.rotation.calls[index].locode,
            environment_id=f"synthetic:{instance.id}:call-{index}:environment",
            density_id=f"synthetic:{instance.id}:call-{index}:density-{density:g}",
            water_density_kg_m3=density,
            provenance=Provenance(created_by="experiments.checker.d50_benchmark",
                parents=["D50:synthetic-next-call-environment-and-density"]))
    payload = instance.model_dump(mode="python")
    payload["voyage"] = VoyageSpec(**identities,
        provenance=Provenance(created_by="experiments.checker.d50_benchmark",
            parents=["D50:default-group-consumption:10-percent-remaining"]))
    return Instance.model_validate(payload)


def main():
    from contextlib import redirect_stdout
    import io
    import json
    import benchmark_full as benchmark
    import stowage.checker.full as full

    original = benchmark.condition
    fill_fraction = float(os.environ.get("STOWAGE_D50_CONSUMABLE_FILL", "0"))
    if not 0 <= fill_fraction <= 1:
        raise ValueError("STOWAGE_D50_CONSUMABLE_FILL must be within [0, 1]")

    def condition(size):
        instance, plan = original(size)
        if fill_fraction:
            grouped = {tid for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids}
            masses = dict(instance.onboard.tank_masses_kg)
            masses.update({tank.id: fill_fraction * tank.capacity_m3 * tank.content_density_kg_m3
                for tank in instance.vessel.tanks if tank.id in grouped})
            instance.onboard.tank_masses_kg = masses
        return declare_voyage(instance), plan

    benchmark.condition = condition
    original_check = full.check
    last_report = []
    physics_calls, current_calls = [], []
    if fill_fraction:
        from functools import wraps
        from time import perf_counter

        def timed_part(name, function):
            @wraps(function)
            def invoke(instance, ship, **kwargs):
                started = perf_counter()
                try:
                    return function(instance, ship, **kwargs)
                finally:
                    current_calls.append(dict(part=name, call_index=kwargs.get("call_index"),
                        tank_mass_kg=sum(ship.tank_masses_kg.values()),
                        seconds=perf_counter() - started))
            return invoke

        for name in ("stability", "strength", "visibility", "lashing"):
            setattr(full, f"check_{name}", timed_part(name, getattr(full, f"check_{name}")))

    def capture_check(*args, **kwargs):
        current_calls.clear()
        report = original_check(*args, **kwargs)
        physics_calls.append(list(current_calls))
        last_report[:] = [report.model_dump(mode="json")]
        return report

    full.check = capture_check
    original_worker = benchmark.worker

    def worker(args):
        stream = io.StringIO()
        with redirect_stdout(stream):
            original_worker(args)
        row = json.loads(stream.getvalue())
        row["report"] = last_report[0]
        row["d50_consumable_fill_fraction"] = fill_fraction
        if fill_fraction:
            row["physics_calls_cold_then_warm_then_optional_profile"] = physics_calls
        print(json.dumps(row, sort_keys=True), flush=True)

    benchmark.worker = worker
    # The benchmark launches fresh Python children through its __file__ global.
    # Point only that experiment dispatcher at this wrapper, so each child gets
    # the identical explicit D50 declaration while keeping physical inputs intact.
    benchmark.__file__ = str(Path(__file__).resolve())
    os.environ["STOWAGE_BENCH_LOADING"] = "homogeneous"
    os.environ["STOWAGE_BENCH_PRODUCTION_MODEL"] = "1"
    benchmark.main()


if __name__ == "__main__":
    main()
