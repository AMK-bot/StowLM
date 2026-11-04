import json,sys,os
from pathlib import Path
K=Path('experiments/mvp/strategy/k1b').resolve()
sys.path.insert(0,str(K.parents[1]/'frontier'))
from sight import sight_clear
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.moves import apply_plan
from stowage.conventions import parse_slot
inputs=sorted(json.load(open(K/'manifest.json'))['inputs'])
out=sys.argv[1]; done=set()
if os.path.exists(out): done={(json.loads(l)['arm'],json.loads(l)['id'],json.loads(l)['sample']) for l in open(out)}
f=open(out,'a')
bases={'8.2':K,'K1r':K/'k1r','null':K/'null'}
for inp in inputs:
    inst=load_instance(inp)
    for arm,samples in (('8.2',(0,1,2)),('K1r',(1,2)),('null',(1,))):
        for s in samples:
            if (arm,inst.id,s) in done: continue
            d=json.load(open(bases[arm]/'candidates'/inst.id/f'sample-{s:02d}.json'))
            row=dict(arm=arm,id=inst.id,sample=s,temp=d.get('temperature'),complete=d['plan'] is not None)
            if d['plan'] is not None:
                p=MovePlan.model_validate(d['plan']); ok,x=sight_clear(inst,p,{})
                ship=apply_plan(inst,p).final_state.ship; L=set(inst.load_list)
                row.update(clear=ok,rules=x['rules'],t3=sum(parse_slot(q.slot).on_deck and parse_slot(q.slot).tier>=86 for q in ship.placements if q.container_id in L))
            f.write(json.dumps(row)+'\n');f.flush()
