"""Joint loading assembly bookkeeping and warm native-reference integration."""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import numpy as np
import pytest
from joint_loading import JointLoading,cargo_mass_moments,reference_balance_offsets,pressed_reference_gm,tank_objective

HERE=Path(__file__).resolve().parent


def simple_cargo():
    boxes=[NS(id='fixed',gross_mass_kg=10000.,height_m=2.591),NS(id='new',gross_mass_kg=8000.,height_m=2.896)]
    cells=[NS(id='0010102',lcg_m=90.,tcg_m=2.),NS(id='0010104',lcg_m=90.,tcg_m=2.)]
    stack=NS(base_z_m=4.,cell_ids=[c.id for c in cells])
    instance=NS(containers=boxes,vessel=NS(cells=cells,stacks=[stack]))
    ship=NS(placements=[NS(container_id=b.id,slot=c.id) for b,c in zip(boxes,cells)])
    return instance,ship


def test_removed_cargo_is_native_body_support_moment_once():
    instance,ship=simple_cargo()
    v=cargo_mass_moments(instance,ship,{'fixed','new'})
    expected=np.array([18000.,18000.*90,18000.*2,10000.*(4+2.591/2)+8000.*(4+2.591+2.896/2)])
    np.testing.assert_allclose(v,expected,rtol=0,atol=1e-8)
    fixed=cargo_mass_moments(instance,ship,{'fixed'})
    assert fixed[0]==10000.
    assert fixed[3]==10000.*(4+2.591/2)


def test_removed_cargo_fails_closed_on_missing_modeled_box():
    instance,ship=simple_cargo()
    with pytest.raises(ValueError,match='every modeled box'):
        cargo_mass_moments(instance,ship,{'fixed','missing'})


def test_reference_balance_bias_has_additive_rhs_sign(monkeypatch):
    import ballast_loading
    monkeypatch.setattr(ballast_loading,'_tank_reference',lambda ctx,mode='half':(np.array([[90.,0.,1.]]),0.))
    point=NS(displacement_kg=19950.,lcb_m=85.)
    ctx=NS(direct_geometry=NS(full_submerged=lambda ta,tf,heel,rho:point),rho=1025.,
           base=NS(mass_kg=10000.),base_moments=np.array([800000.,0.,0.]),capacity=np.array([20000.]))
    offsets,metadata=reference_balance_offsets(ctx,(6.,6.),[.5])
    assert offsets=={'buoyancy_kg':50.,'longitudinal_kgm':4250.}
    assert metadata['reference_model_mass_kg']==20000.
    assert metadata['reference_model_longitudinal_kgm']==1700000.
    # Physical RHS + residual equals the model's reference balance.
    assert point.displacement_kg+offsets['buoyancy_kg']==20000.
    assert point.displacement_kg*point.lcb_m+offsets['longitudinal_kgm']==1700000.


def test_candidate_recipe_preserves_capacity_and_rejects_bad_values():
    joint=JointLoading.__new__(JointLoading)
    joint.tank_ids=('A','B');joint.capacity=np.array([10000.,20000.]);joint.quantum=np.ones(8)
    recipe=joint.candidate_recipe([.25,.8,1,1,1,1,1,1])
    assert recipe.tank_masses_kg=={'A':2500.,'B':16000.}
    with pytest.raises(ValueError):joint.candidate_recipe([1.2,.8,1,1,1,1,1,1])
    with pytest.raises(ValueError):joint.candidate_recipe([.2,.8])


def test_pressed_recipe_rejects_fractional_relaxation_and_snaps_tolerance():
    joint=JointLoading.__new__(JointLoading)
    joint.tank_ids=('A',);joint.capacity=np.array([10000.]);joint.quantum=np.ones(7);joint.tank_mode='pressed'
    with pytest.raises(ValueError,match='binary'):
        joint.candidate_recipe([.5,1,1,1,1,1,1])
    recipe=joint.candidate_recipe([1.-1e-8,1,1,1,1,1,1])
    assert recipe.tank_masses_kg=={'A':10000.}


