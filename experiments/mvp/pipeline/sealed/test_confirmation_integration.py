"""Real sealed/runner/confirmation boundary, with synthetic no-plan execution.

No map file, model, native planner, admission wait or package signer is used.
Reader and confirmation authentication are the actual implementations.
"""
import json
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline import binding
from experiments.mvp.pipeline.binding import adapter
from experiments.mvp.pipeline.binding.confirmation import ConfirmedBrief, verify_confirmation
from experiments.mvp.pipeline.runner import core, limits
from experiments.mvp.pipeline.sealed import worker
from experiments.mvp.pipeline.sealed.instructions import select_rule, sentence_for
from experiments.mvp.pipeline.sealed.test_amend_worker import recorded_job
from experiments.mvp.pipeline.sealed.test_instructions import facts
from experiments.mvp.reader import public_brief
from experiments.mvp.reader.schema import answer_envelope, validate_answer


@pytest.fixture
def confirmation_boundary(monkeypatch):
    raw = facts()
    raw['load_list'] = ['BOX1']
    instance = SimpleNamespace(id='synthetic', load_list=raw['load_list'],
                               model_dump=lambda **kw: raw)
    calls = []
    # Resolve the runner's host boundary to the public package. A previous
    # proxy monkeypatch may have left a restored attribute cached on it.
    monkeypatch.setattr(core, 'binding', binding)
    real_confirm = binding.confirm_brief
    def confirm(receipt, source):
        confirmed = real_confirm(receipt, source)
        assert isinstance(confirmed, ConfirmedBrief)
        assert verify_confirmation(confirmed)['source'] == 'sealed_expected_match'
        calls.append(('confirm', confirmed))
        return confirmed
    def execute(instance, answers, **kwargs):
        calls.append(('execute', list(answers)))
        return None, dict(status='no_checked_plan', passed=False,
            reason='Synthetic integration executor performs no search.',
            rules=[dict(canonical_envelope=answer_envelope(answer)) for answer in answers])
    def engine_call(instance, receipt, profile, on_incumbent, *, timeout_s):
        # Preserve the real binding confirmation gate and reconstruction while
        # replacing only native planning and the process/admission transport.
        return binding.execute_brief(instance, receipt, budget_s=0., finalize_s=0.)
    def direct(function, *args, timeout_s, stage='reader', **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(binding, 'confirm_brief', confirm)
    monkeypatch.setattr(adapter, 'plan_with_rules', execute)
    monkeypatch.setattr(core, 'assemble', lambda packet: instance)
    monkeypatch.setattr(core, 'assembly_metadata', lambda *a: dict(synthetic_fixture=True))
    monkeypatch.setattr(core.envelope, 'check', lambda *a: dict(in_envelope=True, reasons=[]))
    monkeypatch.setattr(core, '_engine_identity', lambda: dict(engine_version='synthetic-fixture',
        engine_manifest_sha256='d'*64, engine_manifest=dict(passed=True, synthetic_fixture=True)))
    monkeypatch.setattr(core, 'configure_caches', lambda: None)
    monkeypatch.setattr(core, 'engine_call', engine_call)
    monkeypatch.setattr(core, '_sign', lambda receipt: receipt)
    monkeypatch.setattr(limits, 'run_in_process_with_limit', direct)
    return instance, calls


def test_sealed_pipeline_confirms_exact_reading_before_actual_binding_execution(confirmation_boundary, tmp_path):
    instance, calls = confirmation_boundary
    expected = select_rule(facts(), 0)
    job, _ = recorded_job(expected)
    job['path'] = 'synthetic-not-opened.json'
    outcome = worker._pipeline(instance, job, tmp_path, worker._parser(job, expected), expected)
    assert outcome['status'] == 'no_checked_plan'
    assert [name for name, _ in calls] == ['confirm', 'execute']
    receipt = calls[0][1]
    assert isinstance(receipt, ConfirmedBrief)
    assert verify_confirmation(receipt) == outcome['confirmation']
    assert outcome['confirmation_source'] == 'sealed_expected_match'
    assert outcome['evidence']['confirmation_source'] == 'sealed_expected_match'
    assert outcome['evidence']['confirmation']['receipt_sha256'] == verify_confirmation(receipt)['receipt_sha256']
    assert answer_envelope(calls[1][1][0]) == expected
    assert outcome['rules'][0]['confirmed'] is True
    assert outcome['packet']['standing'] == ''


def test_unconfirmed_binding_execute_raises_before_executor(confirmation_boundary):
    instance, calls = confirmation_boundary
    expected = select_rule(facts(), 0)
    job, _ = recorded_job(expected)
    receipt = public_brief.read_brief(instance,
        public_brief.VoyageBrief(standing='', voyage=(sentence_for(expected),)),
        parser=worker._parser(job, expected))
    with pytest.raises(ValueError, match='Unconfirmed brief.*confirm_brief'):
        binding.execute_brief(instance, receipt)
    assert calls == []


def test_wrong_accepted_reading_never_confirms_or_executes(confirmation_boundary, tmp_path):
    instance, calls = confirmation_boundary
    expected = select_rule(facts(), 0)
    wrong = dict(schema_version='stowlm.reader/2', constraint=dict(type='close_bay', bay=3))
    job, _ = recorded_job(expected)
    job['path'] = 'synthetic-not-opened.json'
    empty_parser = worker.RecordedParser(job, expected)
    class WrongFixtureParser:
        def parse(self, sentence, context):
            if not sentence.strip():
                return empty_parser.parse(sentence, context)
            return validate_answer(json.dumps(wrong), context), dict(
                parser='SyntheticWrongReading', adapter_sha256=None)
    outcome = worker._pipeline(instance, job, tmp_path, WrongFixtureParser(), expected)
    assert outcome['status'] == 'needs_confirmation'
    assert outcome['reading']['sentences'][-1]['parsed'] == wrong
    assert outcome['rules'][0]['confirmed'] is False
    assert calls == []


def test_cached_lazy_confirmation_alias_cannot_hide_real_host_gate(monkeypatch,request,tmp_path):
    # Runner tests patch the lazy proxy directly. MonkeyPatch undo stores its
    # previous dynamic attribute on the proxy, which then hides a package spy.
    lazy=core._LazyModule('experiments.mvp.pipeline.binding')
    prior=pytest.MonkeyPatch()
    prior.setattr(lazy,'confirm_brief',lambda *args:pytest.fail('prior test stub leaked'))
    prior.undo()
    assert 'confirm_brief' in vars(lazy)
    monkeypatch.setattr(core,'binding',lazy)
    instance,calls=request.getfixturevalue('confirmation_boundary')
    expected=select_rule(facts(),0)
    job,_=recorded_job(expected)
    job['path']='synthetic-not-opened.json'
    outcome=worker._pipeline(instance,job,tmp_path,worker._parser(job,expected),expected)
    assert outcome['status']=='no_checked_plan'
    assert [name for name,_ in calls]==['confirm','execute']
    receipt=calls[0][1]
    assert isinstance(receipt,ConfirmedBrief)
    authenticated=verify_confirmation(receipt)
    assert authenticated==outcome['confirmation']
    assert authenticated['source']=='sealed_expected_match'
