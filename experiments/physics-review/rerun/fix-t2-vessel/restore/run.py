"""Current dual-endpoint finisher campaign on unchanged D27 manual fixtures.

ASSUMPTION: sixty seconds is a bounded repair attempt, never an infeasibility
proof. Historical ballast is merely the initial adjustable-tank state. Cargo,
97% departure consumables and D50 draw-down stay identical to the D56 replay.
Only a subsequent full-checker pass can become a new loading-manual witness.
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

FAILED = [('feeder_500', 'C1'), ('feeder_500', 'C2'), ('feeder_500', 'C3'),
          ('feeder_1000', 'C2'), ('feeder_1700', 'C2'), ('feeder_1700', 'C3')]


def prepare(name, condition):
    from regression import witness_masses
    from stowage.model import MovePlan
    from stowage.vessel.fleet import load_ship_params, load_vessel

    harness = recheck._load_harness()
    source = recheck.REVIEW / f'search_{name}_{condition}.json'
    if source.exists():
        old = json.loads(source.read_text())
        label = (old.get('witness') or old['candidates'][0])['label']
    else:
        if (name, condition) != ('feeder_1700', 'C2'):
            raise FileNotFoundError(source)
        source = recheck.ROOT/'experiments/physics-review/CONVERGENCE.md'
        label = 'DB+HEEL'
    vessel = load_vessel(name)
    initial = witness_masses(vessel, label)
    instance, info = harness.build(name, vessel, load_ship_params(name), condition, sum(initial.values()))
    for box in instance.containers:
        box.max_gross_mass_kg = 30480.
    instance = recheck.attach_voyage(instance)
    instance.onboard.tank_masses_kg.update(initial)
    # Ballast must be absent from the plan: otherwise the public finisher
    # correctly returns 'kept' without attempting any adjustment.
    plan = MovePlan(id=f'{instance.id}.convergence-restore', instance_id=instance.id,
                    call_index=instance.call_index)
    return instance, plan, source, dict(info, initial_ballast_label=label)


def invariant(instance):
    """Content the finisher is not permitted to change."""
    return dict(containers=[box.model_dump(mode='json') for box in instance.containers],
                placements=[p.model_dump(mode='json') for p in instance.onboard.placements],
                consumables={tank.id: instance.onboard.tank_masses_kg.get(tank.id, 0.)
                             for tank in instance.vessel.tanks if not tank.adjustable},
                fixed_loads=[load.model_dump(mode='json') for load in instance.vessel.fixed_loads],
                voyage=instance.voyage.model_dump(mode='json'))


def run_one(name, condition, output, limit, seed):
    from slices import bounded_full_check
    from stowage.solver.ballast import finish_ballast
    from stowage.versions import component_versions

    started = time.perf_counter()
    instance, plan, source, info = prepare(name, condition)
    identity = recheck._input_identity(name, condition, instance, plan, source)
    identity['restore_runner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    identity['time_limit_s'], identity['seed'] = limit, seed
    fingerprint = recheck._digest(identity)
    folder = output/f'{name}_{condition}'
    folder.mkdir(parents=True, exist_ok=True)
    result_path = folder/'result.json'
    if result_path.exists():
        previous = json.loads(result_path.read_text())
        if previous.get('input_fingerprint') != fingerprint:
            raise ValueError(f'stale restore inputs: {result_path}; use fresh --out')
        if previous.get('complete'):
            return previous
    before = invariant(instance)
    (folder/'instance.json').write_text(instance.model_dump_json(indent=2)+'\n')
    (folder/'input-plan.json').write_text(plan.model_dump_json(indent=2)+'\n')
    outcome = finish_ballast(instance, plan, time_limit_s=limit, seed=seed)
    assert invariant(instance) == before, 'finisher mutated fixed cargo/consumables'
    row = dict(ship=name, condition=condition, info=info, input_identity=identity,
               input_fingerprint=fingerprint, invariant_sha256=recheck._digest(before),
               components=component_versions(), outcome=outcome.model_dump(mode='json'),
               input_provenance=instance.provenance.model_dump(mode='json'), complete=False)
    result_path.write_text(json.dumps(row, indent=2)+'\n')
    print(json.dumps(dict(ship=name, condition=condition, status=outcome.status,
                         finisher_s=outcome.wall_time_s, iterations=outcome.iterations)), flush=True)
    if outcome.plan is not None:
        assert outcome.plan.moves == plan.moves
        checked = bounded_full_check(instance, outcome.plan)
        row.update(check=checked, phase_reports=recheck._phase_reports(checked, instance.voyage))
        (folder/'finished-plan.json').write_text(outcome.plan.model_dump_json(indent=2)+'\n')
    after = recheck._input_identity(name, condition, instance, plan, source)
    row['source_drift'] = sorted(key for key in identity['files'].keys() | after['files'].keys()
                                if identity['files'].get(key) != after['files'].get(key))
    if identity['instance_sha256'] != after['instance_sha256']:
        raise ValueError('restore fixture changed during finisher')
    row.update(complete=True, wall_s=time.perf_counter()-started,
               passing_witness=not row['source_drift'] and row.get('check', {}).get('passed') is True,
               peak_parent_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9)
    result_path.write_text(json.dumps(row, indent=2)+'\n')
    print(json.dumps(dict(ship=name, condition=condition, witness=row['passing_witness'],
                         wall_s=row['wall_s'], drift=row['source_drift'])), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ship')
    parser.add_argument('--condition')
    parser.add_argument('--limit', type=float, default=60.)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--out', type=Path, default=HERE/'unchanged')
    args = parser.parse_args()
    if bool(args.ship) != bool(args.condition):
        parser.error('--ship and --condition must be specified together')
    args.out.mkdir(parents=True, exist_ok=True)
    cases = [(args.ship, args.condition)] if args.ship else FAILED
    rows = []
    started = time.perf_counter()
    try:
        for name, condition in cases:
            rows.append(run_one(name, condition, args.out, args.limit, args.seed))
            (args.out/'summary.json').write_text(json.dumps(dict(
                rows=rows, elapsed_s=time.perf_counter()-started,
                witnesses=sum(row['passing_witness'] for row in rows)), indent=2)+'\n')
    finally:
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()  # Only the process handle created by this campaign.


if __name__ == '__main__':
    main()
