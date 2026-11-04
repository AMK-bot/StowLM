"""Preserve transition rows, rerun only cases whose external source changed."""
import argparse,concurrent.futures,json,time
from pathlib import Path
from campaign import HERE,run
p=argparse.ArgumentParser();p.add_argument('label');p.add_argument('--workers',type=int,default=3);a=p.parse_args()
cases={r['id']:r for r in json.loads((HERE/'selection.json').read_text())['rows']}
for round in range(3):
 jobs=[]
 for path in (HERE/a.label/'runs').glob('*/*/done.json'):
  row=json.loads(path.read_text())
  if row.get('external_source_drift'):
   directory=path.parent;target=HERE/a.label/'transitions'/str(time.time_ns())/directory.parent.name/directory.name
   target.parent.mkdir(parents=True,exist_ok=True);directory.rename(target);jobs.append((cases[row['id']],row['solver']))
 if not jobs:break
 print(f'retrying {len(jobs)} transition rows, round{round+1}',flush=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:list(pool.map(lambda x:run(a.label,*x),jobs))
