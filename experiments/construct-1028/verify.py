"""Default solver suite with task-local caches, including macOS spawned workers."""
from pathlib import Path
import os,sys
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[key]='1'
from cache_runtime import redirect_cache
HERE=Path(__file__).resolve().parent
redirect_cache(HERE/'verification-runtime')
if __name__=='__main__':
 import pytest
 slow='--slow' in sys.argv
 marker='slow and not gpu and not net' if slow else 'not slow and not gpu and not net'
 xml='solver-slow-tests.xml' if slow else 'solver-tests.xml'
 raise SystemExit(pytest.main(['-o','addopts=--import-mode=importlib','-q','-p','no:cacheprovider','-m',marker,'--basetemp',str(HERE/'verification-runtime/pytest-tmp'),'--junitxml',str(HERE/xml),'tests/solver']))
