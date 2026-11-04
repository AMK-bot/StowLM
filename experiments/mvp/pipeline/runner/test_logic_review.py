"""Release regressions from logic1 findings 6, 11, 12, 13, 18 and 19."""
import json

import pytest

from experiments.mvp.pipeline.runner import core
from experiments.mvp.pipeline.runner.evidence import json_evidence
from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.runner.test_runner import (
    packet, gates, fallback_package, Parser, evidence, plan_for, result_for,
    bind, stored_for, sentence, verdict,
)
from llm.instructions.schema import CloseBay

ISOLATED_ENGINE_CALL = core.engine_call


@pytest.mark.parametrize('kind,reason,expected', [
    ('proved_full_load', 'no instruction-allowed physical footprint: bay 1 is closed',
     "With every box aboard this limit can't be met"),
    ('solver_timeout', 'Allocation timed out; retry with more time.',
     'Allocation timed out; retry with more time.'),
    ('engine_identity', 'Restore the pinned engine and retry.',
     'Restore the pinned engine and retry.'),
])
def test_refusal_message_uses_real_binding_policy(kind, reason, expected):
    message = core._message('constraint_unavailable', dict(
        status='constraint_unavailable', refusal_kind=kind, reason=reason))
    assert expected in message
    if kind == 'proved_full_load':
        assert "A partial plan wasn't tried." in message
    else:
        assert 'reduce cargo' not in message and "can't be met" not in message


