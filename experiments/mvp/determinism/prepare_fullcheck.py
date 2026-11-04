"""Pin one stored TRAIN gold plan for the admitted whole-check supplement."""
import json,hashlib,shutil
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;R=H.parents[2];oldjob=R/'experiments/mvp/lookahead/formal-v2-contracts/gold-small-30100025/job.json';job=json.loads(oldjob.read_text());case=job['case'];assert'/train/'in case['instance']['source']and'/test/'not in case['instance']['source'];assert'/train/'in case['plan']['source']
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
folder=H/'fullcheck-input';folder.mkdir(exist_ok=True);pins={}
for kind in('instance','plan','checker'):
 pin=case[kind];assert sha(pin['path'])==pin['sha256'];target=folder/f'{kind}.json';shutil.copy2(pin['path'],target);pins[kind]=dict(path=str(target),sha256=sha(target),source=pin['source'])
report=json.loads((folder/'checker.json').read_text());assert report['passed']
jobs=[]
for seed,treatment in((0,'missing'),(0,'provided'),(1,'provided'),(42,'provided')):
 jobs.append(dict(case='TRAIN-gold-small-30100025',hashseed=seed,treatment=treatment,runtime=str(H/f'runtime-asset-{treatment}-old34df'),pins=pins,directory=str(H/'fullcheck'/f'seed{seed}-{treatment}'),duty_fraction=.5,os_cpu_soft_s=28 if treatment=='missing' else 90,os_cpu_hard_s=30 if treatment=='missing' else 100))
(H/'fullcheck-preregistration.json').write_text(json.dumps(dict(created_utc=datetime.now(timezone.utc).isoformat(),train_only=True,jobs=jobs,estimate=dict(cores=1,ram_gb=1,native_threads=1,gpu=False,historical_provided_cpu_s=28.264649,historical_provided_wall_s=28.43216912500793,provided_total_cpu_s_estimate=90,total_wall_minutes_estimate=[3,4],missing_cpu_s_cap=30),admission='Root admitted3provided hashseed0/1/42 plusONEcapped30CPU-s missing whole-check after assetAB naturalexit; serial50%duty, livegateeachchild.'),sort_keys=True,indent=2)+'\n');print(json.dumps(dict(jobs=4,train_only=True,pins=pins)))
