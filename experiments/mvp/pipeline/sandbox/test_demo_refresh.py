"""The current showcase uses the hero's real, unchanged operating limits."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from .demo_refresh import HERO, SOURCE, FIX, digest


def test_demo_hero_changes_only_declared_dg_and_identity():
    hero = json.loads(HERO.read_text())
    derived = json.loads(SOURCE.read_text())
    expected = deepcopy(hero['instance'])
    expected['id'] += '.sandbox-synthetic-dg'
    before = next(b for b in expected['containers'] if b['id'] == 'QZXU0000014')
    modification, = derived['modifications']
    assert before['dg'] == modification['before']
    before['dg'] = modification['after']
    assert expected == derived['instance']
    assert derived['instance']['vessel'] == hero['instance']['vessel']
    assert derived['instance']['rule_overrides'] == hero['instance']['rule_overrides']
    assert derived['limit_changes'] == []
    assert derived['plan']['moves'] == hero['plan']['moves']
    assert derived['plan']['ballast'] == hero['plan']['ballast']
    assert derived['derived_from_sha256'] == digest(HERO)
    assert derived['label'] == 'Demo cargo and limits tightened for this challenge'
    acid = next(b for b in derived['instance']['containers'] if b['id'] == 'QZXU0000349')
    assert acid['dg']['un_number'] == '1789'
    assert before['dg']['un_number'] == '1689'


def test_demo_hero_baseline_is_fresh_and_input_bound():
    from stowage.model import Instance, MovePlan, CheckReport
    from stowage.checker.full import report_matches_inputs
    f = json.loads(SOURCE.read_text())
    baseline = json.loads((FIX/'demo_hero_baseline.json').read_text())
    assert baseline['source_sha256'] == digest(SOURCE)
    report = CheckReport.model_validate(baseline['baseline_report'])
    assert report.passed and baseline['baseline_passed']
    assert report_matches_inputs(report, Instance.model_validate(f['instance']), MovePlan.model_validate(f['plan']))


def test_retired_fitted_limits_cannot_be_regenerated():
    from .discover_demo import finalize_synthetic_design
    with pytest.raises(ValueError, match='retired'):
        finalize_synthetic_design(None, None, None, None, None)


def test_refreshed_showcase_covers_three_honest_outcomes():
    from experiments.mvp.pipeline.contract.schema import MoveVerdict, Explanation
    demo = json.loads((FIX/'demo.json').read_text())
    assert demo['source_sha256'] == digest(SOURCE)
    assert demo['complete'] and not demo['missing']
    assert demo['limit_changes'] == []
    cases = {c['kind']: c for c in demo['cases']}
    assert set(cases) == {'legal', 'crane_order', 'segregation', 'securing'}
    assert {c['verdict']['gauges']['outcome'] for c in cases.values()} == {
        'passes_as_edited', 'stow_fine_crane_order_failed', 'stow_breaks_rule'}
    for case in cases.values():
        assert case['confirmed']
        full = MoveVerdict.model_validate(case['verdict'])
        fast = MoveVerdict.model_validate(case['fast'])
        text = Explanation.model_validate(case['explanation'])
        assert full.screen == 'full' and fast.screen == 'fast'
        assert text.mode.value == 'recorded'
        assert "This edits one box and keeps the crane order and ballast. It doesn't replan." in text.text
        assert 'Projected later-port lifts:' in text.text and '(projection)' in text.text
    passed = cases['legal']['verdict']
    assert passed['legal'] and passed['extra_lifts'] == 0
    assert passed['gauges']['lift_counts']['projected_later_delta'] > 0
    assert "Legal isn't the same as good." in cases['legal']['explanation']['text']
    securing = cases['securing']['verdict']
    assert securing['gauges']['replay_legal']
    assert securing['gauges']['deciding_cause']['rule_id'] == 'iacs.member_irs.container_securing'
    assert any(b['rule_id'] == 'iacs.member_irs.container_securing' and b['measured'] > b['limit'] == 1.0
               for b in securing['broken'])
    order = cases['crane_order']['verdict']
    assert order['gauges']['stow_assessment']['passed'] is True
    assert order['gauges']['deciding_cause']['kind'] == 'crane_order'
    assert any(b['rule_id'].startswith('imdg.segregation.') and
               {'QZXU0000014', 'QZXU0000349'} <= set(b['boxes'])
               for b in cases['segregation']['verdict']['broken'])
