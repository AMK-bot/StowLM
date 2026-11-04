"""Full-check CURRENT production C1-C3 manual recipes, with pinned replay inputs.

Unlike recheck.py's historical baseline, consume standard_manual_conditions
verbatim, including any explicit revised ballast and homogeneous cargo witness.
The historical _wrap builds containers/placements only; its declared expert
settings (E1/E6 25t, E2 tolerance 0) are retained unchanged. Known passive spies
are removed by recheck._load_harness; no checker or physics implementation is
replaced. Synthetic dry ISO20 containers declare a 30,480 kg CSC plate rating.
No solver, cargo regeneration, consumable change or ballast repair is run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import recheck


def prepare(name, condition):
    """Build exactly the selected current recipe; reject wrapper input drift."""
    from stowage.model import Instance, MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.vessel.fleet import load_ship_params, load_vessel
    from stowage.vessel.fleet_manual import standard_manual_conditions

    harness = recheck._load_harness()
    vessel = load_vessel(name)
    matches = [row for row in standard_manual_conditions(vessel, load_ship_params(name))
               if row['condition'] == condition]
    if len(matches) != 1:
        raise ValueError(f'expected one current standard manual recipe: {name} {condition}')
    recipe = matches[0]
    tanks = dict(recipe['departure_tank_masses_kg'])
    instance = harness.h1._wrap(name, vessel, condition, list(recipe['cargo_cells']), tanks)
    payload = instance.model_dump(mode='python')
    # Supply the missing synthetic CSC evidence, preserving every recipe mass.
    for box in payload['containers']:
        box['max_gross_mass_kg'] = 30480.
    instance = recheck.attach_voyage(Instance.model_validate(payload))
    boxes = {box.id:box for box in instance.containers}
    actual = [(placement.slot, boxes[placement.container_id].gross_mass_kg)
              for placement in instance.onboard.placements]
    # ShipState validation canonicalizes placement order. Physical cell/mass
    # membership must be identical; recipe fill-order enumeration is not state.
    if sorted(actual) != sorted(recipe['cargo_cells']):
        raise ValueError('passive wrapper changed current manual cargo')
    if instance.onboard.tank_masses_kg != tanks:
        raise ValueError('passive wrapper changed current manual departure tanks')
    if tuple(call.locode for call in instance.rotation.calls) != tuple(recipe['route']):
        raise ValueError('passive wrapper route differs from current manual route')
    plan = MovePlan(id=f'{instance.id}.current-manual', instance_id=instance.id,
        call_index=instance.call_index, ballast=BallastCondition(
            tank_masses_kg={tank.id:tanks.get(tank.id, 0.) for tank in vessel.tanks if tank.adjustable},
            source='planner'))
    audit = dict(recipe=recipe, cargo_cells_sha256=recheck._digest(actual),
        departure_tank_masses_sha256=recheck._digest(tanks),
        rule_overrides=instance.rule_overrides,
        synthetic_csc_plate_max_gross_kg=30480.,
        cargo_unchanged_from_current_recipe=True, tank_masses_unchanged_from_current_recipe=True,
        voyage_consumption=instance.voyage.consumption,
        no_finisher_or_input_repair=True)
    return instance, plan, audit


def input_identity(name, condition, instance, plan, audit):
    source = recheck.ROOT/'src/stowage/vessel/fleet_manual.py'
    identity = recheck._input_identity(name, condition, instance, plan, source)
    identity['format'] = 'current-manual-voyage-convergence-v1'
    identity['files'][str(Path(__file__).relative_to(recheck.ROOT))] = hashlib.sha256(
        Path(__file__).read_bytes()).hexdigest()
    identity['manual_audit_sha256'] = recheck._digest(audit)
    return identity


def run(name, condition, output):
    """One bounded full check; resume requires identical source and payloads."""
    from slices import bounded_full_check
    from stowage.versions import component_versions

    started = time.perf_counter()
    instance, plan, audit = prepare(name, condition)
    identity = input_identity(name, condition, instance, plan, audit)
    fingerprint = recheck._digest(identity)
    folder = Path(output)/f'{name}_{condition}'
    folder.mkdir(parents=True, exist_ok=True)
    result_path = folder/'result.json'
    artifacts = {'instance.json':instance.model_dump(mode='json'), 'plan.json':plan.model_dump(mode='json')}
    if result_path.exists():
        previous = json.loads(result_path.read_text())
        if previous.get('input_identity') != identity or previous.get('input_fingerprint') != fingerprint:
            raise ValueError(f'stale or unbound current-manual inputs: {result_path}; use fresh --out')
        if previous.get('audit') != json.loads(json.dumps(audit)):
            raise ValueError(f'current-manual audit differs: {result_path}')
        for filename, payload in artifacts.items():
            path = folder/filename
            if not path.exists() or json.loads(path.read_text()) != payload:
                raise ValueError(f'current-manual replay artifact differs: {path}')
        if previous.get('check', {}).get('status') == 'completed':
            if previous.get('phase_reports') != recheck._phase_reports(previous['check'],instance.voyage):
                raise ValueError(f'current-manual phase evidence differs: {result_path}')
            return previous
    for filename, payload in artifacts.items():
        (folder/filename).write_text(json.dumps(payload, indent=2, allow_nan=False)+'\n')
    checked = bounded_full_check(instance, plan)
    if input_identity(name, condition, instance, plan, audit) != identity:
        raise ValueError('current-manual sources or inputs changed during check; result not published')
    row = dict(ship=name, condition=condition, recipe_kind='current_production_manual',
        input_identity=identity, input_fingerprint=fingerprint,
        components=component_versions(), audit=audit,
        voyage=instance.voyage.model_dump(mode='json'),
        artifacts={filename:dict(sha256=recheck._digest(payload),path=filename)
                   for filename,payload in artifacts.items()},
        check=checked,phase_reports=recheck._phase_reports(checked,instance.voyage),
        wall_s=time.perf_counter()-started)
    result_path.write_text(json.dumps(row, indent=2, allow_nan=False)+'\n')
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ships', nargs='+', default=['feeder_500','feeder_1000','feeder_1700'])
    parser.add_argument('--conditions', nargs='+', default=['C1','C2','C3'])
    parser.add_argument('--out', type=Path, default=HERE/'current-results')
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    started = time.perf_counter()
    rows = []
    for name in args.ships:
        for condition in args.conditions:
            row = run(name,condition,args.out)
            rows.append(row)
            print(json.dumps(dict(ship=name,condition=condition,status=row['check']['status'],
                passed=row['check']['passed'],wall_s=row['check']['wall_s'])),flush=True)
            for phase,evidence in row['phase_reports'].items():
                print(json.dumps(dict(ship=name,condition=condition,phase=phase,
                    condition_id=evidence['identity']['id'],available=evidence['available'],
                    passed=evidence['passed'],water_density_kg_m3=evidence['identity']['water_density_kg_m3'])),flush=True)
            summary = dict(recipe_kind='current_production_manual',
                passed=sum(item['check']['passed'] is True for item in rows),total=len(rows),
                completed=sum(item['check']['status']=='completed' for item in rows),
                elapsed_s=time.perf_counter()-started,
                peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9,rows=rows)
            (args.out/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
