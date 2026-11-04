"""Repair's real native solver/checker boundary, without replaced components.

The hull-free geometry fixture is deliberately uncertifiable. This is safety
coverage, not evidence of positive production repair throughput.
"""
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.checker import check
from stowage.model import PlannerResult
from stowage.solver.repair import repair


def test_real_native_repair_missing_physics_never_ships_stale_success_metrics():
    instance = tiny_instance(571, n_load=1, n_other=0, p40=0.)
    assert instance.vessel.hull.hull_offsets_ref is None
    partial = get_action_space().expand(instance, [])
    original = PlannerResult(
        status='dead_end', plan=partial, planner='synthetic-partial',
        planner_version='test', metrics={
            'ballast_status': 'ok', 'checker_passed': True, 'shippable': True,
        })
    failed = check(instance, partial)
    assert not failed.passed
    assert set(failed.shut_out) == set(instance.load_list)
    assert any(v.rule_id == 'objective.all_loaded' for v in failed.violations)
    assert any(v.rule_id == 'checker.missing_vessel_data' for v in failed.violations)

    repaired = repair(instance, original, failed, seed=571, time_limit_s=3.)

    assert repaired.status in {'error', 'timeout'}
    assert repaired.plan is None
    assert repaired.repaired
    assert repaired.metrics['fallback_needed']
    assert repaired.metrics['repair_attempts'] >= 1
    assert not repaired.metrics['repair_succeeded']
    assert not repaired.metrics['repair_checker_passed']
    assert not repaired.shippable
    assert repaired.metrics['shippable'] is False
    # The fallback is functional: caller-owned partial plans remain untouched.
    assert original.plan is partial
    assert original.status == 'dead_end'
    assert partial.ballast is None
