"""D40 signed loading-manual policy is independent from calibration coverage."""
from dataclasses import replace

import pytest

from stowage.linear import operations
from stowage.linear.model import evaluate_linear, linear_constraints, solve_ballast_lp
from stowage.model import VesselFieldProvenance
from stowage.model.fixtures import build_instance
from tests.linear.test_operations import example


FIELDS = ("min_trim_m", "max_trim_m", "min_displacement_kg", "max_displacement_kg")


@pytest.fixture
def loading_context():
    model, ship, instance = example()
    policy = dict(min_trim_m=-.6, max_trim_m=.9,
                  min_displacement_kg=800., max_displacement_kg=1800.)
    evidence = VesselFieldProvenance(assumption="Synthetic loading-manual test bounds")
    hull = build_instance().vessel.hull.model_copy(update={
        **policy, **{name+"_provenance": evidence for name in FIELDS}})
    instance.vessel.hull = hull
    object.__setattr__(model, "measurement_diagnostics", {"loading_policy": dict(policy)})
    return model, ship, instance, policy


@pytest.mark.parametrize("low,high", [(-.6,.9), (.2,.8), (-.8,-.2), (0.,0.)])
def test_declared_signed_trim_bounds_never_infer_symmetry(loading_context, low, high):
    model, _, _, policy = loading_context
    policy.update(min_trim_m=low, max_trim_m=high)
    model = replace(model, max_trim_m=high, measurement_diagnostics={"loading_policy": policy})
    rows = linear_constraints(model, direction="conservative")
    index = rows.names.index("trim")
    assert rows.lb[index] == low
    assert rows.ub[index] == high


def test_declared_policy_intersects_validity_band_and_keeps_margins(loading_context):
    model, _, _, policy = loading_context
    policy.update(min_trim_m=-2., max_trim_m=3., min_displacement_kg=400., max_displacement_kg=3000.)
    model = replace(model, errors=replace(model.errors, trim_m=.1),
                    measurement_diagnostics={"loading_policy": policy})
    rows = linear_constraints(model, direction="conservative")
    trim, disp = map(rows.names.index, ("trim", "displacement"))
    assert rows.lb[trim] == pytest.approx(-.9)
    assert rows.ub[trim] == pytest.approx(.9)
    assert rows.lb[disp] == 500.
    assert rows.ub[disp] == 2500.


def test_declared_displacement_limits_are_not_band_endpoints(loading_context):
    model, _, _, _ = loading_context
    rows = linear_constraints(model, direction="conservative")
    index = rows.names.index("displacement")
    assert rows.lb[index] == 800.
    assert rows.ub[index] == 1800.


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("missing", ["value", "provenance"])
def test_real_hull_missing_loading_policy_fails_closed(loading_context, field, missing):
    _, _, instance, _ = loading_context
    key = field if missing == "value" else field+"_provenance"
    instance.vessel.hull = instance.vessel.hull.model_copy(update={key: None})
    with pytest.raises(ValueError, match=field):
        operations.public_loading_policy(instance)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "1.0"])
def test_mutated_invalid_loading_values_fail_closed(loading_context, field, bad):
    _, _, instance, _ = loading_context
    instance.vessel.hull = instance.vessel.hull.model_copy(update={field: bad})
    with pytest.raises(ValueError, match=field):
        operations.public_loading_policy(instance)


@pytest.mark.parametrize("rule,changes,expected", [
    ("iscode.loading.trim", {"minimum": -.3}, {"min_trim_m": -.3}),
    ("iscode.loading.trim", {"maximum": .5}, {"max_trim_m": .5}),
    ("iscode.loading.displacement", {"minimum": 1000.}, {"min_displacement_kg": 1000.}),
    ("iscode.loading.displacement", {"maximum": 1600.}, {"max_displacement_kg": 1600.}),
])
def test_overrides_can_only_tighten_declared_policy(loading_context, rule, changes, expected):
    model, ship, instance, policy = loading_context
    instance.rule_overrides = {rule: changes}
    effective = operations.public_loading_policy(instance)
    assert effective == policy | expected
    model = replace(model, measurement_diagnostics={"loading_policy": effective})
    evaluate_linear(model, ship, instance)
    solve_ballast_lp(model, ship, instance, direction="conservative")


