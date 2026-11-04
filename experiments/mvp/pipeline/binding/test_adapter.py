"""Independent release and authentication gates; no timed optimisation here."""
from dataclasses import replace
import json
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.binding import adapter
from experiments.mvp.pipeline.binding import confirm_brief
from experiments.mvp.pipeline.binding.test_hooks import fixture_instance
from experiments.mvp.reader import public_brief
from experiments.mvp.reader.schema import OnePortPerHatch
from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers, Refusal
from stowage.actions import get_action_space
from stowage.actions.space import Place


RULES = [CloseBay(type='close_bay', bay=1),
         MaxTiers(type='max_tiers', zone='deck', max_tiers=1),
         DgKeepAway(type='dg_keep_away', imdg_class='3', min_bays=1,
                   away_from={'kind': 'bay', 'bay': 1}),
         OnePortPerHatch(type='one_port_per_hatch')]


@pytest.fixture
def cheap(monkeypatch):
    monkeypatch.setattr(adapter, 'wait_for_capacity', lambda: {'admitted': True})
    # These release seam tests intentionally use minimal stand-in reports.
    # Native provenance rejection is covered separately with real artifacts.
    monkeypatch.setattr(adapter, 'require_clean', lambda *args, **kwargs: None)


def _receipt(instance, *answers):
    empty = Refusal(code='not_an_instruction', reason='Empty standing text')
    readings = iter([empty, *answers])
    parser = NS(parse=lambda *_: (next(readings), {'parser': 'binding-test', 'adapter_sha256': None}))
    return public_brief.read_brief(instance, public_brief.VoyageBrief('', tuple('sentence '+str(i)
                    for i in range(len(answers)))), parser=parser)


def _plan(instance):
    # Expands a real complete move replay; the full-checker decision is controlled
    # only in release seam tests below, independent of engine claims.
    return get_action_space('boxslot').expand(instance, [Place(b, slot) for b, slot in
        zip(instance.load_list, ('0010102', '0030102', '0010202', '0030202'))])


def _verdict(passed, *, level=0, broken=()):
    report = NS(passed=passed, count=NS(weighted_nonproductive=7.),
                model_dump=lambda **_: {'passed': passed, 'violations': list(broken)})
    row = dict(level=level, column='T'+str(level), lifts=7., broken_rules=list(broken),
               boxes_left_ashore=[], completed=True)
    return report, row


class Candidate(NS):
    def model_copy(self, *, update):
        return Candidate(**(vars(self) | update))
    def _record_full_check(self, instance, report):
        self.full_check_recorded = bool(report.passed)


@pytest.mark.parametrize('answer', RULES, ids=lambda a: a.type)
@pytest.mark.parametrize('lane', ['page', 'assignment-lp', 'peel_ladder'])
def test_no_lane_can_release_a_plan_failing_instruction_recheck(monkeypatch, cheap, answer, lane):
    instance = fixture_instance()
    plan = _plan(instance)
    compiled = NS(instance=instance, forbids=lambda *_: None,
                  violations=lambda _: ['confirmed instruction violated'])
    monkeypatch.setattr(adapter, 'compile_constraint', lambda *a, **kw: (compiled, {}))
    report, row = _verdict(True)
    monkeypatch.setattr(adapter, 'check', lambda original, proposed: report)
    monkeypatch.setattr(adapter, 'inspect', lambda *args: row)
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda original, **kw:
        (Candidate(status='ok', plan=plan), {'passed': True, 'winner_lane': lane}))
    result, evidence = adapter.plan_with_rules(instance, [answer], budget_s=0)
    assert result is None and evidence['status'] == 'no_checked_plan'
    assert not evidence['passed'] and not evidence['plan_released']
    assert evidence['rules'][0]['instruction_recheck'] == {
        'passed': False, 'violations': ['confirmed instruction violated']}


def test_full_checker_runs_on_original_input_and_overrules_engine(monkeypatch, cheap):
    instance = fixture_instance()
    plan = _plan(instance)
    seen = []
    report, row = _verdict(False)
    def check(original, proposed):
        seen.append((original, proposed))
        return report
    monkeypatch.setattr(adapter, 'check', check)
    monkeypatch.setattr(adapter, 'inspect', lambda *args: row)
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda original, **kw:
        (Candidate(status='ok', plan=plan), {'passed': True, 'winner_lane': 'page'}))
    result, evidence = adapter.plan_with_rules(instance, [], budget_s=0)
    assert seen and all(original is instance and proposed is plan for original, proposed in seen)
    assert result is None and evidence['status'] == 'no_checked_plan'
    assert not evidence['passed']


def test_checked_improved_plan_streams_once_and_is_released_with_complete_evidence(monkeypatch, cheap):
    import stowage.checker as checker
    instance = fixture_instance()
    plan = _plan(instance)
    report, row = _verdict(True)
    def full_check(original, proposed):
        assert original is instance and proposed is plan
        return report
    monkeypatch.setattr(checker, 'check', full_check)
    monkeypatch.setattr(adapter, 'check', full_check)
    monkeypatch.setattr(adapter, 'inspect', lambda *args: row)
    def run(original, **kwargs):
        checker.check(original, plan)
        checker.check(original, plan)  # An unchanged checked plan is not an improvement.
        return Candidate(status='ok', plan=plan), {'passed': True, 'winner_lane': 'page',
                                                 'stages': {'pages': {'attempted': True}}}
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', run)
    events = []
    result, evidence = adapter.plan_with_rules(instance, [], budget_s=0, on_incumbent=events.append)
    assert result.full_check_recorded
    assert evidence['status'] == 'checked_plan' and evidence['passed']
    assert evidence['checker']['passed'] and evidence['instruction_recheck_passed']
    assert evidence['broken_rules'] == [] and evidence['boxes_ashore'] == []
    assert evidence['engine_version'] == '0.13.0' and evidence['engine_manifest_sha256']
    assert evidence['lane'] == 'page' and evidence['stages']['pages']['attempted']
    assert len(events) == 1 and events[0]['passed']
    assert events[0]['status'] == 'checked_plan' and events[0]['lifts'] == 7.


