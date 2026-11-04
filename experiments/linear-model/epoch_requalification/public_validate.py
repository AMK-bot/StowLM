"""Verify promoted public defaults, including actual LIST completeness.

Interior heldout states describe retention, not safety outside the domain.
Independent signed boundary-moment qualification supplies that evidence.
Timing variants carry distinct fixed point loads and use the real public
builder, without substituting calibration or reference oracles.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.util
import json
import math
from pathlib import Path
import time
from datetime import datetime, timezone

import numpy as np

HERE = Path(__file__).resolve().parent
SHIPS = ('feeder_500', 'feeder_1000', 'feeder_1700')
LEVELS = ('light', 'medium', 'full')


def driver():
    spec = importlib.util.spec_from_file_location('epoch_driver', HERE/'driver.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replay_list(states, *, gm_min_m, gm_reserve_m, limit_rad, moment_reserves):
    """Replay both production affine rows, preserving the lower-GM reserve."""
    details = []
    for state in states:
        mass = float(state['mass_kg'])
        gm = float(state['affine_gm_m'])
        restoring = float(state['affine_restoring_kgm'])-gm_reserve_m*mass
        my = float(state['transverse_moment_kgm'])
        eligible = gm >= gm_min_m+gm_reserve_m
        residual = {side: sign*my-math.tan(limit_rad)*restoring+moment_reserves[side]
                    for side, sign in (('lower_unsafe', -1), ('upper_unsafe', 1))}
        accepted = eligible and max(residual.values()) <= 1e-7
        details.append(dict(gm_eligible=eligible, list_accepted=accepted,
            exact_list_rad=float(state['exact_list_rad']), row_residuals_kgm=residual))
    eligible_count = sum(row['gm_eligible'] for row in details)
    accepted_count = sum(row['list_accepted'] for row in details)
    violating = [i for i,row in enumerate(details)
                 if row['list_accepted'] and abs(row['exact_list_rad']) > limit_rad+1e-9]
    return dict(samples=len(details), gm_eligible_count=eligible_count,
        list_accepted_count=accepted_count, accepted_exact_violations=violating,
        rows=details, warning='Interior population/completeness only; not an independent boundary safety proof.')


def validate_case(helper, root, ship, level):
    from stowage.linear import build_linear_model, linear_constraints
    from stowage.linear.bands import band_for_instance
    from stowage.linear.margin_policy import default_reserves, policy_inputs
    from stowage.vessel.fleet import load_ship_params
    started = time.perf_counter()
    instance, manifest = helper.HELPER['cargo_instance'](ship, level)
    before = helper.snapshot(ship, load_ship_params(ship))
    policy_before = policy_inputs()
    model = build_linear_model(instance, band=band_for_instance(instance))
    constraints = linear_constraints(model, direction="conservative")
    reserves = default_reserves(model, direction="conservative")
    certificate = model.measurement_diagnostics['margin_policy']
    failures = []
    if certificate.get('production_usable') is not True:
        failures.append('public model certificate is not production usable')
    empty = [name for name,lo,hi in zip(constraints.names,constraints.lb,constraints.ub) if lo > hi]
    constant_rows = np.all(constraints.a_cell == 0.,axis=1)&np.all(constraints.a_tank == 0.,axis=1)
    impossible = [name for name,zero,value,lo,hi in zip(constraints.names,constant_rows,
        constraints.const,constraints.lb,constraints.ub) if zero and not lo <= value <= hi]
    if empty or impossible:
        failures.append('empty intervals or impossible constant rows')
    if 'list_margin' in constraints.names:
        failures.append('angular LIST margin exhausted')
    if not reserves or 'list_moment_kgm' not in reserves:
        raise ValueError('current public defaults do not contain qualified boundary LIST moments')
    heldout_path = root/ship/f'holdout-{level}.json'
    record = json.loads(heldout_path.read_text())
    if (record['physical_inputs_start'] != before or not record['physical_inputs_unchanged']
            or record['physical_inputs_end'] != before):
        raise ValueError('heldout state epoch differs from current public model')
    # Band restrictions are persistent fields now; JSON stores tank-window
    # tuples as lists, so compare canonical serialized physical content.
    if helper.serializable(asdict(model.band)) != record['band']:
        raise ValueError('heldout band differs from current public model')
    for field in ('family_calibration_cache_key','response_calibration_identity','reference_identity'):
        if model.measurement_diagnostics[field] != record['calibration'][field]:
            raise ValueError('heldout calibration differs from current public model: '+field)
    list_replay = replay_list(record['list_row_samples'],gm_min_m=model.gm_min_m,
        gm_reserve_m=float(reserves['lower_unsafe']['gm_m']),
        limit_rad=min(model.max_list_rad,model.band.max_abs_heel_rad),
        moment_reserves=reserves['list_moment_kgm'])
    if list_replay['accepted_exact_violations']:
        failures.append('LIST accepted an interior heldout state with violating exact heel')
    if ship == 'feeder_1700' and list_replay['list_accepted_count'] == 0:
        failures.append('1700 LIST retains no measured heldout states; margin may still be exhausted')
    # One captured real loading per case independently checks the vector rows
    # exposed by the public API against the total-moment replay coordinates.
    from stowage.linear.physics import _linear_arrays
    captured=record['public_checker_comparison']['captured_sample']
    cells=np.asarray(captured['cell_masses_kg'],dtype=float)
    tanks=np.asarray(captured['tank_masses_kg'],dtype=float)
    affine=_linear_arrays(model,cells,tanks,instance=instance,solver=True)
    mass=model.const_mass_kg+float(cells.sum()+tanks.sum())
    my=model.const_moment_y_kgm+float(model.cell_tcg_m@cells+model.tank_tcg_m@tanks)
    np.testing.assert_allclose([mass,mass*affine['gm_m'],my],
        [record['list_row_samples'][0][key] for key in
         ('mass_kg','affine_restoring_kgm','transverse_moment_kgm')],rtol=1e-10,atol=1e-6)
    exposed_rows={}
    for name,side in (('list_port','upper_unsafe'),('list_starboard','lower_unsafe')):
        index=constraints.names.index(name)
        value=float(constraints.a_cell[index]@cells+constraints.a_tank[index]@tanks+constraints.const[index])
        expected=list_replay['rows'][0]['row_residuals_kgm'][side]
        np.testing.assert_allclose(value,expected,rtol=1e-10,atol=1e-6)
        if constraints.ub[index] != 0.:
            raise ValueError('public LIST row no longer has expected zero upper bound')
        exposed_rows[name]=dict(public_row_value_kgm=value,replay_value_kgm=expected)
    after = helper.snapshot(ship,load_ship_params(ship))
    policy_after=policy_inputs()
    if after != before:
        failures.append('physical inputs changed during public verification')
    if policy_after != policy_before:
        failures.append('margin policy changed during public verification')
    return instance, dict(ship=ship,level=level,manifest=manifest,
        physical_inputs_start=before,physical_inputs_end=after,
        policy_inputs_start=policy_before,policy_inputs_end=policy_after,
        physical_inputs_unchanged=before==after,production_usable=certificate.get('production_usable'),
        policy_inputs_hash=certificate['inputs_hash'],constraint_count=len(constraints.names),
        empty_intervals=empty,impossible_constant_rows=impossible,
        list_margin_present='list_margin' in constraints.names,
        gm_reserve_m=float(reserves['lower_unsafe']['gm_m']),
        list_moment_reserves_kgm=reserves['list_moment_kgm'],list_replay=list_replay,
        heldout_path=str(heldout_path.resolve()),heldout_seed=record['seed'],
        captured_public_row_equivalence=exposed_rows,
        seconds=time.perf_counter()-started,failures=failures,passed=not failures)


def latency(helper, instance, ship, variants):
    from stowage.linear import build_linear_model,linear_constraints
    from stowage.linear.bands import band_for_instance
    from stowage.model import FixedLoad
    from stowage.vessel.fleet import load_ship_params
    from stowage.linear.margin_policy import policy_inputs
    before = helper.snapshot(ship,load_ship_params(ship))
    policy_before=policy_inputs()
    seconds = []
    keys = []
    for index in range(variants):
        point = FixedLoad(x_m=instance.vessel.hull.lpp_m/2,y_m=0.,z_m=0.,mass_kg=float(index+1))
        current = instance.model_copy(update={'vessel':instance.vessel.model_copy(
            update={'fixed_loads':[*instance.vessel.fixed_loads,point]})})
        started = time.perf_counter()
        model = build_linear_model(current,band=band_for_instance(current))
        linear_constraints(model, direction="conservative")
        seconds.append(time.perf_counter()-started)
        keys.append(model.measurement_diagnostics['family_calibration_cache_key'])
        if model.measurement_diagnostics['margin_policy'].get('production_usable') is not True:
            raise ValueError('latency variant is not production usable')
    if len(set(keys)) != 1:
        raise ValueError('fixed-load constants invalidated calibration cache')
    after = helper.snapshot(ship,load_ship_params(ship))
    policy_after=policy_inputs()
    if before != after:
        raise ValueError('physical inputs changed during public latency verification')
    if policy_after != policy_before:
        raise ValueError('margin policy changed during public latency verification')
    return dict(variants=variants,seconds=seconds,mean_seconds=float(np.mean(seconds)),
        maximum_seconds=max(seconds),calibration_cache_key=keys[0],
        physical_inputs_start=before,physical_inputs_end=after,physical_inputs_unchanged=True,
        policy_inputs_start=policy_before,policy_inputs_end=policy_after,
        includes='band_for_instance + public build_linear_model + default linear_constraints; distinct appended point load per instance')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--latency-variants',type=int,default=0)
    args=parser.parse_args()
    if args.latency_variants < 0:
        parser.error('--latency-variants must be nonnegative')
    helper=driver()
    started=datetime.now(timezone.utc).isoformat()
    records=[]
    timings={}
    for ship in SHIPS:
        for level in LEVELS:
            instance,record=validate_case(helper,args.root,ship,level)
            records.append(record)
            print(json.dumps(dict(ship=ship,level=level,passed=record['passed'],
                retained=record['list_replay']['list_accepted_count'],seconds=record['seconds'])),flush=True)
            if level=='light' and args.latency_variants:
                timings[ship]=latency(helper,instance,ship,args.latency_variants)
            helper.write(args.output,dict(started_utc=started,
                finished_utc=datetime.now(timezone.utc).isoformat(),cases=records,
                latency=timings,passed=len(records)==9 and all(row['passed'] for row in records)))
    if not all(row['passed'] for row in records):
        raise SystemExit(1)


if __name__=='__main__':
    main()
