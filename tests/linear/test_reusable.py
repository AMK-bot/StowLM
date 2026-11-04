"""A family excludes booking/background state; instance constants stay exact."""
from dataclasses import fields,replace
from types import SimpleNamespace as NS

import numpy as np
import pytest

from stowage.linear.model import LinearErrors
from stowage.linear.reusable import assemble_instance,coefficient_family,family_band


class Vessel(NS):
    def model_dump(self,**kwargs):
        return {key:(vars(value) if isinstance(value,NS) else value)
                for key,value in vars(self).items()}


class Instance(NS):
    def model_dump(self,**kwargs):
        return dict(vessel=self.vessel.model_dump(),port=vars(self.port),
            onboard=self.onboard.model_dump(mode='json'),
            containers=[box.model_dump(mode='json') for box in self.containers],
            load_list=self.load_list,discharge_list=self.discharge_list,
            rule_overrides=self.rule_overrides)


class BoxHull:
    lpp_m=10.;beam_m=2.;depth_m=10.;max_z_m=10.
    x_m=np.array([5-5/np.sqrt(3),5+5/np.sqrt(3)])
    integration_weights_m=np.array([5.,5.])
    sections=((np.array([[-1.,0.],[1.,0.],[1.,10.],[-1.,10.]]),),)*2
    def submerged(self,aft,fore,heel,density):
        area=2*(aft+(fore-aft)*self.x_m/10)
        volume=float(area@self.integration_weights_m)
        return NS(displacement_kg=volume*density,sectional_areas_m2=area,
                  volume_m3=volume,vcb_m=(aft+fore)/4,transverse_inertia_m4=20/3)
    def hydrostatic_row(self,t,density):
        return NS(draught_m=t,displacement_kg=20*t*density,kmt_m=t/2+1/(3*t),
                  lcb_m=5.,tpc_kg_per_cm=20*density/100)


@pytest.fixture
def context(monkeypatch,tmp_path):
    import stowage.linear.physics as physics
    import stowage.linear.cache as cache
    from stowage.model.vessel import Cell,Stack
    from stowage.model import ShipState
    monkeypatch.setattr(cache,'_DEFAULT_ROOT',tmp_path)
    monkeypatch.setattr(cache,'source_fingerprint',lambda:'test-source')
    monkeypatch.setattr(physics,'_geometry_from_params',lambda params:BoxHull())
    monkeypatch.setattr(physics,'_maximum_tank_fsms',lambda *args:np.array([]))
    hull=NS(lpp_m=10.,beam_m=2.,depth_m=10.,min_draught_aft_m=.1,min_draught_fwd_m=.1,
        max_draught_m=8.,max_trim_m=.5,max_list_rad=.1,min_gm_m=.15,
        lightship_mass_kg=20000.,lightship_lcg_m=5.,lightship_tcg_m=0.,lightship_vcg_m=1.,
        lightship_distribution=[(0.,10.,20000.)],
        stations=[dict(x_m=x,max_shear_n=1e8,min_shear_n=-1e8,max_bending_nm=1e9,min_bending_nm=-1e9)
                  for x in (0.,2.,5.,8.,10.)])
    # SimpleNamespace needs named station attributes, while the key needs JSON.
    class Hull(NS):
        pass
    hull.stations=[NS(**record) for record in hull.stations]
    class TestVessel(Vessel):
        def model_dump(self,**kwargs):
            return dict(id=self.id,name=getattr(self,'name',self.id),sources=getattr(self,'sources',[]),
                cells=[c.model_dump(mode='json') for c in self.cells],
                stacks=[s.model_dump(mode='json') for s in self.stacks],tanks=self.tanks,
                fixed_loads=self.fixed_loads,hull={**vars(self.hull),'stations':[vars(s) for s in self.hull.stations]})
    cell=Cell(id='0010102',stack_id='00101H',lcg_m=4.,tcg_m=0.,vcg_base_m=1.)
    stack=Stack(id='00101H',bay=1,row=1,on_deck=False,cell_ids=[cell.id],max_mass_kg=30000.,
                max_height_m=6.,base_z_m=1.,hatch_cover_id='HC-h1')
    vessel=TestVessel(id='unit-box',hull=hull,cells=[cell],stacks=[stack],tanks=[],fixed_loads=[],slice_of=None)
    instance=Instance(vessel=vessel,port=NS(water_density_kg_m3=1000.,max_draught_m=8.),
        onboard=ShipState(vessel_id='unit-box'),containers=[],load_list=[],discharge_list=[],rule_overrides={})
    params={'id':'unit-box','tanks':[]}
    return instance,params


