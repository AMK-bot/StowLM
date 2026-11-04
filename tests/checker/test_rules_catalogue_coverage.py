"""Explicit catalogue-to-behaviour audit; missing evidence remains visible.

This inventory guards bookkeeping, not the truth of referenced assertions. The
referenced suites must also run. A documented gap is not passing/failing evidence.
"""

import ast
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from stowage.rules.catalogue import load_catalogue


@dataclass(frozen=True)
class Evidence:
    owner: str
    passing: str | None
    failing: str | None
    gap: str = ""
    supporting: tuple[str, ...] = ()


def ref(module, name):
    return f"tests/{module}.py::test_{name}"


def static(module, name):
    node = ref(f"checker/test_rules_{module}", name)
    return Evidence("2.4", node, node)


def physics(module, passing, failing):
    return Evidence("3.1", ref(f"checker/{module}", passing),
                    ref(f"checker/{module}", failing))


CORE = "checker/test_rules_core"
IMDG = "checker/test_rules_imdg"
MOVES = "moves/test_engine"
CRITERIA = "test_stability_criteria"
LOADING_GAP = "Add rule-specific acceptance assertions; report.ran alone does not establish absence of this violation."
ROTATION_GAP = (
    "Current-call absent/partial restows reject under move access IDs; exact D15 future counts are tested. "
    "APPROXIMATION: D15 contributes an abstract minimum forced-lift objective in its fixed-departure-stow "
    "scenario, not an executable future policy or a minimum over arbitrary executable plans. "
    "Future-sequence enforcement remains strict-xfail: stage B: executable future restows (10.4/11.2), D15.")


