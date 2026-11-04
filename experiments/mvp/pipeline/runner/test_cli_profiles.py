"""Offline CLI/profile and development measurement integrity tests."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.runner.__main__ import main, recorded_parser
from experiments.mvp.pipeline.runner.benchmark import dev_paths, summarize, wait_for_capacity
from experiments.mvp.pipeline.runner.profiles import budget_profile, engine_kwargs, get_profile


def test_cli_passes_explicit_confirmation_and_events(tmp_path, monkeypatch, capsys):
    import experiments.mvp.pipeline.runner as runner
    import experiments.mvp.pipeline.runner.__main__ as command
    packet = tmp_path / 'packet.json'
    packet.write_text(json.dumps({'packet_id': 'synthetic-test'}))
    sentinel = object()
    monkeypatch.setattr(command, 'recorded_parser', lambda path: sentinel)
    calls = []

    def run(value, **kwargs):
        calls.append(kwargs)
        assert kwargs['confirmation_source'] == 'batch_confirm_all'
        kwargs['on_event']({'kind': 'done'})
        assert kwargs['confirm']({'reading': 'accepted'}) is True
        return {'status': 'checked_plan', 'message': 'Full checker passed.'}

    monkeypatch.setattr(runner, 'run_packet', run)
    assert main([str(packet), '--out', str(tmp_path / 'out'), '--confirm-all', '--events']) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['status'] == 'checked_plan'
    assert json.loads(output.err)['kind'] == 'done'
    assert calls[0]['parser'] is sentinel


def test_cli_does_not_confirm_by_default(tmp_path, monkeypatch, capsys):
    import experiments.mvp.pipeline.runner as runner
    import experiments.mvp.pipeline.runner.__main__ as command
    packet = tmp_path / 'packet.json'
    packet.write_text('{}')
    monkeypatch.setattr(command, 'recorded_parser', lambda path: object())

    def run(value, **kwargs):
        assert kwargs['confirm'] is None
        assert kwargs['confirmation_source'] == 'none'
        return {'status': 'needs_confirmation', 'message': 'Confirm the reading.'}

    monkeypatch.setattr(runner, 'run_packet', run)
    assert main([str(packet), '--out', str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'needs_confirmation'


def test_cli_bad_json_is_a_message(tmp_path, capsys):
    packet = tmp_path / 'bad.json'
    packet.write_text('{')
    assert main([str(packet), '--out', str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)['status'] == 'error'
    assert 'Traceback' not in captured.out + captured.err


def test_explicit_recording_is_context_bound_and_labelled(tmp_path):
    from app.brief import NoRecording, RecordedParser
    path = tmp_path / 'readings.json'
    path.write_text(json.dumps({'readings': [dict(sentence='Close bay 1.',
        envelope={'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'close_bay', 'bay': 1}},
        evidence={'parser': 'SyntheticRecordedParser', 'adapter_sha256': None}, ship='synthetic',
        seconds=.1)]}))
    parser = recorded_parser(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert isinstance(parser, RecordedParser)
    answer, evidence = parser.parse('Close bay 1.', {'id': 'synthetic', 'bays': [1, 3]})
    assert answer.bay == 1
    assert evidence['recorded'] is True and evidence['context_bound'] is True
    with pytest.raises(NoRecording):
        parser.parse('Close bay 1.', {'id': 'different', 'bays': [1, 3]})
    with pytest.raises(NoRecording):
        parser.parse('Close bay 1.', {'id': 'synthetic', 'bays': [3]})


def test_custom_recording_requires_external_hash_and_rejects_changed_bytes(tmp_path):
    path = tmp_path / 'handwritten-rule.json'
    path.write_text('{"readings": []}')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='independently trusted SHA256'):
        recorded_parser(path)
    recorded_parser(path, expected_sha256=digest)
    path.write_text('{"readings": []}\n')
    with pytest.raises(ValueError, match='not authenticated'):
        recorded_parser(path, expected_sha256=digest)


def test_bundled_default_uses_existing_pin_and_detects_tampering(tmp_path, monkeypatch):
    import experiments.mvp.pipeline.runner.__main__ as command
    from experiments.mvp.pipeline.runner.stored import BUNDLED_READINGS_SHA256
    original = (command.HERE / 'fixtures/readings.json').read_bytes()
    assert hashlib.sha256(original).hexdigest() == BUNDLED_READINGS_SHA256
    recorded_parser()
    fixtures = tmp_path / 'fixtures'
    fixtures.mkdir()
    (fixtures / 'readings.json').write_bytes(original + b'\n')
    monkeypatch.setattr(command, 'HERE', tmp_path)
    with pytest.raises(ValueError, match='not authenticated'):
        recorded_parser()


@pytest.mark.parametrize('offline', [False, True])
def test_cli_rejects_unpinned_custom_recording_in_every_mode(tmp_path, capsys, offline):
    packet = tmp_path / 'packet.json'
    packet.write_text('{}')
    path = tmp_path / 'handwritten-rule.json'
    path.write_text('{"readings": []}')
    args = [str(packet), '--out', str(tmp_path / 'out'), '--recorded', str(path), '--confirm-all']
    if offline:
        args.append('--offline')
    assert main(args) == 1
    output = json.loads(capsys.readouterr().out)
    assert output['status'] == 'error'
    assert '--recorded requires --recorded-sha256' in output['message']


def test_benchmark_cli_forwards_custom_recording_pin(tmp_path, monkeypatch, capsys):
    import experiments.mvp.pipeline.runner.__main__ as command
    import experiments.mvp.pipeline.runner.benchmark as study
    seen = []

    def parser(path, *, expected_sha256):
        seen.append((path, expected_sha256))
        return object()

    rules = tmp_path / 'rules.json'
    rules.write_text('["Synthetic instruction."]')
    monkeypatch.setattr(command, 'recorded_parser', parser)
    monkeypatch.setattr(study, 'benchmark', lambda *args, **kwargs: {'summary': {'tested': True}})
    path = tmp_path / 'readings.json'
    assert study.main(['--rules', str(rules), '--recorded', str(path), '--recorded-sha256', 'a'*64]) == 0
    assert seen == [(path, 'a'*64)]
    assert json.loads(capsys.readouterr().out) == {'tested': True}


@pytest.mark.parametrize('name,target', [('live', 180.), ('standard', 900.)])
def test_profiles_are_pending_and_config_is_supported(name, target, monkeypatch, tmp_path):
    from experiments.mvp.pipeline.runner import profiles
    monkeypatch.setattr(profiles, 'CALIBRATION', tmp_path / 'no-measurements-yet.json')
    profile = get_profile(name)
    assert profile['pending_measurement'] is True
    assert profile['target_wall_s'] == target
    kwargs = engine_kwargs(profile)
    assert set(kwargs) == {'budget_s', 'finalize_s', 'seed', 'config'}
    assert asdict(kwargs['config'])['assignment_tries'] >= 1


def test_selected_partial_study_fixture_keeps_its_scope_and_authority():
    from experiments.mvp.pipeline.runner import profiles
    path = Path(__file__).with_name('fixtures') / 'profile-selection.json'
    before = path.read_bytes()
    selected = json.loads(before)
    assert selected['schema_version'] == 'stowlm.profile-selection/1'
    assert selected['measured'] is True and selected['completeness']['complete'] is False
    assert selected['decision_authority'] == 'leader'
    assert selected['decision_kind'] == 'explicit_partial_study_selection'
    assert selected['user_authorized'] is True and selected['stopped'] is True
    assert selected['observed_completed_rows'] == selected['completeness']['completed_jobs'] == 68
    assert selected['completeness']['expected_jobs'] == 256
    assert len(selected['row_files']) == 68 and len(selected['data_sha256']) == 64
    for name in ('live', 'standard'):
        profile = profiles.get_profile(name)
        assert profile['measurement_status'] == 'leader_selected_from_partial_study'
        assert profile['selection_kind'] == selected['decision_kind']
        assert profile['completeness'] == selected['completeness']
        assert 'pending_measurement' not in profile
        assert profile['data_sha256'] == selected['data_sha256']
        assert Path(profile['calibration_source']).resolve() == path.resolve()
        assert 'partial' in profile['notes'].lower() and '68' in profile['notes']
    assert path.read_bytes() == before


@pytest.mark.parametrize('name,budget,finalize,cap', [
    ('live', 120., 40., 720.), ('standard', 300., 100., 1800.),
])
def test_selected_profiles_preserve_exact_studied_knobs_and_user_caps(name, budget, finalize, cap):
    from experiments.mvp.pipeline.runner.profile_study import study_profile
    profile = get_profile(name)
    studied = study_profile(str(int(budget)))
    selected = json.loads((Path(__file__).with_name('fixtures') / 'profile-selection.json').read_text())
    kwargs = engine_kwargs(profile)
    assert kwargs['budget_s'] == studied['budget_s'] == budget
    assert kwargs['finalize_s'] == studied['finalize_s'] == finalize
    assert kwargs['seed'] == studied['seed']
    assert asdict(kwargs['config']) == studied['config']
    assert dict(kwargs, config=asdict(kwargs['config'])) == selected['profiles'][name]['engine_kwargs']
    assert profile['engine_timeout_s'] == profile['target_wall_s'] == cap
    assert profile['measurement_status'] == 'leader_selected_from_partial_study'
    assert 'pending_measurement' not in profile and 'peel_budget_choice' not in profile


@pytest.mark.parametrize('field,value', [
    ('decision_authority', 'automatic'), ('decision_kind', 'automatic_partial_selection'),
    ('user_authorized', False), ('stopped', False), ('data_sha256', ''),
    ('observed_completed_rows', 0),
])
def test_partial_selection_requires_explicit_stopped_study_authority(tmp_path, monkeypatch, field, value):
    from experiments.mvp.pipeline.runner import profiles
    source = Path(__file__).with_name('fixtures') / 'profile-selection.json'
    selected = json.loads(source.read_text())
    selected[field] = value
    invalid = tmp_path / 'unauthorized-selection.json'
    invalid.write_text(json.dumps(selected))
    monkeypatch.setattr(profiles, 'CALIBRATION', invalid)
    with pytest.raises(ValueError):
        profiles.get_profile('live')


@pytest.mark.parametrize('value', [0., -1., float('inf'), float('nan'), True])
def test_invalid_budget(value):
    with pytest.raises(ValueError):
        budget_profile(value)


def test_admission_waits_for_missing_stale_and_busy_telemetry(tmp_path):
    state = tmp_path / 'core-state.json'
    updates = iter([{'mask': 255, 'load1': 2., 't': 0},
                    {'mask': 255, 'load1': 46., 't': 1000},
                    {'mask': 127, 'load1': 4., 't': 1000},
                    {'mask': '0xff', 'load1': 4., 't': 1000}])
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        state.write_text(json.dumps(next(updates)))

    result = wait_for_capacity(state, sleep=sleep, now=lambda: 1001.)
    assert len(waits) == 4
    assert result['enabled_cores'] == 8 and result['load1'] == 4.
    assert result['reading_age_s'] == 1.


def test_dev_paths_reject_outside_path_without_opening_it(tmp_path):
    dev = tmp_path / 'dev'
    dev.mkdir()
    listing = tmp_path / 'dev.txt'
    listing.write_text('sealed/never-read.json\n')
    with pytest.raises(ValueError, match='outside the development cohort'):
        dev_paths(listing, dev_root=dev, root=tmp_path)
    listing.write_text('dev/synthetic.json\n')
    assert dev_paths(listing, dev_root=dev, root=tmp_path) == [dev / 'synthetic.json']


def test_summary_counts_full_passes_and_pairs_lift_cost():
    def row(name, budget, status, passed, lifts):
        return dict(map=name, target_budget_s=budget, size_class='medium', with_rules=True,
                    status=status, passed=passed, lifts=lifts, wall_s=2., first_legal_s=1. if passed else None,
                    admission={'load1': 20.})

    rows = [row('a', 60., 'checked_plan', True, 120.), row('b', 60., 'relaxed_plan', False, 1.),
            row('c', 60., 'checked_plan', False, 2.), row('a', 180., 'checked_plan', True, 100.),
            row('b', 180., 'checked_plan', True, 140.), row('c', 180., 'checked_plan', True, 150.)]
    result = summarize(rows)
    short = result['cells'][0]
    assert short['full_passes'] == 1 and short['mean_lifts_passing'] == 120.
    assert short['relaxed_plans'] == 1
    cost = result['shorter_budget_cost'][0]
    assert cost['passes_lost'] == 2 and cost['both_pass_maps'] == 1
    assert cost['mean_extra_lifts_both_pass'] == 20.
    assert 'projected later-port lifts (D15)' in result['notes']


def test_benchmark_reader_budget_grid_with_stub_binding(tmp_path, monkeypatch):
    import experiments.mvp.pipeline.runner.benchmark as study
    from experiments.mvp.reader.schema import OnePortPerHatch
    from llm.instructions.schema import CloseBay
    path = study.ROOT / 'experiments/mvp/corpus/val/tiny/feeder_800/random_yard/seed-020000004.json'
    monkeypatch.setattr(study, 'dev_paths', lambda listing: [path])
    monkeypatch.setattr(study, 'HERE', tmp_path)
    calls, configured, validated = [], [], []
    monkeypatch.setattr(study, 'configure_caches', lambda: configured.append(True))

    def gate(instance, raw, readings):
        validated.append((raw, readings))
        return {'lifts': 12., 'checker': {'passed': True}, 'rules': []} if raw == {'gate': 'good'} else None

    monkeypatch.setattr(study, '_validated_plan', gate)

    class Parser:
        def parse(self, sentence, context):
            answer = (OnePortPerHatch(type='one_port_per_hatch') if sentence == 'One port per hatch.'
                      else CloseBay(type='close_bay', bay=context['bays'][0]))
            return answer, {'parser': 'synthetic-test', 'adapter_sha256': None}

    def execute(instance, receipt, **kwargs):
        from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
        assert verify_confirmation(receipt)['source'] == 'benchmark_batch_confirm_all'
        readings = receipt.evidence()['sentences']
        calls.append((readings, kwargs))
        kwargs['on_incumbent'](None)
        kwargs['on_incumbent']({'checked': True})
        kwargs['on_incumbent']({'plan': {'gate': 'bad'}, 'passed': True})
        kwargs['on_incumbent']({'plan': {'gate': 'good'}})
        return SimpleNamespace(plan={'gate': 'good'}), {'status': 'checked_plan', 'passed': True, 'lifts': 999.}

    result = study.benchmark('unused-dev-list', parser=Parser(), out_dir=tmp_path / 'study',
                             voyage_sentences=['Close the first bay.'], budgets=(60., 180., 900.),
                             executor=execute, admit=lambda: {'load1': 10.})
    assert configured == [True]
    assert len(result['rows']) == 6
    assert [len(rows) for rows, _ in calls] == [1, 1, 1, 2, 2, 2]
    assert [row['profile']['name'] for row in result['rows'][:3]] == ['target_60s', 'live', 'standard']
    for index, name in [(1, 'live'), (2, 'standard')]:
        assert asdict(calls[index][1]['config']) == asdict(get_profile(name)['config'])
    assert len(validated) == 18  # bad callback, checked callback, and final result per run
    assert all(reading['role'] == 'voyage' for _, readings in validated for reading in readings)
    assert all(row['first_legal_s'] is not None for row in result['rows'])
    assert all(row['passed'] for row in result['rows'])
    assert all(row['confirmation_source'] == 'benchmark_batch_confirm_all' for row in result['rows'])
    assert all(len(row['confirmation_sha256']) == 64 for row in result['rows'])
    assert all(row['lifts'] == 12. for row in result['rows'])  # native count overrides supplied evidence
    assert json.loads((tmp_path / 'study/measurements.json').read_text())['summary'] == result['summary']


@pytest.mark.parametrize('unresolved', [False, True])
def test_benchmark_never_counts_ungated_plans_or_executes_unresolved_readings(tmp_path, monkeypatch, unresolved):
    import experiments.mvp.pipeline.runner.benchmark as study
    import experiments.mvp.pipeline.runner as runner
    import experiments.mvp.pipeline.runner.envelope as envelope
    import experiments.mvp.reader.public_brief as reader
    path = tmp_path / 'synthetic.json'
    path.write_text('{}')
    monkeypatch.setattr(study, 'ROOT', tmp_path)
    monkeypatch.setattr(study, 'HERE', tmp_path)
    monkeypatch.setattr(study, 'dev_paths', lambda listing: [path])
    monkeypatch.setattr(study, 'configure_caches', lambda: None)
    monkeypatch.setattr(runner, 'assemble', lambda packet: object())
    monkeypatch.setattr(envelope, 'check', lambda instance: {'in_envelope': True, 'size_class': 'tiny'})
    monkeypatch.setattr(study, '_validated_plan', lambda *args: None)

    def read(instance, brief, **kwargs):
        rows = [dict(role='voyage', status='refused' if unresolved else 'accepted') for _ in brief.voyage]
        return reader._seal({'sentences': rows})

    monkeypatch.setattr(reader, 'read_brief', read)
    executions = []

    def execute(instance, receipt, **kwargs):
        from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
        assert verify_confirmation(receipt)['source'] == 'benchmark_batch_confirm_all'
        executions.append(True)
        kwargs['on_incumbent']({'checked': True, 'passed': True})
        kwargs['on_incumbent']({'plan': {'forged': True}, 'passed': True})
        return SimpleNamespace(plan={'forged': True}), {'status': 'checked_plan', 'passed': True, 'lifts': 1.}

    result = study.benchmark('unused', parser=object(), out_dir=tmp_path / 'study',
                             voyage_sentences=['Synthetic instruction.'], budgets=(60.,),
                             executor=execute, admit=lambda: {'load1': 1.})
    assert len(executions) == (1 if unresolved else 2)
    assert all(row['first_legal_s'] is None and not row['passed'] for row in result['rows'])
    assert all(row['lifts'] is None for row in result['rows'])
    assert result['rows'][0]['status'] == 'no_checked_plan'
    assert result['rows'][1]['status'] == ('needs_confirmation' if unresolved else 'no_checked_plan')


@pytest.mark.parametrize('full_pass,rule_fail', [(True, False), (False, False), (True, True)])
def test_benchmark_gate_runs_full_checker_and_exact_instruction_audit(monkeypatch, full_pass, rule_fail):
    import experiments.mvp.pipeline.runner.benchmark as study
    import experiments.mvp.reader.pipeline as pipeline
    import experiments.mvp.reader.schema as schema
    import stowage.checker as checker
    from stowage.model import MovePlan
    plan = MovePlan(id='synthetic', instance_id='synthetic', call_index=0).model_dump(mode='json')
    checks, audits = [], []

    def check(instance, parsed):
        checks.append(parsed.id)
        return SimpleNamespace(passed=full_pass, count=SimpleNamespace(weighted_nonproductive=7.),
                               model_dump=lambda **kwargs: {'passed': full_pass})

    def intrinsic(instance, answer, parsed):
        audits.append((answer.bay, parsed.id))
        return ['violates exact reading'] if rule_fail else []

    monkeypatch.setattr(checker, 'check', check)
    monkeypatch.setattr(schema, 'context_from_instance', lambda instance: {'bays': [1, 3]})
    monkeypatch.setattr(pipeline, 'intrinsic_violations', intrinsic)
    reading = dict(status='accepted', sentence='Keep bay 3 clear.',
                   parsed={'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'close_bay', 'bay': 3}})
    verdict = study._validated_plan(object(), plan, [reading])
    assert checks == ['synthetic']
    assert audits == ([(3, 'synthetic')] if full_pass else [])
    assert (verdict is not None) == (full_pass and not rule_fail)
    assert study._validated_plan(object(), {'invalid': True}, [reading]) is None
