from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace as NS
import pytest

from experiments.mvp.pipeline.copilot.live import CAP, LiveClient, run_live


@pytest.fixture(autouse=True)
def offline_provider_seam(monkeypatch):
    monkeypatch.setattr('experiments.mvp.pipeline.copilot.live.assert_paid_allowed', lambda purpose: None)


def test_live_adapter_guards_each_call_and_releases_shared_lock(monkeypatch):
    import llm.spark.planner_budget as budget
    state = {'held': False, 'scopes': 0}
    ledger = object()

    @contextmanager
    def guard(actual, *, allocation_usd, prior_job_usd):
        assert actual is ledger
        assert allocation_usd == CAP
        assert prior_job_usd == Decimal('.2')
        assert not state['held']
        state.update(held=True, scopes=state['scopes'] + 1)
        try:
            yield
        finally:
            state['held'] = False

    class Client:
        def complete(self, prompt, **kwargs):
            assert state['held']
            return NS(text='{"say":"Evidence."}', request_id='test-only',
                      cost_usd=Decimal('.001'), usage=NS(input_tokens=10, output_tokens=10))

    client = Client()
    client.ledger = ledger
    monkeypatch.setattr(budget, 'allocation_budget', guard)
    model = LiveClient(client, 'offline-budget-test', prior_job_usd=Decimal('.2'))
    for _ in range(2):
        model.complete('Synthetic prompt')
        assert not state['held']
    assert state['scopes'] == 2
    assert len(model.calls) == 2


def test_existing_live_recording_never_constructs_paid_client(monkeypatch):
    import llm.spark.client as managed

    def forbidden(*args, **kwargs):
        raise AssertionError('Existing recording must not construct a paid client')

    monkeypatch.setattr(managed, 'SparkClient', forbidden)
    value = run_live()
    assert value['scenario_count'] == 42
    cost = sum((Decimal(call['cost_usd']) for row in value['results']
                for call in row['model_calls']), Decimal(0))
    assert cost == Decimal(value.get('completed_response_cost_usd', value['measured_cost_usd']))


def test_charged_incomplete_response_is_counted_and_never_released():
    import pytest
    from llm.spark.client import SparkIncompleteError
    from llm.spark.spend import Usage

    class Client:
        def complete(self, *args, **kwargs):
            raise SparkIncompleteError('max_output_tokens', Usage(10, 16), Decimal('.001'), 'test-only')

    model = LiveClient(Client(), 'incomplete-test')
    with pytest.raises(SparkIncompleteError):
        model.complete('Synthetic prompt')
    assert model.calls == [{'status': 'incomplete', 'request_id': 'test-only', 'cost_usd': '0.001',
                            'input_tokens': 10, 'output_tokens': 16}]


def test_task_ledger_reconciles_completed_and_interrupted_recordings():
    import json
    from experiments.mvp.pipeline.copilot.replay import FIXTURES
    task = json.loads((FIXTURES.parent / 'spend.json').read_text())
    campaign = json.loads((FIXTURES / 'live-cost-audit.json').read_text())
    interrupted = [json.loads((FIXTURES / name).read_text()) for name in
                   ('interrupted-real-attempt.json', 'interrupted-smoke-attempt.json')]
    real = [json.loads((FIXTURES / ('real-' + name + '.json')).read_text())
            for name in ('plain', 'negotiation', 'attack')]
    costs = [Decimal(campaign['measured_cost_usd'])]
    costs.extend(Decimal(value['measured_cost_usd']) for value in interrupted)
    costs.extend(Decimal(call['cost_usd']) for value in real for call in value['model_calls'])
    for path in list(FIXTURES.glob('tuned-v*.json')) + list(FIXTURES.glob('fresh-v*.json')) + list(FIXTURES.glob('agency-v*-*.json')):
        if '-started' not in path.name:
            value = json.loads(path.read_text())
            if 'measured_cost_usd' in value:
                costs.append(Decimal(value['measured_cost_usd']))
    native = FIXTURES / 'real-recovery/results.json'
    if native.exists():
        costs.append(Decimal(json.loads(native.read_text())['measured_cost_usd']))
    for name in ('hero-final', 'hero-verified', 'hero-postfix', 'hero-shot10', 'hero-shot10-final', 'hero-shot10-delivered'):
        hero = FIXTURES / name / 'calls.json'
        if hero.exists():
            costs.append(Decimal(json.loads(hero.read_text())['measured_cost_usd']))
    assert sum(costs) == Decimal(task['measured_cost_usd'])
    assert Decimal(task['held_usd']) >= 0  # Unknown-billing interrupted calls retain their reservation.
    assert campaign['incomplete_responses'] == 1
    assert Decimal(task['measured_cost_usd']) + Decimal(task['held_usd']) <= Decimal(task['cap_usd'])