# Exact test functions, not broad file globs. A paired parameterized function
# appears in both columns because its passing/failing branches assert the rule.
# None is deliberate: do not count related numerical evidence as a rule outcome.
EVIDENCE = {
    "stability.open_hatch_immersion": Evidence(
        "3.1/3.2", None,
        ref("checker/test_strength_physics", "immersed_open_coaming_is_a_hard_physical_violation"),
        gap="Dry open-hatch acceptance evidence pending; immersed-lip rejection is covered."),
    "port.dg.refused_class": Evidence("L2b", None, None, gap="enforcement pending (layer L2b)"),
    "port.dg.direct_delivery": Evidence("L2b", None, None, gap="enforcement pending (layer L2b)"),
    "port.e3.quay_crossing": Evidence(
        "2.3/4.1", ref("moves/test_crane_contract", "ship_quay_crossing_for_every_container_lift"),
        ref("moves/test_crane_contract", "ship_quay_crossing_for_every_container_lift")),
    "port.e3.yard_crossing": Evidence(
        "2.3/4.1", ref("moves/test_yard_height_amendment", "yard_stack_at_its_own_limit_blocks_below_quay_H"),
        ref("moves/test_yard_height_amendment", "yard_stack_at_its_own_limit_blocks_below_quay_H")),
    "port.ballast.transfer_rate": Evidence(
        "4.1/4.2", ref("checker/test_in_port_programme", "t14_long_call_reports_exact_ceiling_and_both_window_endpoints"),
        ref("checker/test_full_in_port_programme", "t15_window_departure_with_worked_fsm_rejects_before_unchanged_k"),
        # These are arithmetic/path and replay-wiring contracts, not a claim
        # that every production vessel passes its physical T4 programme.
        supporting=(
            ref("checker/test_full_in_port_programme", "t13_ballast_delay_is_reported_without_rate_rejection"),
            ref("checker/test_full_in_port_programme", "t16_anticipated_path_reevaluates_each_deviation"),
            ref("checker/test_in_port_programme", "t16_sudden_reversal_with_no_room_is_rate_failure"),
            ref("checker/test_in_port_programme", "t17_internal_transfer_is_counted_once"),
            ref("checker/test_full_in_port_programme", "d74_named_programme_survives_full_report_serialization"))),
    "objective.all_loaded": Evidence(
        "4.1", ref("checker/test_full", "public_exports_and_clean_gate"),
        ref("checker/test_full", "shutout_is_a_hard_gate_and_parts_still_run")),
    "checker.internal_error": Evidence(
        "4.1", ref("checker/test_full", "public_exports_and_clean_gate"),
        ref("checker/test_full", "part_exception_does_not_suppress_other_parts")),
    "checker.missing_vessel_data": Evidence(
        "4.1", ref("checker/test_full", "public_exports_and_clean_gate"),
        ref("checker/test_full", "unknown_instruction_cannot_silently_pass")),
    "checker.invalid_ballast": Evidence(
        "4.1", ref("checker/test_full", "public_exports_and_clean_gate"),
        ref("checker/test_full", "invalid_ballast_fails_closed_but_runs_physics")),
    "port.call.profile_resolution": Evidence(
        "2.3/4.1", ref("moves/test_profile_resolution", "load_profile_errors_are_hard_diagnostics"),
        ref("moves/test_profile_resolution", "load_profile_errors_are_hard_diagnostics")),
    "port.stability.cargo_operations": Evidence("L2b", None, None, gap="enforcement pending (layer L2b)"),
    "expert.physical.slot_occupancy": static("core", "slot_occupancy"),
    "expert.physical.size_fit": static("core", "size_fit"),
    "expert.physical.no_20_on_40": static("core", "no_20_on_40"),
    "expert.physical.stack_weight": static("core", "stack_weight"),
    "expert.physical.tier_height": static("core", "tier_height"),
    "expert.physical.reefer_plug": static("core", "reefer_plug"),
    "expert.rotation.no_overstow": Evidence(
        "2.3/4.1", ref("moves/test_catalogue_gaps_1028", "complete_required_restows_have_legal_supported_returns"),
        ref("moves/test_catalogue_gaps_1028", "absent_or_partial_required_restows_reject_discharge"),
        ROTATION_GAP),
    "expert.rotation.hatch_overstow": Evidence(
        "2.3/4.1", ref("moves/test_catalogue_gaps_1028", "complete_required_restows_have_legal_supported_returns"),
        ref("moves/test_catalogue_gaps_1028", "absent_or_partial_required_restows_reject_discharge"),
        ROTATION_GAP),
    "imdg.segregation.7_2_4": Evidence(
        "2.4", ref(IMDG, "general_segregation_pass"),
        ref(IMDG, "general_segregation_unknown_class_fail")),
    "imdg.segregation.7_4_3": Evidence(
        "2.4", ref(IMDG, "ship_segregation_level_pass"), ref(IMDG, "ship_segregation_level_fail")),
    "imdg.stowage.3_2": Evidence(
        "2.4", ref(IMDG, "stowage_category_pass"), ref(IMDG, "stowage_category_fail")),
    "iscode.loading.displacement": Evidence(
        "3.1", ref("checker/test_catalogue_gaps_1028", "loading_rule_accepts_condition_with_positive_margins"),
        ref("checker/test_catalogue_gaps_1028", "loading_rule_rejects_violated_endpoint")),
    "iscode.loading.draft": Evidence(
        "3.1", ref("checker/test_catalogue_gaps_1028", "loading_rule_accepts_condition_with_positive_margins"),
        ref("checker/test_catalogue_gaps_1028", "loading_rule_rejects_violated_endpoint")),
    "iscode.loading.gm": Evidence(
        "3.1", ref("checker/test_catalogue_gaps_1028", "loading_rule_accepts_condition_with_positive_margins"),
        ref("checker/test_catalogue_gaps_1028", "loading_rule_rejects_violated_endpoint")),
    "iscode.b3_1.free_surface": Evidence(
        "3.1", ref("checker/test_catalogue_gaps_1028", "free_surface_accepts_valid_tank_state"),
        ref("checker/test_catalogue_gaps_1028", "free_surface_rejects_invalid_tank_at_checker_entry")),
    "iscode.a2_2.gz_area_0_30": physics(
        CRITERIA, "all_part_a_pass_and_hand_areas", "part_a_each_lever_area_criterion_fails"),
    "iscode.a2_2.gz_area_0_40": physics(
        CRITERIA, "all_part_a_pass_and_hand_areas", "part_a_each_lever_area_criterion_fails"),
    "iscode.a2_2.gz_area_30_40": physics(
        CRITERIA, "all_part_a_pass_and_hand_areas", "part_a_each_lever_area_criterion_fails"),
    "iscode.a2_2.gz_at_30": physics(
        CRITERIA, "all_part_a_pass_and_hand_areas", "part_a_each_lever_area_criterion_fails"),
    "iscode.a2_2.max_gz_angle": physics(
        CRITERIA, "part_a_peak_angle_and_gm_fail_and_boundary_pass", "part_a_peak_angle_and_gm_fail_and_boundary_pass"),
    "iscode.a2_2.initial_gm": physics(
        CRITERIA, "part_a_peak_angle_and_gm_fail_and_boundary_pass", "part_a_peak_angle_and_gm_fail_and_boundary_pass"),
    "iscode.a2_3.weather": physics(
        CRITERIA, "weather_hand_work_and_all_factors", "weather_fails_large_wind_deck_edge_and_table_applicability"),
    "iscode.b2_3.containership_second_check": physics(
        CRITERIA, "container_form_factor_and_six_limits", "container_form_factor_and_six_limits"),
    "iscode.loading.trim": Evidence(
        "3.1", ref("checker/test_catalogue_gaps_1028", "loading_rule_accepts_condition_with_positive_margins"),
        ref("checker/test_catalogue_gaps_1028", "loading_rule_rejects_violated_endpoint")),
    "iscode.loading.list": Evidence(
        "3.1", ref("checker/test_catalogue_gaps_1028", "loading_rule_accepts_condition_with_positive_margins"),
        ref("checker/test_catalogue_gaps_1028", "loading_rule_rejects_violated_endpoint")),
    "iacs.s11a.longitudinal_strength": Evidence(
        "3.2", ref("checker/test_strength_conditions", "complete_fleet_condition_runs_deterministically_and_closes"),
        ref("checker/test_strength_conditions", "midship_overload_fails_sagging_limit")),
    "iacs.s1.loading_instrument": Evidence(
        "3.2", ref("checker/test_catalogue_gaps_1028", "s1_complete_required_station_readouts_without_applicable_torsion"),
        ref("checker/test_catalogue_gaps_1028", "s1_missing_one_required_readout_fails_closed")),
    "iacs.rec48.validation": Evidence(
        "3.2", None, None,
        "No matching independent approved/public ship inputs plus SF/BM/applicable torsion outputs found in local sources or primary public search (Rec48 section 2.5.1). Synthetic beam identities and calibrated ratios are not that reference; no production reference-comparison contract exists. See fix-catalogue-gaps-1028.md."),
    "iacs.l5.validation": Evidence(
        "2.1/3.1", ref("vessel/test_catalogue_gaps_1028", "l5_kcs_published_displacement_accepts_and_density_error_rejects"),
        ref("vessel/test_catalogue_gaps_1028", "l5_kcs_published_gm_accepts_and_beam_error_rejects"),
        "KCS displacement/draft/derived-GM tolerance acceptance and corrupted-model rejection now tested. D20/KCS lacks independent GZ, GZ-area, FSM, inclined-equilibrium and full loaded-condition KG references; no complete production reference comparator exists. Partial numerical evidence does not close the complete quantity set."),
    "solas.v22.visibility": Evidence(
        "3.3", ref("checker/test_visibility", "hand_computed_tangent_pass_and_fail"),
        ref("checker/test_visibility", "hand_computed_tangent_pass_and_fail")),
    "solas.vi2.verified_gross_mass": static("core", "verified_gross_mass"),
    "iacs.member_irs.container_securing": Evidence(
        "3.3", ref("checker/test_lashing", "light_on_top_passes_heavy_on_top_fails"),
        ref("checker/test_lashing", "light_on_top_passes_heavy_on_top_fails")),
    "expert.e6.heavy_under_deck": Evidence("L2b", None, None, gap="enforcement pending (layer L2b)"),
    "expert.e1.heavy_bottom": Evidence("L2b", None, None, gap="enforcement pending (layer L2b)"),
    "expert.e2.weight_order": Evidence("L2b", None, None, gap="enforcement pending (layer L2b)"),
    "expert.e8.under_deck": static("special", "e8_location_pass_fail"),
    "expert.e8.on_deck": static("special", "e8_location_pass_fail"),
    "expert.e8.away_from_heat": static("special", "e8_away_from_heat_pass_fail"),
    "expert.e8.top_stowage": static("special", "e8_top_stowage_pass_fail"),
    "expert.e9.over_height": Evidence("L2b", None, None, gap="enforcement pending (layer L2b)"),
    "expert.e9.over_width_length": Evidence("L2b", None, None, gap="enforcement pending (layer L2b)"),
    "port.e3.yard_height": Evidence(
        "2.3", ref(MOVES, "yard_rehandle_chain_has_exact_count_and_is_pure"),
        ref(MOVES, "effective_yard_tier_limit")),
    "port.e3.quay_height": static("special", "port_quay_height_pass_fail"),
    "port.call.draft": Evidence(
        "3.1", ref("checker/test_catalogue_gaps_1028", "loading_rule_accepts_condition_with_positive_margins"),
        ref("checker/test_catalogue_gaps_1028", "loading_rule_rejects_violated_endpoint")),
    "port.call.length": static("special", "port_length_pass_fail"),
    "expert.moves.top_access": Evidence(
        "2.3", ref(MOVES, "yard_rehandle_chain_has_exact_count_and_is_pure"),
        ref(MOVES, "yard_rehandle_chain_has_exact_count_and_is_pure")),
    "expert.moves.hatch_clear": Evidence(
        "2.3", ref(MOVES, "hold_load_requires_deck_restow_and_cover_lifts"),
        ref(MOVES, "hold_load_requires_deck_restow_and_cover_lifts")),
    "expert.moves.bottom_up": static("core", "bottom_up"),
    "iscode.stability.margin": Evidence(
        "4.6/4.7/4.8", None, None,
        "Soft preference is unimplemented: exact_engine.solve_engine prunes equal-cost terminals and declares tertiary_certified=False; scalable/twostep retain only lower move costs. No recorded normalised stability-margin objective exists to test. Equal-intake/equal-move preference and priority-preservation remain open."),
    "iacs.strength.margin": Evidence(
        "4.6/4.7/4.8", None, None,
        "Soft preference is unimplemented: exact_engine.solve_engine prunes equal-cost terminals and declares tertiary_certified=False; scalable/twostep retain only lower move costs. No recorded normalised strength-margin objective exists to test. Equal-intake/equal-move preference and priority-preservation remain open."),
}


