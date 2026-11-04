import json,sys,os,collections,statistics as st
from pathlib import Path
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.moves import apply_plan
from stowage.conventions import parse_slot
S=Path('experiments/mvp/strategy/k1b/skill-check')
rows={}
for p in S.glob('rows/*/*/sample-00.json'):
    r=json.load(open(p)); rows[(r['arm'],r['instance_id'])]=r
out=open(sys.argv[1],'w')
for (arm,iid),r in sorted(rows.items()):
    p=S/'prepared'/arm/f'{iid}.json'
    if not r.get('complete') or not p.exists(): continue
    inst=load_instance(r['binding']['input']); plan=MovePlan.model_validate(json.load(open(p)))
    ship=apply_plan(inst,plan).final_state.ship; L=set(inst.load_list); bx={b.id:b for b in inst.containers}
    m=sorted(bx[i].gross_mass_kg for i in L); med=m[len(m)//2]
    deck=[];hold=[];st_={}
    for q in ship.placements:
        if q.container_id not in L: continue
        w=parse_slot(q.slot); b=bx[q.container_id]
        (deck if w.on_deck else hold).append(b.gross_mass_kg)
        if w.on_deck: st_.setdefault((w.bay,w.row),[]).append((w.tier,b.gross_mass_kg,b.length_ft))
    tops=[max(v)[1] for v in st_.values() if len(v)>=2]
    sm=[sum(x[1] for x in v) for v in st_.values()]
    out.write(json.dumps(dict(arm=arm,id=iid,clear=r['sight_clear'],deck_mean=st.mean(deck) if deck else 0,hold_mean=st.mean(hold) if hold else 0,med=med,heavy_deck=sum(x>med for x in deck),stacks2=sum(len(v)>=2 for v in st_.values()),maxstack=max(sm) if sm else 0,toptop=max(tops) if tops else 0,rows=sorted({abs(k[1]) for k in st_}),nst=len(st_),mixed=sum(len({x[2] for x in v})>1 for v in st_.values())))+'\n')
