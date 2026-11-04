import json,collections,statistics as st,sys
SP='/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/9f5a9d0d-9cf5-4e5c-b9b6-16d5b9ffcf5c/scratchpad/sight/'
R=[json.loads(l) for l in open(SP+'feat.jsonl')]
def tier2(r): return sum(t>=84 for t in r['tiers'])
def tier3(r): return sum(t>=86 for t in r['tiers'])
g=collections.defaultdict(list)
for r in R:g[r['arm']].append(r)
for a,v in g.items():
    print(a,len(v),'clear',sum(r['clear'] for r in v),'deck',round(st.mean(r['deck'] for r in v),2),'hold',round(st.mean(r['hold'] for r in v),2),'t2+',round(st.mean(tier2(r) for r in v),2),'t3+',round(st.mean(tier3(r) for r in v),2),'gm',round(st.mean(r['gm'] for r in v),2),'kg',round(st.mean(r['kg'] for r in v),2))
    for c in (True,False):
        w=[r for r in v if r['clear']==c]
        if w: print('   clear' if c else '   fail',len(w),'deck',round(st.mean(r['deck'] for r in w),2),'t2+',round(st.mean(tier2(r) for r in w),2),'t3+',round(st.mean(tier3(r) for r in w),2),'gm',round(st.mean(r['gm'] for r in w),2),'maxtier',collections.Counter(max(r['tiers'] or [0]) for r in w).most_common(5))
msgs=collections.Counter()
for r in R:
    for v in r['viol']:
        import re
        msgs[re.sub(r'[-\d.]+','#',v['msg'])[:90]]+=1
print(msgs.most_common(12))
keys=[k for k in R[0]['metrics'] if 'ratio' in k]
for k in keys:
    print(k,{a:round(st.mean(r['metrics'].get(k,0) for r in v),3) for a,v in g.items()},'failmax',round(max(r['metrics'].get(k,0) for r in R),2), 'fails>1',sum(r['metrics'].get(k,0)>1 for r in R))
print([ (r['arm'],r['viol'][0]) for r in R if r['viol']][:3])