# Frozen independently from live metadata so ownership/hardness changes force
# an explicit audit. This does not introduce an enforcement ownership change.
LINEAR = {
    "expert.physical.slot_occupancy", "expert.physical.size_fit", "expert.physical.stack_weight",
    "expert.physical.reefer_plug", "iscode.loading.displacement", "iscode.loading.gm",
    "expert.e6.heavy_under_deck", "expert.e2.weight_order", "port.call.draft", "port.call.length",
}
CHECKER_ONLY = {"iacs.s1.loading_instrument", "iacs.rec48.validation", "iacs.l5.validation",
                "checker.internal_error", "checker.missing_vessel_data", "checker.invalid_ballast"}
# Whole-plan requirements are not necessary conditions for a feasible prefix.
# D74 explicitly leaves the mask unchanged; full intake is checked at departure.
PLAN_LEVEL = {"port.ballast.transfer_rate", "objective.all_loaded"}
SOFT = {"iscode.stability.margin", "iacs.strength.margin"}
INHERITED = {"expert.e1.heavy_bottom", "expert.e2.weight_order"}


def assert_inventory(rules, evidence=EVIDENCE):
    assert set(evidence) == {rule.id for rule in rules}, "Catalogue coverage audit needs updating"
    for rule in rules:
        targets = {"solver"} if rule.id in SOFT else {"checker"}
        if rule.id in PLAN_LEVEL:
            targets.add("solver")
        elif rule.id not in SOFT | CHECKER_ONLY:
            targets |= {"feasibility_engine", "solver"}
        if rule.id in LINEAR:
            targets.add("linear_model")
        assert set(rule.enforced_by) == targets, f"Re-audit enforcement ownership: {rule.id}"
        hardness = "soft" if rule.id in SOFT else "tolerated_if_inherited" if rule.id in INHERITED else "hard"
        assert rule.hardness == hardness, f"Re-audit rule semantics: {rule.id}"
        item = evidence[rule.id]
        assert item.owner
        assert (item.passing and item.failing) or item.gap, f"Undocumented evidence gap: {rule.id}"