@pytest.mark.parametrize('profile_name', ['live', 'standard'])
@pytest.mark.parametrize('has_incumbents', [False, True])
def test_real_engine_deadline_ignores_internal_budgets_and_rechecks_best_incumbent(
        packet, gates, monkeypatch, tmp_path, record_property, request, profile_name, has_incumbents):
    """Real owned worker, streaming pipe and deadline; no solver, GPU or network."""
    from copy import deepcopy
    from dataclasses import replace
    import multiprocessing
    import os
    import base64
    from time import monotonic, sleep
    from experiments.mvp.pipeline.runner import admission
    from experiments.mvp.pipeline.contract.schema import StageRecord, canonical_json, chain_intact
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if 'fork' not in multiprocessing.get_all_start_methods():
        pytest.skip('The runner subprocess boundary requires POSIX fork')
    timeout_s, internal_budget_s = .5, 10000.
    original_profile, original_hash = core.get_profile, core.sha256_json
    selected_profiles, hashed_timeout_evidence, engine_elapsed, owned = [], [], [], []
    worker_pid = tmp_path/'owned-engine.pid'

    def huge_budgets(name):
        from engine.config import Config
        selected = original_profile(name)
        selected = dict(selected, budget_s=internal_budget_s, finalize_s=internal_budget_s,
            config=replace(selected['config'] or Config(), assignment_budget_s=internal_budget_s,
                assignment_finalize_s=internal_budget_s, lift_polish_s=internal_budget_s,
                peel_proposal_s=internal_budget_s, peel_finalize_s=internal_budget_s),
            engine_timeout_s=timeout_s if has_incumbents else internal_budget_s)
        selected_profiles.append(selected)
        return selected

    def capture_hash(value):
        if isinstance(value, dict) and value.get('timed_out') is True and value.get('engine_version'):
            hashed_timeout_evidence.append(deepcopy(value))
        return original_hash(value)

    def measured_engine_call(*args, **kwargs):
        from experiments.mvp.pipeline.runner.limits import deadline
        began = monotonic()
        try:
            # A broken cap must fail this test instead of leaving its worker hung.
            with deadline(5., stage='test_watchdog'):
                return ISOLATED_ENGINE_CALL(*args, **kwargs)
        finally:
            engine_elapsed.append(monotonic()-began)

    # Capture only the exact Process constructed by this test's real limit call.
    context = multiprocessing.get_context('fork')
    original_process = context.Process
    def process_factory(*args, **kwargs):
        process = original_process(*args, **kwargs)
        original_start = process.start
        def start():
            original_start()
            owned.append((process, process.pid))
        process.start = start
        return process

    def cleanup_owned():
        for process, _ in owned:
            if process._closed:
                continue
            if process.is_alive():
                process.terminate()
            process.join(timeout=.2)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.)
            process.close()
    request.addfinalizer(cleanup_owned)

    monkeypatch.setattr(context, 'Process', process_factory)
    monkeypatch.setattr(core, 'get_profile', huge_budgets)
    monkeypatch.setattr(core, 'sha256_json', capture_hash)
    monkeypatch.setattr(core, 'engine_call', measured_engine_call)
    monkeypatch.setattr(admission, 'wait_for_capacity', lambda: dict(mask=255, enabled_cores=8,
        load1=0., reading_age_s=0., waited_s=0., synthetic_test=True))
    gates.reports.update({'bad-checker': verdict(False, 0.), 'bad-rule': verdict(True, 0.),
                          'first': verdict(True, 9.), 'best': verdict(True, 7.)})
    audits = []
    def recheck(instance, answer, plan):
        audits.append(plan.id)
        return ['synthetic instruction violation'] if plan.id == 'bad-rule' else []
    monkeypatch.setattr(core, 'intrinsic_violations', recheck)

    def hanging_engine(instance, receipt, **kwargs):
        from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
        assert verify_confirmation(receipt)['source'] == 'callback'
        assert kwargs['budget_s'] == kwargs['finalize_s'] == internal_budget_s
        assert kwargs['config'].assignment_budget_s == internal_budget_s
        worker_pid.write_text(str(os.getpid()))
        if has_incumbents:
            for ident in ('bad-checker', 'bad-rule', 'first', 'best'):
                kwargs['on_incumbent'](dict(plan=plan_for(instance, ident).model_dump(mode='json'),
                    lane='synthetic-hang', passed=True, checked=True, lifts=-1.))
        while True:
            sleep(60.)

    bind(monkeypatch, hanging_engine)
    packet['profile'] = profile_name
    options = {} if has_incumbents else {'engine_timeout_s': timeout_s}
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path/'run', confirm=lambda _: True, **options)

    assert len(selected_profiles) == len(engine_elapsed) == len(owned) == 1
    assert selected_profiles[0]['name'] == profile_name
    assert .35 <= engine_elapsed[0] < 4., engine_elapsed
    assert engine_elapsed[0] < internal_budget_s / 100.
    process, pid = owned[0]
    record_property('profile', profile_name)
    record_property('has_incumbents', has_incumbents)
    record_property('engine_wall_s', engine_elapsed[0])
    record_property('engine_deadline_s', timeout_s)
    record_property('internal_budget_s', internal_budget_s)
    record_property('owned_worker_pid', pid)
    assert int(worker_pid.read_text()) == pid
    assert process._closed is True
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)  # The exact owned child was joined and reaped.
    expected_status = 'checked_plan' if has_incumbents else 'no_checked_plan'
    assert outcome['status'] == expected_status, outcome['message']
    assert outcome['passed'] is has_incumbents
    assert outcome['evidence']['timed_out'] is True
    assert outcome['evidence']['runner_deadline'] == dict(timeout_s=timeout_s,
        scope='owned engine call after admission', budget_s_is_wall_limit=False, expired=True)
    guidance = outcome['anytime']
    assert guidance['continue_after_first_pass'] is True and guidance['arrival_time_guaranteed'] is False
    history = guidance['historical_first_legal']
    assert history['range_s'] == [103, 202] and 'historical' in history['label']
    assert 'not packet-start latency' in history['scope']
    assert 'not a deadline, guarantee, new runner measurement' in history['limitation']
    engine_start = next(row for row in outcome['events'] if row['stage'] == 'engine' and row['kind'] == 'stage_start')
    assert engine_start['data']['anytime'] == guidance
    assert outcome['events'][-1]['kind'] == 'done'
    assert outcome['events'][-1]['data']['status'] == expected_status
    assert outcome['message'] and 'Traceback' not in outcome['message']
    assert 'First legal plan:' in outcome['message']
    receipt = outcome['receipt']
    assert chain_intact([StageRecord.model_validate(row) for row in receipt['steps']])
    unsigned = {key: value for key, value in receipt.items() if key != 'signature'}
    Ed25519PublicKey.from_public_bytes(base64.b64decode(receipt['public_key'], validate=True)).verify(
        base64.b64decode(receipt['signature'], validate=True), canonical_json(unsigned).encode())
    engine_step = next(row for row in outcome['receipt']['steps'] if row['stage'] == 'engine')
    assert hashed_timeout_evidence
    assert engine_step['output_sha256'] in [original_hash(row) for row in hashed_timeout_evidence]
    incumbents = [row for row in outcome['events'] if row['kind'] == 'incumbent']
    if has_incumbents:
        assert [row['data']['lifts'] for row in incumbents] == [9., 7.]
        assert incumbents[0]['data']['message'] == 'First legal plan available. Continuing to improve.'
        assert outcome['plan']['id'] == 'best' and outcome['lifts'] == 7.
        assert sum(plan.id == 'best' for _, plan in gates.checks) >= 2
        assert audits.count('best') >= 2
        assert outcome['evidence']['instruction_recheck_passed'] is True
        first = outcome['first_checked_wall_s']
        assert first == outcome['evidence']['first_checked_wall_s']
        assert first == incumbents[0]['data']['first_checked_wall_s'] <= incumbents[0]['t_s']
        assert outcome['receipt']['checker']['passed'] is True
    else:
        assert outcome['plan'] is None and incumbents == []
        assert outcome['first_checked_wall_s'] is None
        assert outcome['receipt']['checker']['passed'] is False
        assert any(word in outcome['message'].lower() for word in ('budget', 'deadline', 'time'))


