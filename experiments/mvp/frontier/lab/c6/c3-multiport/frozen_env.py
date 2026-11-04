"""Native-source bootstrap for this isolated experiment; no live lab imports."""
import os
from pathlib import Path
import sys
EXPERIMENT = Path(__file__).resolve().parent
ROOT = next(p for p in EXPERIMENT.parents if (p / 'src/stowage').is_dir())
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
sys.dont_write_bytecode = True
os.environ.setdefault('STOWAGE_VESSEL_CACHE_DIR', str(EXPERIMENT / 'runtime/vessel-cache'))
