"""Offline verification of the named hero's complete bilingual explanation cache.

This validates stored gate artifacts and rederives selected explanations. It
never runs a new checker, alternative screen, search, network or model request.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import (
    Explanation, Mode, StageRecord, chain_intact, sha256_json,
)
from llm.spark.client import MODEL

from . import hero
from .alternatives import rederive_validate
from .cache import SCHEMA, _body, _boxes, _entry_path, load_cached
from .render import ROLE_BINDING, validate_protected_tokens, validate_role_bound
from .verify_recording import _history, _spend

HERE = Path(__file__).resolve().parent
EXPECTED_BOXES = 87
EXPECTED_SELECTED = 10


def _read(path):
    return json.loads(Path(path).read_text())


def _label_check(payload):
    answer = Explanation.model_validate(payload['explanation'])
    summary = payload.get('summary')
    facts = [s for s in answer.statements if s['id'] != 'summary']
    assert len({s['id'] for s in facts}) == len(facts), 'Duplicate fact ids'
    body, statements = _body(facts, summary, recorded=summary is not None)
    assert answer.text == body and answer.statements == statements, 'Display does not rederive'
    if summary is None:
        assert answer.mode == Mode.live and payload['audit']['model'] is None
        assert answer.text.startswith('Template placement facts (no model summary)')
    else:
        assert answer.mode == Mode.recorded and payload['audit']['model'] == MODEL
        assert payload['audit']['summary']['accepted'] is True
        assert answer.text.startswith('Recorded Spark summary\n')
    return facts


def verify_hero(hero_dir=None, *, write=False, manifest=None):
    """Read-only by default; explicit write=True publishes hero/verification.json.

    Every payload is hash/binding/label checked. All fact pairs receive token,
    role and evidence equality checks. Only the selected ten pairs receive full
    input-based load_cached rederivation; this distinction is reported plainly.
    """
    directory = Path(hero_dir) if hero_dir is not None else HERE / 'fixtures/hero'
    cache = directory / 'cache'
    bundle = hero.load_hero(manifest)
    assert bundle is not None, 'No explicitly published checked hero is available'
    values = (bundle['instance'], bundle['plan'], bundle['report'])
    binding = bundle['binding']
    index = _read(cache / 'index.json')
    index_digest = index.pop('sha256', None)
    assert index_digest == sha256_json(index) and index['schema'] == SCHEMA, 'Hero cache index integrity mismatch'
    assert index['binding'] == binding, 'Hero cache binding differs from current named hero'
    aboard = set(_boxes(*values))
    assert len(aboard) == EXPECTED_BOXES and set(index['entries']) == aboard, 'Hero coverage is incomplete'
    projection = _read(cache / 'prefetch-projection.json')
    assert projection['binding'] == binding and projection['boxes'] == EXPECTED_BOXES
    selected = projection['model_box_ids']
    assert len(selected) == len(set(selected)) == EXPECTED_SELECTED and set(selected) <= aboard
    assert projection['demo_box_ids'] == selected and projection['model_boxes'] == EXPECTED_SELECTED
    assert projection['fast_screens_per_demo_box_upper_bound'] == 6
    records = [StageRecord.model_validate(row) for row in index['records']]
    assert records and chain_intact(records), 'Hero stage chain is incomplete'
    assert records[-1].output_sha256 == sha256_json(index['entries']), 'Latest stage does not bind current entries'
    assert any(record.stage == 'explain.prefetch' for record in records)
    assert all(record.input_sha256 == sha256_json(binding) for record in records if record.stage == 'explain.prefetch')
    spending = _spend(cache)
    summaries, sense_counts = Counter(), Counter()
    issues, prior_checks, payload_hashes = {}, {}, {}
    token_pairs, fast_screens, selected_rederived = 0, 0, 0
    replay_times = []
    for box, entries in sorted(index['entries'].items()):
        assert set(entries) == {'en', 'th'}, 'A hero box lacks one language'
        payloads, facts = {}, {}
        for language in ('en', 'th'):
            entry = entries[language]
            payload = _read(cache / _entry_path(entry))
            assert sha256_json(payload) == entry['sha256'], 'Hero payload integrity mismatch'
            assert payload['binding'] == binding and payload['box_id'] == box and payload['language'] == language
            assert payload['audit'] == entry['audit'], 'Hero entry audit differs from its payload'
            assert payload['explanation']['language'] == language
            payloads[language], facts[language] = payload, _label_check(payload)
            if payload.get('summary') is not None:
                assert box in selected, 'An undeclared box carries a model summary'
                summaries[language] += 1
        assert [s['id'] for s in facts['en']] == [s['id'] for s in facts['th']]
        assert {'position', 'bay_profile', 'restow'} <= {s['id'] for s in facts['en']}
        validate_role_bound(facts['th'])
        for en, th in zip(facts['en'], facts['th']):
            assert validate_protected_tokens(en['text'], th['text']), 'Bilingual protected tokens differ'
            assert th['text'] == en['th'], 'Thai values do not retain their code-rendered roles'
            assert en['evidence'] == th['evidence'], 'Bilingual evidence differs'
            token_pairs += 1
        en_summary, th_summary = (payloads[lang].get('summary') for lang in ('en', 'th'))
        if th_summary is not None:
            assert en_summary is not None and en_summary['slot_ids'] == th_summary['slot_ids']
            assert validate_protected_tokens(en_summary['text'], th_summary['text'])
        audit = payloads['th']['audit']
        assert audit['checks']['fact_roles'] == ROLE_BINDING
        check = audit['checks']['thai_sense']
        assert check['is_proof'] is False and check['native_reader_reviewed'] is False
        if check['status'] == 'unavailable' or any(issue['id'] == 'summary' for issue in check['issues']):
            assert th_summary is None, 'Flagged or unavailable Thai summary is still displayed'
        if box in selected:
            assert check['status'] in {'passed', 'flagged', 'unavailable'}, 'Selected box lacks attempted Thai sense QA'
            sense_counts[check['status']] += 1
            if check['issues']:
                issues[box] = check['issues']
            prior_checks[box] = audit.get('prior_language_checks', [])
            payload_hashes[box] = {lang: entries[lang]['sha256'] for lang in ('en', 'th')}
            for language in ('en', 'th'):
                started = time.perf_counter()
                answer = load_cached(*values, box, language=language, out_dir=cache)
                replay_times.append(time.perf_counter() - started)
                assert answer is not None and answer.language == language
                assert (answer.mode == Mode.recorded) == (payloads[language].get('summary') is not None)
                selected_rederived += 1
            screens = payloads['en'].get('alternatives')
            assert screens is not None and screens == payloads['th'].get('alternatives')
            assert screens['box_id'] == box and screens['label'] == 'Fast screen'
            assert len(screens['alternatives']) == screens['summary']['alternatives'] == 6
            assert len({r['target_slot'] for r in screens['alternatives']}) == 6
            assert sum(screens['summary']['categories'].values()) == 6
            assert screens['summary']['full_check_still_needed'] is True
            check_screens = rederive_validate(screens, *values, rerun_screens=False)
            assert check_screens['passed'] and not check_screens['screen_recomputed'] and not check_screens['full_check']
            fast_screens += len(screens['alternatives'])
    assert selected_rederived == 2 * EXPECTED_SELECTED and fast_screens == 6 * EXPECTED_SELECTED
    value = dict(created_utc=datetime.now(timezone.utc).isoformat(), label='Measured verification', synthetic=True,
        fixture_scope='Named lattice hero; current source and stored checker/physics gates authenticated without rerunning them',
        input_binding=binding, hero_manifest_sha256=bundle['manifest_sha256'],
        hero_entry_sha256=bundle['entry_sha256'], cache_index_sha256=index_digest,
        boxes=len(aboard), bilingual_answers=2 * len(aboard), selected_demo_box_ids=selected,
        selected_bilingual_answers_rederived=selected_rederived,
        selected_facts_evidence_and_summary_slots_rederived=True,
        all_payloads_integrity_and_binding_checked=True, all_template_and_model_labels_truthful=True,
        protected_statement_pairs=token_pairs, all_fact_pair_tokens_roles_and_evidence_checked=True,
        nonselected_fact_check_scope='Payload integrity, code-template role equality, bilingual protected tokens and evidence equality; no per-box input rederivation',
        alternative_fast_screens=fast_screens, alternatives_rederived=True,
        alternative_screen_recomputed=False, full_checker_recomputed=False,
        model_summary_counts={lang: summaries[lang] for lang in ('en', 'th')},
        thai_sense_counts=dict(sense_counts), remaining_thai_sense_issues=issues,
        prior_language_checks=prior_checks, selected_payload_sha256=payload_hashes,
        archived_thai_flags=_history(HERE / 'fixtures/development'),
        native_thai_reader_reviewed=False, sense_is_proof=False, stage_chain_intact=True,
        selected_load_cached_wall_s={'samples': len(replay_times), 'total': sum(replay_times),
                                    'max': max(replay_times)}, **spending)
    value['sha256'] = sha256_json(value)
    if write:
        from .cache import _write
        _write(directory / 'verification.json', value)
    return value


verify = verify_hero


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hero-dir', type=Path, default=HERE / 'fixtures/hero')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--write', action='store_true', help='Publish verification.json after all checks pass')
    args = parser.parse_args(argv)
    value = verify_hero(args.hero_dir, write=args.write, manifest=args.manifest)
    keys = ('boxes', 'bilingual_answers', 'selected_bilingual_answers_rederived', 'alternative_fast_screens',
            'model_summary_counts', 'thai_sense_counts', 'remaining_thai_sense_issues',
            'native_thai_reader_reviewed', 'sense_is_proof', 'measured_cost_usd',
            'paid_calls', 'shared_ledger_calls_matched', 'unknown_billing_holds', 'sha256')
    print(json.dumps({key: value[key] for key in keys}, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
