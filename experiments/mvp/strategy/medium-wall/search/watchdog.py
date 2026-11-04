"""Aggregate map watchdog, exact recorded own PIDs only; no process scanning."""
import os,signal,time,json
from pathlib import Path
H=Path(__file__).resolve().parent
while not (H/'completion.json').exists():
 try:
  progress=json.loads((H/'progress.json').read_text()); own={r['pid'] for r in json.loads((H/'owned-pids.json').read_text())}
  for a in progress['active']:
   if a['pid'] not in own:raise RuntimeError('Not owned')
   folder=H/'runs'/a['map_id']/a['config'];summary=folder/'summary.json'
   if summary.exists():continue
   w=json.loads((folder/'worker.json').read_text())
   completed=[json.loads(p.read_text()) for p in (H/'runs'/a['map_id']).glob('*/summary.json')]
   used=sum(float(r.get('elapsed_s',0)) for r in completed)
   elapsed=time.time()-w['started_unix']
   cutoff=2990 if 'feeder_500-' in a['map_id'] else 3590
   if used+elapsed>=cutoff:
    os.kill(a['pid'],signal.SIGTERM)
    row=dict(status='aggregate_watchdog_timeout',elapsed_s=elapsed,pid=a['pid'],prior_used_s=used,map_id=a['map_id'],config=a['config'],solver_complete=False,checker_certified=False,cutoff_s=cutoff)
    temp=summary.with_suffix('.watchdog-tmp');temp.write_text(json.dumps(row,indent=2)+'\n');temp.replace(summary)
 except FileNotFoundError:pass
 time.sleep(2)
(H/'watchdog-completion.json').write_text(json.dumps({'completed_unix':time.time(),'pid':os.getpid()})+'\n')
