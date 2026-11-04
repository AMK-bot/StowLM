"""Admissibility checks against unchanged final-state geometric rules."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent))
from copy import deepcopy
from fractions import Fraction
from itertools import permutations
import pytest
from ortools.sat.python import cp_model
from bound_tests import fixture
from stowage.model import Instance, Placement
from stowage.feasibility.tables import build_tables
from stowage.checker.rules_special import special_violations
from stowage.checker.rules import check_static_rules
from stowage.rules.catalogue import rule_freeze_v1
from bound import build_model
from strengthening import safe_unary_mask
from geometry_cuts import floor, ceil, SCALE, add_floor_domains, add_oog_pair, add_heat, add_all_oog_pairs


def extended_fixture(*, oog=False, heat=False):
    data = fixture().model_dump()
    for bay in ('001','003'):
        stack = next(s for s in data['vessel']['stacks'] if s['id']==bay+'01D')
        stack['cell_ids'].append(bay+'0184')
        cell = deepcopy(next(c for c in data['vessel']['cells'] if c['id']==bay+'0182'))
        cell['id']=bay+'0184'
        data['vessel']['cells'].append(cell)
    if oog:
        data['containers'][0]['oog'] = {'over_length_fore_m':20.}
    if heat:
        left = next(c for c in data['vessel']['cells'] if c['id']=='0010182')
        deck = next(s for s in data['vessel']['stacks'] if s['id']=='00101D')
        data['vessel']['heat_sources'] = [dict(id='test-heat',kind='engine_room',
            x_min_m=left['lcg_m']-3.,x_max_m=left['lcg_m']+3.,
            y_min_m=left['tcg_m']-1.,y_max_m=left['tcg_m']+1.,
            z_min_m=deck['base_z_m'],z_max_m=deck['base_z_m']+2.591)]
        data['rule_overrides']['expert.e8.away_from_heat']={'minimum_clearance':0.}
        for tank in data['vessel']['tanks']:
            tank['heated']=False
    return Instance.model_validate(data)


def context_fixed(instance,slots):
    T=build_tables(instance)
    T.static[:]=False
    assignment=dict(zip(instance.load_list,slots))
    for bid,slot in assignment.items():
        T.static[T.box_index[bid],T.slot_index[slot]]=True
    return build_model(instance,tables=T,budget_s=5),assignment


def ship_for(instance,assignment):
    return instance.onboard.model_copy(update={'placements':[
        Placement(container_id=bid,slot=slot) for bid,slot in assignment.items()], 'covers_off':{}})


def solve(context):
    solver=cp_model.CpSolver()
    solver.parameters.num_search_workers=1
    solver.parameters.max_time_in_seconds=2
    return solver,solver.solve(context['model'])


def test_floor_rounding_outward_binary64_exact():
    for value in [0.,-.1,.1,2.591,2.896,9.2,19441.60732302192]:
        f=Fraction(value)
        assert Fraction(floor(value),SCALE)<=f<=Fraction(ceil(value),SCALE)


def test_heat_unary_never_removes_exhaustive_static_legal_stow():
    with rule_freeze_v1(enabled=False):
        instance=extended_fixture(heat=True)
        data=instance.model_dump()
        data['containers'][0]['special_stowage']=['away_from_heat']
        instance=Instance.model_validate(data)
        T=build_tables(instance)
        mask,info=safe_unary_mask(instance,T)
        accepted=0
        cells=[sid for si,sid in enumerate(T.slot_ids) if T.slot_n[si]==1]
        for slots in permutations(cells,3):
            assignment=dict(zip(instance.load_list,slots))
            report=check_static_rules(instance,ship_for(instance,assignment),call_index=instance.call_index)
            if any(v.severity=='hard' for v in report.violations):
                continue
            accepted+=1
            for bid,slot in assignment.items():
                assert mask[T.box_index[bid],T.slot_index[slot]]
        assert accepted>0
        assert info['heat_unary_removed']>0


@pytest.mark.parametrize('safe', [False,True])
@pytest.mark.parametrize('eager', [False,True])
def test_oog_disjunction_matches_actual_vertical_pair_rule(safe,eager):
    with rule_freeze_v1(enabled=False):
        instance=extended_fixture(oog=True)
        slots=['0010184','0030182','0010182'] if safe else ['0010182','0030182','0010102']
        context,assignment=context_fixed(instance,slots)
        a,b=instance.load_list[:2]
        violations=special_violations(instance,ship_for(instance,assignment),call_index=instance.call_index)
        pair_bad=any(v.rule_id=='expert.e9.over_width_length' and set(v.containers)=={a,b} for v in violations)
        assert pair_bad != safe
        if eager:
            assert add_all_oog_pairs(context)>0
        else:
            assert add_oog_pair(context,assignment,{a,b})
        _,status=solve(context)
        assert status==(cp_model.OPTIMAL if safe else cp_model.INFEASIBLE)


@pytest.mark.parametrize('safe', [False,True])
def test_heat_disjunction_matches_actual_box_heat_rule(safe):
    with rule_freeze_v1(enabled=False):
        instance=extended_fixture(heat=True)
        slots=['0030184','0010182','0030182'] if safe else ['0010182','0030182','0010102']
        context,assignment=context_fixed(instance,slots)
        bid=instance.load_list[0]
        data=instance.model_dump()
        # A separate source is centered on the candidate's physical column.
        if safe:
            cell=next(c for c in data['vessel']['cells'] if c['id']=='0030182')
            source=data['vessel']['heat_sources'][0]
            source.update(x_min_m=cell['lcg_m']-3.,x_max_m=cell['lcg_m']+3.,
                          y_min_m=cell['tcg_m']-1.,y_max_m=cell['tcg_m']+1.)
        data['containers'][0]['special_stowage']=['away_from_heat']
        instance=Instance.model_validate(data)
        context['instance']=instance
        violations=special_violations(instance,ship_for(instance,assignment),call_index=instance.call_index)
        bad=any(v.rule_id=='expert.e8.away_from_heat' and bid in v.containers for v in violations)
        assert bad != safe
        assert add_heat(context,assignment,bid)
        _,status=solve(context)
        assert status==(cp_model.OPTIMAL if safe else cp_model.INFEASIBLE)


def test_floor_domains_contain_actual_supported_regular_heights():
    with rule_freeze_v1(enabled=False):
        instance=extended_fixture()
        slots=['0030184','0010182','0030182']
        context,assignment=context_fixed(instance,slots)
        add_floor_domains(context)
        solver,status=solve(context)
        assert status==cp_model.OPTIMAL
        T=context['T']
        for bid,slot in assignment.items():
            si=T.slot_index[slot]
            actual=max(float(T.stack_base_z[int(sj)])+int(k)*2.591
                       for sj,k in zip(T.slot_stacks[si],T.slot_k[si]) if sj>=0)
            assert solver.value(context['slot_floor_lo'][si])/SCALE<=actual+1e-10
            assert solver.value(context['slot_floor_hi'][si])/SCALE>=actual-1e-10


def test_integral_cutoff_infeasibility_is_global_lower_bound():
    from certified import lower_bound
    with rule_freeze_v1(enabled=False):
        result=lower_bound(fixture(),budget_s=5,upper_bound=4)
    assert result.lower_bound==4
    assert result.status in {'threshold_infeasible','initial_bound_above_cutoff'}
    assert result.support_flags['integer_objective_cutoff']==3
    assert result.support_flags['load_once_restriction'] is False
    assert result.support_flags['arbitrary_current_restows_allowed'] is True
    assert result.proof_family.startswith('global_arbitrary_current_moves')


@pytest.mark.parametrize('value', [-1.,float('inf'),float('nan')])
def test_invalid_objective_weights_never_get_cutoff_certificate(value):
    from certified import lower_bound
    instance=fixture()
    instance.objective.__dict__['restow']=value
    with pytest.raises(ValueError,match='finite nonnegative'):
        lower_bound(instance,budget_s=1,upper_bound=4)


def test_nonintegral_objective_does_not_use_integer_cutoff():
    from certified import lower_bound
    instance=fixture(future=.25)
    result=lower_bound(instance,budget_s=5,upper_bound=2.5)
    assert result.support_flags['integer_objective_cutoff'] is None
    assert result.lower_bound<=2.5


def test_securing_source_guard_returns_unknown_without_certificate(monkeypatch):
    import bound_securing
    monkeypatch.setattr(bound_securing,'LASHING_SOURCE_SHA256','0'*64)
    result=bound_securing.securing_bound(fixture(),fixture().onboard)
    assert result['status']=='unknown'
    assert result['proof'] is None


def test_reviewed_rod_compliance_monotonicity_selftest():
    from bound_securing import selftest
    result=selftest()
    assert result['cases']==128


def test_real_pure_long_securing_cut_rejects_bad_stow_preserves_checked_eight():
    import json
    from stowage.io import load_instance
    from bound_joint import build_model as build_joint
    from securing_cuts import add_securing_cut
    here=Path(__file__).parent
    bad=json.loads((here/'results/ordered25.json').read_text())['result']['relaxation_assignment']
    good=json.loads((here/'results/pilot25b/sim.small.40100025.json').read_text())
    assert good['passed'] and good['lifts']==8
    instance=load_instance('experiments/mvp/corpus/val/small/feeder_800/simulated_yard/seed-040100025.json')
    with rule_freeze_v1(enabled=False):
        for assignment,expected in [(bad,cp_model.INFEASIBLE),(good['assignment'],cp_model.OPTIMAL)]:
            context=build_joint(instance,budget_s=15)
            cut,proof=add_securing_cut(context,bad,ship_for(instance,bad))
            assert cut['kind']=='source_pinned_pure_long_rod_columns'
            assert cut['ratio']>1
            assert proof['dependency_sha256']['checker.visibility']
            T=context['T']
            for bid,slot in assignment.items():
                context['model'].add(context['chosen'][T.box_index[bid],T.slot_index[slot]]==1)
            _,status=solve(context)
            assert status==expected


def test_dg_no_ship_screen_retains_supported_upper_geometry_counterexample():
    """Reuse the unchanged synthetic SW2 fixture; import no live engine code."""
    import importlib.util
    from catalog import ROOT
    from stowage.checker.rules import placement_violations
    from stowage.model.yard import YardStack
    spec=importlib.util.spec_from_file_location('_c3_synthetic_sw2_fixture',
        ROOT/'tests/feasibility/test_w2_tables.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance,box=module._case(dg=True)
    support=next(b for b in instance.containers if b.id!=box.id and b.length_ft==20)
    support.dg,support.oog,support.special_stowage=None,None,[]
    support.reefer,support.reefer_setpoint_c=False,None
    support.gross_mass_kg,support.height_m,support.pod=15000,3.2,'THBKK'
    instance.containers,instance.load_list=[box,support],[box.id,support.id]
    instance.onboard.placements=[]
    instance.discharge_list=[]
    instance.yard.stacks=[YardStack(key='A-001-01',length_ft=20,boxes=instance.load_list)]
    instance.vessel.fixed_loads=[]
    module._registered_geometry(instance,box,['0010184'])
    with rule_freeze_v1(enabled=False):
        tables=build_tables(instance)
        mask,info=safe_unary_mask(instance,tables)
        row,col=tables.box_index[box.id],tables.slot_index['0010184']
        assert not tables.static[row,col]
        assert mask[row,col]
        ship=instance.onboard.model_copy(update={'placements':[
            Placement(container_id=support.id,slot='0010182')]})
        assert not any(v.severity=='hard' for v in placement_violations(instance,ship,box,'0010184'))
        assert info['dg_rows_rebuilt_optimistically']==1


def test_oog_aware_bottom_e6_cut_preserves_exhaustive_static_legal_stows():
    """The new bottom implication credits all possible lateral OOG blockers."""
    with rule_freeze_v1(enabled=False):
        instance=extended_fixture()
        data=instance.model_dump()
        data['containers'][0]['gross_mass_kg']=23000
        data['containers'][1]['oog']={'over_length_fore_m':20.}
        instance=Instance.model_validate(data)
        tables=build_tables(instance)
        context=build_model(instance,tables=tables,budget_s=10)
        assert context['e6_oog_cuts']>0
        eligible=[sid for si,sid in enumerate(tables.slot_ids) if tables.slot_n[si]==1]
        accepted=0
        for slots in permutations(eligible,3):
            assignment=dict(zip(instance.load_list,slots))
            report=check_static_rules(instance,ship_for(instance,assignment),call_index=instance.call_index)
            if any(v.severity=='hard' for v in report.violations):
                continue
            accepted+=1
            trial=context['model'].clone()
            for bid,slot in assignment.items():
                trial.add(context['chosen'][tables.box_index[bid],tables.slot_index[slot]]==1)
            solver=cp_model.CpSolver()
            solver.parameters.num_search_workers=1
            solver.parameters.max_time_in_seconds=2
            assert solver.solve(trial)==cp_model.OPTIMAL
        assert accepted>0


def test_initial_bound_cutoff_skips_joint_model(monkeypatch):
    import bound_joint
    from certified import lower_bound
    def unexpected(*args,**kwargs):
        raise AssertionError('joint construction is unnecessary after initial proof')
    monkeypatch.setattr(bound_joint,'build_model',unexpected)
    with rule_freeze_v1(enabled=False):
        result=lower_bound(fixture(),budget_s=5,upper_bound=4)
    assert result.lower_bound==4
    assert result.status=='initial_bound_above_cutoff'
    assert result.lazy_iterations==0
