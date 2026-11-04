"""Proofs use global necessary conditions, never a failed local approximation."""

import pytest

from stowage.model import PartReport, Violation
from stowage.model.fixtures import build_instance
from stowage.solver.ballast_proof import prove_infeasible


def test_displacement_above_limit_is_proved_without_geometry():
    instance = build_instance(layover=True)
    instance.rule_overrides = {'iscode.loading.displacement': {'maximum': 1.0}}
    proof = prove_infeasible(instance, instance.onboard)
    assert 'exact mass-balance bound' in proof
    assert 'maximum' in proof


def test_full_capacity_below_minimum_is_proved():
    instance = build_instance()
    instance.rule_overrides = {'iscode.loading.displacement': {'minimum': 1e12}}
    assert 'all adjustable tanks full' in prove_infeasible(instance, instance.onboard)


def test_mass_range_inside_override_is_not_a_proof():
    instance = build_instance()
    instance.rule_overrides = {'iscode.loading.displacement': {'minimum': 1.0, 'maximum': 1e12}}
    assert prove_infeasible(instance, instance.onboard) is None


def test_disjoint_forward_draft_bounds():
    instance = build_instance()
    instance.vessel.hull.min_draught_fwd_m = 10
    assert 'intervals are disjoint' in prove_infeasible(instance, instance.onboard)


def test_complete_declared_envelope_proves_overweight():
    from stowage.vessel.fleet import load_ship_params
    instance = build_instance()
    params = load_ship_params('feeder_500')
    instance.vessel.slice_of = 'feeder_500'
    hull = instance.vessel.hull
    for name in ('lpp_m', 'beam_m', 'depth_m'):
        setattr(hull, name, params['hull'][name])
    hull.lightship_mass_kg = 1e12
    proof = prove_infeasible(instance, instance.onboard)
    assert 'polygon-envelope volume bound' in proof
    assert '2e-6' in proof


def test_exact_rejection_only_proves_singleton_fill_space():
    instance = build_instance()
    report = PartReport(name='stability', violations=[Violation(
        rule_id='iscode.loading.gm', severity='hard', call_index=0, measured=-1, limit=.15, message='GM below minimum',
    )])
    assert prove_infeasible(instance, instance.onboard, exact_parts={'stability': report}) is None
    for tank in instance.vessel.tanks:
        tank.adjustable = False
    proof = prove_infeasible(instance, instance.onboard, exact_parts={'stability': report})
    assert 'singleton' in proof
    assert 'iscode.loading.gm' in proof
    report.ran = False
    assert prove_infeasible(instance, instance.onboard, exact_parts={'stability': report}) is None


def test_missing_geometry_or_open_cover_cannot_establish_proof():
    instance = build_instance()
    assert prove_infeasible(instance, instance.onboard) is None
    instance.onboard.covers_off = {instance.vessel.hatch_covers[0].id: 'quay'}
    instance.rule_overrides = {'iscode.loading.displacement': {'maximum': 1}}
    assert prove_infeasible(instance, instance.onboard) is None


def test_finisher_returns_mass_balance_proof():
    from stowage.model import MovePlan
    from stowage.solver.ballast import finish_ballast

    instance = build_instance()
    instance.rule_overrides = {'iscode.loading.displacement': {'maximum': 1.0}}
    plan = MovePlan(id='impossible-mass', instance_id=instance.id, call_index=0)
    outcome = finish_ballast(instance, plan, time_limit_s=2.)
    assert outcome.status == 'infeasible_proven'
    assert outcome.plan is None
    assert 'exact mass-balance bound' in outcome.proof
    assert outcome.iterations == 0


@pytest.mark.slow
def test_finisher_proves_exact_gm_failure_with_no_adjustable_tanks():
    import runpy
    from pathlib import Path
    from stowage.solver.ballast import finish_ballast

    helpers = runpy.run_path(str(Path(__file__).with_name('test_ballast_integration.py')))
    # Retain the production hull-following tanks, but freeze their fills so
    # the physical ballast domain contains precisely one condition.
    instance = helpers['_fleet_context']('feeder_500')
    for tank in instance.vessel.tanks:
        tank.adjustable = False
    instance.vessel.hull.min_gm_m = 100.
    # Assert singleton-proof semantics with time for the production checker's
    # direct fallback; separate deadline tests enforce the caller's budget.
    outcome = finish_ballast(instance, helpers['_plan'](instance), time_limit_s=60.)
    assert outcome.status == 'infeasible_proven', outcome
    assert outcome.plan is None
    assert 'singleton' in outcome.proof
    assert 'iscode.loading.gm' in outcome.proof
    assert outcome.parts['stability'].ran
    assert outcome.parts['stability'].metrics['gm_m'] < 100.
    assert outcome.iterations == 1
