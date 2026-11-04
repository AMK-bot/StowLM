import json,glob,random,sys
from math import comb
def load(p):
    return {r['instance_id']:r for r in json.load(open(p))}
base='parent-audit-8.2/rebaseline-current/epoch-a8606f863639bb6b/summary.json'
g1=glob.glob('gen-001/audit/**/summary.json',recursive=True)[0]
g3=glob.glob('gen-003/audit/**/summary.json',recursive=True)[0]
A={'8.2':load(base),'gen-0 child':load(g1),'gen-2 child':load(g3)}
print(g1)
def sign(w,l):
    n=w+l
    if n==0:return 1.0
    k=min(w,l);return min(1.0,2*sum(comb(n,i) for i in range(k+1))/2**n)
def cmp(a,b):
    P,C=A[a],A[b]
    m=[i for i in C if C[i]['passed'] and P[i]['passed']]
    d=[C[i]['weighted_moves']-P[i]['weighted_moves'] for i in m]
    w=sum(x<0 for x in d);l=sum(x>0 for x in d);e=sum(x==0 for x in d)
    pm=sum(P[i]['weighted_moves'] for i in m)/len(m);cm=sum(C[i]['weighted_moves'] for i in m)/len(m)
    random.seed(0);bs=[]
    for _ in range(10000):
        s=[random.choice(d) for _ in d];bs.append(sum(s)/len(s))
    bs.sort()
    only_c=sum(C[i]['passed'] and not P[i]['passed'] for i in C);only_p=sum(P[i]['passed'] and not C[i]['passed'] for i in C)
    print(f"{a} -> {b}: passes {sum(r['passed'] for r in P.values())} -> {sum(r['passed'] for r in C.values())} (gain {only_c}, lost {only_p}, p={sign(only_c,only_p):.2f}); mutual {len(m)}: {pm:.2f} -> {cm:.2f} ({cm/pm-1:+.1%}); fewer {w} more {l} equal {e} p={sign(w,l):.2f}; mean diff {sum(d)/len(d):+.2f}, 95% CI [{bs[250]:+.2f},{bs[9750]:+.2f}] moves = [{bs[250]/pm:+.1%},{bs[9750]/pm:+.1%}]")
cmp('8.2','gen-0 child');cmp('8.2','gen-2 child');cmp('gen-0 child','gen-2 child')
# behaviour change: identical action token sequences
def toks(r):
    g=r.get('generation_timings') or {};return g.get('tokens')
for a,b in (('8.2','gen-0 child'),('8.2','gen-2 child'),('gen-0 child','gen-2 child')):
    P,C=A[a],A[b];same=0;n=0
    sims=[]
    for i in C:
        tp,tc=toks(P[i]),toks(C[i])
        if tp and tc:
            n+=1;same+=tp==tc
            k=0
            for x,y in zip(tp,tc):
                if x!=y:break
                k+=1
            sims.append(k/max(len(tp),len(tc)))
    print(f"{a} vs {b}: identical token sequences {same}/{n}; mean shared prefix {sum(sims)/max(len(sims),1):.0%}")
