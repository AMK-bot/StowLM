"""Small direct-geometry C1 recheck; no ballast optimizer or table/KN rebuild.

Uses the design review's exact declared design/ballast definitions. Unused
hydrostatic/cross-curve tables are omitted: both evaluated checkers integrate
HullGeometry directly. No GM, operating limit or securing rating is overridden.
The review's declared lightship/tank design values remain in-memory only.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'.cache'/'vessels')
sys.path.insert(0,str(ROOT/'experiments/physics-review/design'))
import harness as H
import after_run as AR
from stowage.model import HullModel
from stowage.vessel.tanks import build_tanks
from stowage.checker.lashing import check_lashing
from stowage.checker.stability import check_stability

original_load_vessel = H.fleet.load_vessel
original_params = H._ORIGINAL_LOAD

def direct_vessel(name, *, with_hull=True):
    v = original_load_vessel(name,with_hull=False)
    if not with_hull:
        return v
    p = original_params(name)
    declared = {**p['hull'],**p.get('operating_limits',{}),
                'hydrostatics':[],'cross_curves':[],'stations':[]}
    declared.setdefault('max_draught_m',p['hull']['scantling_draught_m'])
    hull = HullModel.model_validate({k:v for k,v in declared.items() if k in HullModel.model_fields})
    return v.model_copy(update={'hull':hull,'tanks':build_tanks(p)})

if __name__ == '__main__':
    start = time.monotonic()
    name = 'feeder_1700'
    raw = (ROOT/'params/fleet/feeder_1700.toml').read_bytes()
    live = original_params(name)
    overrides = json.loads((ROOT/'experiments/physics-review/design/after.json').read_text())[name]
    overrides = copy.deepcopy(overrides)
    overrides.pop('strength_stations',None)  # unused by lashing/stability
    H.fleet.load_vessel = direct_vessel
    vessel, params = AR.apply(name,overrides)
    rows = []
    for prefixes in (['DB','WING','HEEL'],['DB','HEEL']):
        declared = AR._pattern(vessel,prefixes)
        with AR.condition_context(declared,0.1):
            instance, info = H.build_condition(name,vessel,params,'C1')
        t0 = time.monotonic()
        lash = check_lashing(instance,instance.onboard,call_index=0)
        stab = check_stability(instance,instance.onboard,call_index=0)
        row = dict(pattern=prefixes,boxes=info['boxes'],boxes_deck=info['boxes_deck'],
                   ballast_kg=sum(declared.values()),seconds=time.monotonic()-t0,
                   lashing=lash.model_dump(mode='json'),
                   stability=dict(ran=stab.ran,error=stab.error,
                                  metrics=stab.metrics,negative_margins={k:v for k,v in stab.margins.items() if v<0}))
        rows.append(row)
        payload = dict(source_params_sha256=hashlib.sha256(raw).hexdigest(),
                       source_vents_m=sorted({o['z_m'] for o in live['envelope']['openings'] if o['kind']=='hold_ventilator'}),
                       evaluated_vents_m=sorted({o['z_m'] for o in params['envelope']['openings'] if o['kind']=='hold_ventilator'}),
                       design_overrides='physics-review/design/after.json; strength omitted as unused',
                       assembly='geometry-only fleet + declared HullModel particulars; unused tables empty',
                       elapsed_s=time.monotonic()-start,rows=rows)
        (HERE/'design-1700-recheck.json').write_text(json.dumps(payload,indent=2)+'\n')
        print(prefixes,'rod',lash.metrics.get('max_lashing_ratio'),'guide',lash.metrics.get('max_cell_guide_ratio'),
              'GM',lash.metrics.get('gm_m'),'stability_negative',row['stability']['negative_margins'],
              'errors',lash.error,stab.error,flush=True)
