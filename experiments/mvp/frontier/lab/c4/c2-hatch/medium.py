"""Medium scaling: safe aggregate/Hall certificate plus frozen packing bound.

The public certified.lower_bound interface and implementation are unchanged.
No validation benchmark result selects any setting in this wrapper.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from time import monotonic

from catalog import ROOT, load_set
from stowage.feasibility.tables import build_tables
from stowage.io import load_instance

import bound
import medium_layout
import medium_ports

HERE = Path(__file__).resolve().parent
SCOPE = ('fewest lifts among plans the checker passes, counting later ports by '
         'our D15 projection (fixed stow, no later loads)')


def lower_bound(instance, *, budget_s=60., tables=None, seed=0, upper_bound=None,
                aggregate_budget_s=10.):
    """Max independently valid relaxations; retain source interface separately.

    A nominal total time budget includes model construction, which the frozen
    implementation cannot interrupt. Its actual elapsed time is always recorded.
    """
    started = monotonic()
    T = tables if tables is not None else build_tables(instance)
    aggregate = medium_layout.lower_bound(instance, tables=T, seed=seed,
        budget_s=min(aggregate_budget_s, max(.001, budget_s*.25)))
    analytic = medium_ports.lower_bound(instance, tables=T, base_result=aggregate)
    lower = max(aggregate['lower_bound'], analytic['lower_bound'])
    exact = None
    remaining = budget_s-(monotonic()-started)
    if remaining > .01 and (upper_bound is None or lower < upper_bound-1e-8):
        exact = asdict(bound.lower_bound(instance, tables=T, seed=seed,
                                         budget_s=remaining))
        lower = max(lower, exact['lower_bound'])
    if upper_bound is not None and lower > upper_bound+1e-7:
        raise AssertionError('Medium lower bound exceeds checker-passed upper')
    return dict(instance_id=instance.id, lower_bound=lower,
                wall_s=monotonic()-started, packing=exact, aggregate=aggregate,
                hall=analytic, proof_scope=SCOPE,
                proof_family='arbitrary_current_moves_final_stow_relaxation',
                dg_widening=True, load_once_restriction=False,
                upper_bound=upper_bound,
                equality=upper_bound is not None and abs(lower-upper_bound)<1e-7)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget-s', type=float, default=60.)
    parser.add_argument('--out', required=True)
    parser.add_argument('--only', help='optional instance id for scale probe')
    args = parser.parse_args()
    folder = Path(args.out).resolve()
    if not folder.is_relative_to(HERE):
        raise ValueError('Output must remain in experiment folder')
    folder.mkdir(parents=True, exist_ok=True)
    index = json.loads((HERE/'medium-references/upper_index.json').read_text())
    config = dict(budget_s=args.budget_s, aggregate_budget_s=10., seed=0,
                  numerical_threads=1,
                  benchmark_running=(HERE.parents[1]/'c1/leader/BENCH-RUNNING').exists(),
                  code={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                        for name in ('medium.py','medium_layout.py','medium_ports.py',
                                     'bound.py','strengthening.py')})
    rows=[]
    for rel in load_set(str(HERE/'medium.txt')):
        instance = load_instance(ROOT/rel)
        if args.only and instance.id != args.only:
            continue
        reference = index[instance.id]
        digest = hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
        if digest != reference['input_sha256']:
            raise AssertionError('Reference input hash mismatch')
        print(instance.id, 'building', len(instance.load_list), 'boxes', flush=True)
        row = lower_bound(instance, budget_s=args.budget_s,
                          upper_bound=reference['lifts'])
        row.update(input=rel,input_sha256=digest,config=config,
                   upper_evidence=reference['evidence'],
                   upper_evidence_sha256=reference['evidence_sha256'],
                   previous_lower_bound=reference['previous_lower_bound'])
        (folder/f'{instance.id}.json').write_text(json.dumps(row,indent=2)+'\n')
        rows.append(row)
        print(instance.id, 'LB', row['lower_bound'], 'UB', reference['lifts'],
              'seconds', round(row['wall_s'],3), flush=True)
    (folder/'SUMMARY.json').write_text(json.dumps(dict(maps=len(rows),
        lower_bounds=[r['lower_bound'] for r in rows],
        upper_bounds=[r['upper_bound'] for r in rows],
        equality=sum(r['equality'] for r in rows),
        seconds=sum(r['wall_s'] for r in rows),config=config),indent=2)+'\n')


if __name__ == '__main__':
    main()
