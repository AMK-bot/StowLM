"""Regression evidence for solver-reference and mutable weight identity."""
from copy import deepcopy
from hashlib import sha256
import json
import os

import pytest

from eval.harness_integrity import pin_weight_content, validate_reference
from stowage.model import CheckReport, MoveCount, MovePlan, PartReport, PlannerResult, Provenance


def reference(instance, *, method='solver.exact', **metrics):
    row = dict(input_sha256='input', spec_key='spec', split='val', rung='tiny')
    plan = MovePlan(id='reference', instance_id=instance.id, call_index=instance.call_index)
    bindings = [prefix + sha256(json.dumps(value.model_dump(mode='json'), sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                for prefix, value in [('checker.instance.sha256:', instance), ('checker.plan.sha256:', plan)]]
    report = CheckReport(passed=True, count=MoveCount(weighted_nonproductive=20),
        parts={name: PartReport(name=name) for name in (
            'moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints')},
        provenance=Provenance(parents=bindings))
    result = PlannerResult(status='ok', plan=plan, planner=method, planner_version='test', metrics=metrics)
    record = {**row, 'shippable': True, 'result': result.model_dump(mode='json'),
              'report': report.model_dump(mode='json')}
    return record, row


def test_non_solver_reference_rejected_before_objective_comparison(empty_ship_instance):
    record, row = reference(empty_ship_instance, method='ui-placeholder')
    with pytest.raises(ValueError, match='solver'):
        validate_reference(record, row, instance=empty_ship_instance)


def test_same_path_weight_replacement_changes_evaluation_identity(tmp_path):
    adapter = tmp_path / 'adapter.safetensors'
    adapter.write_bytes(b'old weights')
    times = adapter.stat()
    before = pin_weight_content({'adapter_path': str(adapter)})
    adapter.write_bytes(b'new weights')
    os.utime(adapter, ns=(times.st_atime_ns, times.st_mtime_ns))
    after = pin_weight_content({'adapter_path': str(adapter)})
    assert before != after


def test_weight_tree_pins_config_filenames_and_content_without_mutation(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights.safetensors').write_bytes(b'weights')
    (model / 'config.json').write_text('{}')
    config = {'model_path': str(model), 'adapter_path': None, 'temperature': 0.0}
    before = pin_weight_content(config)
    assert config == {'model_path': str(model), 'adapter_path': None, 'temperature': 0.0}
    assert before == pin_weight_content(config)
    (model / 'config.json').write_text('{"x":1}')
    assert before != pin_weight_content(config)
    (model / 'config.json').write_text('{}')
    (model / 'weights.safetensors').rename(model / 'renamed.safetensors')
    assert before != pin_weight_content(config)


@pytest.mark.parametrize('kind', ['missing', 'empty'])
def test_weight_identity_fails_closed_for_unavailable_content(tmp_path, kind):
    path = tmp_path / kind
    if kind == 'empty':
        path.mkdir()
    with pytest.raises(ValueError, match='weight identity'):
        pin_weight_content({'model_path': str(path)})


@pytest.mark.parametrize('key', ['input_sha256', 'spec_key', 'split', 'rung'])
def test_reference_requires_identical_input_and_split(empty_ship_instance, key):
    record, row = reference(empty_ship_instance)
    row[key] = 'other'
    with pytest.raises(ValueError, match='different input'):
        validate_reference(record, row, instance=empty_ship_instance)


def test_reference_report_requires_actual_plan_and_instance_binding(empty_ship_instance):
    record, row = reference(empty_ship_instance)
    assert validate_reference(record, row, instance=empty_ship_instance)['weighted_nonproductive'] == 20
    for mutate in ('plan', 'instance', 'legacy'):
        changed = deepcopy(record)
        instance = empty_ship_instance.model_copy(deep=True)
        if mutate == 'plan':
            changed['result']['plan']['id'] = 'edited-same-input'
        elif mutate == 'instance':
            instance.id = 'edited-instance'
        else:
            changed['report']['provenance']['parents'] = []
        with pytest.raises(ValueError, match='not bound'):
            validate_reference(changed, row, instance=instance)


@pytest.mark.parametrize('change', ['missing_part', 'part_error', 'failed', 'no_plan'])
def test_reference_requires_complete_passing_report(empty_ship_instance, change):
    record, row = reference(empty_ship_instance)
    if change == 'missing_part':
        del record['report']['parts']['stability']
    elif change == 'part_error':
        record['report']['parts']['strength']['error'] = 'unavailable'
    elif change == 'failed':
        record['report']['passed'] = False
    else:
        record['result']['plan'] = None
    with pytest.raises(ValueError, match='complete passing'):
        validate_reference(record, row, instance=empty_ship_instance)


def test_reference_distinguishes_model_proof_from_physical_optimum(empty_ship_instance):
    record, row = reference(empty_ship_instance, optimality_status='OPTIMAL',
        optimality_scope='engine_model_weighted_lifts', physical_domain_optimality_certified=False, objective=20)
    metadata = validate_reference(record, row, instance=empty_ship_instance)
    assert metadata['proof_status'] == 'proven_model_optimum'
    assert metadata['optimality_scope'] == 'engine_model_weighted_lifts'
    assert not metadata['physical_domain_optimality_certified']
    record['result']['metrics']['physical_domain_optimality_certified'] = True
    assert validate_reference(record, row, instance=empty_ship_instance)['proof_status'] == 'proven_optimum'


@pytest.mark.parametrize('change', ['repaired', 'timeout', 'unproved', 'scalable'])
def test_reference_does_not_uplift_incumbents_to_proofs(empty_ship_instance, change):
    record, row = reference(empty_ship_instance, optimality_status='OPTIMAL',
        optimality_scope='engine_model_weighted_lifts', objective=20)
    if change == 'repaired':
        record['result']['repaired'] = True
    elif change == 'timeout':
        record['result']['status'] = 'timeout'
        record['shippable'] = False
    elif change == 'unproved':
        record['result']['metrics']['optimality_status'] = 'TIME_LIMIT'
    else:
        record['result']['planner'] = 'solver.scalable'
        record['result']['metrics'] = {'exact_root_status': 'OPTIMAL'}
    metadata = validate_reference(record, row, instance=empty_ship_instance)
    assert metadata['proof_status'] == 'feasible_incumbent'
    if change == 'repaired':
        assert metadata['optimality_scope'] is None


def test_reference_proof_must_certify_the_checked_objective(empty_ship_instance):
    record, row = reference(empty_ship_instance, optimality_status='OPTIMAL',
        optimality_scope='engine_model_weighted_lifts', objective=10, lower_bound=10)
    assert validate_reference(record, row, instance=empty_ship_instance)['proof_status'] == 'feasible_incumbent'


class HarnessDiagnostic:
    name = 'test.integrity'
    version = '1'
    sets_ballast = False
    deterministic = True

    def __init__(self, adapter_path=None):
        self.adapter_path = adapter_path

    @property
    def evaluation_config(self):
        return {'adapter_path': self.adapter_path}


def harness_selection(monkeypatch, instance):
    """Exercise the full runner with one already-selected, in-memory input."""
    from types import SimpleNamespace
    from eval import harness
    inventory = harness.load_inventory()
    row = dict(input_sha256='input', spec_key='spec', split='val', rung='tiny', path='unused',
               split_version=inventory.version, split_entries_sha256=inventory.entries_sha256)
    calls = []
    monkeypatch.setattr(harness, 'select_instances', lambda *args, **kwargs: [row])
    monkeypatch.setattr(harness, '_load_selected', lambda *args, **kwargs: instance)
    monkeypatch.setattr(harness, 'lower_bound', lambda *args: SimpleNamespace(
        model_dump=lambda **kwargs: {'weighted': 0}))

    def pipeline(planner, instance, **kwargs):
        calls.append(instance.id)
        return PlannerResult(status='error', planner=planner.name, planner_version=planner.version), None

    monkeypatch.setattr(harness, 'run_planner', pipeline)
    return harness, row, calls


def test_weight_replacement_refuses_resume_end_to_end(tmp_path, empty_ship_instance, monkeypatch):
    harness, row, calls = harness_selection(monkeypatch, empty_ship_instance)
    adapter = tmp_path / 'adapter.safetensors'
    adapter.write_bytes(b'old weights')
    planner = HarnessDiagnostic(str(adapter))
    kwargs = dict(run_dir=tmp_path / 'run', rungs=('tiny',), tiny_run=True)
    harness.run_evaluation(planner, **kwargs)
    assert len(calls) == 1
    config = json.loads((tmp_path / 'run/config.json').read_text())
    assert config['planner_parameters']['adapter_content_sha256'] == sha256(b'old weights').hexdigest()
    harness.run_evaluation(planner, **kwargs)
    assert len(calls) == 1
    adapter.write_bytes(b'new weights')
    with pytest.raises(ValueError, match='configuration changed'):
        harness.run_evaluation(planner, **kwargs)
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['valid', 'input', 'method'])
def test_reference_identity_checked_before_planning_end_to_end(
        tmp_path, empty_ship_instance, monkeypatch, change):
    harness, row, calls = harness_selection(monkeypatch, empty_ship_instance)
    record, _ = reference(empty_ship_instance, optimality_status='OPTIMAL',
        optimality_scope='engine_model_weighted_lifts', objective=20)
    if change == 'input':
        record['input_sha256'] = 'other-input-bytes'
    elif change == 'method':
        record['result']['planner'] = 'ui-placeholder'
    path = tmp_path / 'reference/instances/spec.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    kwargs = dict(run_dir=tmp_path / 'run', reference_dir=path.parent.parent,
                  rungs=('tiny',), tiny_run=True)
    if change == 'valid':
        harness.run_evaluation(HarnessDiagnostic(), **kwargs)
        saved = json.loads((tmp_path / 'run/instances/spec.json').read_text())
        assert saved['reference_solver']['method'] == 'solver.exact'
        assert saved['reference_solver']['proof_status'] == 'proven_model_optimum'
        assert saved['reference_solver']['weighted_nonproductive'] == 20
        assert len(calls) == 1
    else:
        with pytest.raises(ValueError, match='different input' if change == 'input' else 'reference method'):
            harness.run_evaluation(HarnessDiagnostic(), **kwargs)
        assert calls == []
        assert not (tmp_path / 'run/instances/spec.json').exists()


def test_cached_plan_mutation_rejects_bound_report_end_to_end(tmp_path, empty_ship_instance, monkeypatch):
    harness, row, calls = harness_selection(monkeypatch, empty_ship_instance)
    record, _ = reference(empty_ship_instance)

    def pipeline(planner, instance, **kwargs):
        calls.append(instance.id)
        result = PlannerResult.model_validate(record['result'])
        report = CheckReport.model_validate(record['report'])
        result._record_full_check(instance, report)
        return result, report

    monkeypatch.setattr(harness, 'run_planner', pipeline)
    kwargs = dict(run_dir=tmp_path / 'run', rungs=('tiny',), tiny_run=True)
    harness.run_evaluation(HarnessDiagnostic(), **kwargs)
    path = tmp_path / 'run/instances/spec.json'
    cached = json.loads(path.read_text())
    assert cached['report_binding_valid'] and cached['shippable']
    cached['result']['plan']['id'] = 'tampered-after-evaluation'
    path.write_text(json.dumps(cached))
    with pytest.raises(ValueError, match='binding|bound'):
        harness.run_evaluation(HarnessDiagnostic(), **kwargs)
    assert len(calls) == 1


def test_preloaded_runtime_without_weight_binding_fails_closed(tmp_path, empty_ship_instance, monkeypatch):
    harness, row, calls = harness_selection(monkeypatch, empty_ship_instance)
    adapter = tmp_path / 'adapter.safetensors'
    adapter.write_bytes(b'weights')
    planner = HarnessDiagnostic(str(adapter))
    planner._model = object()
    with pytest.raises(ValueError, match='preloaded model lacks bound weight identity'):
        harness.run_evaluation(planner, run_dir=tmp_path / 'run', rungs=('tiny',), tiny_run=True)
    assert calls == []


def test_loaded_runtime_cannot_relabel_old_tensors_with_new_disk_weights(tmp_path, empty_ship_instance, monkeypatch):
    harness, row, calls = harness_selection(monkeypatch, empty_ship_instance)
    adapter = tmp_path / 'adapter.safetensors'
    adapter.write_bytes(b'old')
    planner = HarnessDiagnostic(str(adapter))
    planner._model = None
    kwargs = dict(run_dir=tmp_path / 'run', rungs=('tiny',), tiny_run=True)
    harness.run_evaluation(planner, **kwargs)
    planner._model = object()
    adapter.write_bytes(b'new')
    with pytest.raises(ValueError, match='configuration changed'):
        harness.run_evaluation(planner, **kwargs)
    assert len(calls) == 1


def official_reference_run(tmp_path, row, instance):
    """An official reference with row/run linkage, rather than a clean wrapper."""
    from eval import harness
    from stowage.versions import component_versions
    row.update(epoch_id='epoch-current', source_sha256='source-current',
               regen_manifest_sha256='manifest-current')
    record, _ = reference(instance, optimality_status='OPTIMAL',
        optimality_scope='engine_model_weighted_lifts', objective=20)
    record.update({key: value for key, value in row.items() if key != 'path'})
    versions = component_versions()
    for artifact in (record, record['result'], record['result']['plan'], record['report']):
        artifact['provenance'] = {**artifact.get('provenance', {}), 'versions': versions}
    from stowage.checker.full import _input_bindings
    bound_plan = MovePlan.model_validate(record['result']['plan'])
    record['report']['provenance']['parents'] = list(_input_bindings(instance, bound_plan))
    record.update(official=True, tiny_run=False, diagnostic=False, evidence_eligible=True)
    config = {key: row[key] for key in ('epoch_id', 'source_sha256', 'regen_manifest_sha256',
               'split_version', 'split_entries_sha256', 'split')}
    config.update(tiny_run=False, versions=versions, active_stubs={},
                  inputs=[{key: value for key, value in row.items() if key != 'path'}])
    summary = dict(official=True, tiny_run=False, versions=versions, **{
        key: config[key] for key in ('epoch_id', 'source_sha256', 'regen_manifest_sha256')})
    return record, config, summary


def save_reference_run(tmp_path, record, config, summary):
    from eval import harness
    root = tmp_path / 'reference'
    (root / 'instances').mkdir(parents=True, exist_ok=True)
    fingerprint = harness._digest(config)
    record['config_sha256'] = fingerprint
    summary['config_sha256'] = fingerprint
    (root / 'config.json').write_text(json.dumps(config))
    (root / 'summary.json').write_text(json.dumps(summary))
    (root / 'instances/spec.json').write_text(json.dumps(record))
    return root


@pytest.mark.parametrize('change', [
    'diagnostic_config', 'unofficial_summary', 'epoch_id', 'source_sha256',
    'regen_manifest_sha256', 'checker_config', 'checker_report', 'missing_provenance',
    'unbound_row', 'unbound_summary', 'missing_config', 'missing_summary',
    'row_diagnostic', 'row_ineligible',
])
def test_official_reference_rejects_diagnostic_stale_or_unbound_run_before_planning(
        tmp_path, empty_ship_instance, monkeypatch, change):
    harness, row, calls = harness_selection(monkeypatch, empty_ship_instance)
    record, config, summary = official_reference_run(tmp_path, row, empty_ship_instance)
    if change == 'diagnostic_config':
        config['tiny_run'] = True
    elif change == 'unofficial_summary':
        summary['official'] = False
    elif change in ('epoch_id', 'source_sha256', 'regen_manifest_sha256'):
        config[change] = record[change] = summary[change] = 'other-epoch'
    elif change == 'checker_config':
        config['versions'] = {**config['versions'], 'checker': 'old-checker'}
    elif change == 'checker_report':
        record['report']['provenance']['versions'] = {
            **record['report']['provenance']['versions'], 'checker': 'old-checker'}
    elif change == 'missing_provenance':
        record.pop('epoch_id')
    elif change == 'row_diagnostic':
        record['diagnostic'] = True
    elif change == 'row_ineligible':
        record['evidence_eligible'] = False
    root = save_reference_run(tmp_path, record, config, summary)
    if change == 'unbound_row':
        record['config_sha256'] = 'unrelated'
        (root / 'instances/spec.json').write_text(json.dumps(record))
    elif change == 'unbound_summary':
        summary['config_sha256'] = 'unrelated'
        (root / 'summary.json').write_text(json.dumps(summary))
    elif change in ('missing_config', 'missing_summary'):
        (root / ('config.json' if change == 'missing_config' else 'summary.json')).unlink()
    with pytest.raises(ValueError, match='reference'):
        harness.run_evaluation(HarnessDiagnostic(), run_dir=tmp_path / 'run', reference_dir=root,
                               rungs=('tiny',), tiny_run=False)
    assert calls == []


def test_matching_official_reference_accepted_and_rows_retain_status(
        tmp_path, empty_ship_instance, monkeypatch):
    harness, row, calls = harness_selection(monkeypatch, empty_ship_instance)
    record, config, summary = official_reference_run(tmp_path, row, empty_ship_instance)
    root = save_reference_run(tmp_path, record, config, summary)
    result = harness.run_evaluation(HarnessDiagnostic(), run_dir=tmp_path / 'run',
                                   reference_dir=root, rungs=('tiny',), tiny_run=False)
    assert result['official'] and len(calls) == 1
    saved = json.loads((tmp_path / 'run/instances/spec.json').read_text())
    assert saved['official'] is True and saved['tiny_run'] is False
    assert saved['diagnostic'] is False and saved['evidence_eligible'] is True


def test_diagnostic_rows_retain_unofficial_status(tmp_path, empty_ship_instance, monkeypatch):
    harness, row, calls = harness_selection(monkeypatch, empty_ship_instance)
    harness.run_evaluation(HarnessDiagnostic(), run_dir=tmp_path / 'run',
                           rungs=('tiny',), tiny_run=True)
    saved = json.loads((tmp_path / 'run/instances/spec.json').read_text())
    assert saved['official'] is False and saved['tiny_run'] is True
    assert saved['diagnostic'] is True and saved['evidence_eligible'] is False


@pytest.mark.parametrize('change', ['fresh_report', 'cached_report'])
def test_old_checker_report_never_counts_as_current_bound_evidence(
        tmp_path, empty_ship_instance, monkeypatch, change):
    harness, row, calls = harness_selection(monkeypatch, empty_ship_instance)
    record, _, _ = official_reference_run(tmp_path, row, empty_ship_instance)
    if change == 'fresh_report':
        record['report']['provenance']['versions'] = {
            **record['report']['provenance']['versions'], 'checker': 'stale-checker'}

    def pipeline(planner, instance, **kwargs):
        calls.append(instance.id)
        result = PlannerResult.model_validate(record['result'])
        report = CheckReport.model_validate(record['report'])
        result._record_full_check(instance, report)
        return result, report

    monkeypatch.setattr(harness, 'run_planner', pipeline)
    kwargs = dict(run_dir=tmp_path / 'run', rungs=('tiny',), tiny_run=False)
    harness.run_evaluation(HarnessDiagnostic(), **kwargs)
    path = tmp_path / 'run/instances/spec.json'
    saved = json.loads(path.read_text())
    if change == 'fresh_report':
        assert saved['report_binding_valid'] is False
        assert saved['shippable'] is False and saved['report']['passed'] is False
    else:
        assert saved['report_binding_valid'] and saved['shippable']
        saved['report']['provenance']['versions']['checker'] = 'stale-checker'
        path.write_text(json.dumps(saved))
        with pytest.raises(ValueError, match='bound'):
            harness.run_evaluation(HarnessDiagnostic(), **kwargs)
        assert len(calls) == 1
