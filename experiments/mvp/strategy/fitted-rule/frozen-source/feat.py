import json,glob,sys,os,time
from pathlib import Path
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.moves import apply_plan
from stowage.checker.lashing import check_lashing
from stowage.checker.visibility import _loading_condition
from stowage.conventions import parse_slot, cells_of_slot
S=Path('experiments/mvp/strategy/k1b/skill-check')
out=sys.argv[1]; done=set()
if os.path.exists(out):
    done={(json.loads(l)['arm'],json.loads(l)['id']) for l in open(out)}
rows={}
for p in S.glob('rows/*/*/sample-00.json'):
    r=json.load(open(p)); rows[(r['arm'],r['instance_id'])]=r
inputs={k[1]:v['binding']['input'] for k,v in rows.items()}
f=open(out,'a')
for (arm,iid),r in sorted(rows.items()):
    if (arm,iid) in done or not r.get('complete'): continue
    p=S/'prepared'/arm/f'{iid}.json'
    if not p.exists(): continue
    inst=load_instance(inputs[iid]); plan=MovePlan.model_validate(json.load(open(p)))
    t=time.time()
    ship=apply_plan(inst,plan).final_state.ship
    cond=_loading_condition(inst,ship)
    rep=check_lashing(inst,ship,call_index=inst.call_index)
    loads=set(inst.load_list); pl=[q for q in ship.placements if q.container_id in loads]
    boxes={b.id:b for b in inst.containers}
    deck=[parse_slot(q.slot) for q in pl if parse_slot(q.slot).on_deck]
    tiers=[w.tier for w in deck]
    masses=[boxes[q.container_id].mass_kg if hasattr(boxes[q.container_id],'mass_kg') else None for q in pl if parse_slot(q.slot).on_deck]
    v=[dict(msg=str(x.message)[:170],measured=x.measured,limit=x.limit,unit=x.unit) for x in rep.violations if x.severity=='hard']
    f.write(json.dumps(dict(arm=arm,id=iid,clear=r.get('sight_clear'),n=len(pl),deck=len(deck),hold=len(pl)-len(deck),tiers=sorted(tiers),deck_mass=sum(x or 0 for x in masses),
       gm=cond.gm_m,kg=cond.kg_m,t_aft=cond.t_aft_m,t_fwd=cond.t_fwd_m,n_viol=len(v),viol=v[:3],metrics={k:vv for k,vv in (rep.metrics or {}).items() if isinstance(vv,(int,float))},s=round(time.time()-t,1)))+'\n'); f.flush()
