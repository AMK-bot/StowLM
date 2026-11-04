"""Frozen <=30 G1 small-stow ballast qualification, with owned-worker deadlines.

prepare rebuilds only synthetic slice backgrounds through the current public
producer (D42/D48), preserving bookings, geometry and candidate moves. D50
consumables are declared using the production vessel helper; both conditions
use the same synthetic port density (an explicit campaign assumption).
before imports the frozen ballast modules captured before this fix.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[key] = '1'

# Multiprocessing spawn executes this top level as well, so the public watchdog
# resolves its function in the same pinned original package in every process.
if os.environ.get('BALLAST_REVIEW_BEFORE') == '1':
    import stowage.solver
    stowage.solver.__path__.insert(0, str(HERE / 'before_source'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity():
    from stowage.versions import component_versions
    areas=('checker','linear','model','vessel')
    paths=[p for area in areas for p in (ROOT/'src/stowage'/area).rglob('*.py')]
    ballast_root=HERE/'before_source' if os.environ.get('BALLAST_REVIEW_BEFORE')=='1' else ROOT/'src/stowage/solver'
    paths.extend(ballast_root.glob('ballast*.py'))
    return dict(versions=component_versions(),files={str(p.relative_to(ROOT)):digest(p) for p in sorted(paths)})


def prepare(count):
    from stowage.io import load_instance, load_plan, save_instance, save_plan
    from stowage.model import Instance, VoyageSpec, VoyageConditionIdentity
    from stowage.moves import apply_plan
    from stowage.vessel import recondition_slice, departure_tank_masses, load_ship_params, load_vessel, slice_preset
    from stowage.vessel.fleet_voyage import build_voyage_policy
    paths = sorted((ROOT/'experiments/scalable-solver/g1-small-extend').glob('worker-*/results/*.json'))
    selected = []
    for result_path in paths:
        old = json.loads(result_path.read_text())
        if not old.get('measurement', {}).get('completed') or not old.get('returned_candidates'):
            continue
        selection = old['selection']
        source = Path(selection['manifest_root']) / selection['relative']
        plan_source = Path(old['returned_candidates'][0]['path'])
        instance, plan = load_instance(source), load_plan(plan_source)
        if not apply_plan(instance, plan).legal:
            continue
        case = result_path.stem
        folder = HERE/'inputs'/case
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder/'instance.json').exists():
            started = time.monotonic()
            vessel_name = instance.vessel.slice_of or instance.vessel.id
            current_full = load_vessel(vessel_name)
            current_slice = slice_preset(current_full, 'small', name=instance.vessel.id)
            assert {c.id for c in current_slice.cells} == {c.id for c in instance.vessel.cells}
            instance.vessel = current_slice
            params = load_ship_params(vessel_name)
            # D40 operating policy is copied from its declared production source,
            # never inferred from table endpoints or loosened for this campaign.
            fields = {key: value for key, value in params['operating_limits'].items()
                      if key in type(instance.vessel.hull).model_fields}
            instance.vessel.hull = instance.vessel.hull.model_copy(update=fields)
            pin = next(s for s in instance.vessel.sources if s.startswith('params/fleet/'))
            instance.vessel.voyage_policy = build_voyage_policy(instance.vessel.tanks,
                parameter_source=pin, vessel_id=instance.vessel.id)
            mass = sum(c.gross_mass_kg for c in instance.containers if c.id in instance.load_list)
            instance.vessel = recondition_slice(instance.vessel, expected_load_mass_kg=mass,
                seed=int(instance.family['seed']))
            instance.onboard.tank_masses_kg = departure_tank_masses(instance.vessel)
            identities = []
            for offset, phase in enumerate(('departure', 'arrival')):
                ci = instance.call_index+offset
                identities.append(VoyageConditionIdentity(id=f'{case}:{phase}', phase=phase,
                    call_index=ci, locode=instance.rotation.calls[ci].locode,
                    environment_id=f'{case}:synthetic-port', density_id=f'{case}:density',
                    water_density_kg_m3=instance.port.water_density_kg_m3))
            instance.voyage = VoyageSpec(departure=identities[0], arrival=identities[1])
            instance = Instance.model_validate(instance.model_dump())
            plan = plan.model_copy(update={'ballast': None})
            assert apply_plan(instance, plan).legal
            save_instance(instance, folder/'instance.json')
            save_plan(plan, folder/'input-plan.json')
            (folder/'source.json').write_text(json.dumps(dict(source=str(source),
                source_sha256=digest(source), plan_source=str(plan_source),
                plan_source_sha256=digest(plan_source), prior_ballast_status=old['measurement'].get('ballast_status'),
                transform='current public recondition_slice D42/D48; declared D40 policy; production D50 policy; unchanged cargo/moves',
                prepare_s=time.monotonic()-started), indent=2)+'\n')
        selected.append(dict(case=case, instance_sha256=digest(folder/'instance.json'),
            plan_sha256=digest(folder/'input-plan.json')))
        print(json.dumps(dict(prepared=case, count=len(selected))), flush=True)
        if len(selected) >= count:
            break
    assert len(selected) == count
    (HERE/'manifest.json').write_text(json.dumps(selected, indent=2)+'\n')


def physics(instance, plan, limit):
    from stowage.checker.full import check
    from stowage.solver.ballast_deadline import run_with_deadline, BallastDeadline
    started = time.monotonic()
    try:
        report = run_with_deadline(check, (instance, plan), deadline=started+limit)
        return dict(wall_s=time.monotonic()-started, passed=report.passed,
            violations=[dict(rule_id=v.rule_id, message=v.message) for v in report.violations],
            parts={key: value.model_dump(mode='json') for key,value in report.parts.items()})
    except BallastDeadline:
        return dict(wall_s=time.monotonic()-started, timeout=True)


def run(label, count, limit, repeat, check_limit, witness=False):
    from stowage.io import load_instance, load_plan, save_plan
    from stowage.model import BallastCondition
    from stowage.solver.ballast import finish_ballast
    from stowage.solver.ballast_deadline import shutdown_worker
    if witness:
        folder=HERE/'synthetic-witness'
        manifest=[dict(case='synthetic-witness',instance_sha256=digest(folder/'instance.json'),
            plan_sha256=digest(folder/'input-plan.json'))]
    else:
        manifest = json.loads((HERE/'manifest.json').read_text())[:count]
    output = HERE/label
    output.mkdir(exist_ok=True)
    identity=source_identity()
    (output/'identity-start.json').write_text(json.dumps(identity,indent=2)+'\n')
    rows = []
    started = time.monotonic()
    for entry in manifest:
        folder = HERE/'synthetic-witness' if witness else HERE/'inputs'/entry['case']
        instance, plan = load_instance(folder/'instance.json'), load_plan(folder/'input-plan.json')
        row = dict(**entry, calls=[])
        shutdown_worker()  # explicit process-cold first call for each case
        for index in range(repeat):
            now = time.monotonic()
            result = finish_ballast(instance, plan, time_limit_s=limit, seed=0)
            call = dict(repeat=index, worker_warm=index>0, status=result.status,
                wall_s=time.monotonic()-now, iterations=result.iterations, proof=result.proof,
                parts={key:value.model_dump(mode='json') for key,value in result.parts.items()})
            if result.plan is not None:
                save_plan(result.plan, output/f'{entry["case"]}-{index}.plan.json')
                call['full_check'] = physics(instance, result.plan, check_limit)
            row['calls'].append(call)
            (output/f'{entry["case"]}.json').write_text(json.dumps(row, indent=2)+'\n')
            shown={k:v for k,v in call.items() if k not in {'parts','full_check'}}
            if 'full_check' in call:
                shown['full_check_passed']=call['full_check'].get('passed')
            print(json.dumps(dict(case=entry['case'], **shown)), flush=True)
        if check_limit > 0:
            baseline = plan.model_copy(update={'ballast':BallastCondition(source='planner',tank_masses_kg={
                t.id:instance.onboard.tank_masses_kg.get(t.id,0.) for t in instance.vessel.tanks if t.adjustable})})
            row['initial_full_check'] = physics(instance, baseline, check_limit)
        (output/f'{entry["case"]}.json').write_text(json.dumps(row, indent=2)+'\n')
        rows.append(row)
        (output/'results.json').write_text(json.dumps(rows, indent=2)+'\n')
    import collections
    counts = dict(collections.Counter(c['status'] for r in rows for c in r['calls']))
    summary = dict(cases=len(rows), calls=sum(len(r['calls']) for r in rows), statuses=counts,
        not_found_rate=counts.get('not_found',0)/sum(len(r['calls']) for r in rows),
        wall_s=time.monotonic()-started, time_limit_s=limit, repeat=repeat,
        before_source=os.environ.get('BALLAST_REVIEW_BEFORE')=='1')
    final_identity=source_identity()
    summary['source_unchanged']=identity==final_identity
    (output/'identity-end.json').write_text(json.dumps(final_identity,indent=2)+'\n')
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare','run'))
    parser.add_argument('--label', default='before')
    parser.add_argument('--count', type=int, default=6)
    parser.add_argument('--limit', type=float, default=5.)
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--check-limit', type=float, default=8.)
    parser.add_argument('--witness', action='store_true')
    args = parser.parse_args()
    assert 0 < args.count <= 30
    prepare(args.count) if args.mode=='prepare' else run(args.label,args.count,args.limit,args.repeat,args.check_limit,args.witness)
