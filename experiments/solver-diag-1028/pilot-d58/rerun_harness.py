"""Rerun pre-compute harness errors, preserving their exact original records."""
import concurrent.futures,json,time
from pathlib import Path
from dispatch import HERE,execute,save
manifest=json.loads((HERE/'generation/selection.json').read_text());specs=manifest['specs']
jobs=[]
for p in (HERE/'runs').glob('*/*/done.json'):
 r=json.loads(p.read_text())
 if r.get('error')=='no solver checkpoint' and r.get('returncode')==2:
  solver=r['solver'];index=r['index'];target=HERE/'harness-errors'/solver/p.parent.name
  target.parent.mkdir(parents=True,exist_ok=True);p.parent.rename(target)
  jobs.append(('solver',index,solver,specs[index]))
jobs.sort(key=lambda j:(j[3]['size']!='medium',j[3]['size']!='tiny',j[1],j[2]))
save(HERE/'harness-retry-selection.json',jobs)
# Original phase reserves3 slots once its earlier generation workers drain.
while json.loads((HERE/'progress.json').read_text()).get('active',0)>3:time.sleep(1)
deadline=time.time()+51*60
admission=json.loads((HERE/'dispatch-admission.json').read_text())
from datetime import datetime
deadline=datetime.fromisoformat(admission['deadline_utc']).timestamp()
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 results=list(pool.map(lambda j:execute(j,deadline),jobs))
save(HERE/'harness-retry-summary.json',dict(jobs=len(results),complete=sum(bool(r.get('clean_complete_pass')) for r in results)))
