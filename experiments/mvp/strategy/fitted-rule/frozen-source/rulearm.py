"""Review arm: K1r's uniform choice + a one-line preference. Same mask, rewinds, compaction, gate. Validation only."""
import json,os,sys,random,time
import numpy as np
sys.path.insert(0,'/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/9f5a9d0d-9cf5-4e5c-b9b6-16d5b9ffcf5c/scratchpad/sight')
from rf import Ctx
FIT=json.load(open('/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/9f5a9d0d-9cf5-4e5c-b9b6-16d5b9ffcf5c/scratchpad/sight/'+os.environ.get('FITFILE','fitted.json'))); W=np.array(FIT['w']); CTX=None; CAP=None; DEC=None
from pathlib import Path
K=Path('experiments/mvp/strategy/k1b').resolve()
os.environ.update(K1B_OUT='/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/9f5a9d0d-9cf5-4e5c-b9b6-16d5b9ffcf5c/scratchpad/sight/k1bout',COMPACT_FINISHER_S='600')
os.environ.pop('STOWAGE_BALLAST_V4',None)
sys.path.insert(0,str(K.parent)); sys.path.insert(0,str(K))
import compact
SAFE=sys.argv[2]
from stowage.conventions import parse_slot
def level(a):
    s=parse_slot(a.slot); return s.tier if s.on_deck else 0
def choose(actions,banned,rng):
    o=[a for a in actions if a not in banned]
    if not o: return None
    if SAFE=='notier3':            # never deck tier 3+ if anything else is legal
        k=[a for a in o if level(a)<86]; o=k or o
    elif SAFE=='lowest':           # lowest deck tier available; hold counts as lowest
        m=min(level(a) for a in o); o=[a for a in o if level(a)==m]
    elif SAFE=='masscap':          # no deck stack above the ship's cap (fitted on train gold) if anything else is legal
        st=CTX.state(DEC.actions)
        k=[a for a in o if level(a)==0 or CTX.res_mass(a,st)<=CAP]; o=k or o
    elif SAFE in ('fitT1','fitgreedy'):
        st=CTX.state(DEC.actions)
        z=np.array([CTX.row(a,st) for a in o])@W
        if SAFE=='fitgreedy': return o[int(np.argmax(z))]
        z=z-z.max(); p=np.exp(z); return rng.choices(o,weights=p.tolist())[0]
    elif SAFE in ('heavyhold','combo'):        # heaviest box into the hold first; then lowest deck tier, lightest box
        h=[a for a in o if level(a)==0]
        if h:
            m=max(MASS[a.box] for a in h); o=[a for a in h if MASS[a.box]==m]
        else:
            m=min((level(a),MASS[a.box]) for a in o); o=[a for a in o if (level(a),MASS[a.box])==m]
    return rng.choice(o)
MASS={}
MAXB,MAXR,MAXW=32,8,64
def next_k(k,d,e): return 1 if d>e else min(k*2,MAXR)
def sample(dec,codec,rng):
    global DEC
    DEC=dec
    rew,rewound,ek,ed=0,0,0,-1
    while True:
        allowed=dec.allowed_token_ids()
        if dec.status=='complete': return 'complete',rew
        eng=dec.inner.engine
        if allowed and eng.is_complete():
            for t in codec.end_plan_tokens: dec.advance(t)
            continue
        act=choose(eng.legal_actions(),dec.forbidden.get(dec.state_key(),set()),rng) if allowed else None
        if act is not None:
            for t in codec.encode_action(act): dec.advance(t)
            continue
        if rew>=MAXB or rewound>=MAXW: return 'dead_end',rew
        d=len(dec.actions); ek=next_k(ek,d,ed); ed=max(ed,d)
        p=dec.rewind(ek); rew+=1; rewound+=p
        if p==0 and not dec.actions and not dec.allowed_token_ids(): return 'dead_end',rew
from llm.lookahead import lookahead_decoder_factory
from llm.stowlm.backtrack import ForbiddingDecoder
from llm.stowlm.format import load_tokenizer
from llm.stowlm.mask import MaskConfig
from llm.stowlm.sft_data import codec_for
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.io import load_instance
sys.path.insert(0,str(K.parents[1]/'frontier'))
from sight import sight_clear
inputs=sorted(json.load(open(K/'manifest.json'))['inputs'])
out=sys.argv[1]; seeds=[int(x) for x in sys.argv[3].split(',')]
done=set()
if os.path.exists(out): done={(json.loads(l)['id'],json.loads(l)['seed']) for l in open(out)}
f=open(out,'a'); tok=load_tokenizer()
for inp in inputs:
    assert "/val/small/" in inp
    inst=load_instance(inp); MASS.clear(); MASS.update({b.id:b.gross_mass_kg for b in inst.containers}); CTX=Ctx(inst); CAP=FIT['cap_10t'][inp.split('/')[5]]
    space=get_action_space(); eng=make_feasibility_engine(inst,space,config=FeasibilityConfig(extra={'global_screen':0}))
    codec=codec_for(inst,tok); fac=(lookahead_decoder_factory(securing=True,securing_screen=__import__('stowage.solver.securing_screen',fromlist=['x']).DeckSecuringScreen(inst)) if SAFE in ('design','combo') else lookahead_decoder_factory(securing=True))
    decs=list(fac(inst,eng,codec,MaskConfig(),len(seeds)))
    for d0,seed in zip(decs,seeds):
        if (inst.id,seed) in done: continue
        t=time.time(); dec=ForbiddingDecoder(d0)
        st,rw=sample(dec,codec,random.Random(seed))
        row=dict(id=inst.id,seed=seed,arm=SAFE,status=st,rew=rw,clear=False)
        if st=='complete':
            plan=dec.inner.engine.to_plan()
            c,info=compact.compact_plan(inst,plan)
            better=c is not None and info['compact_raw']['total']<info['before']['total']
            ch=c if better else plan
            ok,d=sight_clear(inst,ch,{})
            row.update(clear=ok,rules=d['rules'],cost=info['compact_raw']['total'] if better else info['before']['total'])
            from stowage.moves import apply_plan
            ship=apply_plan(inst,ch).final_state.ship; loads=set(inst.load_list)
            row['tiers']=sorted(parse_slot(p.slot).tier for p in ship.placements if p.container_id in loads and parse_slot(p.slot).on_deck)
        row['s']=round(time.time()-t,1); f.write(json.dumps(row)+'\n'); f.flush()
