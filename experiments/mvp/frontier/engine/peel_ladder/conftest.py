# Contains fixture copied from experiments/mvp/frontier/lab/c5/c5-peel/test_completion.py; engine-local imports.
"""Fixtures for copied C5 policy/matrix/controller tests in scoped imports."""
from types import SimpleNamespace
import importlib
import pytest


@pytest.fixture(autouse=True)
def frozen_test_namespace(request, monkeypatch):
    if not request.module.__name__.split('.')[-1].startswith('test_frozen_'):
        yield
        return
    from .loader import isolated
    with isolated() as method:
        method.load_assignment()
        policy = importlib.import_module('tiers')
        masks = importlib.import_module('assignment_masks')
        monkeypatch.setattr(request.module, 'method', method, raising=False)
        monkeypatch.setattr(request.module, 'masks', masks, raising=False)
        for name in ('ROOT','TABLE_PATH','TierTable','allowed','analyze_report','load_table',
                     'rank_report','tier_for','violation_breach'):
            monkeypatch.setattr(request.module, name, getattr(policy,name), raising=False)
        if request.module.__name__.endswith('test_frozen_elastic'):
            elastic = importlib.import_module('elastic')
            monkeypatch.setattr(request.module, 'ElasticAssignment', elastic.ElasticAssignment, raising=False)
            monkeypatch.setattr(request.module, 'Assignment', elastic.Assignment, raising=False)
        yield


@pytest.fixture
def synthetic(monkeypatch):
    # Copied fixture from lab/c5/c5-peel/test_completion.py; no stopped pages lane.
    from stowage.model import CheckReport, MoveCount, MovePlan, Violation
    import stowage.actions
    import stowage.moves
    import sys
    ids = ('QZXU0000014','QZXU0000020','QZXU0000035','QZXU0000040')
    boxes = [SimpleNamespace(id=bid,length_ft=length,gross_mass_kg=mass)
             for bid,length,mass in [(ids[0],20,1000.),(ids[1],40,2000.),
                                     (ids[2],20,5000.),(ids[3],20,3000.)]]
    instance = SimpleNamespace(id='synthetic-completion',call_index=0,
                               load_list=list(ids),containers=boxes,size_class='medium')
    loaded,reports = {},{}
    def make(name,aboard,*,rules=(),lifts=1.,breach=1.):
        plan=MovePlan(id=name,instance_id=instance.id,call_index=0)
        loaded[name]=set(aboard)
        left=sorted(set(ids)-set(aboard))
        broken=[('objective.all_loaded',None,None)] if left else []
        broken += [(rule,breach+1.,1.) for rule in rules]
        reports[name]=CheckReport(passed=not broken,shut_out=left,
            count=MoveCount(loads=len(aboard),weighted_nonproductive=lifts),
            violations=[Violation(rule_id=rule,severity='hard',call_index=0,
                measured=value,limit=limit,message='Synthetic policy test.')
                for rule,value,limit in broken])
        return plan
    def replay(instance,plan,**kwargs):
        return SimpleNamespace(legal=True,final_state=SimpleNamespace(ship=SimpleNamespace(
            placements=[SimpleNamespace(container_id=bid) for bid in loaded[plan.id]])))
    monkeypatch.setattr(stowage.moves,'apply_plan',replay)
    space=SimpleNamespace(from_plan=lambda instance,plan:tuple(
        SimpleNamespace(box=bid,slot='0010002') for bid in loaded[plan.id]))
    monkeypatch.setattr(stowage.actions,'get_action_space',lambda *args:space)
    for name in ('assignment_method','finishing'):
        module=sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module,'check',lambda instance,plan:reports[plan.id])
    return SimpleNamespace(instance=instance,make=make,reports=reports,loaded=loaded)