def test_buffered_result_is_rejected_when_parent_callback_crosses_engine_deadline(tmp_path, monkeypatch):
    """A result already in the pipe cannot bypass time spent checking an event."""
    import selectors
    from time import monotonic, sleep
    from experiments.mvp.pipeline.runner import limits
    ready = tmp_path/'synthetic-result-ready'
    original_selector = selectors.DefaultSelector

    class BufferedSelector:
        def __init__(self):
            self.delegate, self.first = original_selector(), True

        def register(self, *args, **kwargs):
            return self.delegate.register(*args, **kwargs)

        def select(self, timeout):
            if self.first:
                self.first = False
                until = monotonic()+.1
                while not ready.exists() and monotonic() < until:
                    sleep(.001)
                sleep(.02)  # Both frames reach the pipe before its first read.
            return self.delegate.select(timeout)

        def close(self):
            self.delegate.close()

    def completed_engine(*, report):
        report({'synthetic': True})
        ready.write_text('The synthetic engine has already returned.')
        return {'unchecked_result': 'must not be accepted after the deadline'}

    seen = []
    def slow_check(event):
        seen.append(event)
        sleep(.35)

    monkeypatch.setattr(selectors, 'DefaultSelector', BufferedSelector)
    began = monotonic()
    with pytest.raises(limits.StageTimeout) as caught:
        with limits.deadline(3., stage='test_watchdog'):
            limits.call_with_limit(completed_engine, timeout_s=.25, stage='engine',
                event_kw='report', on_event=slow_check)
    assert caught.value.stage == 'engine' and caught.value.timeout_s == .25
    assert ready.is_file() and seen == [{'synthetic': True}]
    assert .35 <= monotonic()-began < 2.5


@pytest.mark.parametrize('has_incumbent', [False, True])
def test_timeout_retains_checked_incumbent_and_records_pinned_engine(packet, gates, monkeypatch, tmp_path, has_incumbent):
    def execute(instance, receipt, **kwargs):
        if has_incumbent:
            kwargs['on_incumbent'](dict(plan=plan_for(instance).model_dump(mode='json')))
        raise TimeoutError('synthetic deadline')
    bind(monkeypatch, execute)
    result = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert result['status'] == ('checked_plan' if has_incumbent else 'no_checked_plan')
    identity = core._engine_identity()
    assert result['receipt']['engine_version'] == identity['engine_version']
    assert result['receipt']['engine_manifest_sha256'] == identity['engine_manifest_sha256']
    steps = [row for row in result['receipt']['steps'] if row['stage'] == 'engine']
    assert len(steps) == 1
    assert json.loads(steps[0]['notes'])['engine_version'] == identity['engine_version']
    if has_incumbent:
        assert result['evidence']['timed_out'] is True
        assert result['checker']['passed'] and result['plan']


