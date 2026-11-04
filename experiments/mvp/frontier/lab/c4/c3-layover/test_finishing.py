"""Focused work-cap, real-area, inheritance and acceptance regressions."""
from collections import OrderedDict
from types import SimpleNamespace as NS

import numpy as np
import pytest

import ballast_local as local
import ballast_physics as physics
import finishing


def toy_instance():
    point = lambda volume,x,y: NS(volume_m3=volume,lcg_m=x,tcg_m=y,vcg_m=1.)
    tank = NS(id='PAIR-P', adjustable=True, content_density_kg_m3=1., capacity_m3=100.,
              table=[point(0., 30., 2.),point(100.,30.,2.)])
    return NS(vessel=NS(hull=NS(lightship_lcg_m=50., lightship_mass_kg=1000.,lightship_tcg_m=0.),
                        tanks=[tank], fixed_loads=[]),
              containers=[NS(id='retained',gross_mass_kg=100.),NS(id='new',gross_mass_kg=100.),
                          NS(id='due',gross_mass_kg=500.)],
              load_list=['new'], discharge_list=['due'],
              onboard=NS(tank_masses_kg={'PAIR-P':50.},
                         placements=[NS(container_id='retained'),NS(container_id='due')]))


def test_targets_cancel_inherited_moments_and_exclude_due():
    target = local.ballast_targets(toy_instance())
    assert target['cargo_mass_kg'] == 200.
    assert target['target_cargo_lcg_m'] == 55.
    assert target['target_cargo_tcg_m'] == -.5
    assert target['exact_physics_evaluations'] == 0
    assert target['kg_weight'] > 0.


def test_targets_use_departure_lcb_and_conserve_all_longitudinal_moments():
    instance = toy_instance()
    instance.vessel.hull.hydrostatics = [
        NS(displacement_kg=1000., lcb_m=52.),
        NS(displacement_kg=1700., lcb_m=59.)]
    instance.vessel.fixed_loads = [NS(mass_kg=100., x_m=20., y_m=1., z_m=2.)]
    target = local.ballast_targets(instance)
    assert target['estimated_departure_displacement_kg'] == 1350.
    assert target['reference_lcg_m'] == pytest.approx(55.5)
    assert target['reference_source'].startswith('native_even_keel_hydrostatic_lcb')
    total_moment = (1000.*50.+50.*30.+100.*20.+
                    target['cargo_mass_kg']*target['target_cargo_lcg_m'])
    assert total_moment == pytest.approx(1350.*target['reference_lcg_m'])
    total_transverse = 50.*2.+100.*1.+200.*target['target_cargo_tcg_m']
    assert total_transverse == pytest.approx(0.)
    assert target['exact_physics_evaluations'] == 0


def test_targets_use_port_density_for_hydrostatic_displacement():
    instance = toy_instance()
    instance.vessel.hull.water_density_kg_m3 = 1025.
    instance.port = NS(water_density_kg_m3=1000.)
    instance.vessel.hull.hydrostatics = [
        NS(displacement_kg=1000., lcb_m=52.),
        NS(displacement_kg=1500., lcb_m=57.)]
    target = local.ballast_targets(instance)
    assert target['estimated_departure_displacement_kg'] == 1250.
    assert target['hydrostatic_table_displacement_kg'] == pytest.approx(1281.25)
    assert target['reference_lcg_m'] == pytest.approx(54.8125)


@pytest.mark.parametrize('fractions,direction', [([0.],[1.]),([1.],[1.]),([.979],[1.]),
                                               ([.99],[1.]),([.5,.5],[1.,-1.])])
def test_differences_respect_capacity_and_pressed_regime(fractions,direction):
    x, d = np.array(fractions), np.array(direction)
    step = local.difference_step(x,d)
    y = x+step*d
    assert np.all((y >= 0.) & (y <= 1.))
    assert np.array_equal(x >= .98, y >= .98)


