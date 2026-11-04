"""Train-only immutable inputs/prefixes and coherent execution snapshot."""
import hashlib,json,shutil
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;R=H.parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n')
assert not(H/'inventory.json').exists()
cases=[]
for seed,shard,reps in((31100000,1,(6,15)),(31100001,0,(4,13))):
 name=f'train20-{seed}';old=R/f'experiments/mvp/lookahead/formal-paired/shard{shard}'/name
 meta=json.loads((old/'result.json').read_text());pin=meta['job']['case']['instance'];assert'/train/'in pin['source']and'/test/'not in pin['source'];src=Path(pin['path']);assert sha(src)==pin['sha256'];dest=H/'inputs'/name/'instance.json';dest.parent.mkdir(parents=True);shutil.copy2(src,dest)
 shutil.copy2(old/'result.json',dest.parent/'old-case-result.json')
 for rep in reps:
  trace=old/f'baseline-{rep:02}.json';row=json.loads(trace.read_text());assert row['status']=='dead_end_proxy';target=dest.parent/f'baseline-{rep:02}.json';shutil.copy2(trace,target)
  actions=[x['action']for x in row['steps']];prefix=dict(case=name,seed=seed,repetition=rep,depth=len(actions),actions=actions,instance=dict(path=str(dest),sha256=sha(dest),source=pin['source']),trace=dict(path=str(target),sha256=sha(target),source=str(trace)),sampling_seed=row['seed'],historical_dead_end=row.get('dead_end'))
  p=dest.parent/f'prefix-{rep:02}.json';save(p,prefix);cases.append(dict(path=str(p),sha256=sha(p),case=name,repetition=rep,depth=len(actions)))
# Pin every local runtime/resource file, not only Python: no live fallback.
A=H/'runtime-current-1';expected={}
for folder in('src/stowage','src/llm/stowlm','src/llm/securing','src/llm/lookahead','src/llm/instructions','params','data'):
 for p in sorted((R/folder).rglob('*')):
  if p.is_file()and'__pycache__'not in p.parts and p.suffix!='.pyc':expected[str(p.relative_to(R))]=sha(p)
for p in(R/'src/llm/__init__.py',R/'models/glimmer-30b-4bit/tokenizer.json',R/'pyproject.toml',R/'uv.lock'):expected[str(p.relative_to(R))]=sha(p)
for name,digest in expected.items():
 p=R/name;q=A/name;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q);assert sha(q)==digest and sha(p)==digest,name
assert all(sha(R/name)==digest for name,digest in expected.items()),'Source changed while freezing; preserve this failed epoch and create another labelled snapshot.'
save(A/'manifest.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),files=expected,epoch=hashlib.sha256(json.dumps(expected,sort_keys=True,separators=(',',':')).encode()).hexdigest(),policy='All runtime files, params, data and actual CPU tokenizer pinned. No source exclusions; execution only from this root.'))
save(H/'inventory.json',dict(train_only=True,allowed_seeds=[31100000,31100001],prefixes=cases,runtime=str(A),runtime_manifest_sha256=sha(A/'manifest.json'),created_utc=datetime.now(timezone.utc).isoformat()))
save(H/'preregistration.json',dict(train_only=True,primary_prefixes=cases,hashseeds=[0,1,42],cache_matrix=['nativecold-securingcold','nativewarm-securingcold','nativecold-securingwarm','nativewarm-securingwarm'],pilot=dict(prefix=cases[2],hashseed=0,native_cache='cold',securing_cache='cold',cores=1,ram_gb=1,expected_wall_s=[10,30],hard_child_cpu_soft_s=55,hard_child_cpu_hard_s=60,admitted_by='root shortpilot only; fullmatrixqueued'),full_matrix_status='Not admitted; exact cache warmup protocol will be recorded before launch.',comparison='Same instance bytes and exact fixed ACTION prefix; compare full legal ACTION set and actual production TOKEN sets at every proper prefix of each legal action. No test inputs or traces permitted.'))
print(json.dumps(dict(prefixes=len(cases),runtime_files=len(expected),runtime=str(A))))
