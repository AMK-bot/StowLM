from pathlib import Path
import sys,os,tempfile,json,hashlib
HERE=Path(__file__).resolve().parent
RUNTIME=Path(sys.argv[1]).resolve();OUT=Path(sys.argv[2]).resolve();OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(RUNTIME/'src'))
from runtime import source_pins,save,object_sha,assert_runtime_imports,install_write_guard
before=source_pins(RUNTIME)
for name in ('data','dataset_pool','runner','outcome_epoch','checker_watchdog','dpo'):
 module=__import__('llm.exit.'+name,fromlist=['*'])
 actual=Path(module.__file__).resolve();expected=RUNTIME/'src/llm/exit'/(name+'.py')
 assert actual==expected,(name,actual,expected)
 assert hashlib.sha256(actual.read_bytes()).hexdigest()==before[str(expected.relative_to(RUNTIME))]
os.environ['TMPDIR']=str(OUT/'tmp');(OUT/'tmp').mkdir(exist_ok=True);tempfile.tempdir=os.environ['TMPDIR']
install_write_guard((RUNTIME/'src',RUNTIME/'params'))
import pytest
paths=[str(HERE/'tests'),*[str(RUNTIME/'tests/llm/exit'/n) for n in ('test_data_metrics.py','test_runner.py','test_review_boundaries.py','test_recovery.py','test_d129_salvage_integrity.py','test_dpo.py')]]
args=[*paths,'-q','-o','addopts=--import-mode=importlib','-p','no:cacheprovider','--basetemp',str(OUT/'temp'),'--junitxml',str(OUT/'tests.xml'),'-k','not real_mvp_dataset_bytes and not spawned_pool_order_fault']
code=pytest.main(args)
after=source_pins(RUNTIME)
save(OUT/'result.json',dict(code=int(code),argv=args,source_before=before,source_after=after,source_drift=before!=after,imports=assert_runtime_imports(RUNTIME),gpu=False))
raise SystemExit(int(code) or int(before!=after))
