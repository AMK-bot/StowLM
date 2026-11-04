"""Actual proposal iterations, not outer controller labels, establish ladder work."""
from copy import deepcopy
import json
from pathlib import Path

from experiments.mvp.pipeline.runner.evidence import (
    LIVE_UNSEARCHED_LADDER_MESSAGE, normalize_ladder_work,
)
from experiments.mvp.pipeline.runner.test_runner import fallback_package, gates, packet


def trial(level, attempts, *, partial=None):
    search = {'proposal': {'trials': [{'attempt': n} for n in range(attempts)]}}
    if partial is not None:
        search['partial_search'] = {'proposal': {'trials': [{'attempt': n} for n in range(partial)]}}
    return dict(level=level, status=f'no completed plan found at L{level} within this work', search=search)


def evidence(rows):
    seam = dict(status='no plan found at L5 within this work', trials=[{'level': 0}, *rows],
                searched_level=5, budgets={'proposal_s': 20., 'finish_s': 10., 'max_candidates': 1},
                best_safe_partial=dict(searched_level=5, status='no completed plan found at L5 within this work'),
                completed=None)
    return {'status': 'relaxed_plan', 'passed': False, 'stages': {'peel_seam': seam},
            'engine': {'stages': {'peel_seam': deepcopy(seam)}}}


def test_live_empty_assignment_loops_have_exact_warning_and_no_search_claim():
    original = evidence([trial(level, 0, partial=0 if level == 3 else None) for level in range(1, 6)])
    snapshot = deepcopy(original)
    normalized = normalize_ladder_work(original, profile='live')
    assert original == snapshot
    assert normalized['ladder_work'] == 'none'
    assert normalized['ladder_trials'] == 0
    assert normalized['searched_levels'] == []
    assert normalized['unsearched_levels'] == [1, 2, 3, 4, 5]
    assert normalized['budget_skipped_levels'] == [1, 2, 3, 4, 5]
    assert normalized['unsearched_partial_levels'] == [3]
    assert normalized['ladder_message'] == "L1-L3 weren't searched on the live profile. This is the best candidate already found."
    for seam in (normalized['stages']['peel_seam'], normalized['engine']['stages']['peel_seam']):
        assert seam['ladder_message'] == LIVE_UNSEARCHED_LADDER_MESSAGE
        assert seam['searched_level'] is None and seam['reported_searched_level'] == 5
        assert 'within this work' not in seam['status']
        assert 'within this work' in seam['reported_status']
        assert seam['best_safe_partial']['ladder_work'] == 'none'
        assert 'within this work' not in seam['best_safe_partial']['status']
        for row in seam['trials'][1:]:
            assert row['ladder_work'] == 'none'
            assert 'within this work' not in row['status']
    assert normalize_ladder_work(normalized, profile='live') == normalized
    json.dumps(normalized, allow_nan=False)


def test_only_nonempty_proposal_trials_count_including_l3_partial():
    normalized = normalize_ladder_work(evidence([trial(1, 1), trial(2, 0), trial(3, 0, partial=2)]), profile='live')
    assert normalized['ladder_work'] == 'searched'
    assert normalized['ladder_trials'] == 3  # duplicate engine/stages views are not double-counted
    assert normalized['searched_levels'] == [1, 3]
    assert normalized['unsearched_levels'] == [2, 4, 5]
    assert normalized['budget_skipped_levels'] == [2]
    assert normalized['unsearched_partial_levels'] == []
    assert normalized['ladder_message'] != LIVE_UNSEARCHED_LADDER_MESSAGE
    assert normalized['stages']['peel_seam']['searched_level'] == 3


def test_main_l3_work_does_not_claim_that_partial_loop_ran():
    normalized = normalize_ladder_work(evidence([trial(1, 1), trial(2, 1), trial(3, 1, partial=0)]), profile='live')
    assert normalized['ladder_work'] == 'searched'
    assert normalized['searched_levels'] == [1, 2, 3]
    assert normalized['unsearched_partial_levels'] == [3]
    assert normalized['ladder_message'] == 'L3 partial search was not run within its proposal allowance.'
    partial = normalized['stages']['peel_seam']['trials'][3]['search']['partial_search']
    assert partial['ladder_work'] == 'none' and partial['ladder_trials'] == 0


def test_missing_proposal_details_are_unknown_even_if_controller_says_searched():
    normalized = normalize_ladder_work(evidence([dict(level=1, candidates=2, search={})]), profile='live')
    assert normalized['ladder_work'] == 'unknown'
    assert normalized['unknown_levels'] == [1]
    assert normalized['ladder_trials'] == 0
    assert 'ladder_message' not in normalized


def test_l0_pass_without_ladder_is_not_applicable_and_never_a_budget_warning():
    normalized = normalize_ladder_work({'status': 'checked_plan', 'passed': True, 'level': 0}, profile='live')
    assert normalized['ladder_work'] == 'none'
    assert normalized['ladder_not_applicable'] is True
    assert normalized['unsearched_levels'] == []
    assert 'ladder_message' not in normalized