def calibration(family):
    s=len(family.template.station_x_m)
    return LinearErrors(.01,.02,.001,.01,np.ones(s),np.ones(s)),{'samples':192,'domain':'test'}


def test_family_key_ignores_only_fixed_loads_and_booking_identity(context):
    from stowage.linear.cache import coefficient_family_key
    instance,params=context;band=family_band(instance,params)
    first=coefficient_family_key(instance,params,band)
    instance.vessel.fixed_loads=[(2.,0.,1.,5000.)]
    instance.containers=['ignored booking state']
    assert coefficient_family_key(instance,params,band)==first
    instance.vessel.sources=['stowage.slice_condition:{"expected_load_mass_kg":10000,"seed":19}']
    instance.vessel.name='Different display name'
    assert coefficient_family_key(instance,params,band)==first
    old=instance.vessel.hull.lightship_lcg_m
    instance.vessel.hull.lightship_lcg_m=old+.1
    assert coefficient_family_key(instance,params,band)!=first
    instance.vessel.hull.lightship_lcg_m=old
    instance.vessel.cells=[instance.vessel.cells[0].model_copy(update={'lcg_m':4.1})]
    assert coefficient_family_key(instance,params,band)!=first


def test_cached_coefficients_reconstant_equal_full_uncached_rebuild(context,monkeypatch):
    instance,params=context
    family=coefficient_family(instance,params)
    band=replace(family.template.band)
    errors,diagnostics=calibration(family)
    # Every instance changes total mass and all three moments; no calibration
    # identity or numerical fixed-load signature is used by coefficient cache.
    for loads in ([(2.,.1,2.,3000.)],[(7.,-.2,3.,8000.),(4.,.4,1.,2000.)]):
        instance.vessel.fixed_loads=loads
        cached=coefficient_family(instance,params)
        assert cached.cache_key==family.cache_key
        warm=assemble_instance(cached,instance,band,errors=errors,diagnostics=diagnostics)
        cold=coefficient_family(instance,params,force_rebuild=True)
        rebuilt=assemble_instance(cold,instance,band,errors=errors,diagnostics=diagnostics)
        for field in fields(warm):
            a,b=getattr(warm,field.name),getattr(rebuilt,field.name)
            if isinstance(a,np.ndarray):np.testing.assert_array_equal(a,b)
            elif field.name!='errors' and field.compare:assert a==b
        x=warm.station_x_m;g=9.80665
        m=sum(load[3] for load in loads);mx=sum(load[0]*load[3] for load in loads)
        weight_sf=sum((mass*(x>=xx) for xx,y,z,mass in loads),np.zeros(len(x)))
        weight_bm=sum((mass*np.maximum(x-xx,0.) for xx,y,z,mass in loads),np.zeros(len(x)))
        np.testing.assert_allclose(warm.const_sf_n-family.template.const_sf_n,
            family.response_sf@np.array([m,mx])-g*weight_sf,atol=1e-8)
        np.testing.assert_allclose(warm.const_bm_nm-family.template.const_bm_nm,
            g*weight_bm-family.response_bm@np.array([m,mx]),atol=1e-8)


def test_assembly_requires_calibration_and_subset_domain(context):
    instance,params=context;family=coefficient_family(instance,params)
    with pytest.raises(ValueError,match='requires family calibration'):
        assemble_instance(family,instance,family.template.band)
    errors,diagnostics=calibration(family)
    band=replace(family.template.band,displacement_max_kg=family.template.band.displacement_max_kg+1.)
    with pytest.raises(ValueError,match='exceeds the calibrated'):
        assemble_instance(family,instance,band,errors=errors,diagnostics=diagnostics)


