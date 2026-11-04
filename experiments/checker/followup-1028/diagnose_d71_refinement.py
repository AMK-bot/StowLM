"""Read-only default-budget D71 witness cold/warm replay with stage telemetry.

Preparation is a separate process so builder work cannot warm the first check.
Existing production disk caches are retained; no table/certificate is fabricated.
Timing wrappers call each original function exactly once with unchanged inputs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from functools import wraps
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from time import perf_counter
import tomllib

ROOT = Path(__file__).resolve().parents[3]


def save(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + '\n')


def prepare(output, ship, rung):
    from stowage.generate.background_acceptance import background_instance
    from stowage.model import MovePlan, BallastCondition
    from stowage.vessel.fleet_conditions import empty_background_witness_tank_masses
    path = ROOT / 'experiments/background-1028/d71-part1/measure.py'
    spec = importlib.util.spec_from_file_location('d71_measure_inputs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    started = perf_counter()
    instance = background_instance(module.draw(ship, rung, 7))
    recipe = empty_background_witness_tank_masses(instance.vessel)
    for tank in instance.vessel.tanks:
        if tank.adjustable:
            instance.onboard.tank_masses_kg[tank.id] = recipe.get(tank.id, 0.)
    plan = MovePlan(id='first-check', instance_id=instance.id,
        call_index=instance.call_index, moves=[], ballast=BallastCondition(
            source='planner', tank_masses_kg={tank.id: instance.onboard.tank_masses_kg.get(tank.id, 0.)
                for tank in instance.vessel.tanks if tank.adjustable}))
    (output / 'instance.json').write_text(instance.model_dump_json())
    (output / 'plan.json').write_text(plan.model_dump_json())
    save(output / 'preparation.json', dict(seconds=perf_counter()-started, ship=ship,
        rung=rung, seed=7, source='same first explicit witness as d71-part1/diagnose_witness.py',
        instance_sha256=hashlib.sha256((output/'instance.json').read_bytes()).hexdigest(),
        plan_sha256=hashlib.sha256((output/'plan.json').read_bytes()).hexdigest()))
    print(json.dumps(dict(prepared=str(output), seconds=perf_counter()-started)), flush=True)


def replay(output, samples):
    from stowage.checker import full, stability, stability_liquid, stability_tank_transfer as transfer
    from stowage.checker import stability_liquid_tables as tables
    from stowage.io import load_instance, load_plan
    from stowage.vessel import fleet, fleet_cache
    from stowage.checker import stability_policy
    instance, plan = load_instance(output/'instance.json'), load_plan(output/'plan.json')
    ship = instance.vessel.slice_of or instance.vessel.id.split('.')[0]
    raw = fleet._parameter_bytes(ship)
    params = tomllib.loads(raw.decode())
    manifest = stability_liquid.manifest_path(params)
    original_bytes = ((output/'instance.json').read_bytes(), (output/'plan.json').read_bytes())
    assert os.environ.get('STOWAGE_STABILITY_TIME_BUDGET_S') is None
    assert os.environ.get('STOWAGE_STABILITY_DIRECT') != '1'
    assert os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT') != '1'
    assert os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1') == '1'
    events, stages, tank_ids, current = [], [], {}, {}
    consumables = {tank.id for tank in instance.vessel.tanks
                   if tank.kind in ('fuel_oil', 'diesel_oil', 'fresh_water', 'lube_oil')}
    initial_consumables = sum(instance.onboard.tank_masses_kg.get(tid, 0.) for tid in consumables)

    def phase(context, state):
        if context.call_index != instance.call_index:
            return 'arrival'
        fraction = sum(state.tank_masses_kg.get(tid, 0.) for tid in consumables)/initial_consumables
        return 'departure' if fraction == 1. else 'mid_voyage'

    def install(module, name, tag, hook=None):
        original = getattr(module, name)
        @wraps(original)
        def timed(*args, **kwargs):
            previous_phase = current.get('phase')
            if tag == 'in_port_replay':
                current['phase'] = 'in_port_S0'
            started = perf_counter()
            try:
                value = original(*args, **kwargs)
                if hook is not None:
                    hook(value)
                return value
            finally:
                stages.append(dict(sample=current.get('sample'), phase=current.get('phase'),
                    stage=tag, seconds=perf_counter()-started))
                if tag == 'in_port_replay':
                    current['phase'] = previous_phase
        setattr(module, name, timed)

    original_part = full._run_part
    def part(context, state, name, function):
        current['phase'] = phase(context, state)
        current['part'] = name
        started = perf_counter()
        result = original_part(context, state, name, function)
        events.append(dict(sample=current['sample'], phase=current['phase'], part=name,
            seconds=perf_counter()-started, ran=result.ran, error=result.error,
            violations=[v.rule_id for v in result.violations], metrics={key:value
                for key,value in result.metrics.items() if key in (
                    'geometry_table_used','geometry_table_limit_fallback','liquid_table_used',
                    'liquid_table_queries','liquid_direct_queries','elapsed_s','time_budget_s',
                    'time_budget_exceeded','gm_m','free_surface_method')}))
        print(json.dumps(events[-1]), flush=True)
        return result
    full._run_part = part

    def condition_seen(condition):
        for tank in (*condition.actual_tanks, *condition.tanks):
            if tank.geometry is not None:
                tank_ids[id(tank.geometry)] = tank.id
        events.append(dict(sample=current['sample'], phase=current.get('phase'),
            part='liquid_dispatch', tables=sorted(condition.liquid_tables or {}),
            variable=[tank.id for tank in condition.tanks if tank.variable],
            fixed=[tank.id for tank in condition.tanks if not tank.variable]))
    install(stability, '_canonical_fleet_policy', 'canonical_policy')
    install(stability, '_context', 'hull_context')
    install(stability, 'loading_condition', 'loading_condition', condition_seen)
    install(stability_liquid, 'attach_tables', 'attach_liquid_tables', condition_seen)
    install(full, '_in_port', 'in_port_replay')

    calls = []
    original_transfer = transfer.max_transfer
    @wraps(original_transfer)
    def envelope(solid, heel, trim, *, tolerance_m4):
        key = transfer._canonical_cache_key(solid, float(heel), float(trim), float(tolerance_m4))
        hit = key in transfer._CACHE
        started = perf_counter()
        error = None
        try:
            return original_transfer(solid, heel, trim, tolerance_m4=tolerance_m4)
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            calls.append(dict(sample=current.get('sample'), phase=current.get('phase'),
                tank=tank_ids.get(id(solid),'unknown'), heel=float(heel),trim=float(trim),
                tolerance_m4=float(tolerance_m4), cache_hit=hit, seconds=perf_counter()-started,
                error=error))
    transfer.max_transfer = envelope
    baseline = dict(ship=ship, parameter_sha256=hashlib.sha256(raw).hexdigest(),
        process_cold_disk_tables_retained=True, stability_budget_s=30.,
        canonical_pin_eligible=stability_policy.projected_policy(ship,hashlib.sha256(raw).hexdigest(),
            fleet_cache._cache_key(ship,'full',True,raw),stability_policy.provider_identity()) is not None,
        liquid_manifest=str(manifest), liquid_manifest_exists=manifest.exists(),
        missing_component_tables=[])
    # Certificate existence is recorded before checking. Full qualification
    # validation is intentionally left to the normal production dispatcher.
    from stowage.vessel.tanks import TankGeometry
    baseline['component_files'] = []
    for tank in params['tanks']:
        solid = TankGeometry.from_params(tank, ship_params=params)
        path = tables._path(solid)
        baseline['component_files'].append(dict(tank=tank['id'],table=str(path),
            table_exists=path.exists(), certificate_exists=path.with_suffix('.qualified.json').exists()))
    save(output/'cache-inventory.json', baseline)
    summaries = []
    for sample in range(samples):
        current['sample'] = sample
        current['phase'] = None
        started = perf_counter()
        report = full.check(instance, plan)
        duration = perf_counter()-started
        save(output/f'report-{sample}.json', report.model_dump(mode='json'))
        summary = dict(sample=sample, temperature='process-cold' if sample==0 else 'same-process-warm',
            seconds=duration, passed=report.passed,
            failures=[dict(part=name, error=part.error, violations=[v.rule_id for v in part.violations])
                for name,part in report.parts.items() if part.error or part.violations],
            envelope_cache_size=len(transfer._CACHE), manifest_exists=manifest.exists())
        summaries.append(summary)
        save(output/'timings.json', summaries)
        save(output/'stage-events.json', dict(parts=events, stages=stages, envelope_calls=calls))
        print(json.dumps(summary), flush=True)
    assert original_bytes == ((output/'instance.json').read_bytes(),(output/'plan.json').read_bytes())
    aggregate = defaultdict(lambda:dict(calls=0,hits=0,seconds=0.,errors=0))
    for row in calls:
        value = aggregate[(row['sample'],row['phase'],row['tank'])]
        value['calls'] += 1
        value['hits'] += row['cache_hit']
        value['seconds'] += row['seconds']
        value['errors'] += row['error'] is not None
    save(output/'envelope-summary.json', [dict(sample=key[0],phase=key[1],tank=key[2],**value)
        for key,value in aggregate.items()])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--ship',default='feeder_1700')
    parser.add_argument('--rung',default='small')
    parser.add_argument('--samples',type=int,default=2)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1():
        if args.prepare:
            prepare(args.output,args.ship,args.rung)
        else:
            replay(args.output,args.samples)


if __name__=='__main__':
    main()
