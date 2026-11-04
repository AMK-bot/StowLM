"""44 vs 35: finish the re-decoded greedy plans that hit the 120 s limit at 600 s, uncompacted, with the same finisher and checker."""
import json,sys,os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
sys.path.insert(0,'experiments/mvp/strategy')
os.environ['COMPACT_FINISHER_S']='600'
import compact
R=Path('experiments/mvp/strategy/val-greedy-82/run/instances')
def one(i):
    from stowage.io import load_instance
    from stowage.model import MovePlan
    rec=json.loads((R/f'{i}.json').read_text()) if (R/f'{i}.json').exists() else None
    plan=MovePlan.model_validate(json.load(open(f'experiments/mvp/strategy/compact-val/source-plans/{i}.json')))
    inst=load_instance(compact.ROOT/json.load(open(f'experiments/mvp/strategy/compact-val/rows/{i}.json'))['input'])
    c,_=compact.finish_and_check(inst,plan)
    return dict(id=i,**{k:c.get(k) for k in('passed','moves','finisher_s','timed_out','rules')})
if __name__=='__main__':
    ids=[]
    for p in R.glob('*/outcome.json'):
        if json.load(open(p))['outcome']=='finisher time limit': ids.append(p.parent.name)
    with ProcessPoolExecutor(2) as ex:
        rows=list(ex.map(one,sorted(ids)))
    json.dump(rows,open('experiments/mvp/strategy/recheck-120-limit.json','w'),indent=1)
    print(len(rows),sum(r['passed'] is True for r in rows))
    for r in rows: print(r)
