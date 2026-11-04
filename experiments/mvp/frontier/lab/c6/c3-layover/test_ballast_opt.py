"""Count-bounded local finishing keeps the unchanged full checker authoritative."""
from types import SimpleNamespace as NS
from pathlib import Path
import math
import numpy as np
import pytest
import ballast_opt as opt

HERE = Path(__file__).resolve().parent


def fake_environment(monkeypatch, *, feasible=False, cached=False, tank_ids=('T-P',)):
    events = NS(calls=[],lp=[],checks=0)
    class Candidate:
        def model_dump(self, **kwargs):return {'moves':[]}
    class Evaluator:
        def __init__(self,instance,plan):
            self.capacity_kg=np.ones(len(tank_ids))
            self.initial_fractions=np.full(len(tank_ids),.2)
            self.tank_ids=tank_ids
            self.evaluations=0;self.cache_hits=0
            self.ship=NS(placements=[NS(container_id='cargo')])
        def evaluate_fractions(self,x,*,parts,deadline):
            events.calls.append(dict(x=x.copy(),deadline=deadline))
            if cached:self.cache_hits+=1
            else:self.evaluations+=1
            value=float(x[0]-.8)
            return dict(completed=True,physics_feasible=feasible,
                margins={'area':value},raw_margins={'containership_area_0_flood_mrad':value},
                wall_s=0.,metrics={},violation_counts={},
                raw_loading={phase:{'tcg_m':0.} for phase in ('departure','mid_voyage','arrival')})
        def candidate_plan(self,masses):return Candidate()
    def lp(c,*,A_ub,b_ub,bounds,method,options):
        events.lp.append(dict(bounds=bounds,options=options))
        # One tank-pair direction, one margin slack and one absolute-step var.
        n=(len(c)-1)//2
        return NS(status=0,x=np.r_[[bounds[j][1] for j in range(n)], np.zeros(len(c)-n)])
    def full(instance,plan):
        events.checks+=1
        return NS(passed=False,count=NS(weighted_nonproductive=5.),violations=[],
                  model_dump=lambda **kwargs:{'passed':False})
    monkeypatch.setattr(opt,'PhysicsEvaluator',Evaluator)
    monkeypatch.setattr(opt,'linprog',lp)
    monkeypatch.setattr(opt,'check',full)
    monkeypatch.setattr(opt,'write',lambda *args:None)
    return NS(id='test',load_list=['cargo']), Candidate(), events


def test_work_bound_ignores_expired_wall_and_counts_cached_requests(monkeypatch):
    instance,plan,events=fake_environment(monkeypatch,cached=True)
    _,meta=opt.optimize(instance,plan,out=HERE/'results/test-ballast-opt/cached',
        budget_s=-100.,work_bounded=True,max_evaluations=2,max_iterations=10,
        max_lp_iterations=17)
    assert len(events.calls)==meta['evaluations']==2
    assert all(math.isinf(call['deadline']) for call in events.calls)
    assert events.lp[0]['options']=={'threads':1,'maxiter':17}
    assert events.checks==1 and meta['full_checker_passed'] is False
    assert 'budget exhausted' in meta['error']


def test_default_expired_wall_still_stops_before_native_probe(monkeypatch):
    instance,plan,events=fake_environment(monkeypatch)
    _,meta=opt.optimize(instance,plan,out=HERE/'results/test-ballast-opt/wall',budget_s=-100.)
    assert events.calls==[]
    assert meta['evaluations']==0 and events.checks==1


def test_configured_radius_and_lp_iteration_budget_are_forwarded(monkeypatch):
    instance,plan,events=fake_environment(monkeypatch)
    _,meta=opt.optimize(instance,plan,out=HERE/'results/test-ballast-opt/radius',
        work_bounded=True,max_evaluations=4,max_iterations=1,max_lp_iterations=23,
        trust_radius=.15,max_trust_radius=.3)
    assert events.lp[0]['bounds'][0]==(-.15,.15)
    assert events.lp[0]['options']=={'threads':1,'maxiter':23}
    assert meta['initial_trust_radius']==.15
    assert meta['accepted_trust_radius_cap']==.3
    assert meta['lp_calls']==1
    assert len(events.calls)<=4


def test_physics_feasible_does_not_override_full_checker(monkeypatch):
    instance,plan,events=fake_environment(monkeypatch,feasible=True)
    _,meta=opt.optimize(instance,plan,out=HERE/'results/test-ballast-opt/gate',
        work_bounded=True,max_evaluations=2,max_iterations=2)
    assert len(events.calls)==1 and events.lp==[]
    assert events.checks==1 and meta['full_checker_passed'] is False


@pytest.mark.parametrize('radius',[0.,-1.,float('nan')])
def test_invalid_radius_is_rejected_before_any_native_work(monkeypatch,radius):
    instance,plan,events=fake_environment(monkeypatch)
    with pytest.raises(ValueError,match='trust_radius'):
        opt.optimize(instance,plan,out=HERE/'results/test-ballast-opt/invalid',trust_radius=radius)
    assert events.calls==[] and events.checks==0


def test_optional_wing_direction_is_selected_without_changing_default_groups():
    groups={'DB-F':[0,1],'WING-A':[2,3],'HEEL':[4,5],'DB-M':[6,7]}
    assert list(opt._select_groups(groups))==['DB-F','DB-M','HEEL']
    assert list(opt._select_groups(groups,['DB-F','WING-A','HEEL']))==['DB-F','WING-A','HEEL']


def test_optimizer_moves_optional_wing_pair_and_records_actual_groups(monkeypatch):
    ids=('DB-F-P','DB-F-S','WING-A-P','WING-A-S')
    instance,plan,events=fake_environment(monkeypatch,tank_ids=ids)
    _,meta=opt.optimize(instance,plan,out=HERE/'results/test-ballast-opt/wing',
        work_bounded=True,max_evaluations=4,max_iterations=1,preferred_groups=['WING-A'])
    assert meta['group_restriction']==['WING-A']
    assert meta['requested_preferred_groups']==['WING-A']
    assert np.array_equal(events.calls[1]['x'][:2],[.2,.2])
    assert np.all(events.calls[1]['x'][2:]>.2)
    assert len(events.calls)<=4


@pytest.mark.parametrize('names',['WING-A',[],[float('nan')],['bad name']])
def test_preferred_groups_rejects_invalid_finite_list(names):
    with pytest.raises(ValueError,match='preferred_groups'):
        opt._select_groups({'WING-A':[0,1]},names)
