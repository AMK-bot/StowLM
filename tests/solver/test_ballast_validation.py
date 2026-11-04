"""Invalid moves take precedence over an exhausted ballast-search budget."""

import pytest

from stowage.model import BallastCondition, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load
from stowage.solver.ballast import _finish_ballast, finish_ballast


@pytest.mark.parametrize('finisher', [finish_ballast, _finish_ballast])
@pytest.mark.parametrize('budget', [0., -1., float('nan'), float('inf'), -float('inf'), 1e-12])
def test_illegal_moves_raise_before_budget_return(finisher, budget):
    instance = build_instance()
    plan = MovePlan(id='illegal-ballast', instance_id=instance.id, call_index=0,
                    moves=[Load(call_index=0, container_id=instance.containers[0].id,
                                src='A-001-01-02', dst='0020182')])
    with pytest.raises(ValueError, match='legal move sequence'):
        finisher(instance, plan, time_limit_s=budget)


@pytest.mark.parametrize('finisher', [finish_ballast, _finish_ballast])
@pytest.mark.parametrize('budget', [0., -1., float('nan'), float('inf'), -float('inf')])
def test_legal_moves_with_unusable_budget_remain_inconclusive(finisher, budget):
    instance = build_instance()
    plan = MovePlan(id='legal-ballast', instance_id=instance.id, call_index=0)
    outcome = finisher(instance, plan, time_limit_s=budget)
    assert outcome.status == 'not_found'
    assert outcome.plan is None and outcome.proof is None


@pytest.mark.parametrize('finisher', [finish_ballast, _finish_ballast])
def test_existing_ballast_keeps_precedence_over_move_validation(finisher):
    instance = build_instance()
    plan = MovePlan(id='kept-ballast', instance_id=instance.id, call_index=0,
                    moves=[Load(call_index=0, container_id=instance.containers[0].id,
                                src='A-001-01-02', dst='0020182')],
                    ballast=BallastCondition(tank_masses_kg={}, source='planner'))
    outcome = finisher(instance, plan, time_limit_s=0.)
    assert outcome.status == 'kept'
    assert outcome.plan == plan
