"""Small real-spawn proof: no container search or checker call."""
import json,time,os
from types import SimpleNamespace
from pathlib import Path
import medium_ballast_guard as guard
if __name__=='__main__':
    guard.install()
    from stowage.solver import ballast
    from stowage.solver.ballast_deadline import shutdown_worker
    value=ballast.run_with_deadline(guard._probe,(SimpleNamespace(vessel=SimpleNamespace(id='synthetic_3600_closed')),),deadline=time.monotonic()+30)
    assert value=='synthetic_3600_closed'
    from stowage.solver import ballast_deadline
    child_pid=ballast_deadline._process.pid
    child_record=Path(__file__).parent/f'ballast-child-{child_pid}.json'
    observed=json.loads(child_record.read_text());assert observed['parent_pid']==os.getpid() and observed['private_registry_vessel']==value and observed['peak_rss_bytes']>0
    shutdown_worker()
    (Path(__file__).parent/'medium-child-guard-probe.json').write_text(json.dumps(dict(passed=True,actual_spawn=True,returned_private_vessel_id=value,numerical_search=False,owned_child_pid=child_pid,parent_pid=os.getpid(),child_record=str(child_record),private_registry_pid_ppid_rss_verified=True)))
    print('Real-spawn private-registry/PID/RSS probe passed')
