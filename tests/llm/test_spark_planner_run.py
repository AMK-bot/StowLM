"""The later paid command fails closed until the whole test panel is present."""
import importlib.util
from pathlib import Path
import sys

import pytest

from llm.spark.planner_budget import TokenEstimate
from decimal import Decimal


def runner():
    path = Path(__file__).resolve().parents[2] / 'experiments/spark-planner/run.py'
    spec = importlib.util.spec_from_file_location('spark_baseline_runner', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_panel_refuses_before_estimation_or_http(tmp_path, monkeypatch):
    module = runner()
    monkeypatch.setattr(module, 'load_manifest', lambda _: {'entries': [
        {'split': 'test', 'spec': {'id': 1}}]})
    monkeypatch.setattr(module, 'spec_key', lambda s: str(s['id']))
    monkeypatch.setattr(sys, 'argv', ['run.py', '--instances', str(tmp_path), '--run'])
    monkeypatch.setattr(module, 'estimate_instance', lambda *a, **k: pytest.fail('estimated incomplete panel'))
    with pytest.raises(ValueError, match='incomplete test panel'):
        module.main()


@pytest.mark.parametrize('stubbed_run', [False, True])
def test_offline_preflight_never_constructs_client(tmp_path, monkeypatch, empty_ship_instance, capsys, stubbed_run):
    module = runner()
    (tmp_path / 'instance.json').touch()
    monkeypatch.setattr(module, 'load_manifest', lambda _: {
        'entries_sha256': 'synthetic', 'entries': [{'split': 'test', 'spec': {'id': 1}}]})
    monkeypatch.setattr(module, 'spec_key', lambda s: str(s['id']))
    monkeypatch.setattr(module, 'spec_from_instance', lambda _: {'id': 1})
    monkeypatch.setattr(module, 'load_instance', lambda _: empty_ship_instance)
    monkeypatch.setattr(module, 'require_clean', lambda *a, **k: None)
    monkeypatch.setattr(module, 'estimate_instance', lambda *a, **k: TokenEstimate(10, 16, Decimal('0.1')))
    monkeypatch.setattr(module, 'guard_run', lambda *a, **k: Decimal('60'))
    monkeypatch.setattr(module, 'SpendLedger', lambda **k: object())
    monkeypatch.setattr('llm.spark.client.SparkClient', lambda *a, **k: pytest.fail('offline client construction'))
    monkeypatch.setattr(sys, 'argv', ['run.py', '--instances', str(tmp_path)] + (['--run'] if stubbed_run else []))
    if stubbed_run:
        from types import SimpleNamespace
        from stowage.frozen import FROZEN
        monkeypatch.setitem(FROZEN, 'planner.run_planner', SimpleNamespace(implemented=False))
        with pytest.raises(RuntimeError, match='no API request made'):
            module.main()
    else:
        module.main()
    assert '"estimated_usd": "0.1"' in capsys.readouterr().out


@pytest.fixture
def run_setup(tmp_path, monkeypatch, empty_ship_instance):
    """Synthetic runner orchestration with the real client/ledger and fake HTTP."""
    import io
    import json
    from types import SimpleNamespace
    from llm.spark.client import SparkClient
    from llm.spark.spend import SpendLedger
    from stowage.frozen import FROZEN

    module = runner()
    inputs, output = tmp_path / 'instances', tmp_path / 'output'
    inputs.mkdir()
    instances = {str(seed): empty_ship_instance.model_copy(update={'seed': seed}) for seed in (1, 2)}
    for key in instances:
        (inputs / f'{key}.json').touch()
    monkeypatch.setattr(module, 'load_manifest', lambda _: {'entries_sha256': 'synthetic', 'entries': [
        {'split': 'test', 'spec': {'id': int(key)}} for key in instances]})
    monkeypatch.setattr(module, 'spec_key', lambda s: str(s['id']))
    monkeypatch.setattr(module, 'spec_from_instance', lambda i: {'id': i.seed})
    monkeypatch.setattr(module, 'load_instance', lambda path: instances[path.stem])
    monkeypatch.setattr(module, 'require_clean', lambda *a, **k: None)
    monkeypatch.setitem(FROZEN, 'planner.run_planner', SimpleNamespace(implemented=True))
    compute = tmp_path / 'compute.md'
    compute.write_text('## Spend ledger\n\n| Date | Source | Amount | What for | Remaining |\n|---|---|---|---|---|\n')
    ledger_path = tmp_path / 'calls.jsonl'
    ledger = SpendLedger(ledger_path, compute, Decimal('60'))
    monkeypatch.setattr(module, 'SpendLedger', lambda **k: SpendLedger(ledger_path, compute, k['cap_usd']))
    clients, calls, estimated = [], [], []

    def make_client(**kwargs):
        # Deliberately opt in only because urlopen below is a fake transport.
        client = SparkClient('synthetic', ledger_path=ledger_path, compute_path=compute,
                             max_retries=0, credit_ready=True, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr('llm.spark.client.SparkClient', make_client)

    def send(request, **kwargs):
        calls.append(request)
        response = io.BytesIO(json.dumps({'id': f'fake-{len(calls)}', 'status': 'completed',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'synthetic'}]}],
            'usage': {'input_tokens': 100, 'output_tokens': 16}}).encode())
        response.headers = {}
        return response

    monkeypatch.setattr('urllib.request.urlopen', send)
    real_planner = module.SparkPlanner
    # A callable class keeps the real planner's public version in checkpoints.
    class Planner:
        version = real_planner.version
        def __init__(self, client, **kwargs):
            self.client = client
    monkeypatch.setattr(module, 'SparkPlanner', Planner)

    def run(planner, instance, **kwargs):
        assert kwargs['allow_repair'] is False
        planner.client.complete('synthetic', purpose='runner-test', max_output_tokens=16)
        result = {'status': 'ok', 'metrics': {'raw_plan_legal': 1}}
        report = {'passed': True}
        return (SimpleNamespace(model_dump=lambda **k: result),
                SimpleNamespace(model_dump=lambda **k: report))

    monkeypatch.setattr(module, 'run_planner', run)
    def estimate(instance, **kwargs):
        estimated.append(instance.seed)
        return TokenEstimate(10, 16, Decimal('20'))
    monkeypatch.setattr(module, 'estimate_instance', estimate)
    monkeypatch.setenv('SPARK_CREDIT_READY', '1')
    monkeypatch.setattr(sys, 'argv', ['run.py', '--instances', str(inputs), '--output', str(output), '--run'])
    return SimpleNamespace(module=module, instances=instances, output=output, ledger=ledger,
                           compute=compute, clients=clients, calls=calls, estimated=estimated)


