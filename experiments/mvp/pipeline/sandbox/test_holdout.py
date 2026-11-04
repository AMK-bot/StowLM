"""Offline audit of the frozen fresh holdout protocol and recorded results."""
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from experiments.mvp.pipeline.sandbox import holdout

FIXTURES = Path(__file__).with_name('fixtures')


def test_exclusive_protocol_write_cannot_replace_a_draw(tmp_path):
    path = tmp_path/'manifest.json'
    holdout.write(path, {'seed': 177017}, exclusive=True)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        holdout.write(path, {'seed': 4}, exclusive=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize('mask,load,age,expected', [
    (255, 45, 119.9, True), (127, 20, 1, False), (255, 45.01, 1, False),
    (255, 20, 120, False), (255, 20, -.1, False), (255, -1, 1, False)])
def test_admission_requires_fresh_free_capacity(mask, load, age, expected):
    assert holdout.admitted({'mask': mask, 'load1': load, 'age_s': age}) is expected


def test_aggregate_keeps_frozen_denominator_despite_runtime_screen_disagreement():
    rows = [{'request_index': 0, 'fast': {'legal': True}, 'full': {'legal': True, 'broken': []},
             'runtime_fast_agrees_with_frozen_legal': True, 'fast_s': .2, 'full_api_s': 5., 'canonical_checker_s': 4.},
            {'request_index': 1, 'fast': {'legal': False}, 'full': {'legal': False, 'broken': [
                {'rule_id': 'rule.a'}, {'rule_id': 'rule.a'}]},
             'runtime_fast_agrees_with_frozen_legal': False, 'fast_s': .3, 'full_api_s': 6., 'canonical_checker_s': 5.}]
    result = holdout.aggregate(rows, 'manifest', 'ready')['summary']
    assert result['frozen_screen_clear'] == 2 and result['runtime_screen_clear'] == 1
    assert result['false_legal'] == 1 and result['false_legal_fraction'] == .5
    assert result['miss_rule_families'] == {'rule.a': 1}


def test_frozen_manifest_is_distinct_and_selects_without_candidate_full_results():
    path = FIXTURES/'holdout.manifest.json'
    if not path.exists():
        pytest.skip('Holdout selection is still running')
    manifest, digest = holdout.verify_manifest()
    protocol, draw = holdout.read(FIXTURES/'holdout.protocol.json'), holdout.read(FIXTURES/'holdout.draw.json')
    assert sha256_file(path) == digest
    assert manifest['candidate_full_checks_started'] == 0
    assert protocol['created_unix'] < manifest['frozen_unix']
    assert manifest['seed'] == protocol['seed'] == draw['seed'] == 177017
    assert manifest['n_boxes'] == protocol['n_boxes'] == 163
    assert all(protocol['instance_sha256'] != r['instance_sha256']
               and protocol['plan_sha256'] != r['plan_sha256'] for r in protocol['different_from'])
    assert sha256_json(draw['pool']) == draw['pool_sha256']
    rows = manifest['requests']
    assert len(rows) == 40
    assert len({r['requested_state_sha256'] for r in rows}) == 40
    assert len({r['box_id'] for r in rows}) >= 20
    assert manifest['counts'] == {'swap': 20, 'empty': 20}
    assert all(sum(r['box_id'] == box for r in rows) <= 2 for box in {r['box_id'] for r in rows})
    excluded = {tuple(r) for r in draw['excluded_prior_pairs']}
    for row in rows:
        assert row['frozen_fast']['legal']
        assert row['requested_state_sha256'] == row['reached_state_sha256']
        assert (row['box_id'], row['target_slot']) not in excluded
        assert draw['pool'][row['draw_index']] == [row['kind'], row['box_id'], row['target_slot']]


def test_recorded_holdout_has_40_fresh_bound_canonical_calls_after_freeze():
    path = FIXTURES/'holdout.results.json'
    if not path.exists():
        pytest.skip('Holdout full checks have not started')
    data = holdout.read(path)
    manifest, digest = holdout.verify_manifest()
    assert data['complete'] and data['manifest_sha256'] == digest
    assert len(data['moves']) == data['summary']['fresh_canonical_checks'] == 40
    assert {r['request_index'] for r in data['moves']} == set(range(40))
    for row in data['moves']:
        frozen = manifest['requests'][row['request_index']]
        assert row['full_origin'] == 'fresh_canonical_call' and row['canonical_invocations'] == 1
        assert row['started_unix'] > manifest['frozen_unix']
        assert row['canonical_call']['started_unix'] > manifest['frozen_unix']
        assert row['canonical_call']['input_binding_valid']
        assert row['canonical_call']['instance_sha256'] == frozen['instance_sha256']
        assert row['canonical_call']['plan_sha256'] == frozen['candidate_plan_sha256']
        assert row['canonical_call']['report']['passed'] == row['full']['legal']
        assert holdout.admitted(row['admission'])
        assert set(row['session']['native_thread_caps'].values()) == {'1'}
    assert data['summary']['false_legal'] == sum(not r['full']['legal'] for r in data['moves'])


def test_code_fingerprint_is_intern_independent_but_detects_real_mutation():
    import sys
    code = compile("def example():\n    return 'unique whitespace intern check 177017'\n", '<stable-proof>', 'exec').co_consts[0]
    before = holdout.code_fingerprint(code)
    sys.intern(code.co_consts[1])
    assert holdout.code_fingerprint(code) == before
    changed = code.replace(co_consts=(None, 'different actual fact'))
    assert holdout.code_fingerprint(changed) != before
    changed = code.replace(co_code=code.co_code[:-2]+b'\x09\x00')
    assert holdout.code_fingerprint(changed) != before


def test_recovery_manifest_preserves_original_40_and_runtime_archive_proof():
    if not (FIXTURES/'holdout.manifest.json').exists():
        pytest.skip('Recovery rescreen has not finished')
    manifest, _ = holdout.verify_manifest()
    original = holdout.read(FIXTURES/'holdout.state.json')
    audit = holdout.read(FIXTURES/'holdout.marshal-audit.json')
    assert manifest['requests'] == original['selected']
    assert sha256_json(manifest['requests']) == audit['preserved_requests_sha256']
    # This is historical evidence. A later API addition must not rewrite its
    # measured code identity or require today's functions to be the old gates.
    import types
    archived = {}
    for name in holdout.GATES:
        relative = str((holdout.HERE/name).relative_to(holdout.ROOT))
        path = FIXTURES/'holdout.gates-v3'/relative
        assert sha256_file(path) == original['source_sha256'][relative]
        compiled = compile(path.read_bytes(), str(holdout.HERE/name), 'exec')
        functions = {c.co_name: c for c in compiled.co_consts if isinstance(c, types.CodeType)}
        for key in manifest['runtime_gate_code_sha256']:
            if key.rsplit('.', 2)[-2] == name[:-3]:
                archived[key] = holdout.code_fingerprint(functions[key.rsplit('.', 1)[-1]])
    assert archived == manifest['runtime_gate_code_sha256']
