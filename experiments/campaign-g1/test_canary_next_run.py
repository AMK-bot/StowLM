"""The next-run procedure must preserve raw canaries and D74's mask boundary."""
import json
from pathlib import Path

HERE=Path(__file__).parent


def procedure():
    document=(HERE/'D74-D75-PROCEDURE.md').read_text()
    return json.loads(document.split('```json\n',1)[1].split('\n```',1)[0])


def test_next_run_waits_for_both_decisions_and_uses_a_fresh_diagnostic_epoch():
    plan=procedure()
    assert set(plan['requires_landed'])=={'D74','D75'}
    assert plan['fresh_source_and_cache_snapshot'] is True
    assert plan['diagnostic'] is True and plan['evidence_eligible'] is False
    assert plan['c2']=='off_until_linear_requalified'


def test_d74_checks_rate_defects_and_feasible_controls_without_a_mask_waiver():
    plan=procedure()
    assert set(plan['d74']['cases'])=={'short_excessive_transfer_reject','infeasible_heel_steps_reject',
        'long_call_feasible_accept','anticipated_pumping_feasible_accept','window_endpoints_and_arithmetic'}
    assert plan['d74']['mask_must_deny_pump_rate_defects'] is False
    assert plan['raw_canaries']['deny_requires_its_declared_mask_reason'] is True


def test_d75_transforms_only_the_pipeline_comparison_and_preserves_raw_defects():
    plan=procedure()
    assert plan['d75']['pipeline']==['original_plan','shared_resequencer','ballast_finisher','full_checker']
    assert {'same_move_multiset','same_endpoints','same_count','engine_valid_precedence','deterministic',
        'switch_off_no_op'}<=set(plan['d75']['invariants'])
    assert {'list_margin_before_after','in_port_pass_rate_before_after','lifts_moved','runtime'}<=set(plan['d75']['report'])
    assert plan['raw_canaries']['resequence_before_check'] is False


def test_next_run_compares_checker_and_mask_on_identical_expanded_moves():
    plan=procedure()
    assert plan['raw_canaries']['checker_mask_same_expanded_moves'] is True
    assert plan['raw_canaries']['manual_endpoint_edit_is_mask_admission_bug'] is False
    assert plan['raw_canaries']['endpoint_regression']=='tests/feasibility/test_direct_delivery_canary.py'


def test_landed_dg_contexts_are_inputs_awaiting_slice_qualification_not_missing():
    plan=procedure()
    context=plan['dg_contexts']
    assert context['manifest']=='canary-inputs/dg-contexts/requests.json'
    assert context['status']=='pending_qualification'
    requests=json.loads((HERE/context['manifest']).read_text())
    assert set(context['requests'])==set(requests['requests'])
    assert set(context['requests'])=={'SW1','SW2','H1','SW19','SG17','SG25_2_1','SG25_3'}
    assert context['materializer']==requests['materializer']
    assert context['qualify_after_context_materialization'] is True
    assert context['external_export_contract']==requests['external_export_contract']
    assert context['context_only_is_evidence'] is False
