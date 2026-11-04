"""Fresh-process proof that a measured production case is a real model disk hit.

No recalibration is permitted. Stored calibration vectors must match the study;
all public dataclass fields must survive the cached public-builder path exactly.
This verifies cache integrity, not an additional physical error bound.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import runpy
import time
import numpy as np
from stowage.linear.cache import _canonical, load_model, model_cache_key, source_fingerprint
from stowage.linear.model import build_linear_model, linear_constraints
import stowage.linear.physics as physics

HERE=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',type=Path,required=True)
    args=parser.parse_args()
    record=json.loads(args.case.read_text())
    source=source_fingerprint()
    audit=runpy.run_path(str(HERE/'audit_loader_epoch.py'))
    audited=audit['qualified'](args.case,record,source)
    schema=runpy.run_path(str(HERE/'audit_schema13_epoch.py'))
    schema_audited=schema['qualified'](args.case,record,source)
    assert (record['source_clean'] and record['source_fingerprint_end']==source) or audited or schema_audited, 'stale measurement without qualified epoch audit'
    assert record['calibration']['samples']==192 and record['diagnostics']['samples']==128
    assert record['stability_free_surface_method']==os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')
    helper=runpy.run_path(str(HERE/'narrow.py'))
    started=time.perf_counter()
    instance,_=helper['cargo_instance'](record['ship'],record['level'])
    context_seconds=time.perf_counter()-started
    identity=helper['manifest_identity'](instance)
    assert identity==record['departure_manifest_identity'], 'manifest changed'
    assert helper['input_snapshot'](record['ship'])==record['input_hashes_end'], 'production inputs changed'
    band=helper['case_band'](instance,record['ship'],record['level'])
    _,params=physics._params_for(instance)
    key=model_cache_key(instance,band,64,params)
    started=time.perf_counter()
    stored=load_model(key)
    disk_seconds=time.perf_counter()-started
    assert stored is not None, 'cache miss: verification never builds a missing model'
    assert asdict(stored.band)==record['band']
    assert getattr(stored,'_manifest_fingerprint')==identity['sha256']
    for field,value in record['model_errors'].items():
        assert np.array_equal(getattr(stored.errors,field),value),field
    def forbidden(*args,**kwargs):
        raise AssertionError('public builder attempted recalibration instead of disk hit')
    physics.measure_errors=forbidden
    physics._CACHE.clear()
    started=time.perf_counter()
    rebuilt=build_linear_model(instance,band=band)
    public_seconds=time.perf_counter()-started
    digest=lambda model:hashlib.sha256(_canonical(model)).hexdigest()
    assert digest(stored)==digest(rebuilt),'public cached model fields changed'
    durations=[]
    constraints_digest=None
    for _ in range(20):
        started=time.perf_counter()
        constraints=linear_constraints(rebuilt,margin_scale=2.)
        durations.append(time.perf_counter()-started)
        hashed=hashlib.sha256(json.dumps(constraints.names).encode())
        for name in ('a_cell','a_tank','const','lb','ub'):
            array=np.ascontiguousarray(getattr(constraints,name))
            hashed.update(name.encode())
            hashed.update(str(array.shape).encode())
            hashed.update(array.dtype.str.encode())
            hashed.update(array.tobytes())
        current=hashed.hexdigest()
        if constraints_digest is not None:
            assert current==constraints_digest,'constraint generation is not deterministic'
        constraints_digest=current
    assert source_fingerprint()==source,'source changed during verification'
    result={'ship':record['ship'],'level':record['level'],'status':'verified_disk_hit',
            'loader_only_epoch_audit':audited,
            'schema13_metadata_epoch_audit':schema_audited,
            'source_fingerprint':source,'cache_key':key,'departure_manifest_identity':identity,
            'stability_free_surface_method':record['stability_free_surface_method'],
            'model_public_fields_sha256':digest(rebuilt),'context_seconds':context_seconds,
            'disk_load_seconds':disk_seconds,'public_cached_build_seconds':public_seconds,
            'constraints_calls':20,'constraints_margin_scale':2.,
            'constraints_median_seconds':float(np.median(durations)),
            'constraints_max_seconds':max(durations),
            'constraints_sha256':constraints_digest,
            'calibration_samples':192,'heldout_samples':128}
    output=args.case.with_suffix('.cache-verification.json')
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)

if __name__=='__main__': main()
