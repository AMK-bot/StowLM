"""D58 pilot rates, with actual checked/replayed denominators and source drift."""
import collections,json,statistics
from pathlib import Path
HERE=Path(__file__).resolve().parent

def q(rows,key):return sum(r.get(key) is True for r in rows)
def fmt(values):return '—' if not values else f'{statistics.median(values):.2f}/{max(values):.2f}'
def main():
 generations=[json.loads(p.read_text()) for p in (HERE/'generation/outcomes').glob('*.json')]
 rows=[json.loads(p.read_text()) for p in (HERE/'runs').glob('*/*/done.json') if (p.parent/'result.json').exists()]
 planned=json.loads((HERE/'generation/selection.json').read_text())['specs'];bycell=collections.defaultdict(list)
 for r in rows:bycell[(r['solver'],r['requested_spec']['size'],r['requested_spec']['vessel'])].append(r)
 lines=['| solver | rung | ship | run/usable | complete | ballast set/reached | checker pass/checked | shippable | mask pass/replayed | full chain | solve median/max s | OPTIMAL | approved LB gap median/max | drift |','|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---:|'];summary=[]
 for solver in ('exact','scalable','twostep'):
  for rung in ('tiny','small','medium'):
   if solver=='exact' and rung!='tiny':continue
   for ship in ('feeder_500','feeder_800','feeder_1700'):
    rs=bycell.get((solver,rung,ship),[]);n=len(rs)
    usable=sum(r.get('status') in ('generated','resumed') and r['spec']['size']==rung and r['spec']['vessel']==ship for r in generations)
    checked=[r for r in rs if r.get('checker_passed') is not None];replayed=[r for r in rs if r.get('mask_replay_passed') is not None];ballast=[r for r in rs if r.get('ballast_status') not in (None,'not_reached')]
    rawrules=collections.Counter(rule for r in rs for rule in r.get('rules',[]));states=collections.defaultdict(collections.Counter)
    for r in rs:
     for state,rules in r.get('states',{}).items():
      for rule in rules:states[state][rule]+=1
    times=[r['solve_s'] for r in rs if r.get('solve_s') is not None];gaps=[r['gap_to_lb36_absolute'] for r in rs if r.get('gap_is_full_checker_approved') and r.get('gap_to_lb36_absolute') is not None]
    row=dict(solver=solver,rung=rung,ship=ship,n=n,usable=usable,complete=sum(r.get('completion')=='complete' for r in rs),ballast_calls=len(ballast),ballast_found=q(rs,'ballast_found'),checker_checked=len(checked),checker_pass=q(rs,'checker_passed'),shippable=q(rs,'shippable'),mask_replayed=len(replayed),mask_pass=q(rs,'mask_replay_passed'),full_chain=q(rs,'clean_complete_pass'),optimal=sum(r.get('optimality_status')=='OPTIMAL' and r.get('clean_complete_pass') for r in rs),solve_median_s=statistics.median(times) if times else None,solve_max_s=max(times) if times else None,drift=sum(bool(r.get('source_drift')) for r in rs),rules=dict(rawrules),states={k:dict(v) for k,v in states.items()},status=dict(collections.Counter(r.get('final_status',r.get('solver_status','error')) for r in rs)),approved_gaps=gaps)
    summary.append(row);ratio=lambda x,y:f'{x}/{y}' if y else '—'
    lines.append(f'| {solver} | {rung} | {ship} | {n}/{usable} | {ratio(row["complete"],n)} | {ratio(row["ballast_found"],len(ballast))} | {ratio(row["checker_pass"],len(checked))} | {ratio(row["shippable"],n)} | {ratio(row["mask_pass"],len(replayed))} | {ratio(row["full_chain"],n)} | {fmt(times)} | {row["optimal"]} | {fmt(gaps)} | {row["drift"]} |')
 (HERE/'table.md').write_text('Diagnostic current-source pilot. Ballast/checker/mask denominators count only reached phases. OPTIMAL counts require a passing full chain; LB gaps use the checked objective minus the 3.6 lower bound. Drift rows are diagnostic and flagged.\n\n'+'\n'.join(lines)+'\n')
 (HERE/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(HERE/'outcomes.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
 totals=dict(generation_requests=len(generations),generation_accepted=sum(r.get('status') in ('generated','resumed') for r in generations),generation_status=dict(collections.Counter(r.get('status') for r in generations)),solver_finished=len(rows),complete=sum(r.get('completion')=='complete' for r in rows),ballast_found=q(rows,'ballast_found'),checker_pass=q(rows,'checker_passed'),shippable=q(rows,'shippable'),mask_pass=q(rows,'mask_replay_passed'),full_chain=q(rows,'clean_complete_pass'),optimal=sum(r.get('optimality_status')=='OPTIMAL' and r.get('clean_complete_pass') for r in rows),source_drift=sum(bool(r.get('source_drift')) for r in rows),active_checkpoints=sum(not (p.parent/'done.json').exists() for p in (HERE/'runs').glob('*/*/result.json')))
 (HERE/'totals.json').write_text(json.dumps(totals,indent=2)+'\n');print(json.dumps(totals))
if __name__=='__main__':main()
