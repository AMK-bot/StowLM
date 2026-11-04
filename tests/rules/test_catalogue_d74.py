"""D74 plan-level pumpability metadata; execution belongs to checker/finisher."""

from hashlib import sha256
import tomllib

import pytest

from stowage.rules.catalogue import (
    DEFAULT_CATALOGUE_PATH, catalogue_snapshot, get_rule, load_catalogue,
    rule_freeze_v1, rule_freeze_v1_enabled,
)

RULE_ID = "port.ballast.transfer_rate"
DAMEN_CF800_SPEC = (
    "https://www.marine-marchande.net/Voyage/Bruno/Henrike-Schepers/"
    "specification-container-feeders-800.pdf"
)
FRAMO_ANTI_HEELING = (
    "https://www.framo.com/media/azejuddh/framo-anti-heeling_system-brochure-small.pdf"
)


@pytest.mark.parametrize("name,amended_context", [
    ("ballast_pump_m3_h", "500/700/1000"),
    ("heel_transfer_m3_h", "600/1000/1500"),
    ("gross_moves_per_hour", "25 gross moves/h"),
    ("pre_operation_allowance_h", "A=1.0 h"),
    ("post_operation_allowance_h", "B=1.0 h"),
])
def test_d74_amendment_updates_only_labelled_profile_source_context(name, amended_context):
    parameter = get_rule(RULE_ID).parameters[name]
    assumption = parameter.provenance.assumption
    assert amended_context in assumption
    assert all(label in assumption for label in ("APPROXIMATION:", "synthetic", "UNVERIFIED", "Q23", "Q30"))
    assert "amendment" in parameter.provenance.clause.lower()
    # This catalogue pins context; numerical operating values still come from profiles.
    assert parameter.value is None and parameter.profile_field is not None


def test_d74_amendment_replaces_obsolete_rates_and_half_hour_allowances():
    p = get_rule(RULE_ID).parameters
    ballast = p["ballast_pump_m3_h"].provenance.assumption
    heel = p["heel_transfer_m3_h"].provenance.assumption
    crane = p["gross_moves_per_hour"].provenance.assumption
    assert "200-500/300-700/400-1000" in ballast
    assert "300/400/600" not in ballast
    assert "200-2200" in heel
    assert "150/200/300" not in heel
    assert "100-300/150-400/200-600" not in heel
    assert "20 at small_feeder/cat_lai_like" in crane
    assert "30 gross moves/h" not in crane
    for name in ("pre_operation_allowance_h", "post_operation_allowance_h"):
        assert "0.5 h" not in p[name].provenance.assumption


def test_d74_amendment_distinguishes_public_heel_context_from_synthetic_rates():
    rule = get_rule(RULE_ID)
    heel = rule.parameters["heel_transfer_m3_h"].provenance
    assert "Damen" in heel.assumption and "1120 m^3/h" in heel.assumption
    assert "Framo" in heel.assumption and "200-2200 m^3/h" in heel.assumption
    assert heel.url == DAMEN_CF800_SPEC
    assert DAMEN_CF800_SPEC in rule.references and FRAMO_ANTI_HEELING in rule.references
    assert "experiments/review/w2/logic-d74.md" in rule.references
    assert "gravity draining is not modelled (conservative)" in rule.description
    assert all(label in rule.description for label in ("APPROXIMATION:", "synthetic", "UNVERIFIED", "Q23", "Q30"))
    # Public source context does not weaken the hard HEEL/window safety checks.
    assert rule.hardness == "hard" and rule.enforced_by == ["checker", "solver"]


def test_d74_is_a_separate_hard_plan_level_port_rule_with_design_provenance():
    rule = get_rule(RULE_ID)
    assert rule.source == "port"
    assert rule.scope == "port" and rule.hardness == "hard"
    assert rule.status == "provisional"
    assert set(rule.enforced_by) == {"checker", "solver"}
    assert rule.parameters["evaluation_level"].value == "plan"
    assert "D74" in " ".join(rule.references)
    assert all(section in " ".join(rule.references) for section in ("B.1", "B.2", "B.8"))
    assert "port.stability.cargo_operations" in rule.description
    assert "P1" in rule.description and "parallel" in rule.description


@pytest.mark.parametrize("enabled", [False, True])
def test_d74_metadata_is_inactive_off_under_the_single_shared_switch(enabled):
    with rule_freeze_v1(enabled=enabled) as snapshot:
        rule = get_rule(RULE_ID)
        path = rule.parameters["activation_path"].value
        assert "rule_freeze_v1_enabled()" in path
        assert "inactive" in path.lower() and "PortRuleRef" in path
        assert rule_freeze_v1_enabled() is enabled
        assert snapshot.rules[RULE_ID] == rule
        assert rule.pending_contract is None


