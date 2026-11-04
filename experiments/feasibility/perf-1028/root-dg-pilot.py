import json
import time
from pathlib import Path
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig
from candidate import make_engine
from stowage.io import load_instance
import numpy as np
root = Path(__file__).resolve().parent
inst = load_instance(root.parent / 'fix-t4-measure/full-current.json')
rows = []
masks = []
for opt in (0, 1, 0, 1):
    eng = make_engine(inst, {'global_screen':0}, optimized=bool(opt))
    original = eng._c1g
    components=[]
    def measured(*args, **kwargs):
        start=time.perf_counter()
        out=original(*args, **kwargs)
        components.append(time.perf_counter()-start)
        return out
    eng._c1g=measured
    start=time.perf_counter()
    masks.append(eng.legal_index())
    rows.append({'optimized':opt,'index_ms':1000*(time.perf_counter()-start),'c1g_ms':1000*sum(components),'actions':len(masks[-1])})
for mask in masks[1:]:
    np.testing.assert_array_equal(masks[0],mask)
(root / 'root-dg-pilot.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps(rows))
