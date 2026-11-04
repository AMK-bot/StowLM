"""Test D124 as an import overlay; never rewrite the running process's sources."""
import importlib.abc
import importlib.util
from pathlib import Path
import sys

STAGE=Path(__file__).resolve().parent
ROOT=STAGE.parents[4]
TARGETS=('checker_watchdog','outcomes','selection','preferences','metrics','fitness',
         'audit','runner','progress','code_epoch','final_recipe')


class StagedLoader(importlib.abc.Loader):
    def __init__(self,name):self.name=name
    def create_module(self,spec):return None
    def exec_module(self,module):
        source=STAGE/f'{self.name}.py'
        live=ROOT/'src/llm/exit'/source.name
        # Existing modules derive repository paths from __file__. The standalone
        # watchdog executes its own file in subprocesses, so it uses its staged path.
        filename=source if self.name=='checker_watchdog' else live
        module.__file__=str(filename)
        exec(compile(source.read_text(),str(filename),'exec'),module.__dict__)


class StagedFinder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        prefix='llm.exit.'
        if fullname.startswith(prefix) and fullname[len(prefix):] in TARGETS:
            return importlib.util.spec_from_loader(fullname,StagedLoader(fullname[len(prefix):]))
        return None


sys.meta_path.insert(0,StagedFinder())
if __name__=='__main__':
    import pytest
    raise SystemExit(pytest.main(sys.argv[1:] or
        ['tests/llm/exit','-q','-o','addopts=--import-mode=importlib']))