def test_every_catalogue_rule_has_an_explicit_evidence_disposition():
    assert_inventory(load_catalogue().rules)


def test_d74_plan_level_rate_evidence_names_every_t13_through_t17_contract():
    rule_id = "port.ballast.transfer_rate"
    evidence = EVIDENCE[rule_id]
    assert "4.1" in evidence.owner.split("/")
    assert rule_id in PLAN_LEVEL
    assert evidence.passing == ref("checker/test_in_port_programme",
        "t14_long_call_reports_exact_ceiling_and_both_window_endpoints")
    assert evidence.failing == ref("checker/test_full_in_port_programme",
        "t15_window_departure_with_worked_fsm_rejects_before_unchanged_k")
    supporting = getattr(evidence, "supporting", ())
    assert {
        ref("checker/test_full_in_port_programme",
            "t13_ballast_delay_is_reported_without_rate_rejection"),
        ref("checker/test_full_in_port_programme",
            "t16_anticipated_path_reevaluates_each_deviation"),
        ref("checker/test_in_port_programme", "t17_internal_transfer_is_counted_once"),
    } <= set(supporting)


def test_audit_detects_a_new_catalogue_rule():
    rules = load_catalogue().rules
    added = rules[0].model_copy(update={"id": "expert.physical.new_rule"})
    with pytest.raises(AssertionError, match="coverage audit"):
        assert_inventory([*rules, added])


