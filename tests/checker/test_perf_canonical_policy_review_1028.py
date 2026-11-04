"""The review tool accepts exact input changes, never changed policy numbers."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def review_tool(tmp_path, monkeypatch):
    script = (Path(__file__).resolve().parents[2] / 'experiments/checker/'
              'followup-1028/review_canonical_policy_1735.py')
    spec = importlib.util.spec_from_file_location('canonical_review_test', script)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    fields = ('a', 'b', 'c', 'd', 'e')
    values = (1., 2., 3., 4., 0.)
    state = dict(public=values, projection=values)
    source = tmp_path / 'source.py'
    source.write_text('# immutable construction source\n')
    contract = dict(fields=fields, source_sha256={'source.py': tool.digest(source.read_bytes())},
                    auxiliary_sha256={}, providers={'builder': {'code': 'old'}},
                    constructions={'ship': dict(identity='old', parameter_sha256=tool.digest(b'old'))})
    path = tmp_path / 'src/stowage/checker/stability_policy_contract.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(contract))
    original = path.read_bytes()
    baseline = tmp_path / 'prior.json'
    baseline.write_text(json.dumps(dict(new_contract_sha256=tool.digest(original),
        rows=[dict(ship='ship', construction_identity='old', fields=dict(zip(fields, values)))])))
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(contract_sha256=tool.digest(original),
        baseline_sha256=tool.digest(baseline.read_bytes()), sources={}, auxiliary={},
        parameters={'ship': dict(before=tool.digest(b'old'), after=tool.digest(b''), reason='reviewed')},
        providers={'builder': dict(before={'code': 'old'}, after={'code': 'new'}, reason='reviewed')})))
    monkeypatch.setattr(tool, 'ROOT', tmp_path)
    monkeypatch.setattr(tool, 'fleet', SimpleNamespace(
        list_fleet=lambda: ['ship'], _parameter_bytes=lambda name: b'',
        load_vessel=lambda name: SimpleNamespace(hull=dict(zip(fields, state['public'])))))
    monkeypatch.setattr(tool, 'fleet_cache', SimpleNamespace(
        _source_files=lambda: [source], _cache_key=lambda *args: 'new'))
    monkeypatch.setattr(tool, 'stability', SimpleNamespace(
        _CANONICAL_POLICY_FIELDS=fields, _hull_policy_number=lambda hull, field: hull[field]))
    monkeypatch.setattr(tool, 'stability_policy', SimpleNamespace(
        runtime_providers=lambda: {'builder': 'new'}, provider_record=lambda function: {'code': function},
        _project_reviewed_prefix=lambda params: state['projection'], provider_identity=lambda: (b'', ()),
        projected_policy=lambda *args: state['projection']))
    return tool, manifest, baseline, path, original, state, tmp_path / 'evidence'


def test_review_accepts_only_explicit_parameter_and_provider_changes(review_tool):
    tool, manifest, baseline, path, original, state, output = review_tool
    record = tool.review(manifest, baseline, output)
    assert record['installed'] and path.read_bytes() != original
    assert record['reviewed_parameter_changes'] == ['ship']
    assert record['reviewed_provider_changes'] == ['builder']
    assert not record['rows'][0]['differences']


@pytest.mark.parametrize('change', ['public', 'projection'])
def test_review_changed_policy_preserves_pin_and_records_old_new(review_tool, change):
    tool, manifest, baseline, path, original, state, output = review_tool
    state[change] = (1., 2., 3., 4., -0.)  # Even a signed-zero change must refuse.
    with pytest.raises(ValueError, match='leader review required'):
        tool.review(manifest, baseline, output)
    assert path.read_bytes() == original
    record = json.loads((output / 'canonical-policy-review.json').read_text())
    assert not record['installed'] and record['new_contract_sha256'] is None
    assert record['rows'][0]['differences'][0]['field'] == 'e'


@pytest.mark.parametrize('kind', ['parameters', 'providers'])
def test_review_unapproved_input_change_fails_before_public_build(review_tool, kind):
    tool, manifest, baseline, path, original, state, output = review_tool
    document = json.loads(manifest.read_text())
    document[kind] = {}
    manifest.write_text(json.dumps(document))
    tool.fleet.load_vessel = lambda name: pytest.fail('unreviewed build was attempted')
    with pytest.raises(AssertionError, match='unreviewed or stale input review'):
        tool.review(manifest, baseline, output)
    assert path.read_bytes() == original