def test_existing_candidates_only_is_not_new_ladder_search():
    normalized = normalize_ladder_work({'stages': {'peel_seam': {'status': 'existing_witnesses_only'}}}, profile='live')
    assert normalized['ladder_work'] == 'none'
    assert normalized['searched_levels'] == []
    assert normalized['unsearched_levels'] == [1, 2, 3, 4, 5]
    assert normalized['budget_skipped_levels'] == []
    assert normalized['ladder_message'] != LIVE_UNSEARCHED_LADDER_MESSAGE


def test_zero_loop_standard_profile_does_not_claim_live_profile_policy():
    normalized = normalize_ladder_work(evidence([trial(level, 0) for level in range(1, 6)]), profile='standard')
    assert normalized['ladder_work'] == 'none'
    assert normalized['ladder_message'] != LIVE_UNSEARCHED_LADDER_MESSAGE


def test_recorded_short_probe_has_no_actual_new_tier_search():
    path = Path(__file__).with_name('fixtures') / 'relaxed-review/initial-short-budget-run/outcome.json'
    original = json.loads(path.read_text())
    normalized = normalize_ladder_work(original['evidence'], profile={'name': 'live'})
    assert normalized['ladder_work'] == 'none' and normalized['ladder_trials'] == 0
    assert normalized['ladder_message'] == LIVE_UNSEARCHED_LADDER_MESSAGE
    assert original['evidence']['stages']['peel_seam']['trials'][1]['search']['proposal']['trials'] == []


def test_missing_calibration_fallback_enables_nominal_l3_partial_work(monkeypatch, tmp_path):
    from experiments.mvp.pipeline.runner import profiles
    from experiments.mvp.pipeline.runner.profile_study import study_profile
    monkeypatch.setattr(profiles, 'CALIBRATION', tmp_path / 'no-selection.json')
    live = profiles.get_profile('live')
    assert live['config'].peel_proposal_s == 70.
    assert live['engine_timeout_s'] == 720.
    assert live['pending_measurement'] is True
    assert live['peel_budget_choice']['measurement'] == 'projection'
    assert profiles.budget_profile(180.)['config'].peel_proposal_s == 20.
    assert study_profile('60')['config']['peel_proposal_s'] == 20.


def test_leader_selected_live_uses_studied_ladder_allowance_without_runtime_floor():
    from experiments.mvp.pipeline.runner import profiles
    from experiments.mvp.pipeline.runner.profile_study import study_profile
    live = profiles.get_profile('live')
    selected = study_profile('120')
    assert live['budget_s'] == 120. and live['finalize_s'] == 40.
    assert live['config'].peel_proposal_s == selected['config']['peel_proposal_s'] == 40.
    assert live['config'].peel_finalize_s == selected['config']['peel_finalize_s'] == 20.
    assert live['config'].assignment_budget_s == 120.
    assert live['config'].assignment_finalize_s == 40.
    assert live['engine_timeout_s'] == 720.
    assert live['measurement_status'] == 'leader_selected_from_partial_study'
    assert 'pending_measurement' not in live and 'peel_budget_choice' not in live
    assert profiles.budget_profile(180.)['config'].peel_proposal_s == 20.
    assert study_profile('60')['config']['peel_proposal_s'] == 20.


def test_obsolete_measured_live_peel_allowance_gets_explicit_unmeasured_runtime_floor(monkeypatch):
    from experiments.mvp.pipeline.runner import profiles
    measured = dict(profiles.budget_profile(180.), name='live', pending_measurement=False,
                    engine_timeout_s=630., calibration_source='immutable-selection.json',
                    data_sha256='frozen-completed-row-data')
    before = deepcopy(measured)
    monkeypatch.setattr(profiles, 'measured_profile', lambda name: measured)
    live = profiles.get_profile('live')
    assert measured == before  # Selection and its measured config are not rewritten.
    assert live['config'].peel_proposal_s == 70.
    assert live['engine_timeout_s'] == 630.
    assert live['config'].assignment_budget_s == measured['config'].assignment_budget_s
    assert live['calibration_source'] == measured['calibration_source']
    assert live['data_sha256'] == measured['data_sha256']
    assert live['pending_measurement'] is True and live['measured_base_profile'] is True
    assert live['peel_budget_choice']['previous_proposal_s'] == 20.
    assert live['peel_budget_choice']['measurement'] == 'projection'
    assert 'ladder adjustment is unmeasured' in live['notes']