@pytest.mark.parametrize('violates_instruction', [False, True])
def test_ladder_names_broken_rules_but_cannot_peel_instructions(monkeypatch, cheap, violates_instruction):
    instance = fixture_instance()
    plan = _plan(instance)
    compiled = NS(instance=instance, forbids=lambda *_: None,
                  violations=lambda _: ['instruction'] if violates_instruction else [])
    monkeypatch.setattr(adapter, 'compile_constraint', lambda *a, **kw: (compiled, {}))
    broken = ['expert.e2.weight_order']
    report, row = _verdict(False, level=2, broken=broken)
    monkeypatch.setattr(adapter, 'check', lambda *args: report)
    monkeypatch.setattr(adapter, 'inspect', lambda *args: row)
    details = {'stages': {'peel_seam': {'relaxed_plan': plan.model_dump(mode='json')}}}
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda *a, **kw: (None, details))
    result, evidence = adapter.plan_with_rules(instance, [RULES[0]], budget_s=0)
    if violates_instruction:
        assert result is None and evidence['status'] == 'no_checked_plan'
    else:
        assert result is not None and evidence['status'] == 'relaxed_plan'
        assert evidence['broken_rules'] == broken and evidence['level'] == 2
        assert evidence['instruction_recheck_passed']
    assert not evidence['passed']


def test_jointly_impossible_individually_feasible_rules_stop_before_engine(monkeypatch, cheap):
    instance = fixture_instance()
    calls = []
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda *a, **kw: calls.append(True))
    result, evidence = adapter.plan_with_rules(instance, [CloseBay(type='close_bay', bay=1),
                                                         CloseBay(type='close_bay', bay=3)], budget_s=0)
    assert result is None and evidence['status'] == 'constraint_unavailable'
    assert '0 remain open' in evidence['reason']
    assert calls == []


def test_no_physical_footprint_fails_before_search(monkeypatch, cheap):
    instance = fixture_instance()
    calls = []
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda *a, **kw: calls.append(True))
    answer = DgKeepAway(type='dg_keep_away', imdg_class='3', min_bays=20,
                       away_from={'kind': 'bay', 'bay': 1})
    result, evidence = adapter.plan_with_rules(instance, [answer], budget_s=0)
    assert result is None and evidence['status'] == 'constraint_unavailable'
    assert 'no slot left' in evidence['reason'] or 'footprint' in evidence['reason']
    assert calls == []


@pytest.mark.parametrize('field', ['parsed', 'sentence', 'parser', 'role', 'status', 'binds'])
def test_tampered_authenticated_receipt_never_reaches_planner(monkeypatch, field):
    instance = fixture_instance()
    receipt = _receipt(instance, RULES[0])
    payload = json.loads(receipt.payload)
    payload['sentences'][1][field] = 'tampered'
    forged = replace(receipt, payload=json.dumps(payload))
    monkeypatch.setattr(adapter, 'plan_with_rules', lambda *a, **kw: pytest.fail('planner reached'))
    with pytest.raises(ValueError, match='authenticated reader-produced'):
        adapter.execute_brief(instance, forged)


def test_unresolved_voyage_requires_confirmation_before_any_compilation(monkeypatch):
    instance = fixture_instance()
    receipt = _receipt(instance, RULES[0], Refusal(code='ambiguous', reason='Which bay?'))
    monkeypatch.setattr(adapter, 'plan_with_rules', lambda *a, **kw: pytest.fail('planner reached'))
    result, evidence = adapter.execute_brief(instance, confirm_brief(receipt, 'test_host_unresolved_review'))
    assert result is None and evidence['status'] == 'needs_confirmation'
    assert evidence['unresolved_sentence_indices'] == [2]
    assert not evidence['passed']


def test_execute_rebuilds_only_authenticated_voyage_readings(monkeypatch):
    instance = fixture_instance()
    receipt = _receipt(instance, RULES[0], RULES[1])
    seen = []
    def plan(original, answers, **kwargs):
        seen.extend(answers)
        assert original is instance
        return None, {'rules': [{}, {}]}
    monkeypatch.setattr(adapter, 'plan_with_rules', plan)
    # Mutating the inspection copy cannot change the authenticated readings.
    receipt.evidence()['sentences'].clear()
    _, evidence = adapter.execute_brief(instance, confirm_brief(receipt, 'test_host_approval'))
    assert seen == RULES[:2]
    assert evidence['confirmation_source'] == 'test_host_approval'
    assert [row['source_sentence'] for row in evidence['rules']] == ['sentence 0', 'sentence 1']


@pytest.mark.parametrize('invalid', [[{'type': 'close_bay', 'bay': 1}], {'type': 'close_bay'}, 'close bay 1'])
def test_plan_api_requires_validated_reader_objects(cheap, invalid):
    with pytest.raises(TypeError, match='validated reader schema objects'):
        adapter.plan_with_rules(fixture_instance(), invalid, budget_s=0)
