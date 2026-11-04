"""CPU tests against the prospective D124+D128+D129 bundle, without live edits."""
import importlib.abc
import importlib.util
from pathlib import Path
import sys
STAGE=Path(__file__).resolve().parent
PREVIOUS=STAGE.parent/'d124'
ROOT=next(p for p in STAGE.parents if (p/'pyproject.toml').exists())
TARGETS=('checker_watchdog','outcomes','selection','preferences','metrics','fitness',
         'audit','runner','progress','code_epoch','final_recipe','reverify','salvage',
         'finisher_outcomes','control')
class Loader(importlib.abc.Loader):
    def __init__(self,name):self.name=name
    def create_module(self,spec):return None
    def exec_module(self,module):
        source=STAGE/(self.name+'.py')
        if not source.exists():source=PREVIOUS/source.name
        live=ROOT/'src/llm/exit'/source.name
        filename=source if self.name=='checker_watchdog' else live
        module.__file__=str(filename)
        exec(compile(source.read_text(),str(filename),'exec'),module.__dict__)
class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        prefix='llm.exit.'
        if fullname.startswith(prefix) and fullname[len(prefix):] in TARGETS:
            return importlib.util.spec_from_loader(fullname,Loader(fullname[len(prefix):]))
sys.meta_path.insert(0,Finder())
if __name__=='__main__':
    import pytest
    raise SystemExit(pytest.main(sys.argv[1:] or ['tests/llm/exit','-q','-o','addopts=--import-mode=importlib']))
