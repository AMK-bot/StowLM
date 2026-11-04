"""Fixed two-map TRAIN scale test of unchanged certified.lower_bound.

The two gapped feeder500 TRAIN maps, 120 seconds each, are declared before this
run. No validation benchmark is read or used to select a setting.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from time import monotonic

from catalog import ROOT, load_set
from certified import lower_bound
from stowage.io import load_instance

HERE = Path(__file__).resolve().parent
TARGETS = ('D192.medium-feeder_500-f60-high-s192200000',
           'D192.medium-feeder_500-f60-low-s192200000')
SCOPE = ('fewest lifts among plans the checker passes, counting later ports by '
         'our D15 projection (fixed stow, no later loads)')
DEPENDENCIES = ('certified.py','bound.py','bound_joint.py','lazy_bound.py',
                'strengthening.py','geometry_cuts.py','securing_cuts.py',
                'bound_securing.py')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget-s',type=float,default=120.)
    parser.add_argument('--out',required=True)
    parser.add_argument('--only',choices=TARGETS)
    args=parser.parse_args()
    folder=Path(args.out).resolve()
    if not folder.is_relative_to(HERE):
        raise ValueError('Output must remain in private experiment folder')
    folder.mkdir(parents=True,exist_ok=True)
    index=json.loads((HERE/'medium-references/upper_index.json').read_text())
    paths=load_set(str(HERE/'medium.txt'))
    config=dict(budget_s=args.budget_s,seed=0,numerical_threads=1,
                targets=list(TARGETS),source_sha256=digest(__file__),
                dependencies={name:digest(HERE/name) for name in DEPENDENCIES},
                benchmark_running=(HERE.parents[1]/'c1/leader/BENCH-RUNNING').exists())
    started_all=monotonic()
    rows=[]
    for rel in paths:
        instance=load_instance(ROOT/rel)
        if instance.id not in TARGETS or (args.only and instance.id!=args.only):
            continue
        upper=index[instance.id]
        if digest(ROOT/rel)!=upper['input_sha256']:
            raise AssertionError('Copied upper does not match input')
        initial_path=HERE/'medium-results/probe'/f'{instance.id}.json'
        initial=json.loads(initial_path.read_text())
        if initial['input_sha256']!=upper['input_sha256']:
            raise AssertionError('Initial corrected medium certificate mismatch')
        output=folder/f'{instance.id}.json'
        if output.exists():
            previous=json.loads(output.read_text())
            if previous['config']!=config or previous['input_sha256']!=upper['input_sha256']:
                raise ValueError('Cannot resume changed settings/source/input')
            rows.append(previous)
            continue
        print(instance.id,'certified joint',args.budget_s,'seconds',flush=True)
        started=monotonic()
        result=asdict(lower_bound(instance,budget_s=args.budget_s,seed=0,
                                  upper_bound=upper['lifts']))
        elapsed=monotonic()-started
        combined=max(initial['lower_bound'],result['lower_bound'])
        if combined>upper['lifts']+1e-7:
            raise AssertionError('Combined bound exceeds checked reference upper')
        row=dict(instance_id=instance.id,input=rel,input_sha256=upper['input_sha256'],
                 config=config,result=result,wall_s=elapsed,
                 lower_bound=combined,upper_bound=upper['lifts'],
                 gap=upper['lifts']-combined,
                 equality=abs(upper['lifts']-combined)<1e-7,
                 initial_lower_bound=initial['lower_bound'],
                 initial_evidence=str(initial_path.relative_to(ROOT)),
                 initial_evidence_sha256=digest(initial_path),
                 upper_evidence=upper['evidence'],
                 upper_evidence_sha256=upper['evidence_sha256'],
                 proof_scope=SCOPE,load_once_restriction=False,
                 note='Exact inherited first-aboard-order/lazy final-state model; fresh full-checker upper admission is separate.')
        output.write_text(json.dumps(row,indent=2)+'\n')
        rows.append(row)
        print(instance.id,'LB',combined,'UB',upper['lifts'],result['status'],
              'static witness',result['static_legal_witness'],
              'seconds',round(elapsed,3),flush=True)
    summary=dict(maps=len(rows),equality=sum(r['equality'] for r in rows),
                 seconds=sum(r['wall_s'] for r in rows),
                 evaluation_elapsed_s=monotonic()-started_all,
                 lower_bounds=[r['lower_bound'] for r in rows],
                 upper_bounds=[r['upper_bound'] for r in rows],config=config)
    (folder/'SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
