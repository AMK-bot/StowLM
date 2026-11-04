"""Coverage and physical-mass invariants for widened qualification holdouts."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('domain_tanks',ROOT/'qualify_tanks.py')
tanks=importlib.util.module_from_spec(spec)
spec.loader.exec_module(tanks)
case_spec=importlib.util.spec_from_file_location('domain_cases',ROOT.parent/'fallback'/'qualification_cases.py')
cases=importlib.util.module_from_spec(case_spec)
case_spec.loader.exec_module(cases)


@pytest.mark.parametrize('index',range(16))
def test_profiles_have_full_angular_coverage_and_nonnegative_cargo(index):
    capacities={'DB-P':100.,'DB-S':100.,'FPT-P':50.,'FPT-S':50.,'APT':50.}
    fills,mass,angles,trims=tanks.profile_inputs(np.random.default_rng(10),index,list(capacities),capacities,1000.,2000.)
    liquid=sum(capacities[key]*fill for key,fill in fills.items())
    assert 1000.+liquid <= mass <= 2000.
    assert all(0 <= fill <= 1 for fill in fills.values())
    assert max(abs(angles)) <= np.deg2rad(70.)
    assert max(abs(trims)) <= np.arctan(.06)+1e-15
    assert np.deg2rad(10.) in abs(angles)
    if index < 4:
        assert max(abs(angles)) == np.deg2rad(70.)
    if index < 2:
        assert min(trims) == -np.arctan(.06)
        assert max(trims) == np.arctan(.06)
        assert set(fills.values()) == {float(index)}
    if index==0:
        assert mass==1000.
    if index==1:
        assert mass==2000.


def test_over_capacity_profiles_fail_before_any_geometry_integration():
    with pytest.raises(ValueError,match='exceed scantling'):
        tanks.profile_inputs(np.random.default_rng(1),1,['FPT'],{'FPT':2000.},1000.,2000.)


def test_changed_qualification_domain_invalidates_retained_envelopes():
    with pytest.raises(ValueError,match='domain changed'):
        tanks.reusable_envelopes({'domain':None},{'domain':{'heel_rad':[-1.2,1.2]}},[],{},requested=4)


@pytest.fixture
def provider_only_loader(monkeypatch):
    # Numerical moment tests need current hull/tank geometry, not expensive KN
    # rows or strength stations. Production domain_condition keeps its ordinary
    # load_vessel(with_hull=True) call; only this test replaces that dependency.
    from stowage.model.fixtures import build_instance
    from stowage.vessel.fleet import load_ship_params
    from stowage.vessel.tanks import build_tanks
    ordinary=cases.load_vessel
    def lightweight(name,*,with_hull):
        assert with_hull is True
        params=load_ship_params(name)
        vessel=ordinary(name,with_hull=False)
        template=build_instance().vessel.hull
        fields={key:params['hull'][key] for key in type(template).model_fields
                if key in params['hull']}
        fields.update(params['operating_limits'])
        fields.pop('provenance',None)
        fields.update(hydrostatics=template.hydrostatics,
            max_draught_m=params['hull']['scantling_draught_m'])
        if 'lightship_distribution' in fields:
            fields['lightship_distribution']=[tuple(row) for row in fields['lightship_distribution']]
        vessel.hull=template.model_copy(update=fields)
        vessel.tanks=build_tanks(params)
        return vessel
    monkeypatch.setattr(cases,'load_vessel',lightweight)


@pytest.mark.slow
@pytest.mark.parametrize('name',('feeder_500','feeder_1000','feeder_1700'))
def test_provider_extreme_holdout_loads_are_nonnegative_and_set_real_attitude(name,provider_only_loader):
    from stowage.checker.stability_loading import loading_condition,liquid_cg
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.vessel.fleet import load_ship_params
    params=load_ship_params(name)
    geometry=StabilityGeometry(params)
    for index in (0,1,2,5):
        instance,meta=cases.domain_condition(name,np.random.default_rng(3911),index)
        assert meta['lightship_mass_kg'] <= meta['mass_kg'] <= meta['scantling_mass_kg']
        assert all(load.mass_kg>=0 for load in instance.vessel.fixed_loads)
        if index==0:
            assert not any(meta['fill_fractions'].values())
            continue
        condition=loading_condition(instance,instance.onboard,geometry=geometry,ship_params=params,
            free_surface_method=1,precomputed_soundings=True,small_tank_exemption=True)
        assert condition.mass_kg==pytest.approx(meta['mass_kg'],abs=1e-7)
        heel=meta['target_list_rad'];trim=meta['target_trim_m']
        cg=liquid_cg(condition,heel,np.arctan(trim/geometry.lpp_m))
        # Actual-provider centroid verifies the synthetic equilibrium moment;
        # the full direct-root/curve qualification is deliberately separate.
        from scipy.optimize import brentq
        rho=instance.port.water_density_kg_m3
        mean=brentq(lambda mean:geometry.submerged(mean+trim/2,mean-trim/2,heel,rho).displacement_kg-condition.mass_kg,
            -.1*geometry.depth_m,geometry.depth_m+geometry.beam_m)
        buoyancy=geometry.submerged(mean+trim/2,mean-trim/2,heel,rho)
        normal=geometry.waterplane_normal(mean+trim/2,mean-trim/2,heel)
        moment=np.cross(np.array(buoyancy.centroid_m)-cg,normal)
        assert np.linalg.norm(moment)<1e-7


@pytest.mark.parametrize('samples,envelopes',[(0,4),(-1,4),(1,-1),(1,1.5)])
def test_invalid_component_options_fail_before_reading_vessels(tmp_path,monkeypatch,samples,envelopes):
    monkeypatch.setattr(tanks,'load_ship_params',lambda *args:pytest.fail('must validate options first'))
    with pytest.raises(ValueError,match='integer'):
        tanks.run('feeder_500',tmp_path/'unused.json',samples=samples,envelope_samples=envelopes)


def test_full_curve_certification_requires_all_explicit_boundary_conditions(tmp_path):
    spec=importlib.util.spec_from_file_location('domain_full_validator',ROOT/'qualify.py')
    full=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(full)
    with pytest.raises(ValueError,match='eight loaded'):
        full.run('feeder_500',tmp_path/'unused.json',samples=4,certify=True)


@pytest.mark.parametrize('direct_queries,passed',[(0,True),(1,False)])
def test_full_holdout_cannot_certify_mixed_table_and_direct_queries(tmp_path,monkeypatch,direct_queries,passed):
    from types import SimpleNamespace
    from copy import deepcopy
    spec=importlib.util.spec_from_file_location('coverage_full_validator',ROOT/'qualify.py')
    full=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(full)
    base=SimpleNamespace(onboard=SimpleNamespace(tank_masses_kg={'peak':100.}))
    monkeypatch.setattr(full._benchmark._previous._departure,'build_departure',lambda name:(base,{}))
    monkeypatch.setattr(full._domain,'domain_condition',lambda *args:(base,{'target_list_rad':.1745}))
    report=dict(ran=True,error=None,metrics=dict(gm_m=1.,area_0_40_mrad=.1,
        liquid_table_queries=1,liquid_direct_queries=direct_queries),
        curve=dict(angles_rad=[0.,.5],gz_m=[0.,.5],negative_gz_m=[0.,-.5]))
    def run_check(*args,**kwargs):
        from stowage.checker import stability
        stability._near_table_limit({'gm_m':1.},{'gm_m':.0001})
        return deepcopy(report)
    monkeypatch.setattr(full._benchmark._previous,'run_check',run_check)
    result=full.run('synthetic',tmp_path/'coverage.json',samples=1)
    assert result['cases'][0]['production_guard_would_trigger'] is True
    assert result['cases'][0]['production_guard_evaluations']==[True]
    assert result['all_passed']==passed
    if not passed:
        assert 'direct liquid queries' in result['cases'][0]['failure']


def full_validator():
    spec=importlib.util.spec_from_file_location('budget_full_validator',ROOT/'qualify.py')
    full=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(full)
    return full


@pytest.mark.parametrize('previous',[None,'9'])
def test_offline_reference_budget_is_explicit_bounded_and_restores_production(monkeypatch,previous):
    import os
    full=full_validator()
    key='STOWAGE_STABILITY_TIME_BUDGET_S'
    if previous is None:
        monkeypatch.delenv(key,raising=False)
    else:
        monkeypatch.setenv(key,previous)
    with full.reference_budget(120.):
        assert os.environ[key]=='120.0'
    assert os.environ.get(key)==previous
    with pytest.raises(RuntimeError):
        with full.reference_budget(120.):
            raise RuntimeError('reference failed')
    assert os.environ.get(key)==previous


@pytest.mark.parametrize('seconds',[0.,-1.,float('nan'),float('inf')])
def test_invalid_offline_reference_deadline_is_rejected(seconds):
    full=full_validator()
    with pytest.raises(ValueError,match='positive'):
        with full.reference_budget(seconds):
            pytest.fail('must reject invalid allowance')


def test_expired_reference_is_incomplete_not_an_interpolation_error(tmp_path,monkeypatch):
    import os
    from types import SimpleNamespace
    full=full_validator()
    base=SimpleNamespace(onboard=SimpleNamespace(tank_masses_kg={'peak':100.}))
    monkeypatch.setattr(full._benchmark._previous._departure,'build_departure',lambda name:(base,{}))
    monkeypatch.setattr(full._domain,'domain_condition',lambda *args:(base,{}))
    monkeypatch.delenv('STOWAGE_STABILITY_TIME_BUDGET_S',raising=False)
    budgets=[]
    def check(*args,**kwargs):
        budgets.append(os.environ.get('STOWAGE_STABILITY_TIME_BUDGET_S','30'))
        return dict(ran=False,error='reference time budget exceeded',
            metrics=dict(time_budget_exceeded=float(len(budgets)==2)))
    monkeypatch.setattr(full._benchmark._previous,'run_check',check)
    output=tmp_path/'timeout.json'
    result=full.run('synthetic',output,samples=1)
    assert budgets==['30','120.0']
    assert result['reference_timeouts']==1
    assert result['compared_conditions']==0
    assert not result['complete'] and not result['all_passed']
    assert result['maxima']==dict.fromkeys(['max_gz_l5_fraction','max_area_l5_fraction','gm_l5_fraction'])
    assert 'no interpolation verdict' in result['cases'][0]['failure']
    assert 'errors' not in result['cases'][0]
    assert 'Infinity' not in output.read_text()


@pytest.mark.parametrize('raise_inside',[False,True])
def test_measurement_guard_is_observed_disabled_and_restored(monkeypatch,raise_inside):
    full=full_validator()
    from stowage.checker import stability,stability_liquid
    calls=[]
    def original_guard(metrics,margins):
        calls.append((metrics,margins))
        return True
    def original_attach(condition,params,**kwargs):
        assert kwargs['require_manifest'] is False
        return condition
    monkeypatch.setattr(stability,'_near_table_limit',original_guard)
    monkeypatch.setattr(stability_liquid,'attach_tables',original_attach)
    def execute():
        with full.offline_per_tank_qualification() as observations:
            assert stability._near_table_limit({'gm_m':1.},{'gm_m':.0001}) is False
            assert observations==[True]
            assert stability_liquid.attach_tables('condition',{})=='condition'
            if raise_inside:
                raise RuntimeError('failed measured query')
    if raise_inside:
        with pytest.raises(RuntimeError,match='failed measured'):
            execute()
    else:
        execute()
    assert stability._near_table_limit is original_guard
    assert stability_liquid.attach_tables is original_attach
    assert len(calls)==1


def test_measurement_guard_records_untriggered_production_decision(monkeypatch):
    full=full_validator()
    from stowage.checker import stability
    monkeypatch.setattr(stability,'_near_table_limit',lambda *args:False)
    with full.offline_per_tank_qualification() as observations:
        assert stability._near_table_limit({}, {}) is False
        assert observations==[False]
