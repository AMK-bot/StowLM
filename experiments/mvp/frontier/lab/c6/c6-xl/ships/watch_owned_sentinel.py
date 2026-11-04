"""Idle sentinel interlock: request stop only for one recorded owned child."""
import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('ownership',type=Path);a=p.parse_args()
record=json.loads(a.ownership.read_text());pid=record['owned_child_pid'];ships=a.ownership.parent
root=ships.parents[6]
sentinel=root/'experiments/mvp/frontier/lab/c1/leader/BENCH-RUNNING'
execution=a.ownership.with_name(a.ownership.name.replace('.ownership.json','.execution.json'))
started=time.time()
while not execution.exists():
    if sentinel.exists():
        reason=dict(reason='BENCH-RUNNING sentinel appeared: leave primary harness lane sole busy worker',owned_child_pid=pid,ownership=str(a.ownership),time=time.time())
        (ships/f'stop-{pid}.json').write_text(json.dumps(reason,indent=2))
        (ships/f'sentinel-watch-{pid}.json').write_text(json.dumps(reason,indent=2));break
    time.sleep(.1)
else:
    (ships/f'sentinel-watch-{pid}.json').write_text(json.dumps(dict(status='owned_job_finished',owned_child_pid=pid,sentinel=str(sentinel),watched_s=time.time()-started),indent=2))
