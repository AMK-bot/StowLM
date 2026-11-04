"""Compare pre-fix and current strength behavior without replacing physics.

Compile only the saved check_strength body, omitting its registry decorator.
Both bodies use the live, identical numerical integrator/geometry dependencies.
"""
from __future__ import annotations
import os
for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[name] = '1'

import ast
from dataclasses import replace
import json
from pathlib import Path
from time import perf_counter

from stowage.checker import strength
from stowage.model import ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import list_fleet, load_vessel

HERE = Path(__file__).resolve().parent


def bitwise_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(bitwise_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(bitwise_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return left.hex() == right.hex()
    return left == right


def main():
    started = perf_counter()
    source = HERE/'item4-strength-before.py'
    node = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'check_strength')
    node.decorator_list = []
    namespace = vars(strength).copy()
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    before = namespace['check_strength']
    controls = []
    for name in list_fleet():
        instance = build_instance()
        instance.vessel = load_vessel(name)
        instance.onboard = ShipState(vessel_id=instance.vessel.id)
        grouped = {tid for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids}
        for remaining in (0., 1., .55, .1):
            instance.onboard.tank_masses_kg = {
                tank.id: .5*remaining*tank.capacity_m3*tank.content_density_kg_m3
                for tank in instance.vessel.tanks if tank.id in grouped}
            old = before(instance, instance.onboard, call_index=0).model_dump(mode='json')
            new = strength.check_strength(instance, instance.onboard, call_index=0).model_dump(mode='json')
            assert old['ran'] and new['ran'], (name, remaining, old['error'], new['error'])
            assert bitwise_equal(old, new), (name, remaining)
            controls.append(dict(ship=name, remaining_fraction=remaining,
                bitwise_equal=True, old=old, new=new))

    instance = build_instance()
    instance.vessel = load_vessel('feeder_500')
    instance.onboard = ShipState(vessel_id=instance.vessel.id)
    defects = []
    original = strength.integrate_strength

    def truncated(*args, **kwargs):
        curve = original(*args, **kwargs)
        return replace(curve, x_m=curve.x_m[:-2], shear_n=curve.shear_n[:-2],
                       bending_nm=curve.bending_nm[:-2])

    strength.integrate_strength = namespace['integrate_strength'] = truncated
    try:
        old = before(instance, instance.onboard, call_index=0).model_dump(mode='json')
        new = strength.check_strength(instance, instance.onboard, call_index=0).model_dump(mode='json')
        assert old['ran'] and not old['violations']
        assert not new['ran'] and new['violations'][0]['rule_id'] == 'iacs.s1.loading_instrument'
        defects.append(dict(case='truncated_station_readout', old=old, new=new))
    finally:
        strength.integrate_strength = namespace['integrate_strength'] = original
    instance.vessel.hull.stations[len(instance.vessel.hull.stations)//2].max_torsion_nm = 1e6
    old = before(instance, instance.onboard, call_index=0).model_dump(mode='json')
    new = strength.check_strength(instance, instance.onboard, call_index=0).model_dump(mode='json')
    assert old['ran'] and not old['violations']
    assert not new['ran'] and new['violations'][0]['rule_id'] == 'iacs.s1.loading_instrument'
    defects.append(dict(case='explicit_torsion_without_model', old=old, new=new))
    result = dict(control_count=len(controls), controls_bitwise_equal=True,
        defect_count=len(defects), defects_now_fail_closed=True, controls=controls,
        defects=defects, wall_seconds=perf_counter()-started)
    (HERE/'item4-numeric-comparison.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({key: value for key, value in result.items() if key not in ('controls', 'defects')}))


if __name__ == '__main__':
    main()
