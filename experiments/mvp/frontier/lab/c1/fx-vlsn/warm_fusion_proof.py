"""Exactly fuse four saved good plans and link a winner to checker evidence."""
import json
from pathlib import Path
from time import monotonic

from alns import Problem
from lazy_fusion import recombine
from stowage.actions.space import Place
from stowage.io import load_instance

HERE=Path(__file__).resolve().parent


def main():
    saved=json.loads((HERE/'results/ablation8/sim.small.41100007.json').read_text())
    # Exclude the eight-lift fused winner; parents themselves each cost nine.
    parents=[r for r in saved['candidates'] if r['objective']==9.][:4]
    p=Problem(load_instance(saved['input']))
    actions=[tuple(Place(a['box'],a['slot']) for a in r['actions']) for r in parents]
    found=recombine(p,actions,deadline=monotonic()+20.,seed=0)
    checked=json.loads((HERE/'results/ablation_checked/sim.small.41100007.json').read_text())
    record=dict(id=saved['id'],parents=parents,stats=recombine.last_stats,candidates=[])
    for acts in found:
        scored=p.score(acts)
        inherited=checked['passed'] and {a.box:a.slot for a in acts}==checked['assignment']
        record['candidates'].append(dict(objective=scored.objective,
            actions=[dict(box=a.box,slot=a.slot) for a in acts],
            same_stow_as_full_checker_certificate=inherited,
            same_order_as_full_checker_certificate=[dict(box=a.box,slot=a.slot) for a in acts]==checked['actions']))
    (HERE/'results/warm_fusion_proof.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({k:v for k,v in record.items() if k!='parents'}),flush=True)


if __name__=='__main__':
    main()
