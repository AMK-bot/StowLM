"""Catalogue identity pins the existing open-hatch guard and P1 numerics."""

from stowage.rules.catalogue import load_catalogue, rule_freeze_v1


def test_open_hatch_condition_has_one_explained_identity_across_operating_phases():
    catalogue = load_catalogue()
    rule = catalogue["stability.open_hatch_immersion"]
    assert rule.hardness == "hard" and rule.scope == "global"
    assert rule.parameters["operating_conditions"].value == ["port", "seagoing"]
    assert rule.parameters["minimum_coaming_clearance_m"].value == 0
    assert rule.parameters["strict_clearance"].value is True
    port = catalogue["port.stability.cargo_operations"]
    assert rule.id in port.references and port.id in rule.references
    assert "same physical condition" in port.description
    assert "immersion angle" in rule.description
    with rule_freeze_v1():
        assert load_catalogue()[rule.id] == rule


def test_p1_numerical_policy_is_pinned_alongside_d64_criteria():
    rule = load_catalogue()["port.stability.cargo_operations"]
    expected = {
        "equilibrium_residual_tolerance": 2e-6,
        "equilibrium_solver_tolerance": 2e-10,
        "equilibrium_max_function_evaluations": 100,
        "equilibrium_root_tolerance": 1e-9,
        "equilibrium_liquid_cg_tolerance_m": 1e-7,
        "equilibrium_liquid_fixed_point_iterations": 6,
        "equilibrium_liquid_root_max_function_evaluations": 80,
    }
    for name, value in expected.items():
        parameter = rule.parameters[name]
        assert parameter.value == value
        assert "not a safety allowance" in parameter.provenance.assumption
    assert rule.parameters["equilibrium_nonconvergence_is_hard"].value is True
    assert "function evaluations" in rule.description
