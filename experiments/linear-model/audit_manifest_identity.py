"""Prove manifest identity for earlier in-flight measurements without reruns.

A missing identity field is upgraded only when the current exact instance cache
key exists, its stored manifest fingerprint matches the current generator, and
all calibration-error vectors match the recorded measurement exactly. It never
rebuilds a model or accepts an old case for changed physical cargo inputs.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import runpy
import numpy as np
from stowage.linear.cache import source_fingerprint,model_cache_key,load_model
from stowage.linear.physics import _params_for
HERE=Path(__file__).resolve().parent
helpers=runpy.run_path(str(HERE/'narrow.py'))
p=argparse.ArgumentParser(); p.add_argument('--directory',type=Path,default=HERE/'kcs'); a=p.parse_args()
records={}
for ship in ('feeder_500','feeder_1000','feeder_1700'):
    for level in ('light','medium','full'):
        key=f'{ship}/{level}'; path=a.directory/f'narrow-{ship}-{level}.json'
        if not path.exists(): records[key]={'status':'missing'}; continue
        data=json.loads(path.read_text())
        if not data.get('source_clean') or data['source_fingerprint_end']!=source_fingerprint():
            records[key]={'status':'source_dirty_or_stale'}; continue
        instance,_=helpers['cargo_instance'](ship,level)
        identity=helpers['manifest_identity'](instance)
        if data.get('departure_manifest_identity')==identity:
            records[key]={'status':'direct_identity_match','sha256':identity['sha256']}; continue
        if data.get('departure_manifest_identity') is not None:
            records[key]={'status':'manifest_mismatch'}; continue
        band=helpers['case_band'](instance,ship,level)
        _,params=_params_for(instance)
        artifact=model_cache_key(instance,band,64,params)
        model=load_model(artifact)
        if model is None:
            records[key]={'status':'current_instance_cache_miss','cache_key':artifact}; continue
        assert asdict(model.band)==data['band'],key
        assert getattr(model,'_manifest_fingerprint',None)==identity['sha256'],key
        assert tuple(getattr(model,'_manifest_ids',()))==tuple(identity['container_ids']),key
        assert all(np.array_equal(np.asarray(getattr(model.errors,metric)),value)
                   for metric,value in data['model_errors'].items()),key
        data['departure_manifest_identity']=identity
        data['manifest_identity_proof']={'method':'current full-instance source/version/input cache key and exact calibration-error vectors',
                                        'cache_key':artifact,'note':'Identity metadata added after measurement; no numerical values changed.'}
        temporary=path.with_suffix('.tmp'); temporary.write_text(json.dumps(data,indent=2)+'\n'); temporary.replace(path)
        records[key]={'status':'verified_cache_identity_upgrade','sha256':identity['sha256'],'cache_key':artifact}
(a.directory/'manifest-identity-audit.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps(records,indent=2))
if any(value['status'] not in ('direct_identity_match','verified_cache_identity_upgrade') for value in records.values()):
    raise SystemExit(1)
