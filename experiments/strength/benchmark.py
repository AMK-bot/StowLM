"""Cold checker timing using fully assembled production KCS-based vessels.

Production vessel/KN construction occurs before the timed checker calls.
Run: UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/strength/benchmark.py
One CPU core, no GPU, memory well below 4 GB, seconds per feeder.
"""
from time import perf_counter

from stowage.checker.strength import check_strength
from stowage.checker.strength_limits import permissible_stations
from stowage.model import ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import list_fleet, load_ship_params, load_vessel
from stowage.vessel.tanks import build_tanks


def main():
    for name in list_fleet():
        params = load_ship_params(name)
        vessel = load_vessel(name, with_hull=True)
        instance = build_instance()
        instance.vessel = vessel
        for fill in (0., .5):
            state = ShipState(vessel_id=vessel.id, tank_masses_kg={
                t.id: t.capacity_m3*t.content_density_kg_m3*fill for t in vessel.tanks})
            start = perf_counter()
            report = check_strength(instance, state, call_index=0)
            elapsed = perf_counter()-start
            print(f'{name} tank_fill={fill:g} seconds={elapsed:.6f} ran={report.ran} '
                  f'violations={len(report.violations)} error={report.error}', flush=True)


if __name__ == '__main__':
    main()
