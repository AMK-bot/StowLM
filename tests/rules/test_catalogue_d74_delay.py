"""D74 reports R1 delay while retaining hard HEEL and pumping-window safety."""

import pytest

from stowage.rules.catalogue import Catalogue, get_rule, load_catalogue, rule_freeze_v1


RULE_ID = "port.ballast.transfer_rate"


@pytest.mark.parametrize("enabled", [False, True])
def test_r1_delay_is_reported_in_hours_without_a_rule_or_move_objective(enabled):
    with rule_freeze_v1(enabled=enabled):
        rule = get_rule(RULE_ID)
    parameters = rule.parameters
    formula = parameters["ballast_delay_formula"]
    assert formula.value == "ballast_delay_h = max(0, (tau_s - T_s(k)) / 3600)"
    assert formula.unit == "h"
    assert "D74" in formula.provenance.clause
    assert "ballast_delay_h" in parameters["reported_metrics"].value
    assert parameters["r1_delay_is_rejection"].value is False
    assert parameters["ballast_delay_in_objective"].value is False
    notes = parameters["ballast_delay_reporting"].value.lower()
    assert all(phrase in notes for phrase in ("hours", "per plan", "per method", "moves", "d11"))
    assert "no r1 violation" in rule.description.lower()


@pytest.mark.parametrize("enabled", [False, True])
def test_only_heel_and_pumping_window_remain_hard_under_the_shared_activation(enabled):
    with rule_freeze_v1(enabled=enabled):
        rule = get_rule(RULE_ID)
    p = rule.parameters
    assert rule.hardness == "hard" and rule.scope == "port"
    assert rule.enforced_by == ["checker", "solver"]
    assert p["evaluation_level"].value == "plan"
    assert p["rate_violation_codes"].value == ["heel_step"]
    assert p["pumping_window_waiting_states_hard"].value is True
    assert p["pumping_window_endpoint_fills"].value == ["arrival", "departure"]
    assert p["worked_tanks_maximum_fsm"].value is True
    assert p["heel_path_exact_recheck"].value is True
    assert p["exact_recheck_failure_rule"].value == "port.stability.cargo_operations"
    activation = p["activation_path"].value.lower()
    assert all(text in activation for text in ("inactive", "rule_freeze_v1_enabled()", "portruleref", "r2", "window", "delay"))
    assert "no r1 rejection" in activation


@pytest.mark.parametrize("name,formula", [
    ("r1_pass_condition", "tau_s <= T_s(k)"),
    ("finisher_rate_margin_formula", "c_rate = (C_kg - P_kg) / C_kg"),
])
def test_legacy_time_comparisons_are_retained_as_superseded_diagnostics(name, formula):
    p = get_rule(RULE_ID).parameters
    historical = p[name]
    assert historical.value.startswith(formula)
    assert historical.superseded_by == "ballast_delay_formula"
    assert historical.superseded_by in p
    assumption = historical.provenance.assumption.lower()
    assert "historical" in assumption and "diagnostic" in assumption
    assert "not a hard constraint" in assumption


def test_delay_metadata_round_trips_without_adding_a_soft_r1_rule():
    catalogue = load_catalogue()
    assert Catalogue.model_validate_json(catalogue.model_dump_json()) == catalogue
    assert not any(rule.id in {"port.ballast.delay", "port.ballast.r1"} for rule in catalogue.rules)
    rule = catalogue[RULE_ID]
    assert "smallest P" in rule.parameters["finisher_preference"].value
    assert "delay" in rule.parameters["finisher_preference"].provenance.assumption.lower()
