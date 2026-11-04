"""Exact-input replay of explicitly recorded, full-checked demo drag verdicts."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .benchmark import HERE
from .holdout import read, write, now


def request_key(instance, plan, box_id, target_slot, source_fingerprint):
    from stowage.model import Instance, MovePlan
    instance = Instance.model_validate(instance).model_dump(mode='json')
    plan = MovePlan.model_validate(plan).model_dump(mode='json')
    return sha256_json({'instance_sha256': sha256_json(instance),
                        'plan_sha256': sha256_json(plan), 'box_id': box_id,
                        'target_slot': target_slot, 'source_fingerprint': source_fingerprint})


def build_cache(results_path, output):
    """Reuse the predetermined audit subset; never pretend these are new checks."""
    from .demo_audit import verify_ready
    manifest, manifest_sha, ready = verify_ready()
    results = read(results_path)
    if not results['complete'] or results['manifest_sha256'] != manifest_sha:
        raise ValueError('Only a completed exact-manifest audit can supply the cache')
    source = read(HERE / 'fixtures/demo-audit.source.json')
    by_index = {r['request_index']: r for r in results['moves']}
    entries = {}
    for index in manifest['cache_request_indices']:
        row = by_index[index]
        if row['full_origin'] != 'fresh_canonical_call' or row['source_fingerprint'] != ready['source_fingerprint']:
            raise ValueError('Cache row has no exact fresh audit provenance')
        key = request_key(source['instance'], source['plan'], row['box_id'], row['target_slot'], ready['source_fingerprint'])
        explanation = deepcopy(row['explanation'])
        explanation['mode'] = 'recorded'
        entries[key] = {'request_index': index, 'box_id': row['box_id'], 'target_slot': row['target_slot'],
                        'mode': 'recorded', 'label': 'Recorded answer', 'checked_utc': row['completed_utc'],
                        'fast': row['fast'], 'full': row['full'], 'explanation': explanation,
                        'checker_input_kind': row['checker_input_kind'],
                        'canonical_call': row['canonical_call'],
                        'audit_row_sha256': sha256_json(row),
                        'provenance': 'Replayed full verdict from the frozen30 audit; not an additional fresh check.'}
    data = {'schema': 'stowlm.sandbox-drag-cache/1', 'synthetic': True, 'mode': 'recorded',
            'label': 'Recorded answer', 'created_utc': now(), 'entries_count': len(entries),
            'instance_sha256': manifest['instance_sha256'], 'plan_sha256': manifest['plan_sha256'],
            'source_sha256': ready['source_sha256'], 'source_fingerprint': ready['source_fingerprint'],
            'component_versions': ready['component_versions'],
            'manifest_sha256': manifest_sha, 'audit_results_sha256': sha256_file(results_path),
            'cache_request_indices': manifest['cache_request_indices'], 'entries': entries}
    write(output, data, exclusive=True)
    return data


def lookup_cached_drag(instance, plan, box_id, target_slot, *, cache_path=None):
    """Return a recorded envelope, or None for any identity/dependency/request miss.

    This does no recheck. Callers must show ``Recorded answer`` and ``Checked on``
    the stored date. They can always call try_move for a fresh result instead.
    """
    from stowage.model import Instance, MovePlan
    from stowage.versions import component_versions
    from .demo_audit import execution_sources
    cache_path = Path(cache_path) if cache_path is not None else HERE / 'fixtures/drag-cache.json'
    try:
        instance = Instance.model_validate(instance)
        plan = MovePlan.model_validate(plan)
        cache = read(cache_path)
        if (cache['schema'] != 'stowlm.sandbox-drag-cache/1'
                or cache['instance_sha256'] != sha256_json(instance.model_dump(mode='json'))
                or cache['plan_sha256'] != sha256_json(plan.model_dump(mode='json'))
                or cache['source_sha256'] != execution_sources()
                or cache['component_versions'] != component_versions()
                or cache['source_fingerprint'] != sha256_json(cache['source_sha256'])):
            return None
        key = request_key(instance, plan, box_id, target_slot, cache['source_fingerprint'])
        entry = cache['entries'].get(key)
        return deepcopy(entry) if entry is not None else None
    except (OSError, ValueError, TypeError, KeyError):
        return None
