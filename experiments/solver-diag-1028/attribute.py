"""Conservative per-outcome attribution, preserving unknown feasibility."""
import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
rows=[]
for label in ('before','after'):
 controls={p.parent.name:json.loads(p.read_text()) for p in (HERE/label/'runs/background').glob('*/done.json')}
 # Background controls are cargo-identical and are unaffected by solver-only edits.
 if not controls:controls={p.parent.name:json.loads(p.read_text()) for p in (HERE/'before/runs/background').glob('*/done.json')}
 for path in (HERE/label/'runs').glob('*/*/done.json'):
  r=json.loads(path.read_text())
  if r['solver']=='background':continue
  control=controls.get(r['id'],{})
  physical=control.get('physical_rules',[])
  if r.get('external_source_drift'):
   category='quarantined_source_transition';reason='Upstream changed during execution; excluded from stable comparison.'
  elif r.get('checker_passed'):
   category='pass';reason='Full checker passed; shippable is separately recorded.'
  elif r['rung']=='small':
   category='instance_or_background';reason='Released embedded vessel/background predates current finite loading policy, per-box evidence and D57 voyage contract; the public feasibility gate refuses the input before cargo search.'
  elif r.get('solver_metrics',{}).get('model_unavailable') or 'epoch is stale' in (r.get('solver_message') or ''):
   category='instance_or_background';reason='Production C2 dependency qualification is stale; cannot evaluate current solver completion. Independent empty-move control has background/checker failures. This is an infrastructure admission failure, not a ballast infeasibility proof.'
  elif r['solver']=='scalable' and r.get('solver_metrics',{}).get('oracle_dead_end'):
   category='instance_or_background';reason='Public oracle refused initial state; inspect raw oracle_dead_end reason and background control.'
  else:
   category='unresolved';reason='No passing-ballast witness or source-stable physical proof; raw outcome retained.'
  rows.append(dict(epoch=label,id=r['id'],solver=r['solver'],category=category,reason=reason,background_rules=physical,background_states=control.get('states',{}),background_checker_errors=control.get('part_errors',{}),ballast_miss_demonstrated=False,global_infeasibility_proved=False,shippable=r.get('shippable',False),source_drift=r.get('external_source_drift',[]),outcome=str(path.relative_to(HERE))))
(HERE/'attribution.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
from collections import Counter
print(dict(Counter(r['category'] for r in rows)))