def test_pressed_gm_removes_adjustable_slack_fsm_and_cg_bias(monkeypatch):
    import ballast_loading
    monkeypatch.setattr(ballast_loading,'_tank_reference',lambda ctx,mode='half':(np.array([[90.,0.,2.]]),2000.))
    solid=NS(capacity_m3=10.,liquid=lambda v,heel,trim:NS(fsm_m4=0. if v==10. else 10.,centroid_m=(90.,0.,2. if v==10. else 1.)))
    tank=NS(id='water',content_density_kg_m3=1000.)
    ctx=NS(capacity=np.array([10000.]),tanks=[tank],solids={'water':solid},instance=NS(vessel=NS(hull=NS(lpp_m=100.))))
    probe=dict(heel_rad=.01,trim_m=.1,mass_kg=1000000.,gm_m=1.2)
    value,meta=pressed_reference_gm(ctx,probe,[.5])
    assert abs(value-1.205)<1e-12 # +.010 adjustable FSC, -.005 full-arm CG.
    assert meta['actual_adjustable_fsm_kgm']==10000.
    assert meta['selected_full_arm_vertical_kgm']==10000.
    full,meta=pressed_reference_gm(ctx,probe,[1.])
    assert full==probe['gm_m']
    assert meta['added_adjustable_fsc_m']==0.
    assert meta['adjustable_vertical_correction_m']==0.


def test_tank_objective_prices_tonnes_and_tonne_metres_per_fraction():
    costs=tank_objective([100000.,200000.],[1.,12.],mass_reward_per_t=.1,vertical_cost_per_tm=.01)
    np.testing.assert_allclose(costs,[-8.999,4.001],rtol=0.,atol=1e-12)
    # Physical fractions are bounded, so even a negative reward has finite
    # total contribution. Half fill buys half the full-capacity reward/price.
    assert abs(costs[0]*.5-(-4.4995))<1e-12
    np.testing.assert_array_equal(tank_objective([100000.,200000.],[1.,12.]),[.001,.001])


@pytest.mark.parametrize('kwargs',[
    dict(mass_reward_per_t=float('inf')),dict(vertical_cost_per_tm=float('nan')),
    dict(mass_reward_per_t=-.1),dict(vertical_cost_per_tm=-.1),dict(base_cost=float('nan'))])
def test_tank_objective_rejects_nonfinite_or_negative_prices(kwargs):
    with pytest.raises(ValueError):tank_objective([100000.],[1.],**kwargs)


def test_tank_objective_rejects_bad_physical_arrays():
    with pytest.raises(ValueError):tank_objective([float('inf')],[1.])
    with pytest.raises(ValueError):tank_objective([-1.],[1.])
    with pytest.raises(ValueError):tank_objective([100.],[float('nan')])
    with pytest.raises(ValueError):tank_objective([100.],[1.,2.])


@pytest.mark.parametrize('tank_mode',['half','pressed'])
def test_joint_builder_prepends_pair_matrix_and_does_not_mutate_context(monkeypatch,tank_mode):
    import ballast_loading
    instance,ship=simple_cargo()
    joint=JointLoading.__new__(JointLoading)
    ctx=NS(instance=instance,ship=ship,base=NS(mass_kg=20000.),base_moments=np.array([1.,2.,3.]))
    joint.instance=instance;joint.ship=ship;joint.contexts=[('departure',ctx)]
    joint.references=[(6.,6.)];joint.corrections={};joint.gm_floor=1.3
    joint.quantum=np.array([1e-4,1e-3,1e-3]);joint.equality_grid_reserve=False
    joint.tank_mode=tank_mode;joint.aux_integrality=np.array([int(tank_mode=='pressed'),0,0])
    joint.tcg_halfwidth_m=.005
    joint.kg_caps_m={'departure':7.}
    joint.bounds=[(0.,1.),(0.,12.),(0.,12.)];joint.cost=np.zeros(3)
    joint.metadata={};joint.tank_ids=('A',);joint.capacity=np.array([10000.])
    T=NS(box_ids=['fixed','new'],mass=np.array([10000.,8000.]))
    model=NS(boxes=[0,1],pages=[NS()],_slot_xy=lambda page,tier:(90.,2.))
    solver=NS(T=T,m=model,pair=[(0,0),(1,1)],slot_page={0:[(0,0)],1:[(0,1)]},slot_cg_z=lambda b,q:5.+b)
    seen={}
    def assemble(contexts,**kwargs):
        seen.update(kwargs)
        return np.array([[1.,2.,3.,4.,5.]]),np.array([6.]),['row'],[{}]
    monkeypatch.setattr(ballast_loading,'_assemble',assemble)
    spec=joint.build(solver)
    np.testing.assert_array_equal(seen['cargo_mass'],[10000.,8000.])
    np.testing.assert_array_equal(seen['cargo_moments'],[[900000.,20000.,50000.],[720000.,16000.,48000.]])
    assert seen['removed_cargo'][0][0]==18000.
    assert seen['auxiliary_quantum'] is None
    assert seen['tank_reference_mode']==tank_mode
    assert seen['tcg_halfwidth_m']==.005
    assert seen['kg_caps_m']=={'departure':7.}
    np.testing.assert_array_equal(spec['aux_integrality'],[int(tank_mode=='pressed'),0,0])
    np.testing.assert_array_equal(spec['A_pair'].toarray(),[[1.,2.]])
    np.testing.assert_array_equal(spec['A_aux'].toarray(),[[3.,4.,5.]])
    assert ctx.base.mass_kg==20000.
    np.testing.assert_array_equal(ctx.base_moments,[1.,2.,3.])


