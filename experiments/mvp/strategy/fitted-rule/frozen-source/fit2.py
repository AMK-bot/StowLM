import json,glob,random,sys,time
sys.path.insert(0,'/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/9f5a9d0d-9cf5-4e5c-b9b6-16d5b9ffcf5c/scratchpad/sight')
import numpy as np
from rf import Ctx,NAMES
from stowage.io import load_instance,load_plan
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
from stowage.conventions import parse_slot
SP=sys.path[0]+'/'
ps=sorted(p for p in glob.glob('experiments/mvp/gold/train/small/*/*/*/result.json'))
rows=[]
for p in ps:
    r=json.load(open(p))
    if r.get('passed') is True:
        assert '/train/small/' in r['input_path']
        rows.append((p,r['input_path'],r['ship'] if 'ship' in r else p.split('/')[5]))
rng=random.Random(214); rng.shuffle(rows)
by={}
for x in rows: by.setdefault(x[0].split('/')[5],[]).append(x)
chosen=[x for s,v in by.items() for x in v[30:60]]
caps={};X=[];t0=time.time();ok=0
for p,inp,_ in chosen:
    ship=p.split('/')[5]
    try:
        inst=load_instance(inp); plan=load_plan(p.replace('result.json','plan.json'))
        space=get_action_space(); eng=make_feasibility_engine(inst,space,config=FeasibilityConfig(extra={'global_screen':0}))
        ctx=Ctx(inst); acts=list(space.from_plan(inst,plan)); done=[]; groups=[]
        for a in acts:
            leg=list(eng.legal_actions())
            if a in leg:
                st=ctx.state(done)
                o=[x for x in leg if x!=a]; random.Random(len(done)).shuffle(o)
                groups.append(np.array([ctx.row(a,st)]+[ctx.row(x,st) for x in o[:150]],float))
            eng.push(a); done.append(a)
        st=ctx.state(done); caps.setdefault(ship,[]).append(max(st.values()) if st else 0.)
        X+=groups; ok+=1
    except Exception as e:
        print('skip',inp[-30:],type(e).__name__,str(e)[:80],flush=True)
    if time.time()-t0>540: break
print('maps',ok,'groups',len(X),'s',round(time.time()-t0),flush=True)
w=np.zeros(len(NAMES)); m=np.zeros_like(w); v=np.zeros_like(w)
for i in range(1,501):
    g=np.zeros_like(w); nll=0
    for G in X:
        s=G@w; s-=s.max(); pr=np.exp(s); pr/=pr.sum()
        g+=pr@G-G[0]; nll-=np.log(pr[0]+1e-12)
    g=g/len(X)+1e-3*w
    m=.9*m+.1*g; v=.999*v+.001*g*g; w-=.05*(m/(1-.9**i))/(np.sqrt(v/(1-.999**i))+1e-8)
    if i%100==0: print(i,round(nll/len(X),3),flush=True)
cap={s:float(np.percentile(c,90)) for s,c in caps.items()}
base=float(np.mean([np.log(len(G)) for G in X]))
json.dump(dict(names=NAMES,w=w.tolist(),cap_10t=cap,maps=ok,groups=len(X),uniform_nll=base,train_paths=[c[1] for c in chosen]),open(SP+'fitted2.json','w'),indent=0)
print(dict(zip(NAMES,w.round(2))),cap,'uniform nll',round(base,3))
