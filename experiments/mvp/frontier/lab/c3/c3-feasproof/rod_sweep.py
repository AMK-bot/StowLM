"""Final stronger outer sweep; results are separate from page attempts."""
from copy import deepcopy
import json
import os
from pathlib import Path
from time import monotonic

from run import inputs, code_hashes, dump
from audit import load_raw, verify_witness
from packing import build_model, attach_rows
from rod_bound import attach_rod_rows, evaluate_rod_rows
from physics import second_check_envelope
from stowage.io import load_instance

HERE = Path(__file__).resolve().parent


def main():
    out = HERE/'results-rod'
    out.mkdir(exist_ok=True)
    before = code_hashes()
    dump(out/'metadata.json',dict(pid=os.getpid(),source_sha256=before))
    started = monotonic()
    records = []
    for path in inputs():
        then = monotonic()
        raw,digest = load_raw(path)
        instance = load_instance(path)
        base = build_model(instance)
        maximum = float(instance.vessel.hull.max_gm_lashing_m)
        edges = sorted({0.,maximum,*[g for g in (.25,.5,.75,1.,1.5,2.,3.) if g<maximum]})
        row = dict(instance_id=instance.id,input=path,input_sha256=digest,
                   status='unknown',physical_proof=False,branches=[])
        for lo,hi in zip(edges,edges[1:]):
            model = deepcopy(base)
            attach_rows(model,instance,gm_lo=lo,gm_hi=hi)
            metadata = attach_rod_rows(model,instance,gm_lo=lo)
            result = model.solve(time_limit=15.)
            branch = dict(interval_m=[lo,hi],solver_status=int(result.status),
                          status='unknown',physical_proof=False,solve_s=result.wall_s)
            if result.x is not None:
                branch.update(status='relaxation_feasible_with_witness',
                              rod_rows=evaluate_rod_rows(model,result.x),
                              kg_lower_m=result.witness['physical_kg_lower_m'])
                audit = verify_witness(raw,result.witness)
                assert audit['verified'],audit
                assert all(r['ratio']<=1.+1.e-6 for r in branch['rod_rows']),branch['rod_rows']
                if lo==0.:
                    row['packing'] = dict(witness=result.witness,audit=audit)
                if lo<=.6<=hi:
                    row['page_targets'] = result.witness
            row['branches'].append(branch)
        row['second_check_envelope'] = second_check_envelope(instance)
        row['elapsed_s'] = monotonic()-then
        dump(out/(instance.id+'.json'),row)
        brief = dict(instance_id=instance.id,elapsed_s=row['elapsed_s'],
                     feasible_branches=sum(b['status']=='relaxation_feasible_with_witness' for b in row['branches']),
                     branches=len(row['branches']))
        records.append(brief)
        print(json.dumps(brief),flush=True)
        dump(out/'summary.json',dict(maps=records,wall_s=monotonic()-started))
    after = code_hashes()
    dump(out/'source_verification.json',dict(changed=[p for p in before if after.get(p)!=before[p]],
                                            source_sha256=after))


if __name__=='__main__':
    main()
