"""D149 summary over finished rows; read-only."""
import json,glob,statistics as st,collections,sys
rows=[json.load(open(f)) for f in glob.glob('rows/*.json')]
def fmt(g):
    both=[r for r in g if r.get('gold',{}).get('passed') and r.get('twostep',{}).get('passed')]
    d=[r['gold']['moves']-r['twostep']['moves'] for r in both]
    o=dict(n=len(g),gold_pass=sum(bool(r.get('gold',{}).get('passed')) for r in g),two_pass=sum(bool(r.get('twostep',{}).get('passed')) for r in g),both=len(both))
    if both:
        o.update(worse=sum(x>0 for x in d),equal=sum(x==0 for x in d),better=sum(x<0 for x in d),gold_mean=round(st.mean(r['gold']['moves'] for r in both),2),two_mean=round(st.mean(r['twostep']['moves'] for r in both),2),mean_excess_when_worse=round(st.mean(x for x in d if x>0),2) if any(x>0 for x in d) else 0,max_excess=max(d))
    return o
print('all',len(rows),'errors',sum('error' in r for r in rows),'recount==stored',sum(r.get('gold_recount')==r['stored'] for r in rows),'recount!=verified',sum(1 for r in rows if r.get('gold',{}).get('passed') and r['gold']['moves']!=r['gold_recount']))
for rung in('tiny','small'):
    g=[r for r in rows if r['rung']==rung]
    print(rung,fmt(g))
    for s in('feeder_500','feeder_800','feeder_1700'):print('  ',s,fmt([r for r in g if r['ship']==s]))
    for k in sorted({str((r['solver'],r['status'])) for r in g}):print('  ',k,fmt([r for r in g if str((r['solver'],r['status']))==k]))
print('fail reasons',collections.Counter((r['rung'],'gold',str(r['gold'].get('error'))[:60],r['gold'].get('finisher_timeout')) for r in rows if 'gold' in r and not r['gold']['passed']).most_common(8))
print('fail reasons',collections.Counter((r['rung'],'two',str(r['twostep'].get('error'))[:60],r['twostep'].get('finisher_timeout')) for r in rows if 'twostep' in r and not r['twostep']['passed']).most_common(8))
