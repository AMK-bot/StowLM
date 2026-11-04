"""Drain the fixed first20 cohort; seal/report only after controller lock release."""
from pathlib import Path
from datetime import datetime,timezone
import fcntl,hashlib,json,os,subprocess,sys,time
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import matched
P=HERE/'matched'
def now():return datetime.now(timezone.utc).isoformat()
def run():
 guard=(P/'d213-finish.lock').open('a+');fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
 plan=json.loads((P/'stop-plan.json').read_text());man=json.loads((P/'manifest.json').read_text())
 assert plan['decision']=='D213' and plan['map_limit']==20
 assert plan['manifest_sha256']==hashlib.sha256((P/'manifest.json').read_bytes()).hexdigest()
 assert plan['instance_ids']==[j['instance_id'] for j in man['maps'][:20]]
 lock=(P/'controller-D206.lock').open('a+')
 while True:
  # Coverage only while running: no pass, moves, timeout or admission outcomes inspected.
  present=sum((P/'rows'/iid/f'{arm}.json').exists() for iid in plan['instance_ids'] for arm in matched.ARMS)
  status=json.loads((P/'status-D206.json').read_text())
  matched.write(P/'D213-progress.json',dict(status='waiting_fixed20_drain',present=present,expected=60,updated_at=now(),controller=status))
  if status.get('status')=='failed' and present<60:raise RuntimeError('Controller failed before fixed20 completed; preserve rows and investigate')
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:time.sleep(20);continue
  if present!=60:raise RuntimeError('Controller exited without60 planned rows')
  break
 # Old in-memory controller hits the prospective batch fence before spawning map21.
 for j in man['maps'][20:]:
  for directory in ('batches-D206','batches'):
   if (P/directory/j['instance_id']/'start.json').exists():raise RuntimeError('Out-of-plan cohort released')
 for j in man['maps'][:20]:
  if not matched.completed_map(man,j):raise RuntimeError('Missing final cohort')
 stamp=now();status=dict(status='complete',decision='D213',updated_at=stamp,maps=20,total_maps=55,trials=60,planned_stop=True,scope='first20 in original fixed order;20of55 by plan')
 matched.write(P/'status-D213.json',status)
 matched.write(P/'active-controller.json',dict(pid=os.getpid(),status_path=str(P/'status-D213.json'),policy='D206',decision='D213',at=stamp))
 matched.write(P/'D213-final-verification.json',dict(verified_at=stamp,planned_maps=20,planned_trials=60,all_complete_source_bound=True,no_out_of_plan_releases=True,stop_plan_sha256=matched.sha(P/'stop-plan.json')))
 # Active extension marker retains one serial physics worker. Canonical status
 # enables the original supplementary worker once extension handoff permits it.
 matched.write(P/'status.json',status)
 subprocess.run([sys.executable,'-B',str(HERE/'matched_report.py')],check=True,env=os.environ.copy(),cwd=matched.ROOT)
 matched.write(P/'D213-progress.json',dict(status='complete',updated_at=now(),present=60,expected=60))
 print(json.dumps(status),flush=True)
if __name__=='__main__':
 try:run()
 except Exception as e:
  matched.write(P/'D213-progress.json',dict(status='failed',updated_at=now(),error=f'{type(e).__name__}: {e}'));raise
