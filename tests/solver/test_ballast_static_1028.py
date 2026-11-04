"""Only immutable fixed-stow constraints can short-circuit ballast search."""
import pytest

from stowage.model import PartReport, Placement, Violation
from stowage.model.fixtures import build_instance
from stowage.solver.ballast_proof import prove_infeasible


def test_exact_overweight_stack_is_a_fixed_stow_ballast_proof():
    instance = build_instance()
    box = instance.containers[2]
    ship = instance.onboard.model_copy(update={'placements': [Placement(
        container_id=box.id, slot='0010102')]})
    stack = next(s for s in instance.vessel.stacks if s.id == '00101H')
    stack.max_mass_kg = box.gross_mass_kg-1.
    proof = prove_infeasible(instance, ship)
    assert 'fixed cargo stow' in proof
    assert 'expert.physical.stack_weight' in proof
    assert 'another cargo stow is not ruled out' in proof


@pytest.mark.parametrize('ran,severity,rule,proven', [
    (True, 'hard', 'expert.physical.stack_weight', True),
    (True, 'hard', 'expert.physical.tier_height', True),
    (True, 'tolerated', 'expert.physical.stack_weight', False),
    (False, 'hard', 'expert.physical.stack_weight', False),
    (True, 'hard', 'checker.missing_vessel_data', False),
    (True, 'hard', 'iscode.loading.gm', False),
])
def test_unavailable_tolerated_or_ballast_dependent_rules_are_not_proofs(
        monkeypatch, ran, severity, rule, proven):
    import stowage.checker.rules as rules
    monkeypatch.setattr(rules, 'check_static_rules', lambda *a, **kw: PartReport(
        name='rules', ran=ran, violations=[Violation(rule_id=rule, severity=severity,
                                                   call_index=0, message='test')]))
    instance = build_instance()
    proof = prove_infeasible(instance, instance.onboard)
    assert (proof is not None) == proven
