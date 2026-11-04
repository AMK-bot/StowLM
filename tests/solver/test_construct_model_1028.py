"""Construction rows against independent static-checker witnesses.

Fixtures deliberately omit hydrostatics: these are assignment/certificate tests,
not claims that placeholder fixture hulls pass the full checker.
"""
from ortools.linear_solver import pywraplp

from stowage.conventions import make_container_id
from stowage.model import Placement
from stowage.model.fixtures import build_instance
from stowage.solver.construct import (
    _build_assignment_model, _capacity_certificate, _nogood, _selected, construct,
)


def plain_instance():
    instance = build_instance()
    template = instance.containers[2]
    boxes = [template.model_copy(update={'id':b.id, 'special_stowage':[], 'oog':None,
             'dg':None, 'reefer':False, 'pod':'THSGZ'}) for b in instance.containers[1:3]]
    vessel = instance.vessel.model_copy(update={'hull':None, 'bridge':None})
    return instance.model_copy(update={'vessel':vessel, 'containers':boxes,
                                      'load_list':[b.id for b in boxes]})


def force(instance, assignments):
    model = _build_assignment_model(instance, physics=False)
    for cid,slot in assignments.items():
        model.solver.Add(model.x[cid,slot] == 1)
    model.solver.SetTimeLimit(1000)
    return model,model.solver.Solve()


def test_complete_assignment_preserves_capacity_and_checker_weight_order():
    from stowage.checker.rules import check_static_rules
    instance = plain_instance()
    boxes = [instance.containers[0].model_copy(update={'gross_mass_kg':25000}),instance.containers[1]]
    instance = instance.model_copy(update={'containers':boxes})
    placements = {boxes[0].id:'0010102', boxes[1].id:'0010104'}
    model,status = force(instance,placements)
    assert status in (pywraplp.Solver.OPTIMAL,pywraplp.Solver.FEASIBLE)
    assert _selected(model) == placements
    ship = instance.onboard.model_copy(update={'placements':[
        Placement(container_id=cid,slot=slot) for cid,slot in placements.items()]})
    assert not [v for v in check_static_rules(instance,ship,call_index=0).violations if v.severity=='hard']
    # The independent checker rejects the inversion; the model must too.
    inverse = {boxes[1].id:'0010102', boxes[0].id:'0010104'}
    _,status = force(instance,inverse)
    assert status == pywraplp.Solver.INFEASIBLE


def test_bottom_up_no_overlap_and_stack_mass_are_hard():
    instance = plain_instance()
    a,b = instance.load_list
    for positions in ({a:'0010104',b:'0030102'}, {a:'0010102',b:'0010102'}):
        _,status=force(instance,positions)
        assert status == pywraplp.Solver.INFEASIBLE
    stacks = [s.model_copy(update={'max_mass_kg':20000}) for s in instance.vessel.stacks]
    instance=instance.model_copy(update={'vessel':instance.vessel.model_copy(update={'stacks':stacks})})
    _,status=force(instance,{a:'0010102',b:'0010104'})
    assert status == pywraplp.Solver.INFEASIBLE


def test_lazy_nogood_excludes_only_witness_assignment():
    instance=plain_instance()
    a,b=instance.load_list
    model,status=force(instance,{a:'0010102',b:'0030102'})
    assert status in (0,1)
    chosen=_selected(model)
    assert _nogood(model,chosen)
    assert model.solver.Solve() == pywraplp.Solver.INFEASIBLE
    free=_build_assignment_model(instance,physics=False)
    assert _nogood(free,chosen)
    free.solver.SetTimeLimit(1000)
    assert free.solver.Solve() in (0,1)
    assert _selected(free)!=chosen


def test_certificate_is_capacity_relaxation_not_conservative_model_failure():
    instance=plain_instance()
    assert _capacity_certificate(instance) is None
    # More twenty-foot loads than all physical cells, while every original
    # stack, hydrostatic and securing constraint is deliberately omitted.
    template=instance.containers[0]
    boxes=[template.model_copy(update={'id':make_container_id('QZXU',n)})
           for n in range(len(instance.vessel.cells)+1)]
    instance=instance.model_copy(update={'containers':boxes,'load_list':[b.id for b in boxes]})
    certificate=_capacity_certificate(instance)
    assert certificate and 'LP is infeasible' in certificate
    assert 'No D49 empirical row used' in certificate
    assert 'all stability/strength' in certificate


def test_zero_budget_never_returns_unchecked_plan():
    outcome=construct(plain_instance(),time_limit_s=0)
    assert outcome.status=='timeout'
    assert outcome.plan is None and not outcome.stats['checker_passed']


def test_unary_mass_certificate_reads_checker_rating_and_ignores_proxy():
    from stowage.solver.construct import _checker_mass_rating, _unary_certificate
    rating=_checker_mass_rating()
    assert rating is not None
    limit,digest=rating
    assert len(digest)==64
    instance=plain_instance()
    assert _unary_certificate(instance) is None
    boxes=[instance.containers[0].model_copy(update={'gross_mass_kg':limit+1}),
           instance.containers[1]]
    instance=instance.model_copy(update={'containers':boxes})
    # No hull is available; the exact unary contradiction is independent of
    # physics. A proxy-based certificate would incorrectly depend on the hull.
    outcome=construct(instance,time_limit_s=1)
    assert outcome.status=='infeasible' and outcome.plan is None
    assert outcome.stats['certificate_kind']=='exact_checker_unary_mass'
    assert f'{limit:g} kg' in outcome.certificate
    assert digest in outcome.certificate


