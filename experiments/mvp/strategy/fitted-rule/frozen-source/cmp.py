import json,glob,statistics as st,sys
from math import comb
SP='/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/9f5a9d0d-9cf5-4e5c-b9b6-16d5b9ffcf5c/scratchpad/sight/'
K='experiments/mvp/strategy/k1b'
def sign(a,b):
    n=a+b;return min(1.,2*sum(comb(n,i) for i in range(min(a,b)+1))/2**n) if n else 1.
c={}
for p in glob.glob(K+'/kcurve/8.2/*.json'):
    r=json.load(open(p)); c[r['instance_id']]=r['points']['1'].get('raw_best')
cl={}
for p in glob.glob(K+'/skill-check/rows/8.2/*/sample-00.json'):
    r=json.load(open(p)); cl[r['instance_id']]=bool(r.get('sight_clear'))
for f in sys.argv[1:]:
    R={json.loads(l)['id']:json.loads(l) for l in open(SP+f)}
    ids=[i for i in R if R[i]['clear'] and cl.get(i) and c.get(i) is not None]
    x=[R[i]['cost'] for i in ids]; y=[c[i] for i in ids]
    a=sum(p<q for p,q in zip(x,y)); b=sum(p>q for p,q in zip(x,y))
    print(f,len(R),'both clear',len(ids),'rule',round(st.mean(x),2),'8.2',round(st.mean(y),2),'rule fewer/more',a,b,round(sign(a,b),3),'rules',sum(r.get('rew',0) for r in R.values())/len(R),'sec',round(st.mean(r['s'] for r in R.values()),1))
    print(' neither',sum(not R[i]['clear'] and not cl[i] for i in R),'dead',sum(R[i]['status']!='complete' for i in R),'8.2 dead-ends on same',sum(R[i]['status']!='complete' and not cl[i] for i in R))
