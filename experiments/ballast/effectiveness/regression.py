"""Rebuild the reviewer's eight witness-reserved conditions on current ships.

The declaration of ballast reserve belongs to the loading condition. Witness
fills are checked separately and are never supplied to the ballast finisher.
Historical labels round a binary-bisection fill to two decimals (0.38 means
0.375 and 0.88 means 0.875); recover that binary value before rebuilding.
The old forward-peak zone is now FPT plus FWD-DEEP; apply its historical fill to
both current zones (CONVERGENCE.md section 5 item 9, a physical subdivision).
Source: physics-review/convergence/post_d30/search_ballast.py and harness2.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from stowage.conventions import canonical_vessel_id
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
REVIEW = ROOT/'experiments/physics-review/convergence/post_d30'
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'


def witness_masses(vessel, label):
    pieces = label.split('+')
    prefixes = {'DB': ('DB-',), 'HEEL': ('HEEL',), 'WING-F': ('WING-F',), 'WING': ('WING',)}
    full = [prefix for piece in pieces if piece in prefixes for prefix in prefixes[piece]]
    peaks = {piece[:3]: round(float(piece[3:])*32)/32 for piece in pieces
             if piece[:3] in ('FPT', 'APT')}
    if 'FPT' in peaks:
        peaks['FWD-DEEP'] = peaks['FPT']
    return {tank.id: tank.capacity_m3*tank.content_density_kg_m3*
            (1. if any(tank.id.startswith(prefix) for prefix in full)
             else next((fill for prefix, fill in peaks.items() if tank.id.startswith(prefix)), 0.))
            for tank in vessel.tanks if tank.adjustable}


def run_one(source, out, limit):
    sys.path.insert(0, str(REVIEW))
    import harness2
    import stowage.vessel.fleet as fleet
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.io import save_instance, save_plan
    from stowage.solver.ballast import finish_ballast
    from stowage.solver.ballast_deadline import shutdown_worker
    from stowage.versions import component_versions
    from slices import bounded_full_check

    old = json.loads(source.read_text())
    witness = old.get('witness')
    if witness is None:
        return None
    name, cond = canonical_vessel_id(old['ship']), old['condition']
    folder = out/f'{name}_{cond}'
    folder.mkdir(parents=True, exist_ok=True)
    vessel = fleet.load_vessel(name, with_hull=True)
    params = fleet.load_ship_params(name)
    masses = witness_masses(vessel, witness['label'])
    started = time.perf_counter()
    inst, info = harness2.build(name, vessel, params, cond, sum(masses.values()))
    plan = MovePlan(id=f'{inst.id}.regression', instance_id=inst.id, call_index=inst.call_index)
    witness_plan = plan.model_copy(update={'ballast': BallastCondition(tank_masses_kg=masses, source='planner')})
    rec = dict(vessel=name, condition=cond, components=component_versions(), info=info,
               source=str(source.relative_to(ROOT)), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
               vessel_sha256=hashlib.sha256(vessel.model_dump_json().encode()).hexdigest(),
               witness_label=witness['label'], witness_masses_kg=masses,
               witness_mapping='Historical FPT fill applied to current FPT and FWD-DEEP zones; two-decimal labels recovered to nearest 1/32',
               historical_witness_passed=witness['passed'], build_s=time.perf_counter()-started,
               limit_s=limit, calls=[])
    save_instance(inst, folder/'instance.json')
    save_plan(plan, folder/'input-plan.json')
    save_plan(witness_plan, folder/'witness-plan.json')
    rec['witness_check'] = bounded_full_check(inst, witness_plan)
    (folder/'result.json').write_text(json.dumps(rec, indent=2)+'\n')
    print(json.dumps(dict(phase='witness', vessel=name, condition=cond,
                         passed=rec['witness_check']['passed'], wall_s=rec['witness_check']['wall_s'])), flush=True)
    completed = []
    for repeat in range(2):
        started = time.perf_counter()
        result = finish_ballast(inst, plan, time_limit_s=limit, seed=0)
        rec['calls'].append(dict(repeat=repeat, warm=bool(repeat), wall_s=time.perf_counter()-started,
                                 outcome=result.model_dump(mode='json', exclude={'plan'})))
        if result.plan is not None:
            save_plan(result.plan, folder/f'finished-{repeat}.json')
            completed.append((repeat, result.plan))
        (folder/'result.json').write_text(json.dumps(rec, indent=2)+'\n')
        print(json.dumps(dict(phase='call', vessel=name, condition=cond, repeat=repeat,
                              wall_s=rec['calls'][-1]['wall_s'], status=result.status)), flush=True)
    for repeat, final in completed:
        checked = bounded_full_check(inst, final)
        rec['calls'][repeat].update(full_checker=checked, full_checker_passed=checked['passed'],
                                   full_checker_s=checked['wall_s'])
        (folder/'result.json').write_text(json.dumps(rec, indent=2)+'\n')
    shutdown_worker()
    return rec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=HERE/'regression_results')
    parser.add_argument('--limit', type=float, default=10.)
    parser.add_argument('--ships', default='feeder_500,feeder_800,feeder_1700')
    parser.add_argument('--conditions', default='C1,C2,C3')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = []
    for source in sorted(REVIEW.glob('search_feeder_*_C*.json')):
        old = json.loads(source.read_text())
        if canonical_vessel_id(old['ship']) not in {canonical_vessel_id(ship) for ship in args.ships.split(',')} or old['condition'] not in args.conditions.split(','):
            continue
        try:
            row = run_one(source, args.out, args.limit)
        except Exception as exc:
            import traceback
            row = dict(source=str(source), error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
        if row is not None:
            rows.append(row)
            (args.out/'results.json').write_text(json.dumps(rows, indent=2)+'\n')
    print(json.dumps(dict(phase='resources', elapsed_s=time.perf_counter()-started,
                         peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9)), flush=True)


if __name__ == '__main__':
    main()
