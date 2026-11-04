"""Amendment regression tests: synthetic facts, no model or native search."""
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from . import worker
from .common import object_hash
from .instructions import select_rule, sentence_for
from .test_instructions import facts


def recorded_job(rule):
    raw = facts()
    raw['load_list'] = ['BOX1']
    from experiments.mvp.reader.schema import context_from_instance
    context = context_from_instance(raw)
    reading = dict(map_id='m', map_sha256='a'*64, allocation_sha256='b'*64, cohort_index=0,
                   expected_rule=rule, sentence=sentence_for(rule),
                   context_sha256=object_hash(context), parsed=rule, status='accepted',
                   expected_match=True, wall_s=.1,
                   parser=dict(parser='SyntheticRecorded', adapter_sha256=None))
    job = dict(map_id='m', sha256='a'*64, arm='instruction', mode='dry-run', cohort_index=0,
               recorded_reading=reading, reading_sha256=object_hash(reading),
               reading_allocation_sha256='b'*64)
    return job, context


def test_tighter_cap_and_load_list_majority_dg_with_lexical_ties():
    raw = facts()
    assert select_rule(raw, 1)['constraint']['max_tiers'] == 0
    raw['load_list'] = ['BOX1', 'BOX2', 'BOX3']
    raw['containers'] = [dict(id='BOX1', dg=dict(imdg_class='8')),
                         dict(id='BOX2', dg=dict(imdg_class='8', additional=[dict(imdg_class='8')])),
                         dict(id='BOX3', dg=dict(imdg_class='3')),
                         *[dict(id=f'BACKGROUND{i}', dg=dict(imdg_class='3')) for i in range(10)]]
    assert select_rule(raw, 2)['constraint']['imdg_class'] == '8'
    raw['load_list'] = ['BOX1', 'BOX3']
    assert select_rule(raw, 2)['constraint']['imdg_class'] == '3'


def test_workers_replay_without_importing_factories_and_bind_all_fields(monkeypatch):
    rule = select_rule(facts(), 0)
    job, context = recorded_job(rule)
    job['reader_factory'] = 'never_imported:NeverConstructed'
    monkeypatch.setattr(worker.importlib, 'import_module', lambda *a: pytest.fail('worker loaded a reader'))
    parser = worker._parser(job, rule)
    answer, provider = parser.parse(sentence_for(rule), context)
    assert answer.type == 'close_bay'
    assert provider['recorded'] and provider['reading_sha256'] == job['reading_sha256']
    empty, _ = parser.parse('', context)
    assert empty.refused is True
    with pytest.raises(ValueError, match='sentence/context'):
        parser.parse(sentence_for(rule), dict(context, id='changed'))
    for field, value in [('map_id', 'other'), ('reading_allocation_sha256', 'c'*64)]:
        with pytest.raises(ValueError, match='allocation'):
            worker._parser(dict(job, **{field: value}), rule)
    changed = copy.deepcopy(job)
    changed['recorded_reading']['parsed'] = None
    with pytest.raises(ValueError, match='hash mismatch'):
        worker._parser(changed, rule)


def test_prepass_constructs_once_reads_serially_once_and_persists_hashes(monkeypatch, tmp_path):
    raw = facts()
    raw['load_list'] = ['BOX1']
    rule = select_rule(raw, 0)
    seen = []
    class Reader:
        def __init__(self):
            seen.append('constructed')
        def parse(self, sentence, context):
            from experiments.mvp.reader.schema import validate_answer
            seen.append(sentence)
            return validate_answer(json.dumps(rule), context), dict(parser='Synthetic', adapter_sha256=None)
    module = SimpleNamespace(Reader=Reader)
    monkeypatch.setitem(sys.modules, 'sealed_test_reader', module)
    monkeypatch.setattr(worker, '_load', lambda *a, **kw: copy.deepcopy(raw))
    monkeypatch.setattr(worker, 'select_rule', lambda *a: rule)
    maps = [dict(map_id=f'm{i}', sha256=str(i)*64) for i in range(4)]
    prepared = worker.prepare_instructions(maps, tmp_path, tmp_path/'opened',
                                           reader_factory='sealed_test_reader:Reader', mode='sealed')
    assert seen == ['constructed'] + [sentence_for(rule)]*4
    assert len({item['reading_allocation_sha256'] for item in prepared}) == 1
    records = json.loads((tmp_path/'instruction-draw.json').read_text())
    for item, record in zip(prepared, records):
        assert item['reading_sha256'] == object_hash(item['recorded_reading'])
        assert record['reading_sha256'] == item['reading_sha256']
        assert item['recorded_reading']['expected_match'] is True