def checkpoint(setup, key):
    import hashlib
    import json
    row = {'spec_key': key, 'manifest_sha256': 'synthetic',
           'settings': {'planner_version': setup.module.SparkPlanner.version,
                        'max_output_tokens': None, 'allow_repair': False},
           'instance_sha256': hashlib.sha256(setup.instances[key].model_dump_json().encode()).hexdigest(),
           'result': {'metrics': {'raw_plan_legal': 1}}, 'report': {'passed': True}}
    setup.output.mkdir(exist_ok=True)
    target = setup.output / f'{key}.json'
    target.write_text(json.dumps(row))
    return target, row


def test_resume_prices_only_pending_and_retains_prior_spend(run_setup, capsys):
    import json
    setup = run_setup
    checkpoint(setup, '1')
    setup.ledger.append({'cost_usd': '25'})
    setup.module.main()
    summary = json.loads((setup.output / 'summary.json').read_text())
    assert setup.estimated == [2]
    assert summary['estimated_usd'] == '20'
    assert summary['remaining_usd'] == '35'
    assert len(setup.calls) == 1
    assert setup.ledger.total_usd() > Decimal('25')


def test_fully_completed_resume_needs_no_budget_client_or_credit(run_setup, monkeypatch):
    import json
    setup = run_setup
    checkpoint(setup, '1')
    checkpoint(setup, '2')
    setup.ledger.append({'cost_usd': '60'})
    monkeypatch.delenv('SPARK_CREDIT_READY')
    setup.module.main()
    summary = json.loads((setup.output / 'summary.json').read_text())
    assert setup.estimated == []
    assert setup.clients == []
    assert setup.calls == []
    assert summary['estimated_usd'] == '0'
    assert summary['checker_pass_rate'] == 1


@pytest.mark.parametrize('field', ['manifest_sha256', 'instance_sha256', 'settings', 'spec_key'])
def test_mismatched_checkpoint_refuses_before_estimation_or_http(run_setup, field):
    import json
    setup = run_setup
    target, row = checkpoint(setup, '2')
    row[field] = 'mismatched'
    target.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='checkpoint'):
        setup.module.main()
    assert setup.estimated == []
    assert setup.clients == []
    assert setup.calls == []


