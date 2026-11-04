from pathlib import Path
import shutil,json,hashlib
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
RUNTIME=Path('/private/tmp/stowlm-d132-exit-qualified-runtime-20261030')
files=['experiments/mvp/gold_tools/driver.py',*['models/glimmer-30b-4bit/'+n for n in ('config.json','generation_config.json','tokenizer.json','tokenizer_config.json','model.safetensors.index.json')]]
pins={}
for name in files:
 source=ROOT/name;target=RUNTIME/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
 assert source.read_bytes()==target.read_bytes();pins[name]=hashlib.sha256(target.read_bytes()).hexdigest()
for path in (HERE/'tests').glob('*.py'):
 text=path.read_text().replace('/private/tmp/stowlm-d132-assembly-v2-20261030/candidate/src','/private/tmp/stowlm-d132-exit-qualified-runtime-20261030/src')
 path.write_text(text)
(HERE/'support-assets.json').write_text(json.dumps(dict(runtime=str(RUNTIME),weight_shards_copied=False,files=pins),indent=2)+'\n')
from runtime import source_pins,object_sha
pins=source_pins(RUNTIME);(HERE/'runtime-pins').mkdir(exist_ok=True)
(HERE/'runtime-pins'/f'{RUNTIME.name}.json').write_text(json.dumps(dict(files=pins,source_epoch_sha256=object_sha(pins)),indent=2)+'\n')
print(json.dumps(dict(runtime=str(RUNTIME),support_files=len(files),pins=len(pins))))
