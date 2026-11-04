"""Read-only source qualification probe: no missing-row or policy bypass."""
import hashlib,json,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from cache_runtime import redirect_cache
redirect_cache(HERE/'runtime')
from stowage.io import load_instance
from stowage.solver.construct import _build_assignment_model
root=HERE/'necessary';row=next(r for r in json.loads((root/'selection-saved.json').read_text())['eligible'] if r['id']=='saved-025')
started=time.monotonic();result={'instance_id':load_instance(row['path']).id,'input':row['path']}
try:
 model=_build_assignment_model(load_instance(row['path']),direction='necessary',deadline=started+45)
 result.update(available=True,physics_states=model.physics.state_names)
except (ValueError,TimeoutError) as e:result.update(available=False,error=str(e))
result.update(wall_s=time.monotonic()-started,bank_sha256=hashlib.sha256(Path('src/stowage/linear/margin_policy_data.json').read_bytes()).hexdigest())
(root/'qualification-probe.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
