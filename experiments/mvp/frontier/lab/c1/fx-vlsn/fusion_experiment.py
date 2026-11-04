"""Compare eager and lazy exact component fusion on identical saved parents."""
import json
from pathlib import Path
from time import monotonic

from alns import Problem
from catalog import load_set
from stowage.actions.space import Place
from stowage.io import load_instance
from lazy_fusion import recombine as lazy
from recombine import recombine as eager

HERE=Path(__file__).resolve().parent


def main():
    results=[]
    for filename in sorted((HERE/'results'/'from_starts60').glob('*.json')):
        row=json.loads(filename.read_text())
        p=Problem(load_instance(row['input']))
        starts=row['starts']
        # At most four parents, sorted by construction score, identical per arm.
        parents=[tuple(Place(a['box'],a['slot']) for a in s['actions']) for s in starts[:4]]
        for name,fn in [('lazy',lazy),('eager',eager)]:
            began=monotonic()
            plans=fn(p,parents,deadline=began+15,seed=0)
            result=dict(id=row['id'],method=name,budget_s=15,wall_s=monotonic()-began,
                        parent_objectives=[s['objective'] for s in starts[:4]],
                        best=p.score(plans[0]).objective if plans else None,
                        stats=fn.last_stats)
            results.append(result)
            print(json.dumps(result),flush=True)
    (HERE/'results'/'fusion15.json').write_text(json.dumps(results,indent=2)+'\n')


if __name__=='__main__':
    main()
