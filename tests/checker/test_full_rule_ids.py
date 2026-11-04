"""Unsupported instructions fail closed through fixed catalogue diagnostics."""

from types import SimpleNamespace

import pytest

from stowage.checker.full import check_plan_constraints
from stowage.model import PlanConstraint
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("hardness", ["hard", "soft"])
@pytest.mark.parametrize("kind", ["unimplemented_test_instruction", "keep.reefers", "unsafe kind!"])
def test_unsupported_instruction_uses_fixed_catalogue_diagnostic(enabled, hardness, kind):
    instance = SimpleNamespace(call_index=2,
        plan_constraints=[PlanConstraint(kind=kind, hardness=hardness)])
    with rule_freeze_v1(enabled=enabled):
        report = check_plan_constraints(instance, None, call_index=2)
        catalogue_ids = {rule.id for rule in load_catalogue().rules}

    assert not report.ran and report.error == "Unsupported instruction kinds."
    assert report.violations
    assert {item.rule_id for item in report.violations} == {"checker.missing_vessel_data"}
    assert all(item.rule_id in catalogue_ids for item in report.violations)
    assert report.violations[0].severity == hardness
    assert repr(kind) in report.violations[0].message
    assert any(item.severity == "hard" for item in report.violations)
    assert all(item.call_index == 2 for item in report.violations)