def test_paid_runner_refuses_closed_gate_before_client(run_setup, monkeypatch):
    from llm.spark.client import SparkCreditNotReady
    setup = run_setup
    monkeypatch.delenv('SPARK_CREDIT_READY')
    with pytest.raises(SparkCreditNotReady):
        setup.module.main()
    assert setup.clients == []
    assert setup.calls == []


def test_interrupted_run_reconciles_settlement_and_resumes_without_double_counting(run_setup, monkeypatch):
    import json
    setup = run_setup
    real_run = setup.module.run_planner
    def interrupt(planner, instance, **kwargs):
        if instance.seed == 2:
            raise KeyboardInterrupt
        return real_run(planner, instance, **kwargs)
    monkeypatch.setattr(setup.module, 'run_planner', interrupt)
    with pytest.raises(KeyboardInterrupt):
        setup.module.main()
    assert (setup.output / '1.json').exists()
    assert not (setup.output / '2.json').exists()
    actual = setup.ledger.total_usd()
    assert actual > 0
    assert setup.ledger.summary_row().split('|', 5)[3].strip() == f'${actual}'
    assert f'| ${actual} |' in setup.compute.read_text()
    assert setup.compute.read_text().count('Muse Spark harness (spark-jsonl') == 1
    setup.estimated.clear()
    monkeypatch.setattr(setup.module, 'run_planner', real_run)
    setup.module.main()
    assert setup.estimated == [2]
    assert len(setup.calls) == 2
    assert setup.ledger.total_usd() == 2 * actual
    assert f'| ${2 * actual} |' in setup.compute.read_text()
    assert setup.compute.read_text().count('Muse Spark harness (spark-jsonl') == 1
    assert json.loads((setup.output / 'summary.json').read_text())['instances'] == 2


def test_transport_interruption_preserves_pending_hold_in_compute(run_setup, monkeypatch):
    setup = run_setup
    def interrupt(request, **kwargs):
        # Reservation and compute reconciliation must be durable before sending.
        assert 'conservative pending/unknown hold $1.' in setup.compute.read_text()
        assert setup.ledger.path.read_text().count('reservation') >= 1
        raise KeyboardInterrupt
    monkeypatch.setattr('urllib.request.urlopen', interrupt)
    with pytest.raises(KeyboardInterrupt):
        setup.module.main()
    assert not (setup.output / '1.json').exists()
    assert len(setup.ledger.path.read_text().splitlines()) == 1
    held = setup.ledger.total_usd()
    assert held > 1
    assert f'conservative pending/unknown hold ${held}' in setup.compute.read_text()
    assert setup.compute.read_text().count('Muse Spark harness (spark-jsonl') == 1


def test_incomplete_response_settles_measured_usage_in_compute(run_setup, monkeypatch):
    import io
    import json
    from llm.spark.client import SparkIncompleteError
    setup = run_setup
    def incomplete(request, **kwargs):
        response = io.BytesIO(json.dumps({'id': 'incomplete', 'status': 'incomplete',
            'incomplete_details': {'reason': 'max_output_tokens'}, 'output': [],
            'usage': {'input_tokens': 100, 'output_tokens': 16}}).encode())
        response.headers = {}
        return response
    monkeypatch.setattr('urllib.request.urlopen', incomplete)
    with pytest.raises(SparkIncompleteError):
        setup.module.main()
    assert not (setup.output / '1.json').exists()
    records = [json.loads(row) for row in setup.ledger.path.read_text().splitlines()]
    assert records[-1]['status'] == 'incomplete'
    actual = Decimal(records[-1]['cost_usd'])
    assert setup.ledger.total_usd() == actual
    assert f'| ${actual} |' in setup.compute.read_text()
    assert 'conservative pending/unknown hold $0' in setup.compute.read_text()


def test_paid_readme_requires_applied_credit_and_automatic_compute_reconciliation():
    readme = (Path(__file__).resolve().parents[2] / 'experiments/spark-planner/README.md').read_text()
    assert 'credit has been applied' in readme
    assert 'SPARK_CREDIT_READY=1' in readme
    assert 'automatically' in readme and 'docs/compute.md' in readme
    assert 'row for the leader to add' not in readme
