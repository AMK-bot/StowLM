"""Current schema-2 development replay stays offline and labels model work honestly."""
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import Mode, sha256_json
from experiments.mvp.pipeline.explain import explain_box, load_cached
from experiments.mvp.pipeline.explain.api import inputs
from experiments.mvp.pipeline.explain.cache import SCHEMA
from experiments.mvp.pipeline.explain.render import validate_protected_tokens

FIXTURES = Path(__file__).with_name('fixtures')
DEVELOPMENT = FIXTURES / 'development'
CACHE = DEVELOPMENT / 'current-cache'


@pytest.fixture
def no_network(monkeypatch):
    import urllib.request
    from llm.spark.client import SparkClient

    def forbidden(*args, **kwargs):
        pytest.fail('recorded replay attempted a paid or network call')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(SparkClient, 'complete', forbidden)


def source_inputs():
    return inputs(*(json.loads((DEVELOPMENT / f'{name}.json').read_text())
                    for name in ('instance', 'plan', 'report')))


def test_recorded_real_move_verifies_offline(no_network):
    from experiments.mvp.pipeline.explain.verify_what_if import verify
    result = verify()
    assert result['screen'] == 'full'
    assert result['source_slot'] != result['target_slot']
    assert result['cost_usd'] == 0


def test_current_bilingual_fixture_without_network(no_network):
    instance, plan, report = source_inputs()
    fixture = json.loads((DEVELOPMENT / 'fixture.json').read_text())
    for name, value in [('instance', instance), ('plan', plan), ('report', report)]:
        assert fixture[name + '_sha256'] == sha256_json(value.model_dump(mode='json'))
    assert report.passed and all(part.ran for part in report.parts.values())
    index = json.loads((CACHE / 'index.json').read_text())
    assert index['schema'] == SCHEMA and len(index['entries']) >= 10
    assert len(index['entries']) == fixture['boxes_aboard']
    for box, entries in index['entries'].items():
        answers, payloads = {}, {}
        for language in ('en', 'th'):
            answers[language] = load_cached(instance, plan, report, box, language=language, out_dir=CACHE)
            assert answers[language] is not None
            payloads[language] = json.loads((CACHE / entries[language]['path']).read_text())
            summary = payloads[language].get('summary')
            if summary is not None:
                assert answers[language].mode == Mode.recorded
                assert answers[language].text.startswith('Recorded Spark summary')
                assert entries[language]['audit']['summary']['accepted']
                assert entries[language]['audit']['model'] is not None
            else:
                assert answers[language].mode == Mode.live
                assert answers[language].text.startswith('Template placement facts (no model summary)')
                assert entries[language]['audit']['model'] is None
        en = {s['id']: s for s in answers['en'].statements if s['id'] != 'summary'}
        th = {s['id']: s for s in answers['th'].statements if s['id'] != 'summary'}
        assert en.keys() == th.keys()
        assert {'position', 'bay_profile', 'restow'} <= en.keys()
        for key in en:
            assert validate_protected_tokens(en[key]['text'], th[key]['text'])
            assert th[key]['text'] == en[key]['th']
            assert en[key]['evidence'] == th[key]['evidence']
        if payloads['th'].get('summary'):
            assert payloads['en']['summary']
            assert payloads['en']['summary']['slot_ids'] == payloads['th']['summary']['slot_ids']
            assert validate_protected_tokens(payloads['en']['summary']['text'], payloads['th']['summary']['text'])
        audit = entries['th']['audit']
        assert audit['checks']['protected_tokens'] == 'passed'
        assert audit['checks']['fact_roles'] == 'code-template-v1'
        check = audit['checks']['thai_sense']
        assert check['is_proof'] is False and check['native_reader_reviewed'] is False
        # A current check may flag wording; neither status is proof of Thai quality.
        assert check['status'] in {'passed', 'flagged'}


def test_retired_proposer_cache_is_not_the_api_default(no_network, monkeypatch):
    import experiments.mvp.pipeline.explain.cache as cache_module
    observed = []

    def absent(*args, **kwargs):
        observed.append(Path(kwargs['out_dir']))
        return None
    monkeypatch.setattr(cache_module, 'load_cached', absent)
    instance, plan, report = source_inputs()
    box = json.loads((DEVELOPMENT / 'fixture.json').read_text())['explanation_box_ids'][0]
    answer = explain_box(instance, plan, report, box)
    assert answer.mode == Mode.live and 'no model summary' in answer.text
    assert observed == [FIXTURES / 'hero' / 'cache']
    assert observed[0] != CACHE and 'retired-cache' not in observed[0].parts
    assert not (FIXTURES / 'cache' / 'index.json').exists()


def test_verification_keeps_current_and_historical_feedback(no_network):
    from experiments.mvp.pipeline.explain.verify_recording import verify
    archived = (DEVELOPMENT / 'verification.json').read_bytes()
    result = verify()
    assert result['boxes'] >= 10 and result['bilingual_answers'] >= 20
    assert result['all_facts_and_evidence_rederived']
    assert result['model_summary_slots_rederived'] and result['template_labels_honest']
    assert result['native_thai_reader_reviewed'] is False and result['sense_is_proof'] is False
    assert result['archived_thai_flags'], 'The original Thai wording flag must remain visible'
    assert result['shared_ledger_calls_matched'] == result['paid_calls']
    assert result['task_cap_usd'] == 5
    assert (DEVELOPMENT / 'verification.json').read_bytes() == archived
