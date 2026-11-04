from pathlib import Path
import shutil,json,hashlib
from runtime import source_pins,object_sha
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3];RUNTIME=Path('/private/tmp/stowlm-d132-exit-qualified-runtime-20261030')
files=['experiments/mvp/sft/dataset/build.py','experiments/mvp/sft/config.json','experiments/mvp/exit/wait_for_smoke.py','experiments/mvp/corpus/manifest.json','experiments/mvp/corpus/test/SHA256SUMS','experiments/mvp/corpus/showcase/SHA256SUMS']
path=HERE/'support-assets.json';record=json.loads(path.read_text())
for name in files:
 source=ROOT/name;target=RUNTIME/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
 assert source.read_bytes()==target.read_bytes();record['files'][name]=hashlib.sha256(target.read_bytes()).hexdigest()
path.write_text(json.dumps(record,indent=2)+'\n')
pins=source_pins(RUNTIME);(HERE/'runtime-pins'/f'{RUNTIME.name}.json').write_text(json.dumps(dict(files=pins,source_epoch_sha256=object_sha(pins)),indent=2)+'\n')
print('added exact supporting scripts/config + corpus metadata ONLY; no TEST bodies')