def test_unary_certificate_declines_changed_soft_checker_guard(monkeypatch):
    import inspect
    from stowage.checker.lashing import _check_lashing
    from stowage.solver.construct import _checker_mass_rating
    source=inspect.getsource(_check_lashing)
    monkeypatch.setattr(inspect,'getsource',lambda function:source.replace('severity="hard"', 'severity="tolerated"'))
    assert _checker_mass_rating() is None


def test_unavailable_qualification_is_not_reported_as_budget_timeout(monkeypatch):
    import stowage.solver.construct as module
    def unavailable(*args,**kwargs):
        raise ValueError('production linear policy epoch is stale; requalify')
    monkeypatch.setattr(module,'_build_assignment_model',unavailable)
    answer=module.ConstructivePlanner().plan(plain_instance(),time_limit_s=10)
    assert answer.status=='dead_end'
    assert answer.metrics['attribution']=='required linear qualification unavailable'
    assert answer.message=='required linear qualification unavailable'


def test_assignment_physics_defaults_necessary_but_supports_conservative(monkeypatch):
    import stowage.solver.construct_physics as physics
    seen=[]
    def record(solver,instance,x,*,mode,deadline):
        seen.append(mode)
        return object()
    monkeypatch.setattr(physics,'add_construct_physics',record)
    _build_assignment_model(plain_instance())
    _build_assignment_model(plain_instance(),direction='conservative')
    assert seen==['necessary','conservative']


def test_heat_lazy_cut_keeps_unrelated_assignments_available():
    from stowage.model import Violation
    from stowage.solver.construct import _static_cuts
    instance=plain_instance()
    a,b=instance.load_list
    selected={a:'0010102',b:'0030102'}
    model=_build_assignment_model(instance,physics=False)
    violation=Violation(rule_id='expert.e8.away_from_heat',severity='hard',call_index=0,
                        containers=[a],slots=[selected[a]],message='Heat witness at floor')
    assert _static_cuts(model,instance,selected,[violation])==1
    # A floor-level heat violation is independent of the unrelated b box:
    # keep b's assignment fixed and move a somewhere else.
    model.solver.Add(model.x[b,selected[b]]==1)
    model.solver.SetTimeLimit(1000)
    assert model.solver.Solve() in (0,1)
    alternative=_selected(model)
    assert alternative[b]==selected[b] and alternative[a]!=selected[a]


def test_long_slot_requires_equal_actual_support_roofs_with_height_auxiliaries():
    from stowage.actions.expander import Expander
    from stowage.solver.securing_geometry import securing_state_violations
    instance=plain_instance()
    first,second=instance.containers
    long_box=first.model_copy(update={'id':make_container_id('QZXU',777777),
                                      'length_ft':40,'iso_type':'42G1'})
    for difference,expected in ((0.,True),(.305,False)):
        boxes=[first,second.model_copy(update={'height_m':first.height_m+difference}),long_box]
        case=instance.model_copy(update={'containers':boxes,'load_list':[b.id for b in boxes]})
        placement={first.id:'0010182',second.id:'0030182',long_box.id:'0020184'}
        model,status=force(case,placement)
        assert bool(status in (0,1)) is expected
        assert model.height_vars.keys()==set(model.tables.cell_ids)
        ship=case.onboard.model_copy(update={'placements':[
            Placement(container_id=cid,slot=slot) for cid,slot in placement.items()]})
        ex=Expander(case.model_copy(update={'onboard':ship}))
        violations=securing_state_violations(case,ex)
        assert bool(violations) is not expected


def test_mixed_stack_keeps_native_general_cap_and_checker_envelope():
    """The native mask and checker select different mixed-column ratings."""
    from stowage.checker.rules import check_static_rules
    instance=plain_instance()
    first,second=instance.containers
    long_box=first.model_copy(update={'id':make_container_id('QZXU',777778),
                                      'length_ft':40,'iso_type':'42G1'})
    boxes=[first,second,long_box]  # all 12 t: each mixed support carries 18 t
    selected={first.id:'0010182',second.id:'0030182',long_box.id:'0020184'}
    for general_cap,twenty_cap,feasible,checker_pass in (
            (15000.,30000.,False,True),(20000.,30000.,True,True),
            (30000.,15000.,False,False)):
        stacks=[stack.model_copy(update={'max_mass_kg':general_cap,
                    'max_mass_20_kg':twenty_cap,'max_mass_40_kg':30000.})
                for stack in instance.vessel.stacks]
        case=instance.model_copy(update={'containers':boxes,'load_list':[b.id for b in boxes],
            'vessel':instance.vessel.model_copy(update={'stacks':stacks})})
        ship=case.onboard.model_copy(update={'placements':[
            Placement(container_id=cid,slot=slot) for cid,slot in selected.items()]})
        # Both states pass the checker because any 20-foot cargo selects its
        # declared 30 t rating. The first state violates the production mask's
        # 15 t general rating, so construction must reject that assignment.
        report=check_static_rules(case,ship,call_index=0)
        hard=[v.rule_id for v in report.violations if v.severity=='hard']
        assert (not hard) is checker_pass
        if not checker_pass:
            assert set(hard)=={'expert.physical.stack_weight'}
        model,status=force(case,selected)
        assert (status in (0,1)) is feasible
        if checker_pass and not feasible:
            assert _capacity_certificate(case) is None
        if feasible:
            mixed=[values['mixed'].solution_value()
                   for values in model.stack_presence_vars.values()]
            assert sum(v>.5 for v in mixed)==2
