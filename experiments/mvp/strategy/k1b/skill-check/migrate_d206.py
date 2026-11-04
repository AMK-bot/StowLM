"""Fence obsolete D204 queue cooperatively, preserving every completed result."""
import json, os, time
from pathlib import Path
from datetime import datetime, timezone
P=Path(__file__).resolve().parent/'matched'
def write(p,v):
 p.parent.mkdir(parents=True,exist_ok=True)
 q=p.with_suffix(p.suffix+'.migration-tmp');q.write_text(json.dumps(v,indent=1)+'\n');q.replace(p)
def main():
 
 if (P/'migration-D206.json').exists(): raise RuntimeError('Migration already recorded; resume controller, do not rerun migration')
 man=json.loads((P/'manifest.json').read_text()); stamp=datetime.now(timezone.utc).isoformat()
 event=dict(applied_at=stamp,decision='D206',decision_at='2026-10-31T20:10:00Z',old_pid=11607,old_rule='D204: mask >= 4095; load1 <= 30; age < 120 s',new_rule='D206: popcount(mask) >= 8; load1 <= 45; age < 120 s',normal_priority=True,fenced=[],aborted_preparations=[],retained=[])
 for job in man['maps']:
  iid=job['instance_id']; rows=[P/'rows'/iid/f'{a}.json' for a in ('8.2','null','K1r')]
  if all(p.exists() and json.loads(p.read_text()).get('complete') is True for p in rows):
   event['retained'] += [str(p.relative_to(P)) for p in rows]; continue
  batch=P/'batches'/iid
  try: batch.mkdir(parents=True)
  except FileExistsError:
   if (batch/'start.json').exists(): raise RuntimeError(f'Already released cohort: {iid}; drain before migration')
   write(batch/'abort.json',dict(reason='D206 policy migration: cancel unreleased preparation, no finisher started',at=stamp))
   event['aborted_preparations'].append(iid)
  write(batch/'fence.json',dict(reason='D206 migration; obsolete controller must not reuse queue',at=stamp))
  event['fenced'].append(iid)
 # Workers honor abort before release; wait for their incomplete rows, then archive intact.
 for iid in event['aborted_preparations']:
  batch=P/'batches'/iid
  ready=list(batch.glob('*.ready.json'))
  deadline=time.time()+30
  for f in ready:
   arm=f.name.removesuffix('.ready.json'); row=P/'rows'/iid/f'{arm}.json'
   while not row.exists() and time.time()<deadline: time.sleep(.1)
   if not row.exists(): raise RuntimeError(f'No cooperative abort result: {row}')
   data=json.loads(row.read_text())
   if data.get('complete') or data.get('started_at'): raise RuntimeError('Unexpected released trial during migration; preserve and review')
   dest=P/'migration-D206-aborted'/iid/row.name; dest.parent.mkdir(parents=True,exist_ok=True);row.rename(dest)
  if (batch/'start.json').exists(): raise RuntimeError('Legacy release raced migration; inspect before restart')
 write(P/'migration-D206.json',event)
 with (P/'admission-policy-history.jsonl').open('a') as f:f.write(json.dumps(event,sort_keys=True)+'\n')
 print(json.dumps(event))
if __name__=='__main__': main()