def test_recording_is_bound_to_committed_allocation_not_self_consistency(tmp_path):
    rule = select_rule(facts(), 0)
    job, _ = recorded_job(rule)
    job.update(mode='sealed', run_dir=str(tmp_path), expected_rule=rule)
    allocation = tmp_path/'instruction-allocation.json'
    allocation.write_text(json.dumps([job]))
    import hashlib
    job['instruction_allocation_sha256'] = hashlib.sha256(allocation.read_bytes()).hexdigest()
    worker._recorded_reading(job, rule)
    changed = copy.deepcopy(job)
    changed['recorded_reading']['wall_s'] = 123.
    changed['reading_sha256'] = object_hash(changed['recorded_reading'])
    with pytest.raises(ValueError, match='committed instruction allocation'):
        worker._recorded_reading(changed, rule)
    allocation.write_text(json.dumps([changed]))
    with pytest.raises(ValueError, match='allocation hash'):
        worker._recorded_reading(job, rule)


def test_reader_constructor_failure_is_retained_per_instruction(monkeypatch, tmp_path):
    raw = facts()
    rule = select_rule(raw, 0)
    def broken():
        raise RuntimeError('synthetic reader unavailable')
    monkeypatch.setitem(sys.modules, 'sealed_broken_reader', SimpleNamespace(Reader=broken))
    monkeypatch.setattr(worker, '_load', lambda *a, **kw: copy.deepcopy(raw))
    monkeypatch.setattr(worker, 'select_rule', lambda *a: rule)
    maps = [dict(map_id=f'm{i}', sha256=str(i)*64) for i in range(4)]
    prepared = worker.prepare_instructions(maps, tmp_path, tmp_path/'opened',
        reader_factory='sealed_broken_reader:Reader', mode='sealed')
    assert len(prepared) == 4
    assert [item['instruction_type'] for item in prepared] == list(worker.RULE_TYPES)
    assert all(item['recorded_reading']['status'] == 'error' for item in prepared)
    assert all('synthetic reader unavailable' in item['recorded_reading']['error'] for item in prepared)


def test_authorization_failure_cannot_be_reclassified_as_reader_or_draw_failure(monkeypatch, tmp_path):
    from .authorization import AuthorizationError
    def denied(*args, **kwargs):
        raise ValueError('synthetic spent authorization')
    monkeypatch.setattr(worker, '_authorize_open', denied)
    with pytest.raises(AuthorizationError, match='spent authorization'):
        worker.prepare_instructions([dict(map_id='m', path=str(tmp_path/'unopened'),
            sha256='a'*64, mode='sealed')], tmp_path/'draw', tmp_path/'opened', mode='sealed')


def test_packet_empty_standing_and_named_confirmation(monkeypatch, tmp_path):
    import experiments.mvp.pipeline.runner
    rule = select_rule(facts(), 0)
    job, _ = recorded_job(rule)
    job.update(path='never-opened')
    captured = {}
    def run(packet, **kwargs):
        captured.update(packet=packet, **kwargs)
        return dict(status='no_checked_plan')
    monkeypatch.setattr(experiments.mvp.pipeline.runner, 'run_packet', run)
    worker._pipeline(None, job, tmp_path, worker._parser(job, rule), rule)
    assert captured['packet']['standing'] == ''
    assert captured['confirmation_source'] == 'sealed_expected_match'


def test_sensitivity_preserves_constructor_and_forces_300_second_finisher(monkeypatch):
    import stowage.solver.twostep
    import stowage.planner
    budgets = []
    result = SimpleNamespace(status='synthetic', plan=SimpleNamespace(model_dump=lambda **kw: {}))
    class Planner:
        def plan(self, instance, **kw):
            budgets.append(kw['time_limit_s'])
            return result
    def finish(instance, output, **kw):
        budgets.append(kw['time_limit_s'])
        return output
    monkeypatch.setattr(stowage.solver.twostep, 'TwoStepPlanner', Planner)
    monkeypatch.setattr(stowage.planner, 'finalize', finish)
    worker._twostep(None, dict(arm='twostep_300', twostep_s=15, twostep_finalize_s=1))
    assert budgets == [15, 300]


def test_reader_failure_retained_without_engine_and_separate_from_engine_failure(monkeypatch, tmp_path):
    raw = facts()
    raw['load_list'] = ['BOX1']
    rule = select_rule(raw, 0)
    job, _ = recorded_job(rule)
    reading = job['recorded_reading']
    reading.update(status='error', parsed=None, expected_match=False)
    job.update(cohort='envelope24', path='not-opened', cohort_index=0,
               expected_rule=rule, reading_sha256=object_hash(reading))
    monkeypatch.setattr(worker, '_load', lambda *a, **kw: SimpleNamespace(id='m'))
    monkeypatch.setattr(worker, 'select_rule', lambda *a: rule)
    monkeypatch.setattr(worker, '_gate', lambda *a: dict(in_envelope=True))
    monkeypatch.setattr(worker, '_pipeline', lambda *a: pytest.fail('engine ran for failed reader'))
    row = worker.run_job(job, str(tmp_path))
    assert row['status'] == 'needs_confirmation'
    assert row['reader_failure'] is True and row['engine_failure'] is False
    assert row['reading_status'] == 'error' and row['plan'] is None


