import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from . import worker
from .instructions import FixtureParser


def job(**updates):
    return dict(map_id='synthetic', cohort='tiny15', path='not-opened.json', sha256='a'*64,
                arm='pipeline', mode='dry-run', fixture_surrogate=True, **updates)


def gate(in_envelope=True):
    return dict(in_envelope=in_envelope, size_class='tiny', reasons=[], numbers={})


def audit(passed=True, level=0, instruction=None):
    return dict(passed=passed, full_checker_pass=passed, complete=True, mask_ok=True,
        replay_legal=True, all_loaded=True, level=level, instruction_recheck=instruction,
        instruction_violations=[] if instruction else None, lifts_current_port=2,
        lifts_projected_later_port=6, lifts_total=8, weighted_lifts=8., checker={})


def setup(monkeypatch, *, in_envelope=True):
    monkeypatch.setattr(worker, '_load', lambda job, **kw: SimpleNamespace(id='synthetic'))
    monkeypatch.setattr(worker, '_gate', lambda instance: gate(in_envelope))


def test_rejected_maps_are_never_planned_or_passed(monkeypatch, tmp_path):
    setup(monkeypatch, in_envelope=False)
    def forbidden(*args):
        raise AssertionError('planner must not run outside envelope')
    monkeypatch.setattr(worker, '_pipeline', forbidden)
    row = worker.run_job(job(), str(tmp_path))
    assert row['status'] == 'out_of_envelope'
    assert row['passed'] is False
    assert row['lifts_total'] is None


def test_runner_pass_cannot_replace_fresh_checker(monkeypatch, tmp_path):
    setup(monkeypatch)
    monkeypatch.setattr(worker, '_pipeline', lambda *a: dict(status='checked_plan', passed=True, plan={'x': 1}))
    monkeypatch.setattr(worker, '_audit', lambda *a: audit(passed=False, level=None))
    row = worker.run_job(job(), str(tmp_path))
    assert row['status'] == 'no_checked_plan'
    assert row['passed'] is False


def test_relaxed_plan_is_separate_and_preserves_split_lifts(monkeypatch, tmp_path):
    setup(monkeypatch)
    monkeypatch.setattr(worker, '_pipeline', lambda *a: dict(status='relaxed_plan', plan={'x': 1}))
    monkeypatch.setattr(worker, '_audit', lambda *a: audit(passed=False, level=2))
    row = worker.run_job(job(), str(tmp_path))
    assert (row['passed'], row['status'], row['relaxed_level']) == (False, 'relaxed_plan', 2)
    assert (row['lifts_current_port'], row['lifts_projected_later_port']) == (2, 6)


