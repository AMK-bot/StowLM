"""Full-physics A* yardstick on declared fleet hulls and unmodified limits.

Controlled cases contain ordinary empty dry twenty-foot cargo at exact tare;
this changes the cargo distribution, never the hull's strength/stability limits.
Tiny cases have two targets, small cases thirty. The synthetic half-limit hold
background is the existing fleet-slice scenario, retained without rebalance.
Random cases retain the generator's original mix and six/thirty-six targets.
All output, numerical caches and instances stay in this experiment directory.
A failed input preflight is recorded, never bypassed by stale cached physics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

ROOT = Path('experiments/exact-solver')


def configure_caches():
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(ROOT/'cache/vessels')
    # The linear cache has no public directory setting. This changes storage
    # only; no equations, coefficients, sampling or tolerances are modified.
    import stowage.linear.cache as cache
    cache._DEFAULT_ROOT = ROOT/'cache/linear'


def prepare(seed: int, size: str, *, controlled: bool):
    from stowage.generate.random_yard import generate_random_instance
    from stowage.model import Instance
    instance = generate_random_instance(size, seed=seed, blocking_ratio=0.)
    if not controlled:
        return instance
    n = 2 if size == 'tiny' else 30
    data = instance.model_dump()
    if size == 'tiny':
        # Keep the two original one-tier deck stacks, with real whole hull,
        # tanks, fixed slice cargo and original stack/cell capability fields.
        stacks = [s for s in data['vessel']['stacks'] if s['on_deck']]
        cells = {c for s in stacks for c in s['cell_ids']}
        data['vessel']['stacks'] = stacks
        data['vessel']['cells'] = [c for c in data['vessel']['cells'] if c['id'] in cells]
        for cover in data['vessel']['hatch_covers']:
            cover['hold_stack_ids'] = []
    boxes = data['containers'][:n]
    for box in boxes:
        box.update(iso_type='22G1', length_ft=20, height_m=2.591,
                   tare_kg=2200., gross_mass_kg=2200., empty=True,
                   reefer=False, reefer_setpoint_c=None, dg=None, oog=None,
                   special_stowage=[], pod=data['rotation']['calls'][1]['locode'],
                   category='controlled:dry20:2200', weight_class='empty2200')
    data['containers'] = boxes
    data['load_list'] = [b['id'] for b in boxes]
    block = data['port']['yard']['blocks'][0]
    # Original yard dimensions/capacity retained; each target is ground-accessible.
    positions = [(bay,row) for bay in range(1,block['n_bays']+1)
                 for row in range(1,block['n_rows']+1)]
    data['yard']['stacks'] = [dict(key=f"{block['id']}-{bay:03d}-{row:02d}",
                                   length_ft=20, boxes=[box['id']])
                              for box,(bay,row) in zip(boxes,positions)]
    data['rule_overrides'] = {'expert.e6.heavy_under_deck': {'heavy_threshold':30000.},
                              'expert.e1.heavy_bottom': {'heavy_threshold':30000.},
                              'expert.e2.weight_order': {'tolerance':0.}}
    data['id'] = f'exact.fullhull.controlled.{size}.{seed}'
    return Instance.model_validate(data)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size',choices=('tiny','small'),default='tiny')
    parser.add_argument('--count',type=int,default=1)
    parser.add_argument('--start',type=int,default=0)
    parser.add_argument('--time-limit',type=float,default=20.)
    parser.add_argument('--random',action='store_true')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--oracle',action='store_true')
    parser.add_argument('--out',type=Path,default=ROOT/'full-hull.jsonl')
    args=parser.parse_args()
    if args.count < 1 or args.start < 0 or args.time_limit <= 0:
        parser.error('count/time-limit must be positive and start nonnegative')
    configure_caches()
    from stowage.vessel.fleet import load_ship_params
    params=load_ship_params('feeder_500')
    if params['hull_form']['family']!='stowlm_offsets_v1':
        error={'status':'INPUT_BLOCKED','reason':'canonical fleet hull is not checksum-pinned public offsets',
               'family':params['hull_form']['family']}
        (ROOT/'full-hull-preflight.json').write_text(json.dumps(error,indent=2)+'\n')
        raise RuntimeError(error)
    from stowage.solver.exact import solve_exact
    from stowage.io import save_instance
    from stowage.bounds.bruteforce import brute_force_optimum
    sources = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
               for name in ('src/stowage/solver/exact.py',
                            'src/stowage/solver/exact_engine.py')}
    done = {json.loads(line).get('key') for line in args.out.read_text().splitlines()} if args.out.exists() else set()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for seed in range(args.start,args.start+args.count):
        instance=prepare(seed,args.size,controlled=not args.random)
        folder=ROOT/'instances'/('random' if args.random else 'controlled')/args.size
        folder.mkdir(parents=True,exist_ok=True)
        save_instance(instance,folder/f'seed-{seed:08d}.json')
        if args.prepare_only:
            print(instance.id,flush=True)
            continue
        input_sha = hashlib.sha256(instance.model_dump_json().encode()).hexdigest()
        keys = {default: hashlib.sha256(json.dumps(dict(
                    input_sha256=input_sha, sources=sources,
                    time_limit_s=args.time_limit, default=default),
                    sort_keys=True).encode()).hexdigest() for default in (False, True)}
        pending = [default for default in (False, True) if keys[default] not in done]
        if not pending:
            continue
        oracle=None
        if args.oracle:
            t=brute_force_optimum(instance,time_limit_s=args.time_limit,check_static_rules=True)
            oracle=dict(status=t.status,objective=t.objective,wall_time_s=t.wall_time_s)
        for default in pending:
            started=perf_counter()
            result=solve_exact(instance,time_limit_s=args.time_limit,num_workers=1,
                               check_physics=True,default_relocations_only=default)
            row=dict(key=keys[default], input_sha256=input_sha,
                     instance_id=instance.id,size=args.size,controlled=not args.random,
                     seed=seed,default_relocations_only=default,time_limit_s=args.time_limit,
                     status=result.status,objective=result.objective,lower_bound=result.lower_bound,
                     gap=result.gap,expanded_nodes=result.expanded_nodes,
                     wall_time_s=result.wall_time_s,total_wall_time_s=perf_counter()-started,
                     metrics=result.metrics,oracle=oracle,
                     solver_sources_sha256=sources)
            with args.out.open('a') as stream:stream.write(json.dumps(row,sort_keys=True)+'\n')
            print(json.dumps(row),flush=True)
            if result.status=='OPTIMAL' and oracle and oracle['status']=='optimal':
                assert result.objective==oracle['objective'],row


if __name__=='__main__':main()
