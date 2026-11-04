"""Recorded evidence must stay replayable without services or computation."""
import json
import math
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import Explanation, MoveVerdict, sha256_file

FIXTURES = Path(__file__).with_name('fixtures')


def test_recorded_demo_covers_promised_challenges():
    data = json.loads((FIXTURES/'demo.json').read_text())
    assert data['synthetic'] is True
    assert data['complete'] is True
    assert data['baseline_passed'] is True
    source = FIXTURES.parents[4]/data['source_fixture']
    assert sha256_file(source) == data['source_sha256']
    from stowage.model import CheckReport, Instance, MovePlan
    from stowage.checker.full import report_matches_inputs
    baseline = json.loads(source.read_text())
    report = CheckReport.model_validate(data['baseline_report'])
    assert report.passed and report_matches_inputs(report, Instance.model_validate(baseline['instance']),
                                                  MovePlan.model_validate(baseline['plan']))
    assert {c['kind'] for c in data['cases']} == {'legal', 'crane_order', 'segregation', 'securing'}
    assert data['limit_changes'] == []
    assert data['label'] == 'Demo cargo and limits tightened for this challenge'
    assert 'no vessel' in data['label_disclosure']
    prefixes = {'segregation': ('imdg.segregation.',),
                'securing': ('iacs.member_irs.container_securing',)}
    for case in data['cases']:
        fast = MoveVerdict.model_validate(case['fast'])
        fast_text = Explanation.model_validate(case['fast_explanation'])
        assert fast.screen == 'fast' and fast.gauges['full_check_pending'] is True
        assert fast_text.mode.value == 'recorded' and 'Fast screen, full check running' in fast_text.text
        assert fast.gauges['frames'][0]['stability_passed'] is None
        assert fast.gauges['frames'][0]['strength_passed'] is None
        verdict = MoveVerdict.model_validate(case['verdict'])
        text = Explanation.model_validate(case['explanation'])
        assert verdict.screen == 'full'
        assert text.mode.value == 'recorded'
        assert text.statements
        if case['kind'] == 'legal':
            assert verdict.legal
            assert verdict.gauges['outcome'] == 'passes_as_edited'
            assert "Legal isn't the same as good." in text.text
        elif case['kind'] == 'crane_order':
            assert not verdict.legal
            assert verdict.gauges['outcome'] == 'stow_fine_crane_order_failed'
            assert verdict.gauges['deciding_cause']['kind'] == 'crane_order'
            assert verdict.gauges['stow_assessment']['passed'] is True
            assert 'A replan might.' in text.text
        else:
            assert not verdict.legal
            assert verdict.gauges['outcome'] == 'stow_breaks_rule'
            assert any(b['rule_id'].startswith(prefixes[case['kind']]) for b in verdict.broken)
            assert all({'rule_id', 'measured', 'limit', 'boxes'} <= b.keys() for b in verdict.broken)
            if case['kind'] == 'securing':
                assert verdict.gauges['deciding_cause']['kind'] == 'stow_rule'
                assert verdict.gauges['diagnostic_only'] is False
                assert any(b['rule_id'].startswith('iacs.member_irs.container_securing') and
                           b['measured'] > b['limit'] == 1.0 for b in verdict.broken)
        assert verdict.gauges['crane_order_preserved'] is True
        assert "This edits one box and keeps the crane order and ballast. It doesn't replan." in text.text
        assert 'Crane lifts this call:' in text.text and 'Projected later-port lifts:' in text.text
        assert all(s['text'] in text.text and s['evidence'] for s in text.statements)


def test_measured_agreement_is_recountable_and_latency_is_finite():
    for filename in ('benchmark190.json', 'benchmark354.json'):
        data = json.loads((FIXTURES/filename).read_text())
        rows, report = data['moves'], data['summary']
        assert data['complete'] is True
        assert data['baseline_passed'] is True
        assert len(rows) >= 50
        assert len({(r['box_id'],r['target_slot']) for r in rows}) == len(rows)
        assert report['full_legal'] > 0 and report['full_illegal'] > 0
        assert report['moves'] == len(rows)
        assert report['false_legal'] == sum(r['fast']['legal'] and not r['full']['legal'] for r in rows)
        assert report['fast_legal'] == sum(r['fast']['legal'] for r in rows)
        assert report['false_legal_fraction_of_all_moves'] == report['false_legal']/len(rows)
        assert report['false_legal_fraction_of_fast_legal'] == (
            report['false_legal']/report['fast_legal'] if report['fast_legal'] else None)
        for name, digest in data['source_sha256'].items():
            # Historical runs stay bound to their measured implementation after
            # a screen fix; the archived source must match the original digest.
            current = FIXTURES.parent/name
            snapshots = [FIXTURES/folder/(name+'.txt') for folder in ('screen_v1', 'history-v1')]
            assert sha256_file(current) == digest or any(
                snapshot.exists() and sha256_file(snapshot) == digest for snapshot in snapshots)
        for row in rows:
            assert math.isfinite(row['fast_s']) and 0 <= row['fast_s'] <= row['full_s']
            assert row['admission']['load1'] <= 45
            assert row['admission']['mask'].bit_count() >= 8
            assert 0 <= row['admission']['age_s'] < 120
            MoveVerdict.model_validate(row['fast'])
            MoveVerdict.model_validate(row['full'])
