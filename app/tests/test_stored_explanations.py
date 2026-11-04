"""Prefetch tests replay published recordings; no API or physics certification."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm.spark.explain import PlanExplainer
from stowage.model import CheckReport, Instance, MovePlan
from app.tests.report_helpers import bind_report

ROOT = Path(__file__).resolve().parents[2] / 'experiments/spark-explain'


class RecordedClient:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        return SimpleNamespace(text=self.text)


@pytest.fixture
def recorded_case(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: pytest.fail('No network in cache tests'))
    name = 'C_live_stale_vessel_data'
    instance = Instance.model_validate_json((ROOT / 'cases/instance.json').read_text())
    plan = MovePlan.model_validate_json((ROOT / f'cases/{name}.plan.json').read_text())
    report = CheckReport.model_validate_json((ROOT / f'cases/{name}.report.json').read_text())
    report.provenance.parents = [p for p in report.provenance.parents
                                 if not p.startswith(('checker.instance.sha256:', 'checker.plan.sha256:'))]
    bind_report(report, instance, plan)
    rows = json.loads((ROOT / 'recorded.json').read_text())
    row = next(row for row in rows if row['run'] == 'v2.3' and row['case'] == name)
    # The real recordings use the retired free-prose protocol. Keep them as
    # rejection coverage below. This explicit synthetic selection replays the
    # current statement-ID protocol against those same recorded checker facts;
    # it is a test fixture, never claimed to be a new Muse Spark response.
    assert 'reload full vessel' in row['raw']
    raw = json.dumps({'headline': ['incomplete:checker.missing_vessel_data'],
                      'points': [['loads', 'yard_rehandles'], ['not_run:stability', 'later_ports']]})
    return instance, plan, report, raw


def stored(tmp_path, recorded_case):
    from app.stored_explanations import prefetch_explanation
    instance, plan, report, raw = recorded_case
    path = tmp_path / 'plan.json.spark-explanation.json'
    client = RecordedClient(raw)
    artifact = prefetch_explanation(instance, plan, report, explainer=PlanExplainer(client),
        output_path=path, generated_at='2026-10-29T12:00:00+00:00')
    return path, artifact, client


def test_recorded_spark_can_be_prefetched_and_revalidated_offline(tmp_path, recorded_case, monkeypatch):
    from app.stored_explanations import load_stored_explanation
    path, artifact, client = stored(tmp_path, recorded_case)
    assert client.calls == 1
    assert artifact['validator']['accepted'] is True
    assert artifact['generated_at'] == '2026-10-29T12:00:00+00:00'
    monkeypatch.setattr('llm.spark.explain.SparkClient', lambda: pytest.fail('Cache loader must stay offline'))
    loaded = load_stored_explanation(path, *recorded_case[:3])
    assert loaded and '4 boxes are loaded' in loaded['text']
    assert 'NOT VALIDATED' in loaded['text']
    assert loaded['model'] == 'muse-spark-1.3'


@pytest.mark.parametrize('changed', ['instance', 'plan', 'report'])
def test_cached_spark_requires_complete_current_inputs(tmp_path, recorded_case, changed):
    from app.stored_explanations import load_stored_explanation
    path, _, _ = stored(tmp_path, recorded_case)
    instance, plan, report = [obj.model_copy(deep=True) for obj in recorded_case[:3]]
    if changed == 'instance':
        instance.id += '-different'
    elif changed == 'plan':
        plan.id += '-different'
    else:
        report.passed = not report.passed
    assert load_stored_explanation(path, instance, plan, report) is None


@pytest.mark.parametrize('tamper', ['raw', 'text', 'accepted', 'version', 'model', 'date'])
def test_cached_spark_rejects_tampered_or_stale_metadata(tmp_path, recorded_case, tamper):
    from app.stored_explanations import load_stored_explanation
    path, artifact, _ = stored(tmp_path, recorded_case)
    if tamper == 'raw':
        artifact['response_text'] = '{"headline":"This is safe and approved", "points":["Optimal.","All checks passed."]}'
    elif tamper == 'text':
        artifact['text'] = 'This is safe and approved.'
    elif tamper == 'accepted':
        artifact['validator']['accepted'] = False
    elif tamper == 'version':
        artifact['explainer_version'] = 'old'
    elif tamper == 'model':
        artifact['model'] = 'different-model'
    else:
        artifact['generated_at'] = 'not-a-date'
    path.write_text(json.dumps(artifact))
    assert load_stored_explanation(path, *recorded_case[:3]) is None


def test_rejected_recording_is_saved_with_verdict_but_never_served(tmp_path, recorded_case):
    from app.stored_explanations import load_stored_explanation, prefetch_explanation
    instance, plan, report, _ = recorded_case
    rows = json.loads((ROOT / 'recorded.json').read_text())
    raw = next(row['raw'] for row in rows if row['run'] == 'v2.2' and row['case'] == 'C_live_stale_vessel_data')
    path = tmp_path / 'rejected.json'
    artifact = prefetch_explanation(instance, plan, report, explainer=PlanExplainer(RecordedClient(raw)), output_path=path)
    assert artifact['validator']['accepted'] is False
    assert artifact['validator']['problems']
    assert load_stored_explanation(path, instance, plan, report) is None


@pytest.mark.parametrize('payload', [None, 'bad json', '[]', '{}'])
def test_missing_or_malformed_cache_falls_back(tmp_path, recorded_case, payload):
    from app.stored_explanations import load_stored_explanation
    path = tmp_path / 'missing.json'
    if payload is not None:
        path.write_text(payload)
    assert load_stored_explanation(path, *recorded_case[:3]) is None


def test_prefetch_offline_or_credit_refusal_records_fallback_without_call(tmp_path, recorded_case, monkeypatch):
    from app.stored_explanations import prefetch_explanation
    monkeypatch.delenv('MODEL_API_KEY', raising=False)
    monkeypatch.delenv('SPARK_CREDIT_READY', raising=False)
    for use_api in (False, True):
        artifact = prefetch_explanation(*recorded_case[:3], explainer=PlanExplainer(use_api=use_api),
            output_path=tmp_path / f'offline-{use_api}.json')
        assert artifact['validator']['accepted'] is False
        assert artifact['response_text'] is None


def test_prefetch_cli_fresh_checks_every_selected_plan(tmp_path, recorded_case, monkeypatch):
    import app.prefetch_explanations as cli
    from app.stored_explanations import explanation_sidecar_path
    instance, plan, report, raw = recorded_case
    checks = []
    monkeypatch.setattr(cli, 'load_inputs', lambda **kw: SimpleNamespace(instance=instance, plan=plan))
    monkeypatch.setattr(cli, 'check', lambda i, p: checks.append((i, p)) or report)
    monkeypatch.setattr(cli, 'PlanExplainer', lambda: PlanExplainer(RecordedClient(raw)))
    paths = [tmp_path / 'primary.json', tmp_path / 'baseline.json']
    assert cli.main(['--instance', str(tmp_path / 'instance.json'),
                     '--plan', str(paths[0]), '--plan', str(paths[1])]) == 0
    assert checks == [(instance, plan), (instance, plan)]
    assert all(explanation_sidecar_path(path).is_file() for path in paths)


def test_prefetch_cli_rejects_unbound_check_before_creating_client(tmp_path, recorded_case, monkeypatch):
    import app.prefetch_explanations as cli
    instance, plan, report, _ = recorded_case
    report = report.model_copy(deep=True)
    report.provenance.parents = []
    monkeypatch.setattr(cli, 'load_inputs', lambda **kw: SimpleNamespace(instance=instance, plan=plan))
    monkeypatch.setattr(cli, 'check', lambda i, p: report)
    monkeypatch.setattr(cli, 'PlanExplainer', lambda: pytest.fail('No client creation for unbound checker evidence'))
    with pytest.raises(ValueError, match='bound'):
        cli.main(['--instance', str(tmp_path / 'instance.json'), '--plan', str(tmp_path / 'primary.json')])
