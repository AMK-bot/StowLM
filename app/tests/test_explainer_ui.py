"""Offline explanation selection and honest verdict presentation; no Spark calls."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.tests.test_counter_totals import seven_moves
from app.tests.test_renderer import render_page
from app.tests.test_stored_explanations import recorded_case, stored


def test_ui_drops_the_obsolete_section_ordering_claim():
    html = Path('app/static/index.html').read_text()
    assert 'Muse Spark orders' not in html
    assert 'Stored Muse Spark explanations' in html
    assert 'deterministic template' in html


@pytest.mark.parametrize('cause', ['stub', 'not_run', 'explicit'])
def test_tainted_or_incomplete_checker_never_renders_overall_pass(cause):
    replay = deepcopy(seven_moves())
    replay['metadata'].update(checker_status='passed', shippable=True, departure_approved=True)
    replay['checker'] = {'passed': True, 'bound': True, 'binding_status': 'bound',
        'report': {'passed': True, 'parts': {}, 'provenance': {}},
        'stability': {}, 'strength': {}, 'validation_status': 'NOT VALIDATED'}
    if cause == 'stub':
        replay['checker']['report']['provenance']['stubs'] = {'wording': 'test only'}
    elif cause == 'not_run':
        replay['checker']['report']['parts']['stability'] = {'ran': False}
    else:
        replay['metadata']['validation_status'] = 'NOT VALIDATED'
    page = render_page(replay, view='checker')
    assert 'NOT VALIDATED' in page['verification']['text']
    assert 'Full checker passed' not in page['verification']['text']
    assert 'NOT VALIDATED' in page['checker-verdict']['text']
    assert 'departure approved' not in page['checker-verdict']['text']


def test_stored_spark_selected_only_after_current_offline_revalidation(tmp_path, recorded_case, monkeypatch):
    from app.explanations import explanation_for_replay
    from app.stored_explanations import explanation_sidecar_path
    instance, plan, report, _ = recorded_case
    path, artifact, _ = stored(tmp_path, recorded_case)
    source = tmp_path / 'plan.json'
    assert path == explanation_sidecar_path(source)
    replay = {'instance': instance.model_dump(mode='json'), 'plan': plan.model_dump(mode='json'),
              'checker': {'report': report.model_dump(mode='json')}, 'metadata': {}}
    monkeypatch.setattr('llm.spark.explain.SparkClient', lambda: pytest.fail('Never create network client'))
    selected = explanation_for_replay(replay, source_path=source)
    assert selected['source'] == 'spark' and selected['text'] == artifact['text']
    assert selected['generated_at'] == artifact['generated_at']
    artifact['validator']['accepted'] = False
    path.write_text(json.dumps(artifact))
    assert explanation_for_replay(replay, source_path=source)['source'] == 'template'


def test_stored_explanation_label_date_and_text_are_visible_in_replay():
    replay = seven_moves()
    replay['explanation'] = {'source': 'spark', 'text': 'NOT VALIDATED: recorded explanation <tag>',
        'model': 'muse-spark-1.3', 'generated_at': '2026-10-29T12:00:00+00:00',
        'validator': {'accepted': True, 'problems': []}}
    page = render_page(replay)
    assert page['replay-explanation']['hidden'] is False
    assert page['explanation-label']['text'] == "Explained by Muse Spark; the checker's verdict is authoritative"
    assert '2026-10-29' in page['explanation-date']['text']
    assert page['explanation-text']['text'] == replay['explanation']['text']


def test_replay_capture_and_comparison_use_the_bound_sidecar_and_reread_it(tmp_path, recorded_case, monkeypatch):
    from app.capture import checked_replay
    from app.comparison import build_comparison
    from app.flow import DemoService
    from app.inputs import LoadedInputs

    instance, plan, report, _ = recorded_case
    path, artifact, _ = stored(tmp_path, recorded_case)
    source = tmp_path / 'plan.json'
    source.write_text(plan.model_dump_json())
    # Transport fixture from recorded evidence, not a new physical verdict.
    payload = {'instance': instance.model_dump(mode='json'), 'plan': plan.model_dump(mode='json'),
               'checker': {'report': report.model_dump(mode='json')}, 'metadata': {},
               'frames': [{'counts': report.count.model_dump(mode='json')}]}
    service = DemoService(instance, plan, source_metadata={'source_path': str(source)},
                          initial_replay=payload, discover=False)
    monkeypatch.setattr('app.capture.build_replay', lambda *a, **kw: deepcopy(payload))
    monkeypatch.setattr('llm.spark.explain.SparkClient', lambda: pytest.fail('No network client in page transport'))
    replay = service.replay(service.active_plan_id)
    capture = checked_replay(LoadedInputs(instance, plan, {'source_path': str(source)}))
    assert replay['explanation']['source'] == capture['explanation']['source'] == 'spark'
    side = build_comparison(capture, capture)['left']
    assert side['explanation_source'] == 'spark'
    assert side['explanation_date'] == artifact['generated_at']
    assert side['validation_status'] == 'NOT VALIDATED'
    artifact['validator']['accepted'] = False
    path.write_text(json.dumps(artifact))
    assert service.replay(service.active_plan_id)['explanation']['source'] == 'template'
    assert build_comparison(capture, capture)['left']['explanation_source'] == 'template'
