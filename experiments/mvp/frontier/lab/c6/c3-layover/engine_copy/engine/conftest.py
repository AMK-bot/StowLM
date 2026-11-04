"""Allow the required plain pytest command to import frozen Frontier helpers."""
import os
from pathlib import Path
import sys

FRONTIER = Path(__file__).resolve().parent.parent
ROOT = FRONTIER.parents[2]
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
             'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
for path in (ROOT / 'src', FRONTIER, FRONTIER / 'lab'):
    sys.path.insert(0, str(path))
