"""Fixed fourteen-map experiment. All outputs remain beside this script."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from time import monotonic
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LAB = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(LAB.parent))
sys.path.insert(0, str(HERE))

from audit import load_raw, verify_witness
from packing import build_model
from stowage.io import load_instance
from stowage.rules.catalogue import rule_freeze_v1_enabled


def dump(path, data):
    # Failed checker/LP diagnostics can contain unbounded values. Keep their
    # meaning explicit without emitting invalid JSON; plan scalars stay finite.
    def clean(value):
        if isinstance(value,float) and not math.isfinite(value):
            return repr(value)
        if isinstance(value,dict):
            return {key:clean(item) for key,item in value.items()}
        if isinstance(value,(list,tuple)):
            return [clean(item) for item in value]
        return value
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(clean(data), indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def inputs():
    paths = (LAB / 'sets' / 'quick_medium.txt').read_text().splitlines()[:2]
    paths += (LAB / 'sets' / 'medium12.txt').read_text().splitlines()
    return [path for path in paths if path and not path.startswith('#')]


def code_hashes():
    paths = list((ROOT / 'src/stowage/checker').glob('*.py'))
    paths += list(HERE.glob('*.py'))
    paths += [LAB / 'c2/c2-cgscale/rmp_method.py', LAB / 'c2/c2-cgscale/method.py',
              LAB / 'c2/c2-cgscale/pages.py', LAB / 'c2/c2-benders/ballast.py',
              LAB / 'c1/fx-scout/lash_oracle.py', LAB / 'c1/leader/e9fix.py',
              LAB.parent / 'alns.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def solve_one(path, *, physics_enabled=False, spatial=False):
    from stowage.frozen import active_stubs
    if active_stubs():
        raise ValueError('Active frozen-interface overrides: ordinary checker required')
    started = monotonic()
    raw, digest = load_raw(path)
    instance = load_instance(path)
    model = build_model(instance)
    packing_base = deepcopy(model) if spatial else None
    row = dict(instance_id=instance.id, input=path, input_sha256=digest,
               boxes=len(instance.load_list), status='unknown', physical_proof=False,
               rule_freeze_v1=rule_freeze_v1_enabled(),
               relaxation_status='unknown', packing={})
    if physics_enabled:
        from packing import attach_rows
        row['physics'] = attach_rows(model, instance, gm_lo=0.)
        from physics import second_check_envelope
        row['second_check_envelope'] = second_check_envelope(instance)
    solved = model.solve(time_limit=30.)
    row['packing'] = dict(solver_status=int(solved.status), message=str(solved.message),
                          variables=model.n_variables, rows=len(model.rows),
                          solve_s=solved.wall_s)
    if solved.x is not None:
        witness = solved.witness
        row['packing']['witness'] = witness
        row['packing']['audit'] = verify_witness(raw, witness)
        if not row['packing']['audit']['verified']:
            raise AssertionError(row['packing']['audit'])
        row['relaxation_status'] = 'feasible_with_witness'
    if spatial:
        maximum = float(instance.vessel.hull.max_gm_lashing_m)
        edges = sorted({0., maximum, *[x for x in (.25,.5,.75,1.,1.5,2.,3.) if x<maximum]})
        row['spatial_lashing_gm'] = []
        for lo,hi in zip(edges,edges[1:]):
            branch = deepcopy(packing_base)
            attach_rows(branch, instance, gm_lo=lo,gm_hi=hi)
            result = branch.solve(time_limit=10.)
            entry = dict(interval_m=[lo,hi], solver_status=int(result.status),
                         solve_s=result.wall_s, physical_proof=False,
                         status='unknown', excluded_allocations=branch.metadata['physics']['securing_excluded_allocations'])
            if result.x is not None:
                entry.update(status='relaxation_feasible_with_witness',
                    physical_kg_lower_m=result.witness.get('physical_kg_lower_m'),
                    cargo_vertical_moment_lower_kg_m=result.witness['cargo_vertical_moment_lower_kg_m'])
                if lo<=.6<=hi:
                    entry['witness'] = result.witness
                    row['page_targets'] = result.witness
            row['spatial_lashing_gm'].append(entry)
    # An LP status is deliberately never sufficient to label a map infeasible.
    row['elapsed_s'] = monotonic()-started
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--physics', action='store_true')
    parser.add_argument('--spatial', action='store_true')
    parser.add_argument('--followup', action='store_true')
    parser.add_argument('--budget-s', type=float, default=60.)
    parser.add_argument('--ballast-s', type=float, default=8.)
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--count', type=int)
    parser.add_argument('--out', default='results')
    args = parser.parse_args()
    out = HERE / args.out
    out.resolve().relative_to(HERE)
    out.mkdir(exist_ok=True, parents=True)
    begun = monotonic()
    before = code_hashes()
    dump(out / 'run_metadata.json', dict(pid=os.getpid(), source_sha256=before,
         command=sys.argv, numerical_threads={k:os.environ.get(k) for k in
             ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS')},
         benchmark_marker_present=(LAB/'c1/leader/BENCH-RUNNING').exists(),
         process_limit=1 if (LAB/'c1/leader/BENCH-RUNNING').exists() else 2))
    chosen = inputs()[args.start:None if args.count is None else args.start+args.count]
    maps = []
    for path in chosen:
        instance_id = json.loads(Path(path).read_text())['id']
        target = out / (instance_id+'.json')
        try:
            if args.followup:
                if not target.exists():
                    raise ValueError('Run relaxation before constructive follow-up')
                row = json.loads(target.read_text())
                if 'page_followup' in row:
                    maps.append(row)
                    continue
                from page_followup import run
                result = run(load_instance(path), row.get('page_targets',row['packing']['witness']),
                             budget_s=args.budget_s, ballast_s=args.ballast_s)
                row['page_followup'] = result
                row['elapsed_s'] += result['wall_s']
                if result['full_checker_passed']:
                    row['status'] = 'feasible_with_witness'
            else:
                row = solve_one(path, physics_enabled=args.physics,spatial=args.spatial)
        except Exception as error:
            if not args.followup:
                row = dict(instance_id=instance_id, input=path, status='unknown')
            row.update(error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
        dump(target, row)
        maps.append(row)
        print(json.dumps({k:row.get(k) for k in ('instance_id','status','relaxation_status','elapsed_s','error')}
                         | {'followup': row.get('page_followup',{}).get('full_checker_passed')}), flush=True)
        dump(out/'summary.json', dict(maps=[dict(instance_id=r['instance_id'], status=r['status'],
              relaxation_status=r.get('relaxation_status'), elapsed_s=r.get('elapsed_s'),
              replay_complete=r.get('page_followup',{}).get('replay_complete',False),
              full_checker_passed=r.get('page_followup',{}).get('full_checker_passed',False)) for r in maps],
              wall_s=monotonic()-begun))
    after = code_hashes()
    dump(out/'source_verification.json', dict(changed=[k for k in before if after.get(k)!=before[k]],
                                             source_sha256=after))


if __name__ == '__main__':
    main()
