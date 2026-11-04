"""One authorized3600s V4proposal finish, sharing the released map CPU slot."""
from pathlib import Path
import argparse,fcntl,importlib.util,json,os,sys,time
H=Path(__file__).resolve().parent
if Path(sys.prefix).resolve()!=(H.parents[4]/'.venv').resolve():
 python=H.parents[4]/'.venv/bin/python';os.execv(str(python),[str(python),*sys.argv])
p=argparse.ArgumentParser();p.add_argument('--map',required=True);p.add_argument('--result',required=True);p.add_argument('--name',default='cp500000');p.add_argument('--budget',type=float,default=3600);a=p.parse_args()
assert a.map=='medium-feeder_500-random_yard-185100000'
spec=importlib.util.spec_from_file_location('medium_wall_borrowed_finisher',H/'borrowed_finisher.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
mod.HERE=H/'cp-finisher';mod.HERE.mkdir(parents=True,exist_ok=True)
with (mod.HERE/'controller.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 mod.save(mod.HERE/'owned.json',dict(pid=os.getpid(),started_unix=time.time(),num_workers=1,map_id=a.map,source_helper_sha256=mod.sha(H/'borrowed_finisher.py')))
 result=Path(a.result).resolve();assert result.is_relative_to(H/'cp-candidates')
 task=dict(name=a.name,map_id=a.map,raw_result=str(result.relative_to(mod.ROOT)),budget_s=a.budget)
 row=mod.run_task(task);print(json.dumps({k:v for k,v in row.items() if k!='diagnostics'}))
