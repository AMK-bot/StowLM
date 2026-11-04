"""Review only the two authorized D3/M1 construction-source changes.

This does not requalify a linear epoch. A complete real public construction
must reproduce the unchanged five canonical policy fields before metadata is
updated. Unexplained source/parameter/auxiliary changes abort the review.
"""
import hashlib
import json
from pathlib import Path
import struct
from time import perf_counter
import tomllib

from stowage.checker import stability, stability_policy
from stowage.vessel import fleet, fleet_cache

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
path = ROOT / 'src/stowage/checker/stability_policy_contract.json'
original = path.read_bytes()
contract = json.loads(original)
allowed = {'src/stowage/vessel/hydrostatics.py', 'src/stowage/vessel/hull_tanks.py',
    # Concurrent D58 background allocators are conservative construction-key
    # inputs, but the full public builder does not call them or assign any
    # of the five canonical hull policy fields from them. Reviewed read-only.
    'src/stowage/vessel/fleet_balance.py', 'src/stowage/vessel/fleet_conditions.py'}
source_before = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in contract['source_sha256']}
changes = [name for name, digest in contract['source_sha256'].items()
           if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest]
assert set(changes) <= allowed, ('unreviewed construction source changes', changes)
for name, digest in contract['auxiliary_sha256'].items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest, name
rows = []
for name in fleet.list_fleet():
    raw = fleet._parameter_bytes(name)
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == contract['constructions'][name]['parameter_sha256']
    started = perf_counter()
    public = fleet.load_vessel(name)
    expected = tuple(stability._hull_policy_number(public.hull, field)
                     for field in stability._CANONICAL_POLICY_FIELDS)
    projection = stability_policy._project_reviewed_prefix(tomllib.loads(raw.decode()))
    assert len(expected) == len(projection) == 5
    assert all(struct.pack('>d', a) == struct.pack('>d', b)
               for a,b in zip(expected,projection,strict=True)), (name,expected,projection)
    identity = fleet_cache._cache_key(name,'full',True,raw)
    rows.append(dict(ship=name, seconds=perf_counter()-started,
        five_fields_bitwise_equal=True, construction_identity=identity,
        fields=dict(zip(stability._CANONICAL_POLICY_FIELDS, expected, strict=True))))
    contract['constructions'][name]['identity'] = identity
    print(json.dumps(rows[-1]), flush=True)
source_after = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in contract['source_sha256']}
assert source_after == source_before, 'public construction sources changed during review'
contract['source_sha256'] = source_after
contract['providers'] = {name:stability_policy.provider_record(function)
                         for name,function in stability_policy.runtime_providers().items()}
assert path.read_bytes() == original, 'contract changed concurrently'
path.write_text(json.dumps(contract,indent=2,sort_keys=True)+'\n')
for name in fleet.list_fleet():
    raw=fleet._parameter_bytes(name)
    assert stability_policy.projected_policy(name,hashlib.sha256(raw).hexdigest(),
        fleet_cache._cache_key(name,'full',True,raw),stability_policy.provider_identity()) is not None
(HERE/'d3-m1-canonical-policy-review.json').write_text(json.dumps(dict(
    reviewed_source_changes=changes,rows=rows,
    old_contract_sha256=hashlib.sha256(original).hexdigest(),
    new_contract_sha256=hashlib.sha256(path.read_bytes()).hexdigest()),indent=2)+'\n')
print('Reviewed D3/M1 source metadata only; no numerical policy values changed.')
