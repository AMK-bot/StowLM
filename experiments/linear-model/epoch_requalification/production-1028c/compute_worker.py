"""Execute unchanged producer code with an in-process freeze abort monitor."""
from pathlib import Path
import json
import os
import runpy
import sys
import threading
import time
ROOT=Path(__file__).resolve().parent

def monitor():
    while True:
        if (ROOT/'ABORT').exists():
            os.write(2,b'Frozen closure drift: stopping own numerical worker.\n')
            os._exit(86)
        time.sleep(1)
threading.Thread(target=monitor,daemon=True).start()
if sys.argv[1]=='-m':
    module=sys.argv[2];sys.argv=[module,*sys.argv[3:]]
    runpy.run_module(module,run_name='__main__',alter_sys=True)
else:
    path=Path(sys.argv[1]).resolve();sys.argv=[str(path),*sys.argv[2:]]
    sys.path.insert(0,str(path.parent))
    runpy.run_path(str(path),run_name='__main__')
