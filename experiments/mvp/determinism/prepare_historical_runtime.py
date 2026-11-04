"""Prepare historical source epoch under this fence; no replay starts."""
import json,hashlib,shutil
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;R=H.parents[2];source=H/'runtime-current-1';prior=R/'experiments/mvp/lookahead/runtime-v1';target=H/'runtime-historical-1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert not target.exists();old=json.loads((prior/'experiments/mvp/lookahead/runtime-freeze-v1.json').read_text());current=json.loads((source/'manifest.json').read_text());expected=dict(current['files']);overridden=[]
for name,digest in old['lookahead_runtime_sources'].items():
 assert sha(prior/name)==digest;expected[name]=digest;overridden.append(name)
for name,digest in expected.items():
 p=prior/name if name in overridden else source/name;assert sha(p)==digest,name;q=target/name;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q);assert sha(q)==digest,name
value=dict(created_utc=datetime.now(timezone.utc).isoformat(),files=expected,epoch=hashlib.sha256(json.dumps(expected,sort_keys=True,separators=(',',':')).encode()).hexdigest(),historical_lookahead_epoch=old['epoch'],historical_files=overridden,supplemental_assets_from_current_snapshot=[n for n in expected if n not in overridden],policy='HistoricalV1 runtime sources/params exact, augmented only with resource/dependency/tokenizer files already pinned in current snapshot. No test input or trace. Prepared only; no process launched.')
(target/'manifest.json').write_text(json.dumps(value,sort_keys=True,indent=2)+'\n');print(json.dumps(dict(runtime=str(target),files=len(expected),historical_sources=len(overridden),epoch=value['epoch'])))