class ToyPhysics:
    """Area binds while GM is already fine: catches GM-only substitutes."""
    def __init__(self, instance, plan):
        self.initial_fractions = np.array([.5])
        self.capacity_kg = np.array([100.])
        self.tank_ids = ('PAIR-P',)
        self.calls = 0

    def evaluate_fractions(self, x):
        self.calls += 1
        margins = {'arrival/stability/containership_area_0_flood_mrad':float(x[0]-.52),
                   'departure/lashing/rod':float(.6-x[0]),
                   'departure/stability/gm_m':1.}
        return dict(completed=True, margins=margins, raw_margins=margins,
                    physics_feasible=all(v >= 0. for v in margins.values()),
                    raw_loading={'arrival':{'tcg_m':0.}})

    def candidate_plan(self, masses):
        return np.array(masses)


def test_local_lp_uses_real_area_and_never_exceeds_oracle_cap(monkeypatch):
    monkeypatch.setattr(local, 'PhysicsEvaluator', ToyPhysics)
    candidate, result = local.optimize(None,None,max_evaluations=3,max_iterations=2,use_loading_seed=False)
    assert result['oracle_calls'] == 3
    assert result['physics_feasible']
    assert candidate[0] >= 52.
    assert result['real_area_margins']['arrival/stability/containership_area_0_flood_mrad'] >= 0.
    assert not result['physical_infeasibility_proven']


def test_single_oracle_call_returns_unknown_not_proof(monkeypatch):
    monkeypatch.setattr(local, 'PhysicsEvaluator', ToyPhysics)
    _, result = local.optimize(None,None,max_evaluations=1,max_iterations=99,use_loading_seed=False)
    assert result['oracle_calls'] == 1
    assert not result['physics_feasible']
    assert not result['physical_infeasibility_proven']


class ToyNonlinearPhysics(ToyPhysics):
    def evaluate_fractions(self, x):
        result = super().evaluate_fractions(x)
        delta = float(x[0]-.5)
        result['margins']['arrival/stability/containership_area_0_flood_mrad'] = -.2+delta-8.*delta**2
        result['margins']['departure/lashing/rod'] = float(.95-x[0])
        result['physics_feasible'] = all(v >= 0. for v in result['margins'].values())
        return result


def test_larger_radius_exactly_backtracks_when_full_step_worsens_area(monkeypatch):
    monkeypatch.setattr(local,'PhysicsEvaluator',ToyNonlinearPhysics)
    candidate,result = local.optimize(None,None,max_evaluations=4,max_iterations=1,
        trust_radius=.15,use_loading_seed=False)
    assert result['initial_trust_radius'] == .15
    assert result['oracle_calls'] == 4
    assert result['trace'][-2]['label'] == 'trial-0-1.0'
    assert result['trace'][-2]['merit'] > result['trace'][0]['merit']
    assert result['trace'][-1]['label'] == 'trial-0-0.5'
    assert result['trace'][-1]['merit'] < result['trace'][0]['merit']
    assert candidate[0] == pytest.approx(57.5)
    assert not result['physics_feasible']


@pytest.mark.parametrize('radius',[0.,-1.,float('nan'),float('inf')])
def test_trust_radius_requires_finite_positive_value(radius):
    with pytest.raises(ValueError,match='trust_radius'):
        local.optimize(None,None,trust_radius=radius)


def test_visibility_scaling_uses_native_distance_and_angle_limits():
    report = NS(metrics={'limit_m':200.})
    assert physics.margin_scale('visibility','blind_distance_m',-10.,NS(),report) == 200.
    assert physics.margin_scale('visibility','total_blind_sector_rad',-.1,NS(),report) == pytest.approx(np.radians(20.))
    assert physics.margin_scale('visibility','min_clear_sector_rad',-.1,NS(),report) == pytest.approx(np.radians(5.))


