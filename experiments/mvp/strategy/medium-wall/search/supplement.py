"""Conditional unused-budget probe, one worker per map across both controllers."""
from pathlib import Path
import fcntl,json,time,subprocess,sys,os
H=Path(__file__).resolve().parent
heartbeat=H/'supplement-progress.json'
if heartbeat.exists() and not (H/'supplement-completion.json').exists() and time.time()-heartbeat.stat().st_mtime<30:raise RuntimeError('Supplement controller heartbeat fresh; do not duplicate')
lock=(H/'supplement.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
reg=json.loads((H/'preregister.json').read_text());pending={j['map_id']:j for j in reg['jobs']};active={};outcomes=[];owned=[]
(H/'supplement-controller.json').write_text(json.dumps(dict(pid=os.getpid(),started_unix=time.time()))+'\n')
while pending or active:
 for mapid,job in list(pending.items()):
  folder=H/'runs'/mapid/'restricted_first_staged'
  if (folder/'summary.json').exists():outcomes.append(dict(map_id=mapid,status='resumed'));del pending[mapid];continue
  if not all((H/'runs'/mapid/c['name']/'summary.json').exists() for c in reg['configs']):continue
  past=[json.loads(p.read_text()) for p in (H/'runs'/mapid).glob('*/summary.json')];used=sum(float(r.get('elapsed_s',0)) for r in past);cap=2990 if job['ship']=='feeder_500' else 3590
  if cap-used<30:outcomes.append(dict(map_id=mapid,status='skipped_budget',prior_s=used,remaining_s=cap-used));del pending[mapid];continue
  folder.mkdir(parents=True,exist_ok=True)
  command=[sys.executable,str(H/'search.py'),'worker','--map',mapid,'--config','restricted_first_staged']
  with (folder/'worker.log').open('a') as log:p=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
  active[mapid]=p;del pending[mapid];owned.append(dict(map_id=mapid,pid=p.pid,command=command,prior_s=used,remaining_s=cap-used,started_unix=time.time()));(H/'supplement-owned-pids.json').write_text(json.dumps(owned,indent=2)+'\n')
 for mapid,p in list(active.items()):
  if p.poll() is not None:outcomes.append(dict(map_id=mapid,status='worker_exited',returncode=p.returncode));del active[mapid]
 (H/'supplement-progress.json').write_text(json.dumps(dict(active={m:p.pid for m,p in active.items()},pending=list(pending),outcomes=outcomes,updated_unix=time.time()),indent=2)+'\n')
 time.sleep(5)
(H/'supplement-completion.json').write_text(json.dumps(dict(pid=os.getpid(),completed_unix=time.time(),outcomes=outcomes),indent=2)+'\n')
