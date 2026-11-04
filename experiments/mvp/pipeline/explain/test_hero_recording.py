"""Mocked verifier checks plus explicitly published hero-fixture replay checks."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import StageRecord, sha256_json
from experiments.mvp.pipeline.explain import verify_hero as verifier
from experiments.mvp.pipeline.explain.cache import SCHEMA, _body, _save_index, _write
from experiments.mvp.pipeline.explain.render import ROLE_BINDING

HERO = Path(__file__).with_name('fixtures') / 'hero'


@pytest.fixture
def fake_recording(tmp_path, monkeypatch):
    boxes = [f'BOX{i:02}' for i in range(87)]
    selected = boxes[:10]
    values = {'instance': {'containers': [{'id': box} for box in boxes]}, 'plan': {}, 'report': {'passed': True}}
    binding = {key + '_sha256': sha256_json(value) for key, value in values.items()}
    bundle = dict(values, binding=binding, manifest_sha256='a' * 64, entry_sha256='b' * 64)
    monkeypatch.setattr(verifier.hero, 'load_hero', lambda manifest=None: bundle)
    monkeypatch.setattr(verifier, '_boxes', lambda *args: boxes)
    monkeypatch.setattr(verifier, '_history', lambda directory: {'prior': {'check': {'status': 'flagged'}}})
    monkeypatch.setattr(verifier, '_spend', lambda directory: {
        'paid_calls': 0, 'measured_cost_usd': '0', 'unknown_billing_holds': 0,
        'shared_ledger_calls_matched': 0, 'spend_state': 'fake', 'task_cap_usd': 5})
    loaded, screens_checked = [], []
    def load(*args, language, out_dir):
        loaded.append((args[3], language))
        entry = read_index(out_dir)['entries'][args[3]][language]
        payload = json.loads((Path(out_dir) / entry['path']).read_text())
        return type('Answer', (), dict(language=language, mode=verifier.Mode(payload['explanation']['mode'])))()
    def screens(data, *args, rerun_screens):
        assert rerun_screens is False
        screens_checked.append(data['box_id'])
        return {'passed': True, 'screen_recomputed': False, 'full_check': False}
    monkeypatch.setattr(verifier, 'load_cached', load)
    monkeypatch.setattr(verifier, 'rederive_validate', screens)
    directory = tmp_path / 'hero'
    cache = directory / 'cache'
    cache.mkdir(parents=True)
    index = {'schema': SCHEMA, 'binding': binding, 'entries': {}, 'records': []}
    for box in boxes:
        menu = [
            {'id': 'position', 'text': f'{box} is at 0010182.', 'th': f'{box} อยู่ที่ 0010182.', 'evidence': []},
            {'id': 'bay_profile', 'text': 'Bay mass 12 kg; mean 20 kg.',
             'th': 'มวลในระวาง 12 kg; ค่าเฉลี่ย 20 kg.', 'evidence': []},
            {'id': 'restow', 'text': 'Projection: 2 restow lifts.',
             'th': 'ค่าคาดการณ์: ยกสับเปลี่ยน 2 ครั้ง.', 'evidence': []}]
        alternatives = None if box not in selected else {'box_id': box, 'label': 'Fast screen',
            'alternatives': [{'target_slot': f'00101{i + 2:02}'} for i in range(6)],
            'summary': {'alternatives': 6, 'categories': {'broken_rule': 6}, 'full_check_still_needed': True}}
        index['entries'][box] = {}
        for lang in ('en', 'th'):
            rows = deepcopy(menu)
            if lang == 'th':
                for row in rows:
                    row['text'] = row['th']
            has_summary = (lang == 'en' and box in selected[:2]) or (lang == 'th' and box == selected[0])
            summary = None if not has_summary else {'text': 'Model framing.', 'slot_ids': ['position', 'restow'], 'evidence': []}
            body, statements = _body(rows, summary, recorded=has_summary)
            sense = {'status': 'passed' if box in selected else 'not_run', 'issues': [],
                     'is_proof': False, 'native_reader_reviewed': False}
            if box == selected[0]:
                sense.update(status='flagged', issues=[{'id': 'bay_profile', 'reason': 'A wording flag'}])
            audit = {'model': verifier.MODEL if has_summary else None,
                     'summary': {'accepted': has_summary}, 'prior_language_checks': [],
                     'checks': {'fact_roles': ROLE_BINDING, 'thai_sense': sense}}
            payload = {'binding': binding, 'box_id': box, 'language': lang, 'audit': audit,
                       'explanation': {'language': lang, 'text': body, 'statements': statements,
                                       'mode': 'recorded' if has_summary else 'live'},
                       'summary': summary, 'alternatives': alternatives}
            digest = sha256_json(payload)
            _write(cache / (digest + '.json'), payload)
            index['entries'][box][lang] = {'path': digest + '.json', 'sha256': digest, 'audit': audit}
    index['records'] = [StageRecord(stage='explain.prefetch', mode='live', input_sha256=sha256_json(binding),
        output_sha256=sha256_json(index['entries']), wall_s=0).model_dump(mode='json')]
    _save_index(cache / 'index.json', index)
    _write(cache / 'prefetch-projection.json', dict(binding=binding, boxes=87, model_box_ids=selected,
        demo_box_ids=selected, model_boxes=10, fast_screens_per_demo_box_upper_bound=6))
    return directory, loaded, screens_checked


def read_index(directory):
    value = json.loads((Path(directory) / 'index.json').read_text())
    value.pop('sha256')
    return value


def edit_payload(directory, box, language, mutate):
    cache = directory / 'cache'
    index = read_index(cache)
    entry = index['entries'][box][language]
    payload = json.loads((cache / entry['path']).read_text())
    mutate(payload)
    digest = sha256_json(payload)
    _write(cache / (digest + '.json'), payload)
    index['entries'][box][language] = {'path': digest + '.json', 'sha256': digest, 'audit': payload['audit']}
    index['records'][-1]['output_sha256'] = sha256_json(index['entries'])
    _save_index(cache / 'index.json', index)


def test_mock_verification_checks_all_pairs_and_only_rederives_selected(fake_recording):
    directory, loaded, screens = fake_recording
    result = verifier.verify_hero(directory)
    assert result['boxes'] == 87 and result['bilingual_answers'] == 174
    assert len(loaded) == result['selected_bilingual_answers_rederived'] == 20
    assert len(screens) == 10 and result['alternative_fast_screens'] == 60
    assert result['protected_statement_pairs'] == 261
    assert result['model_summary_counts'] == {'en': 2, 'th': 1}
    assert result['remaining_thai_sense_issues']['BOX00']
    assert result['archived_thai_flags'] and result['native_thai_reader_reviewed'] is False
    assert not (directory / 'verification.json').exists()


def test_mock_explicit_write_publishes_content_bound_verification(fake_recording):
    directory, _, _ = fake_recording
    result = verifier.verify_hero(directory, write=True)
    stored = json.loads((directory / 'verification.json').read_text())
    assert stored == result
    digest = stored.pop('sha256')
    assert digest == sha256_json(stored)


def test_mock_incomplete_hero_is_rejected_before_replay(fake_recording):
    directory, loaded, _ = fake_recording
    cache = directory / 'cache'
    index = read_index(cache)
    index['entries'].pop('BOX86')
    _save_index(cache / 'index.json', index)
    with pytest.raises(AssertionError, match='coverage'):
        verifier.verify_hero(directory)
    assert loaded == []


def test_mock_swapped_roles_fail_even_with_rehashed_payload(fake_recording):
    directory, _, _ = fake_recording
    def swap(payload):
        row = payload['explanation']['statements'][1]
        row['text'] = row['th'] = 'มวลในระวาง 20 kg; ค่าเฉลี่ย 12 kg.'
        payload['explanation']['text'], _ = _body(payload['explanation']['statements'])
    edit_payload(directory, 'BOX86', 'th', swap)
    with pytest.raises(AssertionError, match='roles'):
        verifier.verify_hero(directory)


def test_mock_unavailable_thai_summary_must_be_withheld(fake_recording):
    directory, _, _ = fake_recording
    edit_payload(directory, 'BOX00', 'th', lambda p: p['audit']['checks']['thai_sense'].update(status='unavailable'))
    with pytest.raises(AssertionError, match='still displayed'):
        verifier.verify_hero(directory)


def test_mock_template_never_borrows_model_label(fake_recording):
    directory, _, _ = fake_recording
    edit_payload(directory, 'BOX86', 'en', lambda p: p['explanation'].update(mode='recorded'))
    with pytest.raises(AssertionError):
        verifier.verify_hero(directory)


@pytest.fixture(scope='module')
def published_hero_verification():
    # Root publishes this only after the complete prefetch and explicit verify.
    # Unit tests above never inspect a live, partial recording.
    path = HERO / 'verification.json'
    if not path.exists():
        pytest.skip('Await complete hero prefetch and explicit verify_hero --write')
    before = path.read_bytes()
    result = verifier.verify_hero(HERO, write=False)
    assert path.read_bytes() == before, 'Fixture verification tests must be read-only'
    return result


def test_published_hero_covers_all_boxes_and_selected_alternatives(published_hero_verification):
    result = published_hero_verification
    assert result['boxes'] == 87 and result['bilingual_answers'] == 174
    assert len(result['selected_demo_box_ids']) == 10
    assert result['selected_bilingual_answers_rederived'] == 20
    assert result['alternative_fast_screens'] == 60
    assert result['alternatives_rederived'] and not result['alternative_screen_recomputed']
    assert not result['full_checker_recomputed']


def test_published_hero_labels_slots_thai_flags_and_spend_are_honest(published_hero_verification):
    result = published_hero_verification
    assert result['selected_facts_evidence_and_summary_slots_rederived']
    assert result['all_template_and_model_labels_truthful']
    assert result['all_fact_pair_tokens_roles_and_evidence_checked']
    assert all(0 <= result['model_summary_counts'][lang] <= 10 for lang in ('en', 'th'))
    assert sum(result['thai_sense_counts'].values()) == 10
    assert result['native_thai_reader_reviewed'] is False and result['sense_is_proof'] is False
    assert result['archived_thai_flags'], 'The previous Thai wording flags must remain visible'
    assert result['paid_calls'] == result['shared_ledger_calls_matched']
    assert result['unknown_billing_holds'] == 0 and result['task_cap_usd'] == 5
