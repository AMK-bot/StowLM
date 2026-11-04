"""Keep experiment tests inside the write fence, including third-party caches."""
from pathlib import Path
import os,sys
sys.dont_write_bytecode=True
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['STOWAGE_VESSEL_CACHE_DIR']=str(Path(__file__).parent/'runtime-cache'/str(os.getpid()))
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','STOWAGE_IN_PORT_WORKERS'):
    os.environ[name]='1'
