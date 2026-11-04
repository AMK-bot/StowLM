"""Independent current-live default contract epoch; no primary artifact mutation."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parent))
from worker import HERE,ROOT,snapshot,canonical,save,sha
out=HERE/'formal-live-v2-contracts';out.mkdir(exist_ok=True);freeze=out/'runtime-freeze-live.json';assert not freeze.exists()
b=snapshot(False);s=snapshot(True);save(freeze,dict(created_utc=datetime.now(timezone.utc).isoformat(),epoch=canonical(s),base_sources=b,lookahead_runtime_sources=s,worker_sha256=sha(HERE/'worker.py'),controller_sha256=sha(HERE/'controller.py'),policy='Current live production->lookahead->securing outer, default12/128, full strict source hashes, no exclusions.'))
old=json.loads((HERE/'formal-v2-contracts/manifest.json').read_text());weighted=[]
for job in old['jobs']:
 oldresult=json.loads((HERE/'formal-v2-contracts'/job['case']['case']/'result.json').read_text());weighted.append((oldresult['cpu_s'],job))
shards=[[],[]];weights=[0.,0.]
for cost,job in sorted(weighted,key=lambda x:-x[0]):
 i=min(range(2),key=weights.__getitem__);shards[i].append(job);weights[i]+=cost
for i,jobs in enumerate(shards):
 d=out/f'shard{i}';d.mkdir();
 for job in jobs:job.update(directory=str(d/job['case']['case']),freeze_manifest=str(freeze),lookahead_config={'support_conditioned':True},duty_fraction=.9)
 save(d/'manifest.json',dict(directory=str(d),worker=str(HERE/'worker.py'),watchdog_s=650,jobs=jobs,estimate_s=420,old_contract_cpu_weight_s=weights[i],admission='Root authorized2CPU4GB, native1, noGPU, current-live100gold contracts, target<=7min per balanced shard; no random rollouts.'))
save(out/'preparation.json',dict(epoch=canonical(s),counts=list(map(len,shards)),old_cpu_weights_s=weights,estimate_wall_s_each=420,created_utc=datetime.now(timezone.utc).isoformat(),native_threads=1,gpu=False))
print(json.dumps(dict(epoch=canonical(s),counts=list(map(len,shards)),old_cpu_weights_s=weights)))