def test_checker_exception_clears_passing_evidence(packet, gates, monkeypatch, tmp_path):
    bind(monkeypatch, lambda instance, receipt, **kwargs: (result_for(plan_for(instance)), evidence()))
    def broken(*args):
        raise RuntimeError('synthetic checker unavailable')
    monkeypatch.setattr(core, 'check', broken)
    result = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert result['status'] == 'error' and not result['passed']
    assert result['evidence']['passed'] is False and result['evidence']['level'] is None
    assert result['evidence']['checker']['passed'] is False
    assert result['receipt']['checker']['level'] is None
    assert result['receipt']['checker']['passed'] is False


def test_hard_standing_needs_question_even_with_batch_acceptance(packet, monkeypatch, tmp_path):
    packet['standing'] = 'Keep bay 1 clear.'
    bind(monkeypatch, lambda *args, **kwargs: pytest.fail('Hard standing was silently dropped'))
    result = core.run_packet(packet, parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path, confirm=lambda _: True, confirmation_source='batch_confirm_all')
    assert result['status'] == 'needs_confirmation'
    assert result['questions'][0]['field'] == 'standing'
    assert 'voyage_sentences' in result['questions'][0]['prompt']


def test_confirmation_source_is_inside_signed_step(packet, gates, monkeypatch, tmp_path):
    bind(monkeypatch, lambda instance, receipt, **kwargs: (result_for(plan_for(instance)), evidence()))
    result = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path, confirm=lambda _: True, confirmation_source='batch_confirm_all')
    assert result['status'] == 'checked_plan'
    step = next(row for row in result['receipt']['steps'] if row['stage'] == 'confirmation')
    assert json.loads(step['notes'])['confirmation_source'] == 'batch_confirm_all'
    assert result['receipt']['signature']


@pytest.mark.parametrize('source', ['callback', 'batch_confirm_all', 'sealed_expected_match'])
def test_engine_receives_authenticated_host_confirmation_after_exact_callback_gate(
        packet, gates, monkeypatch, tmp_path, source):
    from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
    actions = []
    original = core.binding.confirm_brief

    def confirm(row):
        actions.append(('callback', row['sentence']))
        assert row['role'] == 'voyage' and row['status'] == 'accepted'
        return True

    def seal(receipt, passed_source):
        assert actions == [('callback', 'Keep bay 1 clear.'), ('callback', 'Do not load in bay 1.')]
        assert passed_source == source
        actions.append(('sealed', passed_source))
        return original(receipt, passed_source)

    def execute(instance, receipt, **kwargs):
        authenticated = verify_confirmation(receipt)
        assert authenticated['source'] == source
        assert actions[-1] == ('sealed', source)
        rows = receipt.evidence()['sentences']
        assert [row['sentence'] for row in rows if row['role'] == 'voyage'] == [
            'Keep bay 1 clear.', 'Do not load in bay 1.']
        actions.append(('executed', source))
        kwargs['on_incumbent'](dict(plan=plan_for(instance).model_dump(mode='json'),
            confirmation_source='forged binding event source', confirmation={'source': 'forged'},
            confirmation_sha256='f'*64))
        return result_for(plan_for(instance)), evidence(confirmation_source='forged binding result source',
            confirmation={'source': 'forged'}, confirmation_sha256='f'*64)

    monkeypatch.setattr(core.binding, 'confirm_brief', seal)
    bind(monkeypatch, execute)
    sentence(packet)
    sentence(packet, 'Do not load in bay 1.')
    result = core.run_packet(packet, parser=Parser(*[CloseBay(type='close_bay', bay=1)]*2),
        out_dir=tmp_path, confirm=confirm, confirmation_source=source)
    assert result['status'] == 'checked_plan', result['message']
    assert actions[-1] == ('executed', source)
    assert result['confirmation_source'] == result['evidence']['confirmation_source'] == source
    assert result['evidence']['confirmation'] == result['confirmation']
    assert result['evidence']['confirmation_sha256'] == result['confirmation']['confirmation_sha256']
    incumbents = [row for row in result['events'] if row['kind'] == 'incumbent']
    assert incumbents and all(row['data']['confirmation_source'] == source for row in incumbents)
    assert all(row['data']['confirmation'] == result['confirmation'] for row in incumbents)
    step = next(row for row in result['receipt']['steps'] if row['stage'] == 'confirmation')
    assert json.loads(step['notes'])['confirmation_source'] == source


