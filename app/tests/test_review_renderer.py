"""Regression scenarios from wave-2 demo logic review (presentation only)."""
from copy import deepcopy
from pathlib import Path

import pytest

from app.tests.test_renderer import layover_replay, render_page


def test_review_meta_identity_is_always_visible_without_placeholder_claim(layover_replay):
    page = render_page(layover_replay[0])
    html = Path('app/static/index.html').read_text()
    assert 'StowLM = Muse Glimmer 30B + LoRA (MLX), decoded under a feasibility mask; the checker decides' in html
    assert page['model-run-state']['text'] == 'no Glimmer run imported yet'
    assert 'Fewer moves. Clearer decisions.' not in html


def test_review_decode_trace_shows_pre_mask_preferences_and_escapes_text(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['metadata']['model_run'] = dict(verified=True, label='Base Muse Glimmer 30B + feasibility mask',
        trace=[dict(step=0, n_legal=2, n_actions=8, chosen=dict(action_id=2, text='load <box>'),
                    top_k=[dict(text='illegal <lift>', legal=False, logprob=-.1), dict(text='load <box>', legal=True, logprob=-.4)])])
    page = render_page(replay)
    assert 'Base Muse Glimmer' in page['model-run-state']['text']
    markup = page['decode-trace']['html']
    assert '2 / 8 legal actions' in markup
    assert 'load &lt;box&gt;' in markup
    assert '<s>illegal &lt;lift&gt;</s>' in markup
    assert 'before masking' in markup


def test_review_unverified_model_import_never_gets_stowlm_identity(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['metadata'].update(label='StowLM', planner='stowlm', model_run=dict(verified=False, label='StowLM'))
    page = render_page(replay)
    assert page['model-run-state']['text'] == 'Imported (unverified source)'
    assert page['planner-name']['text'] == 'Imported (unverified source)'


def test_review_repaired_badge_names_solver_and_blocks_comparison_approval(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['metadata'].update(repaired=True, repairing_solver='solver.scalable', repair_status='ok')
    side = dict(label='Two-step baseline', shippable=True, checker_passed=True, repaired=True,
                repairing_solver='solver.scalable', shut_out=[])
    comparison = dict(left=side, right=side, rows=[], comparable=True, saved_objective=4)
    page = render_page(replay, comparison=comparison)
    assert 'REPAIRED' in page['repair-warning']['text']
    assert 'solver.scalable' in page['repair-warning']['text']
    markup = page['comparison-content']['html']
    assert 'No approved winner' in markup
    assert 'Repaired' in markup
    assert 'DEPARTURE APPROVED' not in markup


def test_review_exact_feasible_is_not_presented_as_proven_optimal(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['metadata'].update(planner='solver.exact', label='Exact solver', optimality_status='FEASIBLE')
    page = render_page(replay)
    assert page['planner-name']['text'] == 'Exact (not proven optimal)'


def test_review_synthetic_port_header_and_h_source_follow_instance(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['instance']['port'].update(archetype='singapore_like', max_crane_height_tiers=7, berthing_side='port')
    page = render_page(replay)
    assert 'Singapore-like (synthetic)' in page['terminal-label']['text']
    assert 'synthetic' in page['crane-height']['text']
    assert 'THAILAND' not in page['terminal-label']['text']
    replay['instance']['port']['archetype'] = 'laem_chabang_like'
    page = render_page(replay)
    assert 'expert Q3' in page['crane-height']['text']
    assert 'Laem Chabang-like (synthetic)' in page['legend']['html']


def test_review_crane_geometry_draws_h_quay_lane_and_orients_rows(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['instance']['port'].update(max_crane_height_tiers=2, berthing_side='starboard')
    replay['instance']['port']['yard']['blocks'][0]['truck_lane_side'] = 'high'
    starboard = render_page(replay)
    assert 'crane-height-line' in starboard['bay']['html']
    assert 'QUAY · STARBOARD' in starboard['bay']['html']
    assert 'truck-lane' in starboard['yard']['html']
    assert 'STARBOARD' in starboard['row-orientation']['text']
    replay['instance']['port']['berthing_side'] = 'port'
    port = render_page(replay)
    assert starboard['bay']['html'] != port['bay']['html']
    assert 'PORT' in port['row-orientation']['text']


def test_review_h_high_crossing_uses_geometry_and_tags_reported_shift(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['instance']['port'].update(max_crane_height_tiers=1, berthing_side='starboard')
    replay['frames'][1]['move'].update(reason='crane_height_shift')
    replay['frames'][1]['highlight']['crane_height_crossing'] = {'blocking_stack_ids':['00101'], 'source':'engine'}
    page = render_page(replay, index=1)
    assert 'crane-height shift' in page['move-label']['text']
    assert 'crane-crossing' in page['bay']['html']
    assert 'engine' in page['crossing-source']['text']


def test_review_inport_stability_pending_is_visible(layover_replay):
    page = render_page(layover_replay[0], view='checker')
    assert 'not yet checked during cargo operations (D64 pending)' in page['port-stability']['html']
    html = Path('app/static/index.html').read_text()
    assert 'departure, mid-voyage, arrival' in html


def test_review_inport_stability_shows_per_lift_and_changeover(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['checker']['port_stability'] = dict(available=True, passed=False, ballast_changeover_k=2,
        per_lift=[dict(move_index=1, passed=False, gm_m=.12)], metrics={}, violations=[])
    page = render_page(replay, view='checker')
    markup = page['port-stability']['html']
    assert 'Ballast changeover k = 2' in markup
    assert 'Lift 2' in markup and 'FAIL' in markup
    assert 'D64 pending' not in markup


def test_review_unbound_inport_evidence_is_never_displayed(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['checker'].update(bound=False, port_stability=dict(available=True, per_lift=[{'secret':'unbound-lift'}]))
    page = render_page(replay, view='checker')
    assert 'unbound-lift' not in page['port-stability']['html']
    assert 'Unbound report' in page['port-stability']['html']


def test_review_comparison_context_and_future_delta(layover_replay):
    replay = deepcopy(layover_replay[0])
    side = dict(label='Plan', shippable=False, checker_passed=False, shut_out=[], explanation='Checked facts')
    comparison = dict(left=side, right=side, rows=[], comparable=False, saved_objective=None, projected_lift_difference=3)
    page = render_page(replay, comparison=comparison)
    html = Path('app/static/index.html').read_text()
    assert '1 instance, illustrative' in html
    assert 'Stored Muse Spark explanations' in html and 'No live Spark calls' in html
    markup = page['comparison-content']['html']
    assert 'Projected later-port lift difference' in markup and '3' in markup
    assert "two-step baseline = a simple two-step planner, modelled from one field expert's description (E10)" in markup
    assert "Navis and human-planner performance aren't public" in markup


def test_review_raw_clearance_is_not_rounded_into_pass(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['checker']['violations'] = [dict(rule_id='height', severity='hard', message='Low clearance', margin=-.000000123456789)]
    page = render_page(replay, view='checker', panel='violations')
    assert '-1.23456789e-7' in page['checker-content']['html']


def test_review_crossing_geometry_follows_baplie_starboard_and_uses_before_state(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['instance']['port'].update(max_crane_height_tiers=1, berthing_side='starboard')
    # Odd rows are starboard: row03 lies between quay and target row01.
    replay['frames'][1]['move'] = dict(kind='load', src='truck', dst='0010182', container_id='test')
    replay['frames'][0]['state']['ship']['placements'].append(dict(container_id='blocker', slot='0010382'))
    # Add a real geometry cell so its outline can be drawn.
    cell = deepcopy(replay['instance']['vessel']['cells'][0]); cell['id'] = '0010382'
    replay['instance']['vessel']['cells'].append(cell)
    page = render_page(replay, index=1)
    assert 'crane-crossing' in page['bay']['html']
    assert 'Instance geometry only' in page['crossing-source']['text']
    assert 'not a move-engine crossing verdict' in page['crossing-source']['text']


def test_review_port_label_uses_model_locode_not_profile_guess(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['instance']['port'].update(archetype='custom-fixture-port', locode='SGSIN')
    assert 'Singapore-like (synthetic)' in render_page(replay)['terminal-label']['text']


def test_review_per_lift_recorded_index_is_not_replaced_by_array_order(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['checker']['port_stability'] = dict(available=True, passed=True, ballast_changeover_k=0,
        per_lift=[dict(index=4, metrics={'gm_m':.7})], violations=[])
    page = render_page(replay, view='checker')
    assert 'Lift 4' in page['port-stability']['html']


@pytest.mark.parametrize('archetype,label,source', [
    ('laem_chabang_like', 'Laem Chabang-like (synthetic)', 'expert Q3'),
    ('singapore_like', 'Singapore-like (synthetic)', 'synthetic'),
    ('us_west_coast', 'US West Coast-like (synthetic)', 'synthetic'),
])
@pytest.mark.parametrize('enabled', [False, True], ids=['freeze-off', 'freeze-on'])
def test_review_real_port_profile_archetype_drives_header_and_h(layover_replay, archetype, label, source, enabled):
    from stowage.ports import load_port
    from stowage.rules.catalogue import rule_freeze_v1

    replay = deepcopy(layover_replay[0])
    with rule_freeze_v1(enabled=enabled):
        replay['instance']['port'] = load_port(archetype).model_dump(mode='json')
    page = render_page(replay)
    assert page['terminal-label']['text'] == label
    if archetype == 'laem_chabang_like':
        expected = ('H = 8 tiers · expert Q3' if enabled else
                    'H unavailable · legacy quay limit 7 tiers')
        assert page['crane-height']['text'] == 'CRANE HEIGHT · ' + expected
    else:
        assert source in page['crane-height']['text']


def test_review_yard_h_high_crossing_between_source_and_truck_lane(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['instance']['port'].update(max_crane_height_tiers=2, berthing_side='port')
    replay['instance']['port']['yard']['blocks'][0]['truck_lane_side'] = 'high'
    next(block for block in replay['instance']['port']['yard']['blocks'] if block['id'] == 'B')['truck_lane_side'] = 'high'
    replay['frames'][1]['move'] = dict(kind='load', src='B-001-01-01', dst='0010182', container_id='test')
    replay['frames'][0]['state']['yard']['stacks'].append(dict(key='B-001-02', boxes=['blocking1','blocking2']))
    replay['frames'][1]['state']['yard']['stacks'].append(dict(key='B-001-02', boxes=['blocking1','blocking2']))
    page = render_page(replay, index=1)
    assert 'yard-crossing' in page['yard']['html']


def test_review_expert_h_source_does_not_spread_to_other_thlch_archetypes(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['instance']['port'].update(archetype='custom_terminal', locode='THLCH', max_crane_height_tiers=5)
    page = render_page(replay)
    assert 'synthetic' in page['crane-height']['text']
    assert 'expert Q3' not in page['crane-height']['text']


def test_review_repair_note_visible_in_replay_and_comparison(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay['metadata'].update(repaired=True, repairing_solver='solver.scalable', repair_status='ok',
                              repair_note='Completed missing exports after invalid original plan')
    side = dict(replay['metadata'], checker_passed=True, shippable=True, shut_out=[])
    comparison = dict(left=side, right=side, rows=[], comparable=False, saved_objective=None)
    page = render_page(replay, comparison=comparison)
    assert 'Completed missing exports' in page['repair-warning']['text']
    assert 'Completed missing exports' in page['comparison-content']['html']
