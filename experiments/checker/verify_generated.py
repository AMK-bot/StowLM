"""Seeded report properties on actual task 3.5 archived generator instances.

Partial plans intentionally leave shut-outs: move legality does not imply a
safe or complete departure. No physics is replaced. Archives predate current
strength envelopes; regenerate only their documented synthetic S11A station
limits, exactly as benchmark_full.py does. See docs/data-sources.md §4.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from random import Random
from time import perf_counter

for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'

ROOT = Path(__file__).resolve().parents[2]


def partial_plan(instance, seed):
    from stowage.model import BallastCondition, MovePlan
    from stowage.model.moves import Load
    from stowage.moves import IllegalMoveError, apply_move, initial_state

    rng = Random(seed)
    state, moves = initial_state(instance), []
    slots = [c.id for c in instance.vessel.cells if int(c.id[-2:]) >= 80]
    wanted = 1 + seed % 3
    while len(moves) < wanted:
        candidates = [(stack, slot) for stack in state.yard.stacks
                      if stack.boxes and stack.boxes[-1] in instance.load_list for slot in slots]
        rng.shuffle(candidates)
        for stack, slot in candidates:
            move = Load(call_index=instance.call_index, container_id=stack.boxes[-1],
                        src=f'{stack.key}-{len(stack.boxes):02d}', dst=slot)
            try:
                following = apply_move(instance, state, move)
            except IllegalMoveError:
                continue
            state = following
            moves.append(move)
            break
        else:
            break
    assert moves, 'archived tiny corpus must offer at least one accessible deck load'
    ballast = {t.id: instance.onboard.tank_masses_kg.get(t.id, 0.)
               for t in instance.vessel.tanks if t.adjustable}
    return MovePlan(id=f'generated-property.{seed}', instance_id=instance.id,
                    call_index=instance.call_index, moves=moves,
                    ballast=BallastCondition(source='planner', tank_masses_kg=ballast))


def main():
    from stowage.checker.full import check
    from stowage.checker.strength_limits import permissible_stations
    from stowage.io import load_instance
    from stowage.moves import apply_plan
    from stowage.versions import component_versions
    from stowage.vessel.fleet import load_ship_params

    source = ROOT / 'experiments/instances/tiny/seed-00000001.json'
    base = load_instance(source)
    params = load_ship_params(base.vessel.slice_of or base.vessel.id)
    base.vessel.hull.stations = permissible_stations(params, base.vessel.hull)
    rows = []
    for seed in range(3):
        instance = base.model_copy(deep=True)
        plan = partial_plan(instance, seed)
        replay = apply_plan(instance, plan)
        assert replay.legal, replay.violations
        before = instance.model_dump_json(), plan.model_dump_json()
        times, reports = [], []
        for _ in range(2):
            start = perf_counter()
            reports.append(check(instance, plan))
            times.append(perf_counter() - start)
        first, second = reports
        assert first == second
        assert before == (instance.model_dump_json(), plan.model_dump_json())
        loaded = {p.container_id for p in replay.final_state.ship.placements}
        expected_shutout = sorted(set(instance.load_list) - loaded)
        assert first.shut_out == expected_shutout
        assert first.count == replay.count
        assert not first.passed and expected_shutout
        assert any(v.rule_id == 'objective.all_loaded' and v.severity == 'hard' for v in first.violations)
        rows.append(dict(seed=seed, loads=len(plan.moves), shut_out=len(expected_shutout),
                         check_seconds=times, passed=first.passed,
                         parts_ran={name: part.ran for name, part in first.parts.items()},
                         part_errors={name: part.error for name, part in first.parts.items() if part.error},
                         rule_ids=sorted({v.rule_id for v in first.violations})))
    result = dict(source=str(source.relative_to(ROOT)), random_plans=3, full_checks=6,
                  properties=['move legality', 'determinism', 'input purity', 'never raises',
                              'count equality', 'exact shut-outs', 'hard incomplete-plan rejection'],
                  component_versions=component_versions(), rows=rows)
    destination = Path(__file__).with_name('generated-validation.json')
    destination.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