def test_audit_detects_enforcement_ownership_change():
    rules = load_catalogue().rules
    changed = rules[0].model_copy(update={"enforced_by": ["solver"]})
    with pytest.raises(AssertionError, match="enforcement ownership"):
        assert_inventory([changed, *rules[1:]])


def test_audit_detects_missing_outcome_without_documented_gap():
    evidence = dict(EVIDENCE)
    rule = "expert.physical.slot_occupancy"
    evidence[rule] = Evidence("2.4", evidence[rule].passing, None)
    with pytest.raises(AssertionError, match="Undocumented evidence gap"):
        assert_inventory(load_catalogue().rules, evidence)


def assert_evidence_references(evidence=EVIDENCE):
    root = Path(__file__).resolve().parents[2]
    nodes = {node for item in evidence.values()
             for node in (item.passing, item.failing, *item.supporting) if node}
    for node in sorted(nodes):
        path, name = node.split("::")
        functions = {item.name for item in ast.walk(ast.parse((root / path).read_text()))
                     if isinstance(item, ast.FunctionDef)}
        assert name in functions, f"Stale catalogue evidence reference: {node}"


def test_audit_references_existing_test_functions():
    assert_evidence_references()


def test_audit_detects_stale_supporting_evidence():
    evidence = dict(EVIDENCE)
    rule_id = "port.ballast.transfer_rate"
    evidence[rule_id] = replace(evidence[rule_id], supporting=(
        ref("checker/test_in_port_programme", "missing_supporting_contract"),))
    with pytest.raises(AssertionError, match="Stale catalogue evidence reference"):
        assert_evidence_references(evidence)
