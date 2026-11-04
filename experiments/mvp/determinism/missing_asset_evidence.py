"""Train-only provenance correction from preserved raw diagnostics and asset hashes."""
import json,hashlib,tomllib
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;R=H.parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
params=R/'params/fleet/feeder_1700.toml';v=tomllib.loads(params.read_text());matches=[]
def walk(x,path=()):
 if isinstance(x,dict):
  if'offsets_path'in x:matches.append(dict(table=list(path),path=x['offsets_path'],sha256=x.get('offsets_sha256')))
  for k,z in x.items():walk(z,(*path,k))
 elif isinstance(x,list):
  for i,z in enumerate(x):walk(z,(*path,i))
walk(v);assert len(matches)==1;decl=matches[0];asset=R/decl['path'];assert sha(asset)==decl['sha256']
rows=[]
for seed,shard,rep in((31100000,1,6),(31100001,0,4)):
 name=f'train20-{seed}';p=R/f'experiments/mvp/lookahead/formal-v2-archive/shard{shard}'/name/f'baseline-{rep:02}.json';x=json.loads(p.read_text());screen=x['diagnostics']['securing_screen'];assert screen['unavailable:FileNotFoundError']==1 and screen['evaluations']==0 and screen['fail_open:bounds']>0;rows.append(dict(case=name,repetition=rep,artifact=str(p),artifact_sha256=sha(p),status=x['status'],screen=screen))
old=R/'experiments/mvp/lookahead/runtime-v2';current=H/'runtime-current-1'
result=dict(created_utc=datetime.now(timezone.utc).isoformat(),train_only=True,declared_asset=decl,params_path=str(params),params_sha256=sha(params),asset_live_sha256=sha(asset),old_archive_asset_present=(old/decl['path']).exists(),current_snapshot_asset_present=(current/decl['path']).exists(),current_snapshot_asset_sha256=sha(current/decl['path']),train_witnesses=rows,root_cause='Archived execution omitted the declared hull-offset resource. NecessaryDeckSecuringScreen caught FileNotFoundError during initialization, left bounds_available=False, and deliberately failed open. Source-only provenance did not establish equivalent securing policy.',not_established='No hashseed/float/cache nondeterminism is established by the earlier seven changed control outcomes. Current fixed-prefix matrix separately tests current source.',proposed_fix='Archive declared path+SHA asset closure and gate benchmark eligibility on evidenced bounds availability; preserve old artifacts and mark affected recovered rows non-equivalent. No production force-equation or mask-semantics patch justified.')
(H/'missing-asset-evidence.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps({'asset':decl,'old_archive_present':result['old_archive_asset_present'],'train_witnesses':len(rows)}))