def _isolated_native_oracle(monkeypatch):
    """Use real separation code while isolating heavy native providers.

    Provider spies replace the ordinary native module entry points; the test
    verifies separation dispatches to those exact entry points in every D57
    state rather than computing a private visibility approximation.
    """
    from stowage.checker import stability, stability_loading, lashing
    from stowage.model import PartReport
    from stowage.solver import ballast_v4_evaluator

    class State(NS):
        def model_copy(self, *, update):
            return type(self)(**{**vars(self),**update})

    instance = NS(vessel=NS(hull=NS(max_list_rad=.2)),call_index=0)
    evaluator = object.__new__(physics.PhysicsEvaluator)
    evaluator.instance = instance
    evaluator.ship = State(tank_masses_kg={'PAIR-P':50.})
    evaluator.capacity_kg = np.array([100.])
    evaluator.tank_ids = ('PAIR-P',)
    evaluator.initial_masses_kg = np.array([50.])
    evaluator.fixed_masses_kg = {}
    evaluator.cache = OrderedDict()
    evaluator.cache_size = 8
    evaluator.evaluations = evaluator.cache_hits = 0
    evaluator.source_sha256 = {}
    monkeypatch.setattr(ballast_v4_evaluator,'conditions',lambda inst,state:[
        (phase,NS(vessel=inst.vessel,call_index=index),state)
        for phase,index in [('departure',0),('mid_voyage',0),('arrival',1)]])
    monkeypatch.setattr(stability,'_context',lambda vessel:(None,{}))
    monkeypatch.setattr(stability_loading,'loading_condition',lambda *a,**kw:
        NS(mass_kg=1000.,lcg_m=50.,tcg_m=0.,kg_m=5.,fsc_m=0.,fsm_kgm=0.,errors=[]))
    monkeypatch.setattr(stability,'check_stability',lambda *a,**kw:
        PartReport(name='stability',margins={'gm_m':1.}))
    monkeypatch.setattr(lashing,'check_lashing',lambda *a,**kw:
        PartReport(name='lashing',margins={'max_rod_ratio':.2}))
    return evaluator


def test_native_visibility_called_all_three_states_and_prevents_false_feasibility(monkeypatch):
    from stowage.checker import visibility
    from stowage.model import PartReport, Violation
    evaluator = _isolated_native_oracle(monkeypatch)
    calls = []
    def violated(inst,state,*,call_index):
        calls.append((call_index,state.tank_masses_kg['PAIR-P']))
        return PartReport(name='visibility',metrics={'limit_m':100.,'blind_distance_m':110.},
            margins={'blind_distance_m':-10.,'total_blind_sector_rad':.1,'min_clear_sector_rad':.1},
            violations=[Violation(rule_id='solas.v22.visibility',severity='hard',
                call_index=call_index,message='distance exceeds native limit')])
    monkeypatch.setattr(visibility,'check_visibility',violated)
    result = evaluator.evaluate_fractions([.5])
    assert calls == [(0,50.),(0,50.),(1,50.)]
    assert result['completed']
    assert not result['physics_feasible']
    assert set(result['parts']) == {'stability','lashing','visibility'}
    for phase in physics.PHASES:
        key = phase+'/visibility/blind_distance_m'
        assert result['raw_margins'][key] == -10.
        assert result['margin_scales'][key] == 100.
        assert result['margins'][key] == -.1

    def cleared(inst,state,*,call_index):
        calls.append((call_index,state.tank_masses_kg['PAIR-P']))
        return PartReport(name='visibility',metrics={'limit_m':100.,'blind_distance_m':90.},
            margins={'blind_distance_m':10.,'total_blind_sector_rad':.1,'min_clear_sector_rad':.1})
    monkeypatch.setattr(visibility,'check_visibility',cleared)
    cleared_result = evaluator.evaluate_fractions([.5])
    assert len(calls) == 6  # Changed native provider identity invalidates memoization.
    assert cleared_result['physics_feasible']


def test_unrepresented_native_individual_sector_failure_still_rejects(monkeypatch):
    from stowage.checker import visibility
    from stowage.model import PartReport, Violation
    evaluator = _isolated_native_oracle(monkeypatch)
    monkeypatch.setattr(visibility,'check_visibility',lambda inst,state,*,call_index:
        PartReport(name='visibility',metrics={'limit_m':100.},
            margins={'blind_distance_m':10.,'total_blind_sector_rad':.1,'min_clear_sector_rad':.1},
            violations=[Violation(rule_id='solas.v22.visibility',severity='hard',
                call_index=call_index,message='individual sector exceeds limit')]))
    result = evaluator.evaluate_fractions([.5])
    assert result['completed']
    assert not result['physics_feasible']
    for phase in physics.PHASES:
        assert result['raw_margins'][phase+'/visibility/unrepresented_hard_violation'] == -1.