@pytest.mark.parametrize("name,path,unit", [
    ("ballast_pump_m3_h", "vessel.ballast_system.ballast_pump_m3_h", "m^3/h"),
    ("heel_transfer_m3_h", "vessel.ballast_system.heel_transfer_m3_h", "m^3/h"),
    ("max_cranes", "vessel.ballast_system.max_cranes", "1"),
    ("gross_moves_per_hour", "port.quay_crane.gross_moves_per_hour", "moves/h"),
    ("pre_operation_allowance_h", "port.quay_crane.pre_operation_allowance_h", "h"),
    ("post_operation_allowance_h", "port.quay_crane.post_operation_allowance_h", "h"),
    ("port_cranes", "port.quay_crane.count", "1"),
    ("water_density_kg_m3", "port.water_density_kg_m3", "kg/m^3"),
])
def test_d74_profile_fields_match_native_contract_units_and_never_duplicate_defaults(name, path, unit):
    parameter = get_rule(RULE_ID).parameters[name]
    assert parameter.profile_field == path and parameter.unit == unit
    assert parameter.value is None
    assert "D74" in parameter.provenance.clause
    if name not in {"water_density_kg_m3", "port_cranes"}:
        assert "APPROXIMATION:" in parameter.provenance.assumption
        assert "Q23" in parameter.provenance.assumption and "UNVERIFIED" in parameter.provenance.assumption


def test_d74_formulas_convert_hours_to_seconds_and_keep_r1_r2_distinct():
    p = get_rule(RULE_ID).parameters
    assert p["seconds_per_hour"].value == 3600
    assert p["seconds_per_hour"].unit == "s/h"
    assert "max(sum_positive_delta_kg, sum_negative_delta_kg)" in p["pumped_mass_formula"].value
    assert "non-HEEL adjustable" in p["pumped_mass_formula"].value
    assert "sum(abs(delta_kg))" in p["absolute_change_formula"].value
    assert "min(port_cranes, max_cranes)" in p["working_cranes_formula"].value
    assert "3600 / (gross_moves_per_hour * n_c)" in p["lift_time_formula"].value
    assert "3600 * P_kg / (water_density_kg_m3 * ballast_pump_m3_h)" in p["pump_time_formula"].value
    assert "k == n + 1" in p["available_time_formula"].value
    assert "/ 3600" in p["heel_step_formula"].value
    assert "forward-backward" in p["heel_path_method"].value
    assert p["pumping_window_endpoint_fills"].value == ["arrival", "departure"]
    assert p["worked_tanks_maximum_fsm"].value is True
    assert p["heel_path_exact_recheck"].value is True
    assert p["heel_total_preserved_at_changeover"].value is True
    assert p["exact_recheck_failure_rule"].value == "port.stability.cargo_operations"
    assert get_rule(p["exact_recheck_failure_rule"].value).id == "port.stability.cargo_operations"
    assert p["rate_violation_codes"].value == ["heel_step"]
    assert p["mask_screens_pump_rates"].value is False
    assert "D75" in p["sequence_contract"].provenance.clause
    assert "every MovePlan.moves entry" in p["sequence_contract"].value


def test_d74_profile_parameters_and_approximations_round_trip():
    from stowage.rules.catalogue import Catalogue

    catalogue = load_catalogue()
    assert Catalogue.model_validate_json(catalogue.model_dump_json()) == catalogue
    p = catalogue[RULE_ID].parameters
    assert "lower bound" in p["pumped_mass_formula"].provenance.assumption
    assert "APPROXIMATION:" in p["pumping_window_formula"].provenance.assumption
    assert "heel_path_linearised_miss" in p["heel_path_method"].provenance.assumption
    assert {"pumped_mass_t", "pump_time_required_h", "pump_time_available_h", "heel_max_ideal_step_t",
            "heel_step_limit_t", "heel_path_feasible", "heel_path_linearised_miss"} <= set(p["reported_metrics"].value)


def test_d74_content_snapshot_identity_and_file_remain_coherent_across_activation():
    before = DEFAULT_CATALOGUE_PATH.read_bytes()
    assert tomllib.loads(before.decode())["activation"]["rule_freeze_v1"] is False
    snapshot = catalogue_snapshot()
    assert RULE_ID in snapshot.rules
    assert snapshot.content_sha256 == "sha256:" + sha256(snapshot.content).hexdigest()
    with rule_freeze_v1(enabled=True) as active:
        assert active.content_sha256 != snapshot.content_sha256
        assert active.rules[RULE_ID].model_dump_json() == snapshot.rules[RULE_ID].model_dump_json()
    assert DEFAULT_CATALOGUE_PATH.read_bytes() == before
    assert catalogue_snapshot() is snapshot