def test_warm_native_reference_subtraction_matches_exact_base():
    """Diagnostic unit probe only: reads one explicitly development saved stow."""
    from stowage.model import Instance,MovePlan
    from stowage.checker.stability_loading import loading_condition
    from arrival import prepare,restow_options
    from arrival_model import Model
    from alns import Problem
    from gap import Assignment,Settings
    from stow import reference
    path=HERE/'results/dev/v2-331401024/layover/train.layover.small.o30.s331401024.json'
    if not path.exists():pytest.skip('optional warm development witness absent')
    row=json.loads(path.read_text())
    instance=Instance.model_validate_json((HERE/'cohort/train.layover.small.o30.s331401024.json').read_text())
    plan=MovePlan.model_validate(row['replay_evidence'][0]['plan'])
    joint=JointLoading(instance,plan,gm_floor=1.3)
    assert len(joint.contexts)==3
    branch=prepare(instance,dict(restow_options(instance))['all'],label='all')
    p=Problem(branch.working);m=Model(p,branch)
    solver=Assignment(m,reference(instance.vessel.hull,1.3),Settings())
    spec=joint.build(solver)
    assert spec['A_pair'].shape[1]==solver.n
    assert spec['A_aux'].shape[1]==len(joint.tank_ids)+6
    assert all(all(band==0. for band in v.get('equality_bands_unscaled',{}).values()) for v in spec['metadata']['assembly'])
    reference_slots={p.container_id:p.slot for p in joint.ship.placements}
    pair_values=np.array([float(reference_slots.get(m.T.box_ids[b])==m.T.slot_ids[q]) for b,q in solver.pair])
    assert pair_values.sum()==len(m.boxes)
    aux=np.r_[joint.initial_fractions,np.asarray(joint.references).ravel()]
    residual=np.asarray(spec['A_pair']@pair_values+spec['A_aux']@aux-spec['b']).ravel()
    balances=[k for k,name in enumerate(spec['names']) if '/buoyancy/' in name or '/longitudinal_equilibrium/' in name]
    assert len(balances)==12
    assert max(abs(residual[balances]))<1e-9
    for (_,ctx),removed in zip(joint.contexts,spec['metadata']['removed_cargo_kg_kgm']):
        empty=ctx.fixed_ship.model_copy(update={'placements':[]})
        background=loading_condition(ctx.instance,empty,geometry=ctx.geometry,ship_params=ctx.params,
            free_surface_method=ctx.method,precomputed_soundings=True,small_tank_exemption=True)
        assert not background.errors
        assert abs(ctx.base.mass_kg-removed[0]-background.mass_kg)<1e-6
        expected=background.mass_kg*np.array([background.lcg_m,background.tcg_m,background.kg_m])
        np.testing.assert_allclose(ctx.base_moments-np.array(removed[1:]),expected,rtol=0,atol=1e-6)
    assert all(np.isfinite(q['native_minus_affine_m']) for q in joint.gm_calibration)