def fake_report(passed,part='stability'):
    violation = NS(severity='hard')
    return NS(passed=passed, parts={part:NS(violations=[] if passed else [violation])})


def test_inherited_full_pass_stops_before_local_search(monkeypatch):
    inherited = object()
    monkeypatch.setattr(finishing,'inherited_plan',lambda i,p:inherited)
    monkeypatch.setattr(finishing,'_check',lambda i,p:(fake_report(True),dict(passed=True)))
    monkeypatch.setattr(local,'optimize',lambda *a,**k:pytest.fail('unexpected local search'))
    plan, metadata = finishing.finish(None,None,context={'finishing_config':{'max_evaluations':4}})
    assert plan is inherited
    assert metadata['passed']
    assert len(metadata['lanes']) == 1


def test_local_physics_feasible_candidate_still_requires_full_checker(monkeypatch):
    monkeypatch.setattr(finishing,'inherited_plan',lambda i,p:'inherited')
    checks = []
    def rejected(i,p):
        checks.append(p)
        return fake_report(False),dict(passed=False)
    monkeypatch.setattr(finishing,'_check',rejected)
    monkeypatch.setattr(local,'optimize',lambda *a,**k:('candidate',dict(physics_feasible=True)))
    plan,metadata = finishing.finish(None,None)
    assert plan is None
    assert not metadata['passed']
    assert checks == ['inherited','candidate']


def test_static_hard_failure_skips_water_search(monkeypatch):
    monkeypatch.setattr(finishing,'inherited_plan',lambda i,p:'inherited')
    monkeypatch.setattr(finishing,'_check',lambda i,p:(fake_report(False,'rules'),dict(passed=False)))
    monkeypatch.setattr(local,'optimize',lambda *a,**k:pytest.fail('water cannot fix static rule'))
    plan,metadata = finishing.finish(None,None)
    assert plan is None
    assert 'non-water hard violation' in metadata['local_skip']


def test_condition_id_cannot_turn_static_failure_into_water_part(monkeypatch):
    monkeypatch.setattr(finishing,'inherited_plan',lambda i,p:'inherited')
    report = fake_report(False,'condition:stability-departure:constraints')
    monkeypatch.setattr(finishing,'_check',lambda i,p:(report,dict(passed=False)))
    monkeypatch.setattr(local,'optimize',lambda *a,**k:pytest.fail('static failure is not water'))
    plan,metadata = finishing.finish(None,None)
    assert plan is None
    assert 'local_skip' in metadata


def test_unavailable_nonwater_part_skips_water_search(monkeypatch):
    monkeypatch.setattr(finishing,'inherited_plan',lambda i,p:'inherited')
    report = fake_report(False)
    report.parts['moves'] = NS(violations=[],ran=False,error='unavailable')
    monkeypatch.setattr(finishing,'_check',lambda i,p:(report,dict(passed=False)))
    monkeypatch.setattr(local,'optimize',lambda *a,**k:pytest.fail('unavailable moves cannot be repaired'))
    plan,metadata = finishing.finish(None,None)
    assert plan is None
    assert 'moves' in metadata['local_skip']


def test_native_inherited_declaration_copies_only_adjustable_tanks():
    from stowage.model import MovePlan
    instance = toy_instance()
    instance.vessel.tanks.append(NS(id='FIXED',adjustable=False))
    instance.onboard.tank_masses_kg['FIXED'] = 12.
    raw = MovePlan(id='test',instance_id='i',call_index=0)
    declared = finishing.inherited_plan(instance, raw)
    assert raw.ballast is None
    assert declared.ballast.tank_masses_kg == {'PAIR-P':50.}
    assert finishing.inherited_plan(instance,declared) is declared
