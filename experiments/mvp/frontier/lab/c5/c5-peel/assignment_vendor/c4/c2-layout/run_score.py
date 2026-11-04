"""One-process score runner with scoped caches and recorded owned PID/load."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'vessel-cache')
sys.dont_write_bytecode = True
if '--workers' not in sys.argv or sys.argv[sys.argv.index('--workers')+1]!='1':
    raise ValueError('This wrapper requires --workers 1')
output = Path(sys.argv[sys.argv.index('--out')+1])
if not output.resolve().is_relative_to(HERE):
    raise ValueError('Output must be inside this executive folder')
output.mkdir(parents=True,exist_ok=True)
info = dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),
            loadavg_start=list(os.getloadavg()),arguments=sys.argv[1:])
record = output/'RUNINFO.txt'
record.write_text(json.dumps(info,indent=2)+'\n')
print('Owned score PID',os.getpid(),flush=True)
try:
    runpy.run_path(str(HERE.parents[1]/'score.py'),run_name='__main__')
finally:
    info.update(ended_utc=datetime.now(timezone.utc).isoformat(),loadavg_end=list(os.getloadavg()))
    record.write_text(json.dumps(info,indent=2)+'\n')
