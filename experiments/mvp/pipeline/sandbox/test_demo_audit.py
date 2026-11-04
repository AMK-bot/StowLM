"""The demo audit stays outcome-blind and its cache cannot cross identities."""
from collections import Counter
from copy import deepcopy
import json

import pytest

from experiments.mvp.pipeline.contract.schema import Explanation, MoveVerdict, sha256_file, sha256_json
from .benchmark import HERE
from .demo_audit import SEED, select_requests, summarize
from .demo_cache import request_key, lookup_cached_drag

FIXTURES = HERE / 'fixtures'


def recorded(name):
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f'{name} not yet recorded')
    return json.loads(path.read_text())


def test_geometry_selection_has_exact_quotas_and_fixed_cache_subset_without_outcomes():
    pool = [{'kind': kind, 'box_id': f'{category}:{kind}:{i}', 'target_slot': str(i),
             'tags': [category]} for category in ('far', 'heavy', 'cross_hatch')
            for kind in ('swap', 'empty') for i in range(9)]
    selected, draw = select_requests(pool)
    assert select_requests(pool) == (selected, draw)
    assert len(selected) == 30
    assert Counter((r['assigned_stratum'], r['kind']) for r in selected) == {
        (category, kind): 5 for category in ('far', 'heavy', 'cross_hatch') for kind in ('swap', 'empty')}
    assert len({r['box_id'] for r in selected}) == 30
    assert all(not {'fast', 'full', 'legal'} & r.keys() for r in selected)
    assert sorted(draw) == list(range(len(pool)))


def test_manifest_fixes_30_new_demo_drags_and_25_cache_ids_before_any_fulls():
    manifest = recorded('demo-audit.manifest.json')
    protocol = recorded('demo-audit.protocol.json')
    draw = recorded('demo-audit.draw.json')
    assert manifest['seed'] == protocol['seed'] == SEED
    assert manifest['candidate_full_checks_started'] == protocol['candidate_full_checks_started'] == 0
    assert sha256_file(FIXTURES/'demo-audit.manifest.json') == (FIXTURES/'demo-audit.manifest.sha256').read_text().strip()
    assert manifest['cache_request_indices'] == list(range(25))
    assert len(manifest['requests']) == 30
    assert len({r['box_id'] for r in manifest['requests']}) >= 20
    assert max(Counter(r['box_id'] for r in manifest['requests']).values()) <= 2
    assert manifest['requests'] == select_requests(draw['pool'], SEED)[0]
    excluded = {tuple(v) for v in draw['excluded_prior_pairs']}
    assert all((r['box_id'], r['target_slot']) not in excluded for r in manifest['requests'])
    assert all('fast' not in r and 'full' not in r for r in manifest['requests'])
    for stem in ('source', 'protocol', 'draw'):
        assert manifest[f'{stem}_sha256'] == sha256_file(FIXTURES/f'demo-audit.{stem}.json')
    source = recorded('demo-audit.source.json')
    from stowage.model import Instance, MovePlan
    assert manifest['instance_sha256'] == sha256_json(Instance.model_validate(source['instance']).model_dump(mode='json'))
    assert manifest['plan_sha256'] == sha256_json(MovePlan.model_validate(source['plan']).model_dump(mode='json'))
    assert manifest['frozen_unix'] > protocol['created_unix']


def test_cache_key_normalizes_model_defaults_and_numeric_json_representations():
    from stowage.model import Instance, MovePlan
    source = recorded('demo-audit.source.json')
    instance = Instance.model_validate(source['instance'])
    plan = MovePlan.model_validate(source['plan'])
    assert request_key(instance, plan, 'box', 'slot', 'pins') == request_key(
        source['instance'], source['plan'], 'box', 'slot', 'pins')


