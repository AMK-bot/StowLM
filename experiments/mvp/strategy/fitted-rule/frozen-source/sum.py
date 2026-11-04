import json,glob,collections,statistics as st,sys,os
from math import comb
SP='/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/9f5a9d0d-9cf5-4e5c-b9b6-16d5b9ffcf5c/scratchpad/sight/'
def sign(a,b):
    n=a+b;return min(1.,2*sum(comb(n,i) for i in range(min(a,b)+1))/2**n) if n else 1.
base={}
for p in glob.glob('experiments/mvp/strategy/k1b/skill-check/rows/8.2/*/sample-00.json'):
    r=json.load(open(p)); base[r['instance_id']]=bool(r.get('sight_clear'))
ship={}
for p in glob.glob('experiments/mvp/strategy/k1b/skill-check/rows/8.2/*/sample-00.json'):
    r=json.load(open(p)); ship[r['instance_id']]=r['binding']['input'].split('/')[5]
s1={}
for l in open(SP+'samp.jsonl'):
    r=json.loads(l)
    if r['arm']=='8.2' and r['sample']==1: s1[r['id']]=bool(r.get('clear'))
for arm in sys.argv[1:]:
    f=SP+f'rule-{arm}.jsonl'
    if not os.path.exists(f): continue
    R=[json.loads(l) for l in open(f)]
    for seed in sorted({r['seed'] for r in R}):
        v={r['id']:r for r in R if r['seed']==seed}
        comp=sum(r['status']=='complete' for r in v.values()); cl=sum(r['clear'] for r in v.values())
        a=sum(base[i] and not v[i]['clear'] for i in v); b=sum(v[i]['clear'] and not base[i] for i in v)
        a1=sum(s1[i] and not v[i]['clear'] for i in v); b1=sum(v[i]['clear'] and not s1[i] for i in v)
        print(arm,seed,len(v),'complete',comp,'clear',cl,'|complete',round(cl/max(comp,1),3),'cost',round(st.mean(r['cost'] for r in v.values() if 'cost' in r),2),'rew',round(st.mean(r['rew'] for r in v.values()),1),
         'vs8.2s0',a,b,round(sign(a,b),5),'vs8.2s1',a1,b1,round(sign(a1,b1),5),'t3',round(st.mean(sum(t>=86 for t in r['tiers']) for r in v.values() if 'tiers' in r),2),
         {s[7:]:(sum(v[i]['clear'] for i in v if ship[i]==s),sum(v[i]['status']=='complete' for i in v if ship[i]==s)) for s in ('feeder_500','feeder_800','feeder_1700')})
