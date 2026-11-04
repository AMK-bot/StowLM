"""Review canonical-policy metadata against an explicit content review.

No linear requalification or policy values are written. Every new construction
must reproduce the prior review's five values and the independently computed
projection, bitwise, through the real public builder with vessel-cache reuse off.
Unknown or concurrently changing source/parameter/provider/auxiliary inputs abort.
Explicitly reviewed parameter/provider changes are allowed, but changed policy
values produce comparison evidence and abort without installing a new contract.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from time import perf_counter
import tomllib

from stowage.checker import stability, stability_policy
from stowage.vessel import fleet, fleet_cache

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def hashes(names):
    return {name: digest((ROOT / name).read_bytes()) for name in names}


def validate_review(kind, pinned, actual, approved):
    changes = {name for name in pinned if pinned[name] != actual[name]}
    assert changes == set(approved), ('unreviewed or stale input review', kind, changes, set(approved))
    for name in changes:
        entry = approved[name]
        assert entry['before'] == pinned[name] and entry['after'] == actual[name], (kind, name)
        assert entry['reason'].strip(), ('missing source review', name)
    return sorted(changes)


def review(reviewed_inputs, baseline_review, output_dir):
    path = ROOT / 'src/stowage/checker/stability_policy_contract.json'
    original = path.read_bytes()
    contract = json.loads(original)
    approved_raw = reviewed_inputs.read_bytes()
    approved = json.loads(approved_raw)
    baseline_raw = baseline_review.read_bytes()
    baseline = json.loads(baseline_raw)
    assert digest(original) == approved['contract_sha256'], 'contract differs from reviewed starting pin'
    assert digest(baseline_raw) == approved['baseline_sha256'], 'baseline review changed'
    assert baseline['new_contract_sha256'] == digest(original), 'baseline does not attest current starting pin'
    assert tuple(contract['fields']) == stability._CANONICAL_POLICY_FIELDS
    source_before = hashes(contract['source_sha256'])
    auxiliary_before = hashes(contract['auxiliary_sha256'])
    assert {str(p.relative_to(ROOT)) for p in fleet_cache._source_files()} == set(source_before)
    changes = validate_review('source', contract['source_sha256'], source_before, approved['sources'])
    auxiliary_changes = validate_review('auxiliary', contract['auxiliary_sha256'], auxiliary_before, approved['auxiliary'])
    providers = {name: stability_policy.provider_record(function)
                 for name, function in stability_policy.runtime_providers().items()}
    assert set(providers) == set(contract['providers']), 'runtime provider membership changed'
    provider_changes = validate_review('providers', contract['providers'], providers,
                                      approved.get('providers', {}))
    parameters = {name: fleet._parameter_bytes(name) for name in fleet.list_fleet()}
    assert set(parameters) == set(contract['constructions'])
    parameter_hashes = {name: digest(raw) for name, raw in parameters.items()}
    parameter_changes = validate_review('parameters',
        {name: row['parameter_sha256'] for name, row in contract['constructions'].items()},
        parameter_hashes, approved.get('parameters', {}))
    baseline_rows = {row['ship']: row for row in baseline['rows']}
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = output_dir / 'canonical-policy-review.json'
    assert not evidence_path.exists(), 'use a fresh output directory; preserve prior evidence'
    rows = []
    previous_cache_mode = os.environ.get('STOWAGE_VESSEL_CACHE')
    os.environ['STOWAGE_VESSEL_CACHE'] = '0'
    try:
        for name, raw in parameters.items():
            prior = baseline_rows[name]
            assert prior['construction_identity'] == contract['constructions'][name]['identity']
            started = perf_counter()
            public = fleet.load_vessel(name)
            values = tuple(stability._hull_policy_number(public.hull, field)
                           for field in stability._CANONICAL_POLICY_FIELDS)
            projection = stability_policy._project_reviewed_prefix(tomllib.loads(raw.decode()))
            previous = tuple(prior['fields'][field] for field in stability._CANONICAL_POLICY_FIELDS)
            assert len(values) == len(projection) == len(previous) == 5
            bits = lambda sequence: [struct.pack('>d', value).hex() for value in sequence]
            identity = fleet_cache._cache_key(name, 'full', True, raw)
            rows.append(dict(ship=name, seconds=perf_counter() - started,
                vessel_cache_reuse=False, five_fields_bitwise_equal=bits(values) == bits(projection),
                unchanged_from_prior_review_bitwise=bits(values) == bits(previous), construction_identity=identity,
                prior_fields=dict(zip(stability._CANONICAL_POLICY_FIELDS, previous, strict=True)),
                projection_fields=dict(zip(stability._CANONICAL_POLICY_FIELDS, projection, strict=True)),
                differences=[dict(field=field, old=old, new=new, projection=projected)
                    for field, old, new, projected in zip(stability._CANONICAL_POLICY_FIELDS,
                        previous, values, projection, strict=True)
                    if bits((old,)) != bits((new,)) or bits((new,)) != bits((projected,))],
                fields=dict(zip(stability._CANONICAL_POLICY_FIELDS, values, strict=True)),
                float64_hex=dict(zip(stability._CANONICAL_POLICY_FIELDS, bits(values), strict=True))))
            contract['constructions'][name]['identity'] = identity
            contract['constructions'][name]['parameter_sha256'] = digest(raw)
            print(json.dumps(rows[-1]), flush=True)
    finally:
        if previous_cache_mode is None:
            os.environ.pop('STOWAGE_VESSEL_CACHE', None)
        else:
            os.environ['STOWAGE_VESSEL_CACHE'] = previous_cache_mode
    assert hashes(source_before) == source_before, 'construction sources changed during review'
    assert hashes(auxiliary_before) == auxiliary_before, 'auxiliary inputs changed during review'
    assert all(fleet._parameter_bytes(name) == raw for name, raw in parameters.items()), 'parameters changed during review'
    assert {name: stability_policy.provider_record(function)
            for name, function in stability_policy.runtime_providers().items()} == providers
    assert {str(p.relative_to(ROOT)) for p in fleet_cache._source_files()} == set(source_before)
    assert path.read_bytes() == original, 'contract changed concurrently'
    assert reviewed_inputs.read_bytes() == approved_raw and baseline_review.read_bytes() == baseline_raw
    record = dict(reviewed_source_changes=changes, reviewed_auxiliary_changes=auxiliary_changes,
        reviewed_parameter_changes=parameter_changes, reviewed_provider_changes=provider_changes,
        review_manifest_sha256=digest(approved_raw), baseline_review_sha256=digest(baseline_raw),
        old_contract_sha256=digest(original), rows=rows)
    if any(row['differences'] for row in rows):
        record.update(installed=False, new_contract_sha256=None,
                      refusal='policy value or public/projection difference; leader review required')
        evidence_path.write_text(json.dumps(record, indent=2) + '\n')
        raise ValueError(record['refusal'])
    contract['source_sha256'] = source_before
    contract['auxiliary_sha256'] = auxiliary_before
    contract['providers'] = providers
    contract['verification'] = str(evidence_path.relative_to(ROOT))
    replacement = (json.dumps(contract, indent=2, sort_keys=True) + '\n').encode()
    # Exercise the exact candidate contract before installing it. The public
    # runtime guards still run; only the caller-supplied contract bytes differ.
    candidate_identity = (replacement, stability_policy.provider_identity()[1])
    for name, raw in parameters.items():
        identity = fleet_cache._cache_key(name, 'full', True, raw)
        assert identity == contract['constructions'][name]['identity']
        assert stability_policy.projected_policy(name, digest(raw), identity, candidate_identity) is not None
    assert path.read_bytes() == original, 'contract changed concurrently'
    assert reviewed_inputs.read_bytes() == approved_raw and baseline_review.read_bytes() == baseline_raw
    record.update(installed=True, new_contract_sha256=digest(replacement))
    evidence_path.write_text(json.dumps(record, indent=2) + '\n')
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.policy-review-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(replacement)
    try:
        assert path.read_bytes() == original, 'contract changed before installation'
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print('Reviewed construction metadata only; all 15 policy values unchanged bitwise.')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed-inputs', type=Path, required=True)
    parser.add_argument('--baseline-review', type=Path, default=HERE / 'review1735-canonical-policy-review.json')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    review(args.reviewed_inputs.resolve(), args.baseline_review.resolve(), args.output_dir.resolve())


if __name__ == '__main__':
    main()
