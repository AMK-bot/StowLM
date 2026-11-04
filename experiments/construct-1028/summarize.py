"""Rebuild honest per-ship outcome table from resumable worker checkpoints."""
from __future__ import annotations
from collections import defaultdict,Counter
import json,statistics
from pathlib import Path
HERE=Path(__file__).resolve().parent

def main():
 groups=defaultdict(list);rows=[]
 selected={r['id']:r for r in json.loads((HERE/'selection-saved.json').read_text())}
 for p in (HERE/'generation/outcomes').glob('*.json'):
  r=json.loads(p.read_text());selected[r['id']]=r
 for p in sorted((HERE/'runs').glob('*/*/done.json')):
  r=json.loads(p.read_text());spec=selected.get(r['id'],{}).get('spec',{})
  r.update(spec=spec);groups[(r['solver'],r['id'].split('-')[0],spec.get('size','?'),spec.get('vessel',r.get('vessel','?')))].append(r);rows.append(r)
 lines=['| solver | source | size | ship | n | construct | finisher found/set | checker | shippable | mask | full chain | solve median/max s | total median/max s | checked moves/LB median |', '|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|']
 summary=[]
 for key,rs in sorted(groups.items()):
  def timing(field):
   values=[r[field] for r in rs if isinstance(r.get(field),(int,float))]
   return f'{statistics.median(values):.1f}/{max(values):.1f}' if values else '—'
  def count(pred):return f'{sum(bool(pred(r)) for r in rs)}/{len(rs)}'
  successful=[r for r in rs if r.get('checker_passed')]
  moves=[r['checked_objective'] for r in successful if r.get('checked_objective') is not None]
  bounds=[r['lower_bound_36'] for r in successful if r.get('lower_bound_36') is not None]
  objective=f'{statistics.median(moves):.1f}/{statistics.median(bounds):.1f}' if moves and bounds else '—'
  complete=lambda r:r.get('completion')=='complete'
  row=[*key,str(len(rs)),count(complete),count(lambda r:r.get('ballast_found')),count(lambda r:r.get('checker_passed')),count(lambda r:r.get('shippable')),count(lambda r:r.get('mask_replay_passed')),count(lambda r:r.get('clean_complete_pass')),timing('solve_s'),timing('process_wall_s'),objective]
  lines.append('| '+' | '.join(row)+' |')
  summary.append(dict(key=key,n=len(rs),full_chain=sum(bool(r.get('clean_complete_pass')) for r in rs),errors=dict(Counter(r.get('error') for r in rs if r.get('error')))))
 (HERE/'table.md').write_text('\n'.join(lines)+'\n')
 (HERE/'all-results.json').write_text(json.dumps(rows,indent=2,default=str)+'\n')
 (HERE/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 print('\n'.join(lines))
if __name__=='__main__':main()