@pytest.mark.parametrize('decision', [None, False, 'true'])
def test_unapproved_reading_never_gets_confirmation_capability(
        packet, monkeypatch, tmp_path, decision):
    def forbidden(*args, **kwargs):
        pytest.fail('An unapproved reading was sealed or executed')
    monkeypatch.setattr(core.binding, 'confirm_brief', forbidden)
    bind(monkeypatch, forbidden)
    callback = None if decision is None else lambda _: decision
    result = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path, confirm=callback, confirmation_source='sealed_expected_match')
    assert result['status'] == 'needs_confirmation'
    assert result['plan'] is None and result['passed'] is False


def test_unresolved_reading_is_not_sealed_even_with_confirm_all(packet, monkeypatch, tmp_path):
    from llm.instructions.schema import Refusal
    def forbidden(*args, **kwargs):
        pytest.fail('An unresolved reading was sealed or executed')
    monkeypatch.setattr(core.binding, 'confirm_brief', forbidden)
    bind(monkeypatch, forbidden)
    result = core.run_packet(sentence(packet), parser=Parser(Refusal(code='ambiguous', reason='Which bay?')),
        out_dir=tmp_path, confirm=lambda _: True, confirmation_source='batch_confirm_all')
    assert result['status'] == 'needs_confirmation'
    assert result['plan'] is None and not result['passed']


@pytest.mark.parametrize('field,value', [('engine_version', '0.9.0-forged'), ('engine_manifest_sha256', 'f'*64)])
def test_authenticated_stored_provenance_must_match_current_manifest(packet, gates, tmp_path, field, value):
    stored = stored_for(packet)
    stored[field] = value
    result = core.run_packet(packet, parser=Parser(), out_dir=tmp_path, stored=stored,
                             stored_sha256=sha256_json(stored))
    assert result['status'] == 'error' and result['plan'] is None


def test_stored_candidate_requires_independent_authentication(packet, gates, tmp_path):
    stored = stored_for(packet)
    result = core.run_packet(packet, parser=Parser(), out_dir=tmp_path, stored=stored)
    assert result['status'] == 'error' and result['plan'] is None


def test_active_stubs_block_stored_release(packet, gates, monkeypatch, tmp_path):
    stored = stored_for(packet)
    monkeypatch.setattr(core, 'active_stubs', lambda: {'checker': 'synthetic stub'})
    result = core.run_packet(packet, parser=Parser(), out_dir=tmp_path, stored=stored,
                             stored_sha256=sha256_json(stored))
    assert result['status'] == 'constraint_unavailable'
    assert not result['passed'] and result['plan'] is None
    assert 'stubs' in result['message']


def test_authenticated_stored_relaxed_plan_is_classified_fresh(packet, gates, tmp_path):
    stored = stored_for(packet)
    stored['status'] = 'relaxed_plan'
    plan = stored['plan']
    gates.reports[plan['id']] = verdict(False)
    gates.inspected[plan['id']] = dict(level=3, completed=True, boxes_left_ashore=[],
        boxes_aboard_percent=100., broken_rules=['expert.synthetic'], lifts=7., t0_peeled=False)
    result = core.run_packet(packet, parser=Parser(), out_dir=tmp_path, stored=stored,
                             stored_sha256=sha256_json(stored))
    assert result['status'] == 'relaxed_plan' and result['label'] == 'stored'
    assert result['passed'] is False and result['receipt']['checker']['level'] == 3


def test_inf_rank_remains_unknown_but_hashes_as_json():
    with pytest.raises(ValueError):
        sha256_json({'rank': (0, float('inf'))})
    safe = json_evidence({'rank': (0, float('inf')), 'nested': {'rank': [float('-inf'), float('nan')]}})
    assert safe['rank'] == [0, None]
    assert safe['nonfinite_values'][0] == dict(path='rank.1', kind='positive_infinity')
    assert len(sha256_json(safe)) == 64


