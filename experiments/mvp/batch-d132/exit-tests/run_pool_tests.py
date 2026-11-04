from pathlib import Path
import sys,os,tempfile,json
HERE=Path(__file__).resolve().parent;RUNTIME=Path(sys.argv[1]).resolve();OUT=Path(sys.argv[2]).resolve();OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(RUNTIME/'src'));os.environ['TMPDIR']=str(OUT/'tmp');(OUT/'tmp').mkdir(exist_ok=True);tempfile.tempdir=os.environ['TMPDIR']
from runtime import source_pins,verify_runtime,save,assert_runtime_imports
before=verify_runtime(RUNTIME)
import pytest
args=[str(HERE/'tests/test_dataset_integration_v2.py'),'-q','-p','no:cacheprovider','-o','addopts=--import-mode=importlib','--basetemp',str(OUT/'temp'),'--junitxml',str(OUT/'tests.xml')]
code=pytest.main(args)
save(OUT/'result.json',dict(code=int(code),argv=args,source_before=before,source_unchanged=source_pins(RUNTIME)==before['files'],imports=assert_runtime_imports(RUNTIME),gpu=False))
raise SystemExit(int(code))