def test_audit_results_are_fresh_bound_checks_of_candidate_or_named_snapshot():
    from stowage.model import Instance, MovePlan, CheckReport
    from stowage.checker import report_matches_inputs
    from .moves import prepare_move, _snapshot, _same_placements
    results = recorded('demo-audit.results.json')
    if not results['complete']:
        pytest.skip('Demo audit in progress')
    manifest = recorded('demo-audit.manifest.json')
    ready = recorded('demo-audit.readiness.json')
    source = recorded('demo-audit.source.json')
    instance, plan = Instance.model_validate(source['instance']), MovePlan.model_validate(source['plan'])
    assert len(results['moves']) == 30
    assert results['summary'] == summarize(results['moves'])
    for row in results['moves']:
        frozen = manifest['requests'][row['request_index']]
        assert (row['box_id'], row['target_slot']) == (frozen['box_id'], frozen['target_slot'])
        assert row['started_unix'] > ready['created_unix'] > manifest['frozen_unix']
        assert row['canonical_call']['started_unix'] > row['started_unix']
        assert row['canonical_invocations'] == 1 and row['full_origin'] == 'fresh_canonical_call'
        assert row['canonical_call']['input_binding_valid']
        assert row['source_fingerprint'] == ready['source_fingerprint']
        assert set(row['native_thread_caps'].values()) == {'1'}
        prepared = prepare_move(instance, plan, row['box_id'], row['target_slot'])
        reached = _same_placements(prepared.replay.final_state.ship, prepared.ship)
        expected_i, expected_p = (instance, prepared.plan) if reached else _snapshot(prepared)
        report = CheckReport.model_validate(row['canonical_call']['report'])
        assert report_matches_inputs(report, expected_i, expected_p)
        assert report.count is not None
        assert report.parts and all(part.ran and not part.error for part in report.parts.values())
        assert row['canonical_call']['instance_sha256'] == sha256_json(expected_i.model_dump(mode='json'))
        assert row['canonical_call']['plan_sha256'] == sha256_json(expected_p.model_dump(mode='json'))
        assert row['checker_input_kind'] == ('candidate_plan' if reached else 'diagnostic_requested_stow_snapshot')
        assert row['full']['legal'] == bool(report.passed and reached and prepared.replay.legal)
        if not reached:
            assert row['diagnostic_snapshot_cannot_approve_original_plan'] and not row['full']['legal']
        MoveVerdict.model_validate(row['full'])
        Explanation.model_validate(row['explanation'])
    clears = [r for r in results['moves'] if r['fast']['legal']]
    assert results['summary']['fast_clear'] == len(clears)
    assert results['summary']['false_legal'] == sum(not r['full']['legal'] for r in clears)


def test_cache_uses_only_frozen_subset_and_refuses_modified_input_or_dependencies(monkeypatch):
    from . import demo_audit
    source = recorded('demo-audit.source.json')
    cache = recorded('drag-cache.json')
    index = recorded('drag-cache.index.json')
    results = recorded('demo-audit.results.json')
    manifest = recorded('demo-audit.manifest.json')
    rows = {r['request_index']: r for r in results['moves']}
    assert cache['entries_count'] == len(cache['entries']) == 25
    cache_sha = sha256_file(FIXTURES/'drag-cache.json')
    assert (FIXTURES/'drag-cache.sha256').read_text().strip() == cache_sha
    assert index['cache_sha256'] == cache_sha
    assert [(v['request_index'], v['box_id'], v['target_slot']) for v in index['requests']] == sorted(
        (v['request_index'], v['box_id'], v['target_slot']) for v in cache['entries'].values())
    assert cache['cache_request_indices'] == manifest['cache_request_indices'] == list(range(25))
    assert {v['request_index'] for v in cache['entries'].values()} == set(range(25))
    assert cache['audit_results_sha256'] == sha256_file(FIXTURES/'demo-audit.results.json')
    for key, entry in cache['entries'].items():
        row = rows[entry['request_index']]
        assert key == request_key(source['instance'], source['plan'], row['box_id'], row['target_slot'], cache['source_fingerprint'])
        assert entry['full'] == row['full'] and entry['fast'] == row['fast']
        assert entry['audit_row_sha256'] == sha256_json(row)
        assert entry['mode'] == entry['explanation']['mode'] == 'recorded'
        assert entry['label'] == 'Recorded answer'
    monkeypatch.setattr(demo_audit, 'execution_sources', lambda: cache['source_sha256'])
    sample = next(iter(cache['entries'].values()))
    hit = lookup_cached_drag(source['instance'], source['plan'], sample['box_id'], sample['target_slot'])
    assert hit == sample
    hit['label'] = 'mutated'
    assert lookup_cached_drag(source['instance'], source['plan'], sample['box_id'], sample['target_slot'])['label'] == 'Recorded answer'
    changed = deepcopy(source['plan'])
    changed['id'] += '.different'
    assert lookup_cached_drag(source['instance'], changed, sample['box_id'], sample['target_slot']) is None
    changed = deepcopy(source['instance'])
    changed['containers'][0]['gross_mass_kg'] += 1
    assert lookup_cached_drag(changed, source['plan'], sample['box_id'], sample['target_slot']) is None
    assert lookup_cached_drag(source['instance'], source['plan'], sample['box_id'], 'nonexistent') is None
    monkeypatch.setattr(demo_audit, 'execution_sources', lambda: {})
    assert lookup_cached_drag(source['instance'], source['plan'], sample['box_id'], sample['target_slot']) is None
