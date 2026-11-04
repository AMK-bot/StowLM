"""Follow-up table: missing required data remain distinct from solver yield."""
from collections import defaultdict,Counter
import json,statistics
from pathlib import Path
HERE=Path(__file__).resolve().parent
rows=[];groups=defaultdict(list)
for p in sorted((HERE/'runs/construct').glob('*/done.json')):
 r=json.loads(p.read_text());rows.append(r);spec=r['spec'];groups[(r['source'],spec['size'],spec['vessel'])].append(r)
lines=['| cohort | size | ship | attempted | construct plan | finisher | full checker | shippable | token mask | required qualification missing | construct median/max s | total median/max s | moves/LB |','|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|']
for key,rs in sorted(groups.items()):
 n=len(rs);count=lambda key:f'{sum(bool(r.get(key)) for r in rs)}/{n}'
 def timing(key):
  values=[r[key] for r in rs if isinstance(r.get(key),(int,float))];return f'{statistics.median(values):.2f}/{max(values):.2f}' if values else '—'
 unavailable=sum(r.get('solver_metrics',{}).get('attribution')=='required linear qualification unavailable' for r in rs)
 lines.append('| '+' | '.join([*key,str(n),f'{sum(r.get("solver_status")=="ok" for r in rs)}/{n}',count('ballast_found'),count('checker_passed'),count('shippable'),count('mask_replay_passed'),f'{unavailable}/{n}',timing('solve_s'),timing('process_wall_s'),'unreached'])+' |')
(HERE/'table.md').write_text('\n'.join(lines)+'\n');(HERE/'all-results.json').write_text(json.dumps(rows,indent=2)+'\n')
summary={'attempts':len(rows),'full_chain':sum(bool(r.get('clean_complete_pass')) for r in rows),'attribution':dict(Counter(r.get('solver_metrics',{}).get('attribution') for r in rows)),'generation':[]}
for p in sorted((HERE/'generation/outcomes').glob('*.json')):
 r=json.loads(p.read_text());summary['generation'].append({k:r.get(k) for k in ('id','status','spec','error','wall_s','cargo_generator_version')})
(HERE/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('\n'.join(lines))
