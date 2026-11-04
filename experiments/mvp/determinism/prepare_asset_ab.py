"""Prepare identical old-source runtimes differing only in one declared data asset."""
import json,hashlib,shutil,tomllib
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;R=H.parents[2];old=R/'experiments/mvp/lookahead/runtime-v2';f=json.loads((old/'experiments/mvp/lookahead/runtime-freeze-v2.json').read_text());inv=json.loads((H/'inventory.json').read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n')
expected=dict(f['lookahead_runtime_sources'])
for p in(old/'src/llm/__init__.py',old/'models/glimmer-30b-4bit/tokenizer.json'):expected[str(p.relative_to(old))]=sha(p)
params=tomllib.loads((old/'params/fleet/feeder_1700.toml').read_text());decl=params['hull_form'];asset_path=decl['offsets_path'];asset_digest=decl['offsets_sha256'];assert sha(R/asset_path)==asset_digest
runtimes={}
for treatment in('missing','provided'):
 target=H/f'runtime-asset-{treatment}-old34df';assert not target.exists();files=dict(expected)
 for name,digest in files.items():
  source=old/name;assert sha(source)==digest;dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest);assert sha(dest)==digest
 if treatment=='provided':
  dest=target/asset_path;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(R/asset_path,dest);files[asset_path]=asset_digest;assert sha(dest)==asset_digest
 save(target/'manifest.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),files=files,epoch=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest(),source_epoch=f['epoch'],treatment=treatment,declared_asset=dict(path=asset_path,sha256=asset_digest),policy='Byte-identical old34df source/params/tokenizer. Only treatment is absent vs checksum-correct declared KCS offset file. No test inputs/traces.'))
 runtimes[treatment]=str(target)
assert all(sha(Path(runtimes['missing'])/name)==sha(Path(runtimes['provided'])/name)for name in expected)
prefix=next(p for p in inv['prefixes']if p['case']=='train20-31100001'and p['repetition']==4);jobs=[]
for seed in(0,1,42):
 for treatment in('missing','provided'):
  jobs.append(dict(case=prefix['case'],repetition=4,prefix=prefix['path'],prefix_sha256=prefix['sha256'],runtime=runtimes[treatment],hashseed=seed,treatment=treatment,cache_mode='cold',duty_fraction=.5,directory=str(H/'asset-ab'/f'seed{seed}-{treatment}'),os_cpu_soft_s=90,os_cpu_hard_s=100,public_lashing=True))
save(H/'asset-ab-preregistration.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),status='PREPARED; numerical admission pending',train_only=True,jobs=jobs,old_source_epoch=f['epoch'],declared_asset=dict(path=asset_path,sha256=asset_digest),estimate=dict(cores=1,ram_gb=1,native_threads=1,gpu=False,cpu_minutes_max=5,wall_minutes_max=10,basis='6.25CPU coldprefix plus5–20s unverified publiclashing stage each; serial50%duty.'),expected_contrast='Missing boundsFalse/pass-through legalPlace vs provided boundsTrue/fittingratio1.117reject, invariant across processhashseeds. No force equations or production source altered.'))
print(json.dumps(dict(jobs=len(jobs),runtimes=runtimes,source_epoch=f['epoch'])))
