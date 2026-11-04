"""Compare the reviewed projection against the real public fleet resolver."""
import os
for variable in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[variable]='1'
import hashlib
import json
from pathlib import Path
import struct
from time import perf_counter

from stowage.checker import stability, stability_policy
from stowage.vessel import fleet, fleet_cache

rows=[]
for name in fleet.list_fleet():
    raw=fleet._parameter_bytes(name)
    digest=hashlib.sha256(raw).hexdigest()
    key=fleet_cache._cache_key(name,'full',True,raw)
    start=perf_counter()
    vessel=fleet.load_vessel(name)
    public_s=perf_counter()-start
    expected=tuple(stability._hull_policy_number(vessel.hull,field) for field in stability._CANONICAL_POLICY_FIELDS)
    start=perf_counter()
    actual=stability_policy.projected_policy(name,digest,key,stability_policy.provider_identity())
    projected_s=perf_counter()-start
    assert actual is not None, f'{name}: reviewed construction/provider contract changed'
    assert all(struct.pack('>d',a)==struct.pack('>d',b) for a,b in zip(actual,expected,strict=True))
    rows.append(dict(ship=name,construction_identity=key,parameter_sha256=digest,
        public_seconds=public_s,projected_seconds=projected_s,five_fields_bitwise_equal=True,
        public=dict(zip(stability._CANONICAL_POLICY_FIELDS,expected)),
        projected=dict(zip(stability._CANONICAL_POLICY_FIELDS,actual))))
    print(json.dumps(rows[-1]),flush=True)
Path(__file__).with_suffix('.json').write_text(json.dumps(rows,indent=2)+'\n')