def test_assembled_and_cached_family_bind_actual_vessel_limits(context):
    from stowage.linear.operations import vessel_physics_identity
    from stowage.linear.model import evaluate_linear, solve_ballast_lp
    instance,params=context
    family=coefficient_family(instance,params,force_rebuild=True)
    errors,diagnostics=calibration(family)
    model=assemble_instance(family,instance,family.template.band,errors=errors,diagnostics=diagnostics)
    assert model._vessel_physics_fingerprint == vessel_physics_identity(instance.vessel)
    # Exercise persisted metadata on a real coefficient cache hit as well.
    from stowage.linear.cache import load_model
    assert load_model(family.cache_key)._vessel_physics_fingerprint == model._vessel_physics_fingerprint
    instance.vessel.hull.max_trim_m *= .5
    for use in (evaluate_linear,solve_ballast_lp):
        with pytest.raises(ValueError,match='geometry or limits differ'):
            use(replace(model),instance.onboard,instance, **({"direction": "conservative"} if use is solve_ballast_lp else {}))


@pytest.mark.parametrize('heights',[(2.591,2.591),(2.591,2.896)])
def test_constraint_height_envelope_covers_every_known_two_box_order(context,heights):
    from stowage.model.fixtures import build_instance
    from stowage.model.vessel import Cell
    instance,params=context
    first=instance.vessel.cells[0]
    second=Cell(id='0010104',stack_id='00101H',lcg_m=4.,tcg_m=0.,vcg_base_m=3.591)
    instance.vessel.cells=[first,second]
    instance.vessel.stacks=[instance.vessel.stacks[0].model_copy(update={'cell_ids':[first.id,second.id]})]
    prototype=build_instance().containers[:2]
    instance.containers=[box.model_copy(update={'length_ft':20,'height_m':height,'oog':None,
        'gross_mass_kg':10000.,'special_stowage':[]}) for box,height in zip(prototype,heights)]
    instance.load_list=[box.id for box in instance.containers]
    family=coefficient_family(instance,params)
    errors,diagnostics=calibration(family)
    errors=replace(errors,list_rad=3.2)  # Raw negative-GM branches can differ by more than pi.
    model=assemble_instance(family,instance,family.template.band,errors=errors,diagnostics=diagnostics)
    for bottom,top in (heights,heights[::-1]):
        actual=1.+np.array([bottom/2,bottom+top/2])
        assert np.all(model._constraint_cell_vcg_m>=actual-1e-12)
    if heights[0]==heights[1]:
        np.testing.assert_allclose(model._constraint_cell_vcg_m,model.cell_vcg_m,rtol=0,atol=1e-12)
        assert model.errors.list_rad==3.2
    else:
        assert model.errors.list_rad==pytest.approx(2*np.pi)
    assert model.measurement_diagnostics['family_measured_absolute_errors']['list_rad']==3.2


@pytest.mark.parametrize('load',[(5.,0.,-1.,1.),(5.,0.,11.,1.),(-1.,0.,1.,1.),
                                (11.,0.,1.,1.),(5.,1.1,1.,1.)])
def test_positive_fixed_loads_outside_calibrated_centroid_domain_fail_closed(context,load):
    instance,params=context;family=coefficient_family(instance,params)
    errors,diagnostics=calibration(family)
    diagnostics['family_domain']=dict(kg_min_m=0.,kg_max_m=10.,fixed_x_min_m=0.,
        fixed_x_max_m=10.,fixed_y_abs_max_m=1.)
    instance.vessel.fixed_loads=[load]
    with pytest.raises(ValueError,match='positive fixed load centroid'):
        assemble_instance(family,instance,family.template.band,errors=errors,diagnostics=diagnostics)
    instance.vessel.fixed_loads=[(*load[:3],0.)]
    accepted=assemble_instance(family,instance,family.template.band,errors=errors,diagnostics=diagnostics)
    assert accepted.measurement_diagnostics['family_cache_key']==family.cache_key
    assert accepted.measurement_diagnostics['family_calibration_samples']==192


