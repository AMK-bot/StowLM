"""Summarize all diagnostic attempts without treating missing verdicts as passes."""
import collections,json,statistics
from pathlib import Path
HERE=Path(__file__).resolve().parent

def summary(rows):
 n=len(rows); attempted=[r for r in rows if r.get('ballast_status') not in (None,'not_reached')]
 timings=[r['solve_s'] for r in rows if r.get('solve_s') is not None]
 rules=collections.Counter(rule for r in rows for rule in r.get('rules',[]))
 engine=collections.Counter()
 for r in rows:
  for rule,count in r.get('solver_metrics',{}).get('dead_end_rule_ids',{}).items():engine[rule]+=count
 return dict(n=n,complete=sum(r.get('completion')=='complete' for r in rows),timeout=sum(bool(r.get('timed_out') or r.get('solver_status')=='timeout') for r in rows),ballast_attempted=len(attempted),ballast_found=sum(r.get('ballast_status') in ('ok','kept') for r in attempted),ballast_statuses=dict(collections.Counter(r.get('ballast_status','not_reached') for r in rows)),checker_pass=sum(r.get('checker_passed') is True for r in rows),checker_fail=sum(r.get('checker_passed') is False for r in rows),checker_unreached=sum(r.get('checker_passed') is None for r in rows),shippable=sum(r.get('shippable') is True for r in rows),rules=dict(rules),engine_rules=dict(engine),median_s=statistics.median(timings) if timings else None,max_s=max(timings) if timings else None,wall_median_s=statistics.median([r.get('wall_s',r.get('process_wall_s',0)) for r in rows]),drift_rows=sum(bool(r.get('external_source_drift')) for r in rows),external_epochs=sorted({r.get('external_source_sha256','unknown') for r in rows}),errors=dict(collections.Counter(r.get('error') for r in rows if r.get('error'))))
def group(rows):
 groups=collections.defaultdict(list)
 for r in rows:groups[(r['solver'],r['rung'],r['generator'],r['release_member'])].append(r)
 return groups

def main():
 summaries={};allrows=[]
 for label in ('before','after'):
  paths=sorted((HERE/label/'runs').glob('*/*/done.json'))
  rows=[json.loads(p.read_text()) for p in paths];allrows.extend(rows)
  summaries[label]={'|'.join(map(str,key)):summary(rs) for key,rs in group(rows).items()}
 (HERE/'summary.json').write_text(json.dumps(summaries,indent=2)+'\n')
 (HERE/'outcomes.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in allrows))
 lines=['| Epoch | solver | rung | generator | coverage | n | complete | timeout | ballast found/attempted | checker pass/fail/unreached | solve median/max s | source drift rows | hard rule ids |','|---|---|---|---|---|---:|---:|---:|---:|---|---|---:|---|']
 for label,groups in summaries.items():
  for key,r in sorted(groups.items()):
   solver,rung,gen,release=key.split('|')
   if solver=='background':continue
   fmt=lambda v:'—' if v is None else f'{v:.3f}'
   rules=', '.join(r['rules']) or ', '.join(r['engine_rules']) or 'checker not reached'
   lines.append(f'| {label} | {solver} | {rung} | {gen} | {"release" if release=="True" else "saved diagnostic"} | {r["n"]} | {r["complete"]}/{r["n"]} | {r["timeout"]} | {str(r["ballast_found"])+"/"+str(r["ballast_attempted"]) if r["ballast_attempted"] else "N/A (0 calls)"} | {r["checker_pass"]}/{r["checker_fail"]}/{r["checker_unreached"]} | {fmt(r["median_s"])}/{fmt(r["max_s"])} | {r["drift_rows"]} | {rules} |')
 (HERE/'table.md').write_text('Diagnostic only. Each group has six preselected cases. Tiny cases are saved non-release diagnostics; released small covers feeder_500 only. Ballast N/A means no call reached. Checker columns are pass/fail/unreached counts. Source-drift rows are quarantined, never evidence or causal speedup measurements.\n\n'+'\n'.join(lines)+'\n')
 controls=[r for r in allrows if r['solver']=='background']
 control_summary={label:summary([r for r in controls if r['label']==label]) for label in ('before','after')}
 (HERE/'background-summary.json').write_text(json.dumps(control_summary,indent=2)+'\n')
 before={(r['id'],r['solver']):r for r in allrows if r['label']=='before' and r['solver']!='background'}
 after={(r['id'],r['solver']):r for r in allrows if r['label']=='after' and r['solver']!='background'}
 pairs=[(a,after[k]) for k,a in before.items() if k in after]
 qualification=dict(pairs=len(pairs),identical_inputs=sum(a['input_sha256']==b['input_sha256'] for a,b in pairs),identical_external_hash_and_no_drift=sum(a.get('external_source_sha256')==b.get('external_source_sha256') and not a.get('external_source_drift') and not b.get('external_source_drift') for a,b in pairs),transition_rows_preserved=len(list(HERE.glob('*/transitions/*/*/*/done.json'))),solver_attempts=sum(r['solver']!='background' for r in allrows),complete=sum(r['solver']!='background' and r.get('completion')=='complete' for r in allrows),shippable=sum(r.get('shippable',False) for r in allrows))
 (HERE/'qualification.json').write_text(json.dumps(qualification,indent=2)+'\n')
 print(json.dumps({'rows':len(allrows),'summaries':{k:len(v) for k,v in summaries.items()}}))
if __name__=='__main__':main()
