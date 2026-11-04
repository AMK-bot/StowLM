"""I/O-only sampler of existing durable telemetry; no search or physics calls."""
from pathlib import Path
import json,time,os
H=Path(__file__).resolve().parent
while not (H/'completion.json').exists():
 rows=[]
 try:
  progress=json.loads((H/'progress.json').read_text())
  for active in progress['active']:
   p=H/'runs'/active['map_id']/active['config']/'greedy-progress.json'
   if not p.exists():continue
   r=json.loads(p.read_text());rows.append(dict(time_unix=time.time(),**active,greedy_aboard=r.get('max_aboard'),greedy_nodes=r.get('nodes_seen'),constructor_depth=r.get('constructor',{}).get('max_depth'),constructor_empty_prefixes=r.get('constructor',{}).get('empty_prefixes')))
  with (H/'curves.jsonl').open('a') as f:
   for r in rows:f.write(json.dumps(r,sort_keys=True)+'\n')
 except (FileNotFoundError,json.JSONDecodeError):pass
 time.sleep(30)
(H/'observer-completion.json').write_text(json.dumps(dict(pid=os.getpid(),completed_unix=time.time()))+'\n')