@pytest.mark.parametrize("rule,changes", [
    ("iscode.loading.trim", {"minimum": -.7}),
    ("iscode.loading.trim", {"maximum": 1.}),
    ("iscode.loading.displacement", {"minimum": 700.}),
    ("iscode.loading.displacement", {"maximum": 1900.}),
    ("iscode.loading.trim", {"minimum": .95}),
    ("iscode.loading.trim", {"maximum": float("nan")}),
    ("iscode.loading.trim", {"minimum": True}),
    ("iscode.loading.trim", {"unexpected": .2}),
    ("iscode.loading.displacement", {"minimum": -1.}),
    ("iscode.loading.gm", {"minimum_gm": .3}),
])
def test_weakened_invalid_or_unknown_safety_override_rejects(loading_context, rule, changes):
    _, _, instance, _ = loading_context
    instance.rule_overrides = {rule: changes}
    with pytest.raises(ValueError):
        operations.public_loading_policy(instance)


@pytest.mark.parametrize("operation", [evaluate_linear, solve_ballast_lp])
def test_changed_effective_loading_policy_requires_rebuild(loading_context, operation):
    model, ship, instance, _ = loading_context
    instance.rule_overrides = {"iscode.loading.trim": {"maximum": .5}}
    with pytest.raises(ValueError, match="loading policy.*rebuild"):
        operation(model, ship, instance, **({"direction": "conservative"} if operation is solve_ballast_lp else {}))


@pytest.mark.parametrize("field", FIELDS)
def test_reuse_identity_includes_all_declared_loading_endpoints(loading_context, field):
    _, _, instance, _ = loading_context
    instance.vessel.stacks = []
    instance.vessel.cells = []
    instance.vessel.tanks = []
    # Minimal namespace projects as a mapping, just like coefficient fixtures.
    instance.vessel.model_dump = lambda **kwargs: {
        "hull": instance.vessel.hull.model_dump(mode="json"), "cells": [], "stacks": [], "tanks": []}
    first = operations.vessel_physics_identity(instance.vessel)
    value = getattr(instance.vessel.hull, field)
    instance.vessel.hull = instance.vessel.hull.model_copy(update={field: value+.1})
    assert operations.vessel_physics_identity(instance.vessel) != first


def test_real_policy_cannot_use_model_without_saved_policy_binding(loading_context):
    model, ship, instance, _ = loading_context
    model = replace(model, measurement_diagnostics={})
    with pytest.raises(ValueError, match="loading policy.*rebuild"):
        evaluate_linear(model, ship, instance)


def test_legacy_minimal_algebra_hull_stays_usable():
    model, ship, instance = example()
    assert operations.public_loading_policy(instance) is None
    evaluate_linear(model, ship, instance)


def test_explicit_missing_hull_is_unavailable_loading_policy():
    _, _, instance = example()
    instance.vessel.hull = None
    with pytest.raises(ValueError, match="hull"):
        operations.public_loading_policy(instance)


@pytest.mark.parametrize("evidence", [None, {}, {"assumption": "untyped"}, True,
    VesselFieldProvenance(assumption="test").model_copy(update={"source": " "})])
def test_invalid_or_untyped_field_evidence_rejects(loading_context, evidence):
    _, _, instance, _ = loading_context
    instance.vessel.hull = instance.vessel.hull.model_copy(update={"min_trim_m_provenance": evidence})
    with pytest.raises(ValueError, match="min_trim_m_provenance"):
        operations.public_loading_policy(instance)


@pytest.mark.parametrize("policy", [
    {"min_trim_m": -.1},
    dict(min_trim_m=True,max_trim_m=.5,min_displacement_kg=1.,max_displacement_kg=2.),
    dict(min_trim_m=1.,max_trim_m=-1.,min_displacement_kg=1.,max_displacement_kg=2.),
])
def test_malformed_saved_policy_cannot_generate_solver_rows(loading_context, policy):
    model, _, _, _ = loading_context
    model = replace(model, measurement_diagnostics={"loading_policy": policy})
    with pytest.raises(ValueError, match="loading policy"):
        linear_constraints(model, direction="conservative")


def test_signed_policy_empty_intersection_stays_infeasible(loading_context):
    model, ship, instance, policy = loading_context
    policy.update(min_trim_m=2., max_trim_m=3.)
    instance.vessel.hull = instance.vessel.hull.model_copy(update=policy)
    model = replace(model, measurement_diagnostics={"loading_policy": policy})
    assert solve_ballast_lp(model, ship, instance, direction="conservative").status == "infeasible"