def test_stored_file_hash_authentication_detects_edited_date(tmp_path):
    from pathlib import Path
    from experiments.mvp.pipeline.runner.offline import stored_plan
    from experiments.mvp.pipeline.runner.stored import BUNDLED_PLAN_SHA256
    source = Path(core.HERE) / 'fixtures/stored.json'
    path = tmp_path / 'stored.json'
    path.write_bytes(source.read_bytes())
    assert stored_plan(path, expected_sha256=BUNDLED_PLAN_SHA256)['engine_version'] == '0.13.0'
    data = json.loads(path.read_text())
    data['computed_utc'] = '1999-01-01T00:00:00Z'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='not authenticated'):
        stored_plan(path, expected_sha256=BUNDLED_PLAN_SHA256)


def test_missing_fleet_file_recovers_before_engine_clock(packet, monkeypatch, tmp_path):
    from experiments.mvp.pipeline.runner import admission, limits
    sequence = []
    state = tmp_path / 'judge-clone-no-telemetry.json'

    def recover(seconds):
        sequence.append('waited')
        state.write_text(json.dumps({'mask': 255, 'load1': 12., 't': 1000}))

    def limited(*args, **kwargs):
        assert sequence == ['waited']
        assert kwargs['admission']['enabled_cores'] == 8
        assert kwargs['admission']['load1'] == 12.
        sequence.append('clock_started')
        raise TimeoutError('synthetic deadline after admission')

    monkeypatch.setattr(core, 'engine_call', ISOLATED_ENGINE_CALL)
    monkeypatch.setattr(admission, 'STATE', state)
    monkeypatch.setattr(admission.time, 'sleep', recover)
    monkeypatch.setattr(admission.time, 'time', lambda: 1001.)
    monkeypatch.setattr(limits, 'call_with_limit', limited)
    result = core.run_packet(packet, parser=Parser(), out_dir=tmp_path / 'run')
    assert sequence == ['waited', 'clock_started']
    assert result['status'] == 'no_checked_plan'
    assert 'timed out' in result['message'].lower()
    assert result['evidence']['timed_out'] is True
    assert result['evidence']['runner_deadline']['expired'] is False
    assert result['receipt']['engine_version'] == core._engine_identity()['engine_version']


def test_admission_wait_is_outside_engine_timeout(monkeypatch):
    import time
    from experiments.mvp.pipeline.runner import admission, limits
    sequence = []
    def admit():
        time.sleep(.03)
        sequence.append('admitted')
        return dict(mask=255, enabled_cores=8, load1=0., reading_age_s=0., waited_s=.03)
    def limited(function, *args, **kwargs):
        assert sequence == ['admitted']
        assert kwargs['timeout_s'] == .01
        sequence.append('clock_started')
        return 'synthetic-result'
    monkeypatch.setattr(admission, 'wait_for_capacity', admit)
    monkeypatch.setattr(limits, 'call_with_limit', limited)
    profile = dict(budget_s=0., finalize_s=0., seed=0, config=None)
    assert ISOLATED_ENGINE_CALL(None, None, profile, None, timeout_s=.01) == 'synthetic-result'
    assert sequence == ['admitted', 'clock_started']


def test_partial_relaxed_names_percentage_and_retains_safe_partial(packet, gates, monkeypatch, tmp_path):
    partial = dict(level=2, completed=False, boxes_aboard_percent=66.6667, boxes_left_ashore=['synthetic'])
    def execute(instance, receipt, **kwargs):
        plan = plan_for(instance, 'partial')
        gates.reports[plan.id] = verdict(False)
        gates.inspected[plan.id] = dict(partial, broken_rules=['expert.synthetic'], lifts=7., t0_peeled=False)
        return result_for(plan), evidence(status='relaxed_plan', passed=False,
            stages={'peel_seam': {'best_safe_partial': partial, 'rank': (1, float('inf'))}})
    bind(monkeypatch, execute)
    result = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert result['status'] == 'relaxed_plan' and not result['passed']
    assert 'partial' in result['message'].lower() and '66.7%' in result['message']
    assert result['best_safe_partial'] == partial
    assert result['receipt']['checker']['best_safe_partial'] == partial
    sha256_json(result)