def test_possible_cargo_height_outside_calibrated_domain_fails_closed(context):
    from stowage.model.fixtures import build_instance
    instance,params=context;family=coefficient_family(instance,params)
    box=build_instance().containers[0].model_copy(update={'length_ft':20,'height_m':2.591,
        'oog':None,'gross_mass_kg':10000.,'special_stowage':[]})
    instance.containers=[box];instance.load_list=[box.id]
    errors,diagnostics=calibration(family)
    diagnostics['family_domain']=dict(kg_min_m=0.,kg_max_m=2.,fixed_x_min_m=0.,
        fixed_x_max_m=10.,fixed_y_abs_max_m=1.)
    with pytest.raises(ValueError,match='possible cargo height'):
        assemble_instance(family,instance,family.template.band,errors=errors,diagnostics=diagnostics)


def test_public_builder_reuses_calibration_for_changed_fixed_loads_and_matches_full_rebuild(context,monkeypatch):
    import stowage.linear.physics as physics
    import stowage.linear.family_calibration as calibration_module
    from stowage.linear.model import build_linear_model,evaluate_linear
    instance,params=context
    monkeypatch.setattr(physics,'_params_for',lambda current:('unit-box',params))
    monkeypatch.setattr(calibration_module,'reference_inputs',lambda current:{'method':1})
    monkeypatch.setattr(calibration_module,'reference_inputs_cached',lambda current:calibration_module.reference_inputs(current))
    physics._CACHE.clear()
    calls=[]
    def measured(family,current,*,samples,**kwargs):
        calls.append(samples)
        errors,diag=calibration(family)
        diag['p99']={name:getattr(errors,name) for name in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')}
        diag['family_domain']=calibration_module.domain_for(family,current)
        # Zero directional toy residuals isolate the cache/constants contract;
        # the real independent response integrations are exercised above.
        metrics=('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm','draught_aft_m','draught_forward_m')
        diag['signed_errors']={side:{name:[0.]*len(family.template.station_x_m) if name in ('sf_n','bm_nm') else 0.
            for name in metrics} for side in ('lower_unsafe','upper_unsafe')}
        return errors,diag
    monkeypatch.setattr(calibration_module,'calibrate_family',measured)
    band=family_band(instance,params)
    baseline=build_linear_model(instance,band=band)
    assert calls==[192]
    for loads in ([(2.,.1,2.,3000.)],[(7.,-.2,3.,8000.),(4.,.4,1.,2000.)]):
        instance.vessel.fixed_loads=loads
        before=len(calls)
        cached=build_linear_model(instance,band=band)
        assert len(calls)==before
        with pytest.raises(ValueError,match='fixed loads differ'):
            evaluate_linear(baseline,instance.onboard,instance)
        fresh=physics.build_model(instance,band=band,_force_rebuild=True)
        assert len(calls)==before+1
        for field in fields(cached):
            a,b=getattr(cached,field.name),getattr(fresh,field.name)
            if isinstance(a,np.ndarray):np.testing.assert_array_equal(a,b)
            elif field.name!='errors' and field.compare:assert a==b
        for field in fields(cached.errors):
            np.testing.assert_array_equal(getattr(cached.errors,field.name),getattr(fresh.errors,field.name))
        evaluate_linear(cached,instance.onboard,instance)


def test_family_memory_reuses_coefficients_but_forced_rebuild_recomputes(context,monkeypatch):
    import stowage.linear.physics as physics
    import stowage.linear.cache as cache
    from stowage.linear.reusable import _FAMILY_MEMORY
    instance,params=context
    _FAMILY_MEMORY.clear()
    support=physics._km_support_lower_intercept;calls=[]
    def counted(*args,**kwargs):
        calls.append(1)
        return support(*args,**kwargs)
    monkeypatch.setattr(physics,'_km_support_lower_intercept',counted)
    first=coefficient_family(instance,params)
    assert len(calls)==1
    def forbidden(*args,**kwargs):
        raise AssertionError('a warm family must not deserialize another disk payload')
    monkeypatch.setattr(cache,'load_model',forbidden)
    warm=coefficient_family(instance,params)
    assert warm.template is first.template
    assert len(calls)==1
    fresh=coefficient_family(instance,params,force_rebuild=True)
    assert len(calls)==2
    np.testing.assert_array_equal(first.template.sf_per_kg_cell,fresh.template.sf_per_kg_cell)


def test_reference_paths_are_memoized_but_file_bytes_and_identity_are_rechecked(monkeypatch,tmp_path):
    import stowage.linear.cache as cache
    import stowage.linear.dependencies as dependencies
    import stowage.linear.family_calibration as calibration_module
    import stowage.checker.stability_liquid as liquid
    import stowage.checker.stability_liquid_tables as tables
    from stowage.vessel.tanks import TankGeometry
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')
    monkeypatch.delenv('STOWAGE_STABILITY_LIQUID_DIRECT',raising=False)
    import stowage.linear.dependencies as dependencies
    monkeypatch.setattr(dependencies,'dependency_fingerprint',lambda artifact:'test-path-source')
    manifest=tmp_path/'manifest.json';manifest.write_text('first')
    tank_path=tmp_path/'tank.npz'
    monkeypatch.setattr(liquid,'manifest_path',lambda params:manifest)
    monkeypatch.setattr(tables,'_path',lambda solid:tank_path)
    builds=[]
    def solid(*args,**kwargs):
        builds.append(1)
        return NS(shape='hull_sections')
    monkeypatch.setattr(TankGeometry,'from_params',solid)
    calibration_module._reference_paths.cache_clear()
    params={'tanks':[{'id':'T','geometry':{'shape':'hull_sections'}}]}
    first=calibration_module.reference_inputs(params)
    manifest.write_text('changed same path')
    second=calibration_module.reference_inputs(params)
    assert first['files']!=second['files'] and len(builds)==1
    params['tanks'][0]['geometry']['parameter_change']=1
    calibration_module.reference_inputs(params)
    assert len(builds)==2
    monkeypatch.setattr(dependencies,'dependency_fingerprint',lambda artifact:'changed-source')
    calibration_module.reference_inputs(params)
    assert len(builds)==3
    calibration_module._reference_paths.cache_clear()


def test_response_calibration_identity_reuses_full_and_slice_layouts_but_not_gm_threshold(context,monkeypatch):
    import stowage.versions as versions
    from stowage.linear.cache import response_calibration_identity,family_calibration_key
    instance,params=context;family=coefficient_family(instance,params)
    model=family.template;s=len(model.station_x_m)
    # Synthetic calibration contains no cell masses. A different decision
    # layout changes columns, not the hull/tank response being measured.
    sliced=replace(family,cache_key='different-cell-layout',template=replace(model,
        vessel_id='unit-box-slice',cell_ids=(),cell_lcg_m=np.array([]),cell_tcg_m=np.array([]),
        cell_vcg_m=np.array([]),sf_per_kg_cell=np.empty((s,0)),bm_per_kg_cell=np.empty((s,0))))
    identity=response_calibration_identity(family)
    assert response_calibration_identity(sliced)==identity
    ref={'method':1,'qualified_numeric_grid':'test'};domain={'kg_min_m':0.,'kg_max_m':10.}
    first=family_calibration_key(family,ref,domain)
    assert family_calibration_key(sliced,ref,domain)==first
    monkeypatch.setattr(versions,'CHECKER_VERSION','unrelated')
    monkeypatch.setattr(versions,'BALLAST_VERSION','unrelated')
    assert response_calibration_identity(family)==identity
    assert family_calibration_key(family,ref,domain)==first
    changed=replace(family,template=replace(model,gm_min_m=model.gm_min_m+.01))
    assert response_calibration_identity(changed)!=identity
    assert family_calibration_key(changed,ref,domain)!=first
    assert family_calibration_key(family,ref,{**domain,'kg_max_m':11.})!=first
