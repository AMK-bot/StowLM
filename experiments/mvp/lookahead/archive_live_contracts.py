"""Preserve all strict current-live contract runtime bytes and invoked harness."""
import json,hashlib,shutil
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;R=H.parents[2];F=H/'formal-live-v2-contracts/runtime-freeze-live.json';D=H/'runtime-live-v2-contracts'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
f=json.loads(F.read_text());expected=dict(f['lookahead_runtime_sources'])
for p in(H/'worker.py',H/'controller.py',F,R/'models/glimmer-30b-4bit/tokenizer.json',R/'pyproject.toml',R/'uv.lock',R/'src/llm/__init__.py'):
 expected[str(p.relative_to(R))]=sha(p)
assert sha(H/'worker.py')==f['worker_sha256'] and sha(H/'controller.py')==f['controller_sha256']
assert not(D/'archive-manifest.json').exists()
for name,digest in expected.items():
 p=R/name;assert sha(p)==digest,name;q=D/name;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q);assert sha(q)==digest,name
(D/'archive-manifest.json').write_text(json.dumps(dict(created_utc=datetime.now(timezone.utc).isoformat(),epoch=f['epoch'],files=expected,verified=len(expected),scope='Current-live default100gold contract epoch, separate from old primary rollout epoch. src/llm/__init__.py supplemental package initializer; no runtime policy exclusion.'),indent=2,sort_keys=True)+'\n')
print(json.dumps(dict(verified=len(expected),manifest_sha256=sha(D/'archive-manifest.json'))))
