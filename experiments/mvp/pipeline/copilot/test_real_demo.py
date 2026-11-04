"""Real recorder seams without credentials, native search or provider calls."""
from decimal import Decimal
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from experiments.mvp.pipeline.copilot.real_demo import _Session, SharedSparkReader, _score, load_source
from experiments.mvp.reader.pipeline import ParserOutputError


@pytest.fixture(autouse=True)
def offline_provider_seam(monkeypatch):
    monkeypatch.setattr('experiments.mvp.pipeline.copilot.real_demo.assert_paid_allowed', lambda purpose: None)


def test_named_development_source_hashes_and_envelope():
    instance, plan, source = load_source()
    assert source['envelope']['in_envelope']
    assert instance.id == plan.instance_id == 'random.small.40100016.0.5'
    assert 'sealed' not in source['input']


def test_reader_uses_shared_managed_client_and_existing_strict_validator(monkeypatch):
    from llm.spark import planner_budget
    @contextmanager
    def allocation(ledger, **kwargs):
        yield ledger
    monkeypatch.setattr(planner_budget, 'allocation_budget', allocation)
    raw = '{"schema_version":"stowlm.reader/2","constraint":{"type":"close_bay","bay":2}}'
    client = NS(ledger=object(), complete=Mock(return_value=NS(text=raw, request_id='offline', cost_usd=Decimal('0'),
                    usage=NS(input_tokens=1, output_tokens=1, cached_tokens=0))))
    session = _Session(client)
    answer, evidence = SharedSparkReader(session).parse('Bay 2 is closed.', {'bays': [1, 2, 3]})
    assert answer.bay == 2 and evidence['adapter_sha256'] is None
    assert session.client is client
    assert client.complete.call_args.kwargs['purpose'] == 'pipeline-t1.6-copilot/real-reader'
    assert len(session.calls) == 1
    assert 'not the adopted fine-tuned reader' in evidence['label']
    client.complete.return_value.text = raw[:-1] + ',"injected":true}'
    with pytest.raises(ParserOutputError):
        SharedSparkReader(session).parse('untrusted sentence', {'bays': [1, 2, 3]})


def test_plain_score_cannot_replace_failed_replan_with_baseline_check():
    digest = 'a' * 64
    checked = dict(status='checked_plan', passed=True, checker={'passed': True, 'screen': 'full'}, plan_sha256=digest)
    calls = [dict(name='read_instruction', result={}), dict(name='replan', result={'passed': False}),
             dict(name='check_plan', result=checked)]
    rows = [dict(turn=dict(tool_calls=calls, plan_sha256=digest))]
    score = _score('plain', rows, {'planner_confirmed': True})
    assert not score['passed'] and not score['actual_replan_passed']
    calls[1]['result'] = checked
    assert _score('plain', rows, {'planner_confirmed': True})['passed']


def test_reader_proof_recording_excludes_native_tools():
    from experiments.mvp.pipeline.copilot.real_demo import ReaderProofTools
    instance, plan, _ = load_source()
    checker = Mock(side_effect=AssertionError('Native checker must stay outside recording scope'))
    replanner = Mock(side_effect=AssertionError('Native replan must stay outside recording scope'))
    tools = ReaderProofTools(instance, plan=plan, checker=checker, replanner=replanner)
    for name, args in [('check_plan', {}), ('replan', {'reading_id': 'unconfirmed'}), ('try_ladder', {})]:
        assert tools.call(name, args)['status'] == 'rejected'
    checker.assert_not_called()
    replanner.assert_not_called()
    rows = [{'turn': {'tool_calls': [{'name': 'read_instruction', 'result': {}}],
                      'proposed_reading': {'planner_confirmed': False}, 'plan_sha256': None}}]
    score = _score('plain', rows, None, read_only=True)
    assert score['passed']
    assert score['recording_scope'] == 'reader_and_proof_tools'
    assert not score['actual_replan_passed']


def test_allocation_guard_is_per_provider_call_and_not_held_during_replanner(monkeypatch):
    from llm.spark import planner_budget
    from experiments.mvp.pipeline.copilot.test_tools import tools, propose
    state = dict(active=False, guards=[], replan_called=False)
    ledger = object()
    @contextmanager
    def allocation(actual_ledger, **kwargs):
        assert actual_ledger is ledger and not state['active']
        state['active'] = True
        state['guards'].append(kwargs)
        try:
            yield actual_ledger
        finally:
            state['active'] = False
    monkeypatch.setattr(planner_budget, 'allocation_budget', allocation)
    def provider(*args, **kwargs):
        assert state['active']
        return NS(text='offline', request_id='fixture', cost_usd=Decimal('0'),
                  usage=NS(input_tokens=1, output_tokens=1, cached_tokens=0))
    client = NS(ledger=ledger, complete=provider)
    session = _Session(client, prior_job_usd=Decimal('0.28699165'))
    session.complete('first', purpose='fixture-agent', max_output_tokens=16, reasoning_effort='low')
    assert not state['active']
    def replanner(*args, **kwargs):
        assert not state['active']
        state['replan_called'] = True
        return None, {'status': 'no_checked_plan'}
    toolset = tools(replanner=replanner)
    reading_id = propose(toolset)
    toolset.confirm_reading(reading_id)
    result = toolset.call('replan', {'reading_id': reading_id})
    assert result['status'] == 'no_checked_plan' and state['replan_called']
    session.complete('second', purpose='fixture-reader', max_output_tokens=16, reasoning_effort='minimal')
    assert not state['active'] and len(state['guards']) == 2
    assert all(guard == {'allocation_usd': Decimal('12'), 'prior_job_usd': Decimal('0.28699165')}
               for guard in state['guards'])


def test_per_call_scopes_retain_canonical_job_spend_and_twelve_dollar_cap(tmp_path):
    from llm.spark.spend import SpendLedger, SpendCapExceeded
    compute = tmp_path / 'compute.md'
    compute.write_text('')
    ledger = SpendLedger(path=tmp_path / 'calls.jsonl', compute_path=compute, cap_usd=Decimal('100'))
    def provider(*args, **kwargs):
        # Offline managed-ledger double. Each synthetic response costs one
        # dollar and must pass the real scoped ledger guard before appending.
        ledger.append({'purpose': kwargs['purpose'], 'cost_usd': '1'})
        return NS(text='offline', request_id='fixture', cost_usd=Decimal('1'),
                  usage=NS(input_tokens=1, output_tokens=1, cached_tokens=0))
    session = _Session(NS(ledger=ledger, complete=provider), prior_job_usd=Decimal('10.5'))
    session.complete('first', purpose='fixture-agent', max_output_tokens=16, reasoning_effort='low')
    job = ledger._spark_job
    assert '_spark_active_allocation_usd' not in vars(ledger)
    with pytest.raises(SpendCapExceeded):
        session.complete('second', purpose='fixture-reader', max_output_tokens=16, reasoning_effort='minimal')
    assert ledger._spark_job is job
    assert ledger.total_usd() == Decimal('1')
    assert '_spark_active_allocation_usd' not in vars(ledger)
    assert len(session.calls) == 1
