"""D71 round2: current-source, resumable background/S0 diagnostics.

This measures fixed backgrounds, never reference-solver plan binding shares or
production-generator yield. Full-empty ships receive berth S0 only, not an
empty seagoing certificate. No checker or finisher functions are patched.
Run only after compute admission and the intended source/parameter freeze.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import time

from stowage.generate.background_acceptance import evaluate_background
from stowage.generate.physics_identity import current_physics_identity
from stowage.generate.random_yard import _tiny_region
from stowage.generate.random_voyage import build_voyage_spec
from stowage.model import Instance
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import ShipState
from stowage.model.yard import YardState
from stowage.params import repo_root
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_vessel, _parameter_bytes
from stowage.vessel.fleet_berth import ARRIVAL_PREFIX, prepare_arrival
from stowage.vessel.fleet_conditions import departure_tank_masses, slice_condition
from stowage.vessel.fleet_slices import recondition_slice, slice_preset

FORMAT = 'd71-background-s0-round2-v1'


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_identity():
    """Read all production code/parameters; mtime cannot hide changed bytes."""
    root = repo_root()
    sources = {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
               for directory in (root/'src'/'stowage', root/'params')
               for path in sorted(directory.rglob('*'))
               if path.is_file() and path.suffix in {'.py', '.toml', '.json'}}
    return {'sha256': digest(sources), 'files': sources}


def changed_paths(before, after):
    a, b = before.get('files', {}), after.get('files', {})
    return sorted(key for key in a.keys() | b.keys() if a.get(key) != b.get(key))


def physics_identity():
    """Retain the gate's full content binding, including qualified table inputs."""
    try:
        identity = current_physics_identity()
        environment = identity.pop('environment', {})
        identity['environment_keys'] = sorted(environment)
        identity['environment_sha256'] = digest(environment)
        return identity
    except Exception as error:
        return {'error': f'{type(error).__name__}: {error}'}