def test_sealed_reader_has_no_implicit_surrogate():
    expected = {'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'close_bay', 'bay': 1}}
    with pytest.raises(ValueError, match='frozen reader'):
        worker._parser(dict(mode='sealed'), expected, None)
    with pytest.raises(ValueError, match='surrogate'):
        worker._parser(dict(mode='sealed'), None, FixtureParser())
    # The no-rule arm has no sentence to read; empty standing is local replay.
    assert isinstance(worker._parser(dict(mode='sealed'), None), worker.RecordedParser)


def test_open_log_records_actual_bytes_even_on_bad_hash(tmp_path):
    path = tmp_path/'synthetic.json'
    path.write_text('{"synthetic":true}')
    log = tmp_path/'opened.jsonl'
    with pytest.raises(ValueError, match='manifest'):
        worker._load(dict(job(), path=str(path), open_log=str(log)))
    record = json.loads(log.read_text())
    assert record['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_instruction_prepass_ranks_facts_and_draws_before_planning(monkeypatch, tmp_path):
    from .test_instructions import facts
    maps = [dict(map_id=f'm{i:02}', cohort='envelope24', path=f'm{i:02}', sha256='b'*64)
            for i in range(24)]
    monkeypatch.setattr(worker, '_load', lambda item, **kw: dict(facts(), id=item['map_id']))
    monkeypatch.setattr(worker, 'public_facts', lambda instance: {'fact': instance['id']})
    monkeypatch.setattr(worker, 'instruction_targets', lambda *a: dict(
        instruction_target_count=None, no_matching_cargo=None))
    first = worker.prepare_instructions(list(reversed(maps)), tmp_path/'first', tmp_path/'log',
                                        fixture_surrogate=True)
    second = worker.prepare_instructions(maps, tmp_path/'second', tmp_path/'log',
                                         fixture_surrogate=True)
    assert {m['map_id']: m['cohort_index'] for m in first} == {m['map_id']: m['cohort_index'] for m in second}
    assert sorted(m['cohort_index'] for m in first) == list(range(24))
    assert len(json.loads((tmp_path/'first'/'instruction-draw.json').read_text())) == 24


def test_audit_rejects_same_process_and_modified_counts(monkeypatch, tmp_path):
    setup(monkeypatch)
    request = job()
    raw = dict(request, map_sha256=request['sha256'], planning_pid=os.getpid(),
               plan={'x': 1}, envelope=gate(), **audit())
    target = tmp_path/'raw.json'
    target.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='different process'):
        worker.audit_job(dict(request, target=str(target)))
    raw['planning_pid'] = os.getpid()+1
    raw['plan_sha256'] = hashlib.sha256(worker.canonical(raw['plan'])).hexdigest()
    raw['lifts_total'] = 9
    target.write_text(json.dumps(raw))
    monkeypatch.setattr(worker, '_audit', lambda *a: audit())
    with pytest.raises(ValueError, match='differs'):
        worker.audit_job(dict(request, target=str(target)))


def test_instruction_failure_cannot_be_accepted_as_relaxed(monkeypatch, tmp_path):
    setup(monkeypatch)
    expected = {'constraint': {'type': 'close_bay'}}
    monkeypatch.setattr(worker, 'select_rule', lambda *a: expected)
    monkeypatch.setattr(worker, 'sentence_for', lambda *a: 'Synthetic instruction')
    monkeypatch.setattr(worker, '_recorded_reading', lambda *a: dict(
        status='accepted', expected_match=True, wall_s=0.))
    monkeypatch.setattr(worker, '_pipeline', lambda *a: dict(status='relaxed_plan', plan={'x': 1}))
    monkeypatch.setattr(worker, '_audit', lambda *a: audit(passed=False, level=2, instruction=False))
    row = worker.run_job(dict(job(), arm='instruction', cohort_index=0,
                             reading_sha256='a'*64, reading_allocation_sha256='b'*64), str(tmp_path))
    assert row['status'] == 'no_checked_plan'
    assert row['instruction_recheck'] is False


def test_native_audit_requires_complete_replay_and_splits_d15(monkeypatch):
    import stowage.model
    import stowage.checker
    import stowage.moves
    import stowage.actions
    import stowage.feasibility
    import engine.peel
    instance = SimpleNamespace(id='synthetic', call_index=0, load_list=['BOX1'])
    plan = SimpleNamespace(instance_id='synthetic', call_index=0)
    monkeypatch.setattr(stowage.model.MovePlan, 'model_validate', lambda raw: plan)
    count = SimpleNamespace(relocations=2, restow_lifts=3, hatch_cover_lifts=4,
        future=[SimpleNamespace(restow_lifts=6, hatch_cover_lifts=2)], weighted_nonproductive=17.)
    report = SimpleNamespace(passed=True, count=count, model_dump=lambda **kwargs: {'passed': True})
    monkeypatch.setattr(stowage.checker, 'check', lambda *a: report)
    replay = SimpleNamespace(legal=True, final_state=SimpleNamespace(ship=SimpleNamespace(
        placements=[SimpleNamespace(container_id='BOX1', slot='0010182')])))
    monkeypatch.setattr(stowage.moves, 'apply_plan', lambda *a: replay)
    monkeypatch.setattr(stowage.actions, 'get_action_space', lambda *a: SimpleNamespace(from_plan=lambda *a: [1]))
    mask = SimpleNamespace(is_legal=lambda action: True, push=lambda action: None, is_complete=lambda: False)
    monkeypatch.setattr(stowage.feasibility, 'make_feasibility_engine', lambda *a, **kw: mask)
    monkeypatch.setattr(engine.peel, 'inspect', lambda *a: {'level': 0})
    measured = worker._audit(instance, {}, None)
    assert measured['full_checker_pass'] is True
    assert measured['passed'] is True
    assert measured['complete'] is True
    assert measured['mask_complete'] is False
    assert measured['lifts_current_port'] == 9
    assert measured['lifts_projected_later_port'] == 8
    mask.is_legal = lambda action: False
    assert worker._audit(instance, {}, None)['passed'] is True
    assert worker._audit(instance, {}, None)['mask_ok'] is False
    replay.legal = False
    assert worker._audit(instance, {}, None)['passed'] is False
    replay.legal = True
    replay.final_state.ship.placements = []
    assert worker._audit(instance, {}, None)['passed'] is False


def test_packet_is_standard_and_confirmation_checks_intended_rule(monkeypatch, tmp_path):
    import experiments.mvp.pipeline.runner
    expected = {'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'close_bay', 'bay': 1}}
    captured = {}
    def run(packet, **kwargs):
        captured.update(packet=packet, **kwargs)
        return {'status': 'no_checked_plan'}
    monkeypatch.setattr(experiments.mvp.pipeline.runner, 'run_packet', run)
    worker._pipeline(None, job(), tmp_path, FixtureParser(expected), expected)
    assert captured['packet']['profile'] == 'standard'
    assert captured['packet']['voyage_sentences'][0]['text'] == 'Do not load containers in bay 1.'
    assert captured['confirm']({'parsed': expected}) is True
    assert captured['confirm']({'parsed': {'wrong': True}}) is False


def test_baseline_violating_counterfactual_instruction_still_passes(monkeypatch, tmp_path):
    setup(monkeypatch)
    monkeypatch.setattr(worker, '_pipeline', lambda *a: dict(status='checked_plan', plan={'x': 1}))
    monkeypatch.setattr(worker, '_audit', lambda *a: audit())
    monkeypatch.setattr(worker, '_counterfactual', lambda *a: dict(
        counterfactual_instruction_recheck=False, counterfactual_instruction_violations=['bay occupied']))
    row = worker.run_job(dict(job(), expected_rule={'constraint': {'type': 'close_bay'}}), str(tmp_path))
    assert row['passed'] is True
    assert row['status'] == 'checked_plan'
    assert row['instruction_recheck'] is None
    assert row['counterfactual_instruction_recheck'] is False


def test_standalone_worker_refuses_sealed_path_before_reading(monkeypatch, tmp_path):
    from . import driver
    sealed = tmp_path/'unopened-synthetic'
    monkeypatch.setattr(driver, 'V3', sealed)
    path = sealed/'must-not-open.json'
    # No file is created: absence of authorization must precede any byte read.
    with pytest.raises(ValueError, match='active claim'):
        worker._load(dict(job(), path=str(path)))


def test_sealed_open_requires_frozen_exact_membership(monkeypatch, tmp_path):
    from . import driver
    sealed = tmp_path/'sealed-synthetic'
    monkeypatch.setattr(driver, 'V3', sealed)
    path = sealed/'unopened.json'
    run = tmp_path/'run'
    run.mkdir()
    claim = tmp_path/'CLAIM.json'
    monkeypatch.setattr(driver, 'GLOBAL_CLAIM', claim)
    claim.write_text(json.dumps(dict(run_id='fixed', out_dir=str(run))))
    (run/'STARTED.json').write_text(json.dumps(dict(run_id='fixed')))
    (run/'config.json').write_text(json.dumps(dict(mode='sealed', maps=[])))
    monkeypatch.setattr(driver, 'verify_freeze', lambda directory: dict(run_id='fixed', mode='sealed'))
    request = dict(job(), mode='sealed', path=str(path), run_dir=str(run),
                   open_log=str(run/'opened-maps.jsonl'))
    with pytest.raises(ValueError, match='exact member'):
        worker._load(request)
    (run/'COMPLETE.json').write_text('{}')
    with pytest.raises(ValueError, match='active matching'):
        worker._load(request)


def test_out_of_envelope_instruction_preserves_fixed_draw_and_audits_it(monkeypatch, tmp_path):
    setup(monkeypatch, in_envelope=False)
    rule = dict(schema_version='stowlm.reader/2', constraint=dict(type='close_bay', bay=1))
    request = dict(job(), arm='instruction', cohort_index=0, expected_rule=rule)
    monkeypatch.setattr(worker, 'select_rule', lambda *args: rule)
    monkeypatch.setattr(worker, '_pipeline', lambda *args: pytest.fail('must not plan rejected map'))
    row = worker.run_job(request, str(tmp_path))
    assert row['status'] == 'out_of_envelope'
    assert row['expected_rule'] == rule
    assert row['instruction_type'] == 'close_bay'
    assert row['instruction_sentence'] == 'Do not load containers in bay 1.'
    row['planning_pid'] = os.getpid()+1
    target = tmp_path/'row.json'
    target.write_text(json.dumps(row))
    verified = worker.audit_job(dict(request, target=str(target)))
    assert verified['audit_verified'] is True
    row['expected_rule'] = dict(schema_version='stowlm.reader/2', constraint=dict(type='close_bay', bay=3))
    target.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='fixed draw'):
        worker.audit_job(dict(request, target=str(target)))
