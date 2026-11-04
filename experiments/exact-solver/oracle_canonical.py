"""Independent full-physical relaxation plus actual-checker witness pilot.

A certificate requires an exact physical brute-force lower bound and a witness
that passes the unchanged full checker. The brute-force search itself omits
ballast/nonlinear physics; that omission is a relaxation, never silently called
a full checker search. Witnesses are full-physical references and can contain
overrides excluded by the public v2 mask. No C2 model or calibration is run.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from canonical import eligible_rows,load_selected

ROOT=Path(__file__).resolve().parent


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('experiments/instances-v2'))
    p.add_argument('--offset',type=int,default=0)
    p.add_argument('--limit',type=int,default=1)
    p.add_argument('--oracle-time-limit',type=float,default=5.)
    p.add_argument('--out',type=Path,default=ROOT/'oracle-canonical-pilot.jsonl')
    args=p.parse_args()
    release,rows=eligible_rows(args.root,generators=['random_yard'],profiles=['tiny'],splits=['train','val'])
    from stowage.solver.exact_oracle import checker_brute_force_certificate
    from stowage.actions import get_action_space
    space=get_action_space()
    for relative,metadata in rows[args.offset:args.offset+args.limit]:
        instance=load_selected(args.root,relative,metadata)
        print(json.dumps({'phase':'start','instance':str(relative),'split':metadata['split']}),flush=True)
        started=perf_counter()
        result=checker_brute_force_certificate(instance,time_limit_s=args.oracle_time_limit,max_nodes=100000)
        row={f.name:getattr(result,f.name) for f in fields(result) if f.name not in ('plan','report')}
        row.update(instance=str(relative),split=metadata['split'],sha256=metadata['sha256'],
                   spec_key=metadata['spec_key'],manifest_sha256=release['manifest_sha256'],
                   total_s=perf_counter()-started,brute_limit_s=args.oracle_time_limit,
                   scope='full_physical_reference_not_public_mask',
                   oracle_source_sha256=sha256(Path('src/stowage/solver/exact_oracle.py').read_bytes()).hexdigest())
        if result.report:
            row['violations']=[v.rule_id for v in result.report.violations if v.severity=='hard']
        if result.plan:
            actions=space.from_plan(instance,result.plan)
            row['canonical_actions']=[space.encode(a) for a in actions]
            row['override_actions']=[space.encode(a) for a in actions if type(a).__name__ in ('Relocate','RestowOff')]
            path=ROOT/'oracle-plans';path.mkdir(exist_ok=True)
            (path/(metadata['spec_key']+'.json')).write_text(result.plan.model_dump_json(indent=2)+'\n')
        with args.out.open('a') as stream:stream.write(json.dumps(row,sort_keys=True)+'\n')
        print(json.dumps(row),flush=True)


if __name__=='__main__':main()
