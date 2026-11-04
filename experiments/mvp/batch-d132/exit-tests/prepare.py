from pathlib import Path
import shutil,json,hashlib
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
SRC=ROOT/'experiments/mvp/batch-d132/checker'
for n in ('admission.py','ledger.py','owned.py','runtime.py'):
 t=(SRC/n).read_text().replace('D132-checker','D132-exit').replace('batch-d132/checker','batch-d132/exit-tests')
 if n=='runtime.py':
  t=t.replace('("src/stowage", "params")','("src", "params", "models/glimmer-30b-4bit", "experiments/mvp/gold_tools")')
  t=t.replace('if name == "stowage" or name.startswith("stowage."):','if name in ("stowage", "llm") or name.startswith(("stowage.", "llm.")):').replace('root / "src/stowage"','root / "src"')
 (HERE/n).write_text(t)
TESTS=HERE/'tests';TESTS.mkdir(exist_ok=True)
V2=Path('/private/tmp/stowlm-d132-assembly-v2-20261030/candidate')
BASE=V2.parent/'baseline'
for folder in ('d132','d134-dataset','d135-dpo-checkpoint'):
 stage=ROOT/'experiments/mvp/exit/patches'/folder
 for path in (stage/'tests').glob('test_*.py'):
  t=path.read_text()
  t=t.replace('STAGE=Path(__file__).resolve().parents[1]',f"STAGE=Path({str(V2/'src/llm/exit')!r})")
  t=t.replace('STAGE = Path(__file__).resolve().parents[1]',f"STAGE = Path({str(V2/'src/llm/exit')!r})")
  if folder=='d134-dataset':
   t=t.replace("str(STAGE/'tests/pool_probe.py')", "str(Path(__file__).parent/'pool_probe.py')")
   # Full saved dataset integration is separate, under explicitly coordinated pool2.
   t=t.replace("inputs=STAGE/'measurements/inputs.json'", "pytest.skip('separate explicit pool2 saved-data qualification'); inputs=STAGE/'measurements/inputs.json'")
  if folder=='d135-dpo-checkpoint':
   t=t.replace("ROOT/'src/llm/exit/dpo.py'",f"Path({str(BASE/'src/llm/exit/dpo.py')!r})")
  (TESTS/path.name).write_text(t)
 probe=stage/'tests/pool_probe.py'
 if probe.exists():
  t=probe.read_text().replace("llm.exit.__path__.insert(0,str(Path(__file__).resolve().parents[1]))",f"assert str(Path(llm.exit.__file__).resolve()).startswith({str(V2/'src')!r})")
  (TESTS/probe.name).write_text(t)
print('prepared tooling and copied tests only')