def json_value(value):
    """JSON has no NaN/Infinity; missing numerical readouts stay explicit nulls."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def state_diagnostics(instance, metrics, *, in_port=False, phase=None):
    """Typed display margins from reported metrics; never a checker verdict."""
    hull = instance.vessel.hull
    rows = []
    def criterion(name, measured, limit, *, lower=False, strict=False):
        if not finite(measured) or not finite(limit):
            return
        margin = measured-limit if lower else limit-measured
        scale = abs(limit)
        rows.append(dict(name=name, measured=measured, limit=limit, lower_bound=lower,
                         strict=strict, margin=margin,
                         fractional_margin=margin/scale if scale else None))
    criterion('gm_floor_m', metrics.get('gm_m'), .15 if in_port else hull.min_gm_m, lower=True)
    criterion('list_rad', abs(metrics.get('list_rad', float('nan'))), hull.max_list_rad)
    if in_port:
        criterion('berth_trim_m', abs(metrics.get('trim_m', float('nan'))), hull.lpp_m*math.tan(math.radians(1.)))
        criterion('P1_equilibrium_residual', metrics.get('equilibrium_residual'), 2e-6)
        criterion('open_hatch_immersion_rad', metrics.get('angle_open_hatch_immersion_rad'), hull.max_list_rad,
                  lower=True, strict=True)
    else:
        criterion('trim_max_m', metrics.get('trim_m'), hull.max_trim_m)
        criterion('trim_min_m', metrics.get('trim_m'), hull.min_trim_m, lower=True)
        criterion('displacement_kg', metrics.get('displacement_kg'), hull.max_displacement_kg)
        criterion('forward_draught_floor_m', metrics.get('draught_fwd_m'), hull.min_draught_fwd_m, lower=True)
        criterion('D56_lashing_gm_cap_m', metrics.get('gm_m'), hull.max_gm_lashing_m)
        criterion('B23_ratio', metrics.get('b23_ratio'), 1., lower=True)
        if phase == 'arrival':
            criterion('D58_B23_arrival_design_ratio', metrics.get('b23_ratio'), 1.05, lower=True)
        criterion('weather_work_ratio', metrics.get('weather_ratio'), 1., lower=True)
        if finite(metrics.get('draught_mean_m')) and metrics['draught_mean_m'] > 0:
            criterion('weather_B_over_d_applicability', hull.beam_m/metrics['draught_mean_m'], 3.5, strict=True)
    for key in ('draught_aft_m', 'draught_fwd_m'):
        criterion(key, metrics.get(key), hull.max_draught_m)
    if instance.port.max_draught_m is not None:
        criterion('berth_draught_m', max(metrics.get('draught_aft_m', float('nan')),
                  metrics.get('draught_fwd_m', float('nan'))), instance.port.max_draught_m)
    comparable = [row for row in rows if finite(row['fractional_margin'])]
    return dict(criteria=rows, smallest_display_fractional_margin=min(comparable,
        key=lambda row: row['fractional_margin']) if comparable else None,
        interpretation='Diagnostic reported-metric margins only. Full-checker verdict and violation IDs remain authoritative; this is not D71 reference-plan binding coverage.')


def draw(ship,rung,seed):
    parent=load_vessel(ship)
    v=parent if rung=='full' else _tiny_region(parent) if rung=='tiny' else slice_preset(parent,rung)
    if rung!='full':
        mass={'tiny':50_000.,'small':250_000.,'medium':{
            'feeder_500':1_917_238.3810685035,'feeder_800':3_734_513.4862812967,
            'feeder_1700':6_914_017.2082863245}[ship]}[rung]
        v=recondition_slice(v,expected_load_mass_kg=mass,seed=seed)
    port=load_port('laem_chabang_like')
    rotation=Rotation(service='d71-in-port-background',calls=[
        PortCall(locode=port.locode,archetype=port.archetype,index=i) for i in(0,1)])
    return Instance(id=f'd71.{ship}.{rung}.{seed}',stage='A',size_class=rung,seed=seed,
        vessel=v,port=port,rotation=rotation,call_index=0,containers=[],load_list=[],
        yard=YardState(yard_id=port.yard.id),onboard=ShipState(vessel_id=v.id,
            tank_masses_kg=departure_tank_masses(v)),voyage=build_voyage_spec(rotation,0,port,seed=seed))


def measure(ship, rung, seed, *, time_limit_s, use_cache):
    started = time.perf_counter()
    row = dict(format=FORMAT, ship=ship, rung=rung, seed=seed,
        phase='current_landed_parameter_replay', script_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        protocol='Synthetic fixed-background diagnostic; no production yield or reference-solver plan coverage claim',
        pid=os.getpid(), stages_s={}, requested_gate_time_limit_s=time_limit_s, requested_use_cache=use_cache)
    stages = row['stages_s']
    source_start = time.perf_counter()
    before = source_identity()
    stages['sources_before'] = time.perf_counter()-source_start
    row['sources_before'] = before
    row['parameter_sha256_before'] = sha256(_parameter_bytes(ship)).hexdigest()
    try:
        phase = time.perf_counter()
        instance = draw(ship, rung, seed)
        stages['build'] = time.perf_counter()-phase
        condition = slice_condition(instance.vessel)
        row['condition'] = condition
        phase = time.perf_counter()
        prepared = prepare_arrival(instance)
        stages['prepare_arrival'] = time.perf_counter()-phase
        phase = time.perf_counter()
        row['physics_before_gate'] = physics_identity()
        stages['physics_before_gate'] = time.perf_counter()-phase
        phase = time.perf_counter()
        result = evaluate_background(instance, arrival_instance=prepared,
            time_limit_s=time_limit_s, use_cache=use_cache)
        stages['gate'] = time.perf_counter()-phase
        row['result'] = asdict(result)
        phase = time.perf_counter()
        row['physics_after_gate'] = physics_identity()
        stages['physics_after_gate'] = time.perf_counter()-phase
        tanks = {tank.id: tank for tank in instance.vessel.tanks}
        adjustable = {key for key, tank in tanks.items() if tank.adjustable}
        def fills(masses):
            return {key: dict(mass_kg=mass, fraction=mass/(tanks[key].capacity_m3*tanks[key].content_density_kg_m3))
                    for key, mass in masses.items() if key in adjustable}
        baseline, arrival = instance.onboard.tank_masses_kg, prepared.onboard.tank_masses_kg
        row.update(arrival_fills=fills(arrival), booking_fills=fills(baseline),
            witness_fills=fills(result.witness_tank_masses_kg),
            nominal_booking_budget=(condition or {}).get('booking_departure_budget'),
            booking_reference_ballast_sizing=(condition or {}).get('booking_reference_ballast_sizing'),
            empty_witness_sizing=(condition or {}).get('empty_background_witness_sizing'),
            berth_added_ballast_kg=sum(arrival.get(key, 0.)-baseline.get(key, 0.) for key in adjustable),
            berth_tank_changes_kg={key: arrival.get(key, 0.)-baseline.get(key, 0.)
                for key in set(arrival)|set(baseline) if arrival.get(key, 0.) != baseline.get(key, 0.)},
            nonadjustable_contents_preserved=all(arrival.get(key, 0.) == baseline.get(key, 0.)
                for key, tank in tanks.items() if not tank.adjustable),
            heel_pairs=[pair.model_dump(mode='json') for pair in instance.vessel.tank_pairs],
            lashing_gm_cap_m=instance.vessel.hull.max_gm_lashing_m,
            half_berth_trim_limit_m=instance.vessel.hull.lpp_m*math.tan(math.radians(1.))/2,
            maximum_displacement_kg=instance.vessel.hull.max_displacement_kg,
            empty_initial_displacement_kg=instance.vessel.hull.lightship_mass_kg+
                sum(load.mass_kg for load in instance.vessel.fixed_loads)+sum(baseline.values()),
            departure_nonadjustable_mass_kg=sum(baseline.get(key, 0.) for key, tank in tanks.items() if not tank.adjustable),
            preparation_audit=[json.loads(source[len(ARRIVAL_PREFIX):])
                for source in prepared.provenance.parents if source.startswith(ARRIVAL_PREFIX)],
            diagnostic_margins={phase: state_diagnostics(instance, metrics, phase=phase)
                for phase, metrics in result.states.items()},
            arrival_s0_diagnostics=state_diagnostics(prepared, result.in_port.get('metrics', {}), in_port=True),
            arrival_s0_p1_p6=dict(ran=result.in_port.get('ran'), error=result.in_port.get('error'),
                metrics=result.in_port.get('metrics', {}),
                passed=bool(result.in_port.get('ran') and not result.in_port.get('error') and
                    not any(reason.startswith('arrival_in_port/') for reason in result.reasons))))
        row['certificate_scope'] = 'arrival_s0_only' if rung == 'full' else 'arrival_s0_and_empty_background_D57'
    except Exception as error:
        row['error'] = f'{type(error).__name__}: {error}'
    phase = time.perf_counter()
    after = source_identity()
    stages['sources_after'] = time.perf_counter()-phase
    row['sources_after'] = after
    row['parameter_sha256_after'] = sha256(_parameter_bytes(ship)).hexdigest()
    changes = changed_paths(before, after)
    pi, pf = row.get('physics_before_gate', {}), row.get('physics_after_gate', {})
    physics_changed = bool(pi.get('sha256') and pf.get('sha256') and pi['sha256'] != pf['sha256'])
    gate_changed = 'background.physics_input.changed' in row.get('result', {}).get('reasons', [])
    unavailable = 'error' in pi or 'error' in pf
    row['input_changes'] = dict(source_paths=changes, physics_identity_changed=physics_changed,
                                physics_identity_unavailable=unavailable,
                                gate_reported_changed=gate_changed)
    row['classification'] = ('source_inputs_changed' if changes else 'physics_inputs_changed'
        if physics_changed or gate_changed else 'physics_inputs_unavailable' if unavailable else 'execution_error' if 'error' in row else
        'accepted' if row.get('result', {}).get('accepted') else 'rejected')
    row['first_trial_diagnostics'] = dict(available=False,
        reason='Gate public result exposes final verdict/states, not individual finisher trials. No duplicate checker invocation or monkeypatch is used.')
    row['elapsed_s'] = time.perf_counter()-started
    return row


def summary(paths):
    """Summarise completed rows without running any numerical physics."""
    rows = {}
    for path in paths:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            rows[(row['ship'], row['rung'], row['seed'])] = row
    print('| Ship | Rung | Verdict | S0 | Arrival B23 | GM min–max m | Witness t | Booking headroom t | Berth added t | Gate s |')
    print('|---|---|---|---|---:|---:|---:|---:|---:|---:|')
    for (ship, rung, _), row in sorted(rows.items()):
        result = row.get('result', {})
        states = result.get('states', {})
        gm = [state['gm_m'] for state in states.values() if finite(state.get('gm_m'))]
        witness = sum(item['mass_kg'] for item in row.get('witness_fills', {}).values())/1000
        budget = row.get('nominal_booking_budget') or {}
        headroom = budget.get('remaining_displacement_headroom_kg')
        ratio = states.get('arrival', {}).get('b23_ratio')
        def fmt(value, digits=3):
            return f'{value:.{digits}f}' if finite(value) else '—'
        gm_text = f'{min(gm):.3f}–{max(gm):.3f}' if gm else '—'
        print(f"| {ship} | {rung} | {row.get('classification')} | {row.get('arrival_s0_p1_p6', {}).get('passed')} | {fmt(ratio)} | {gm_text} | {fmt(witness) if rung != 'full' else '—'} | {fmt(headroom/1000 if finite(headroom) else None)} | {fmt(row.get('berth_added_ballast_kg', 0.)/1000)} | {fmt(row.get('stages_s', {}).get('gate'), 2)} |")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ships', nargs='+', default=['feeder_500', 'feeder_800', 'feeder_1700'])
    parser.add_argument('--rungs', nargs='+', choices=['tiny', 'small', 'medium', 'full'], default=['tiny', 'small', 'medium', 'full'])
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--draws', type=int, default=1)
    parser.add_argument('--time-limit-s', type=float, default=10.)
    parser.add_argument('--use-cache', action='store_true', help='Default is an uncached gate, so every cell is actually checked')
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('results.jsonl'))
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    parser.add_argument('--summary-only', nargs='+', type=Path, metavar='JSONL')
    args = parser.parse_args()
    if args.summary_only:
        summary(args.summary_only)
        return
    if args.draws < 1 or args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error('positive draws/shard-count and 0 <= shard-index < shard-count are required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Give each concurrent shard its own append-only file.
    output = args.output if args.shard_count == 1 else args.output.with_name(
        f'{args.output.stem}.shard-{args.shard_index}{args.output.suffix}')
    old = [json.loads(line) for line in output.read_text().splitlines()] if output.exists() else []
    script_sha = sha256(Path(__file__).read_bytes()).hexdigest()
    with rule_freeze_v1():
        for ship_index, ship in enumerate(args.ships):
            if ship_index % args.shard_count != args.shard_index:
                continue
            for rung in args.rungs:
                for seed in range(args.seed, args.seed+(1 if rung == 'full' else args.draws)):
                    identity = source_identity()['sha256']
                    # Mandatory pins and external qualification/table identity are
                    # checked before any resume hit, just as in the public gate.
                    current_physics = physics_identity()
                    physics_sha = current_physics.get('sha256')
                    if physics_sha and any(row.get('format') == FORMAT and row.get('ship') == ship and row.get('rung') == rung
                           and row.get('seed') == seed and row.get('script_sha256') == script_sha
                           and row.get('sources_before', {}).get('sha256') == identity
                           and row.get('physics_after_gate', {}).get('sha256') == physics_sha
                           and row.get('requested_gate_time_limit_s') == args.time_limit_s
                           and row.get('requested_use_cache') == args.use_cache
                           and row.get('classification') in {'accepted', 'rejected'} for row in old):
                        print(json.dumps(dict(ship=ship, rung=rung, seed=seed, resumed=True)), flush=True)
                        continue
                    row = measure(ship, rung, seed, time_limit_s=args.time_limit_s, use_cache=args.use_cache)
                    with output.open('a') as stream:
                        stream.write(json.dumps(json_value(row), sort_keys=True, default=str, allow_nan=False)+'\n')
                        stream.flush()
                    old.append(row)
                    print(json.dumps(dict(ship=ship, rung=rung, seed=seed, classification=row['classification'],
                        elapsed_s=row['elapsed_s'], gate_s=row['stages_s'].get('gate'),
                        reasons=row.get('result', {}).get('reasons'), error=row.get('error'))), flush=True)


if __name__ == '__main__':
    main()