def test_core_displays_no_search_warning_for_empty_live_ladder_loops(packet, gates, monkeypatch, tmp_path):
    from experiments.mvp.pipeline.runner import core
    from experiments.mvp.pipeline.runner.test_runner import (
        Parser, assert_clear, bind, evidence as binding_evidence, plan_for, result_for, verdict,
    )
    broken = ['synthetic.class_limit']

    def execute(instance, receipt, **kwargs):
        plan = plan_for(instance, 'existing-relaxed-L4')
        gates.reports[plan.id] = verdict(False)
        gates.inspected[plan.id] = dict(level=4, broken_rules=broken, boxes_left_ashore=[],
            completed=True, lifts=7., t0_peeled=False)
        seam = dict(status='relaxed_assignment_ladder', level=4,
                    trials=[{'level': 0}, *[trial(level, 0, partial=0 if level == 3 else None)
                                           for level in range(1, 5)]],
                    budgets={'proposal_s': 20., 'finish_s': 10., 'max_candidates': 1})
        return result_for(plan), binding_evidence(status='relaxed_plan', passed=False,
            level=4, broken_rules=broken, stages={'peel_seam': seam})

    bind(monkeypatch, execute)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert_clear(outcome, 'relaxed_plan')
    assert outcome['level'] == 4 and outcome['passed'] is False
    assert outcome['ladder_work'] == outcome['evidence']['ladder_work'] == 'none'
    assert outcome['searched_levels'] == []
    assert outcome['evidence']['budget_skipped_levels'] == [1, 2, 3, 4]
    assert LIVE_UNSEARCHED_LADDER_MESSAGE in outcome['message']
    ladder = [event for event in outcome['events'] if event['stage'] == 'ladder' and event['kind'] == 'stage_end']
    assert len(ladder) == 1
    assert ladder[0]['data']['ladder_work'] == 'none'
    assert ladder[0]['data']['ladder_message'] == LIVE_UNSEARCHED_LADDER_MESSAGE


def portfolio(*mapping_rows, selected=1):
    mappings = [dict(index=index, status='relaxed_plan', stages=evidence(rows)['stages'])
                for index, rows in enumerate(mapping_rows, 1)]
    stages = deepcopy(mappings[selected-1]['stages'])
    return dict(status='relaxed_plan', selected_mapping_index=selected, stages=stages,
                engine={'stages': deepcopy(stages)}, hatch_mapping_trials=mappings)


def test_portfolio_selected_zero_loop_mapping_cannot_hide_another_mappings_search():
    zero = [trial(level, 0, partial=0 if level == 3 else None) for level in range(1, 4)]
    searched = [trial(1, 1), trial(2, 2), trial(3, 1, partial=1)]
    original = portfolio(zero, searched)
    snapshot = deepcopy(original)
    normalized = normalize_ladder_work(original, profile='live')
    assert original == snapshot
    assert normalized['ladder_work'] == 'searched'
    assert normalized['searched_levels'] == [1, 2, 3]
    assert normalized['unsearched_levels'] == [4, 5]
    assert normalized['ladder_trials'] == 5
    assert normalized['hatch_mapping_trials'][0]['ladder_work'] == 'none'
    assert normalized['hatch_mapping_trials'][1]['ladder_work'] == 'searched'
    assert normalized['stages']['peel_seam']['ladder_work'] == 'none'
    assert 'Hatch mapping 1 skipped L1, L2, L3' in normalized['ladder_message']
    assert 'Hatch mapping 2 searched L1, L2, L3' in normalized['ladder_message']
    assert LIVE_UNSEARCHED_LADDER_MESSAGE not in json.dumps(normalized)
    assert normalize_ladder_work(normalized, profile='live') == normalized


def test_portfolio_exact_live_warning_requires_known_empty_l1_l3_on_every_mapping():
    zero = [trial(level, 0, partial=0 if level == 3 else None) for level in range(1, 5)]
    original = portfolio(zero, zero)
    snapshot = deepcopy(original)
    normalized = normalize_ladder_work(original, profile='live')
    assert original == snapshot
    assert normalized['ladder_work'] == 'none'
    assert normalized['ladder_trials'] == 0
    assert normalized['ladder_message'] == LIVE_UNSEARCHED_LADDER_MESSAGE
    assert normalized['budget_skipped_levels'] == [1, 2, 3, 4]
    assert len(normalized['ladder_mapping_work']) == 2
    assert normalize_ladder_work(normalized, profile='live') == normalized
    unknown = deepcopy(original)
    unknown['hatch_mapping_trials'][1].pop('stages')
    uncertain = normalize_ladder_work(unknown, profile='live')
    assert uncertain['ladder_work'] == 'unknown'
    assert LIVE_UNSEARCHED_LADDER_MESSAGE not in json.dumps(uncertain)


def test_portfolio_trial_count_excludes_selected_root_and_engine_duplicates():
    original = portfolio([trial(1, 2)], [trial(1, 3)], selected=2)
    normalized = normalize_ladder_work(original, profile='live')
    assert normalized['ladder_trials'] == 5
    assert [row['ladder_trials'] for row in normalized['ladder_mapping_work']] == [2, 3]
    assert normalized['stages']['peel_seam']['ladder_trials'] == 3
    assert normalized['engine']['stages']['peel_seam']['ladder_trials'] == 3
    assert normalize_ladder_work(normalized, profile='live') == normalized