def test_accepted_reading_without_engine_pass_is_engine_failure(monkeypatch, tmp_path):
    rule = select_rule(facts(), 0)
    job, _ = recorded_job(rule)
    job.update(cohort='envelope24', path='not-opened', expected_rule=rule)
    monkeypatch.setattr(worker, '_load', lambda *a, **kw: SimpleNamespace(id='m'))
    monkeypatch.setattr(worker, 'select_rule', lambda *a: rule)
    monkeypatch.setattr(worker, '_gate', lambda *a: dict(in_envelope=True))
    monkeypatch.setattr(worker, '_pipeline', lambda *a: dict(status='no_checked_plan', plan=None))
    row = worker.run_job(job, str(tmp_path))
    assert row['reader_failure'] is False and row['engine_failure'] is True
    assert row['reading_status'] == 'accepted'


def test_public_ship_population_id_survives_sliced_vessel_identity(monkeypatch, tmp_path):
    from .test_worker import job
    monkeypatch.setattr(worker, '_load', lambda *a, **kw: SimpleNamespace(
        id='m', vessel=SimpleNamespace(id='feeder_500-sliced-geometry')))
    monkeypatch.setattr(worker, '_gate', lambda *a: dict(in_envelope=False))
    row = worker.run_job(dict(job(), ship_id='feeder_500'), str(tmp_path))
    assert row['ship_id'] == 'feeder_500'
    assert row['vessel_id'] == 'feeder_500-sliced-geometry'


def test_counterfactual_explicitly_pins_intended_rule(monkeypatch):
    import stowage.model
    import experiments.mvp.reader.pipeline
    monkeypatch.setattr(stowage.model.MovePlan, 'model_validate', lambda *a: None)
    monkeypatch.setattr(experiments.mvp.reader.pipeline, 'intrinsic_violations', lambda *a: ['bound'])
    rule = select_rule(facts(), 1)
    result = worker._counterfactual(facts(), {}, rule)
    assert result['counterfactual_instruction_recheck'] is False
    assert result['counterfactual_rule'] == rule
    assert result['counterfactual_rule_sha256'] == object_hash(rule)


def test_fresh_audit_rejects_changed_final_cells(monkeypatch, tmp_path):
    from .test_worker import job, gate, audit
    import os
    request = job()
    row = dict(request, map_sha256=request['sha256'], planning_pid=os.getpid()+1,
               plan={'x': 1}, envelope=gate(), final_cells={'BOX1': ['0010182']}, **audit())
    row['plan_sha256'] = object_hash(row['plan'])
    target = tmp_path/'raw.json'
    target.write_text(json.dumps(row))
    monkeypatch.setattr(worker, '_load', lambda *a, **kw: None)
    monkeypatch.setattr(worker, '_gate', lambda *a: gate())
    monkeypatch.setattr(worker, '_audit', lambda *a: dict(audit(), final_cells={'BOX1': ['0030182']}))
    with pytest.raises(ValueError, match='differs'):
        worker.audit_job(dict(request, target=str(target)))


@pytest.mark.parametrize('arm', ['twostep', 'twostep_300'])
def test_envelope_twostep_fresh_audit_does_not_claim_pipeline_counterfactual(monkeypatch, tmp_path, arm):
    from .test_worker import job, gate, audit
    import os
    request = dict(job(), arm=arm, cohort='envelope24',
                   expected_rule=select_rule(facts(), 0))
    monkeypatch.setattr(worker, '_load', lambda *a, **kw: SimpleNamespace(id='m'))
    monkeypatch.setattr(worker, '_gate', lambda *a: gate())
    monkeypatch.setattr(worker, '_twostep', lambda *a: dict(status='checked_plan', plan={'x': 1}))
    monkeypatch.setattr(worker, '_audit', lambda *a: dict(audit(), final_cells={'BOX1': ['0010182']}))
    row = worker.run_job(request, str(tmp_path/'planning'))
    assert row['passed'] is True
    row['planning_pid'] = os.getpid()+1
    target = tmp_path/'raw.json'
    target.write_text(json.dumps(row))
    verified = worker.audit_job(dict(request, target=str(target)))
    assert verified['audit_verified'] is True
    assert verified.get('counterfactual_rule') is None
