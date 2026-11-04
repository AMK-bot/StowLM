"""Follow-up solver verification, task-local caches and recorded owned PID."""
import json,os,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;BASE=HERE.parent
sys.path.insert(0,str(BASE));sys.dont_write_bytecode=True
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='1'
from cache_runtime import redirect_cache
redirect_cache(BASE/'runtime')
if __name__=='__main__':
 import pytest
 from stowage.solver.ballast_deadline import shutdown_worker
 slow='--slow' in sys.argv;name='slow' if slow else 'default';started=time.monotonic()
 (HERE/f'verify-{name}-pid.json').write_text(json.dumps({'pid':os.getpid(),'mode':name,'start_monotonic':started})+'\n')
 try:
  code=pytest.main(['-o','addopts=--import-mode=importlib','-q','-p','no:cacheprovider','-m','slow and not gpu and not net' if slow else 'not slow and not gpu and not net','--basetemp',str(HERE/f'pytest-{name}-tmp'),'--junitxml',str(HERE/f'solver-{name}-tests.xml'),'tests/solver'])
 finally:shutdown_worker()
 (HERE/f'verify-{name}-done.json').write_text(json.dumps({'returncode':code,'wall_s':time.monotonic()-started})+'\n')
 raise SystemExit(code)
