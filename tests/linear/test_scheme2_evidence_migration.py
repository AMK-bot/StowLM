"""Live scheme-2 admission and rows are distinct from recorded scheme-1 history."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from stowage.linear import cache, dependencies, margin_policy as policy
from stowage.linear.operations import constraints
from stowage.linear.qualification import qualify_holdout
from stowage.linear.reusable import CoefficientFamily
from tests.linear.test_boundary_list_policy import boundary_context

ROOT=Path(__file__).resolve().parents[2]
ARCHIVE=ROOT/'experiments/linear-model/epoch_requalification/hash-scheme-1028-review/scheme1-history'


def archive():
    spec=importlib.util.spec_from_file_location('scheme1_live_admission_history',ARCHIVE/'scheme1_payloads.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('ship',['feeder_500','feeder_1000','feeder_1700'])
def test_live_scheme2_never_admits_archived_scheme1_coefficients_or_bank(ship):
    history=archive();raw,bank,identity=history.read_archive_inputs(ship)
    model=history.load_archive_model(ship)
    arrays=[getattr(model,'_family_'+name) for name in
            ('response_sf','response_bm','buoyancy_sf','buoyancy_bm','lightship_sf','lightship_bm')]
    family=CoefficientFamily(model,*arrays,model._reference_params,identity['coefficient_family_cache_key'])
    assert dependencies.LINEAR_DEPENDENCY_HASH_SCHEME_VERSION=='2'
    assert identity['historical_hash_scheme_version']=='1'
    assert identity['production_usable'] is False
    assert raw['physical_inputs']['coefficient_builder']!=dependencies.dependency_fingerprint('coefficient')
    assert raw['physical_inputs']['calibration_builder']!=dependencies.dependency_fingerprint('calibration')
    live_response=cache.response_calibration_identity(family)
    assert live_response!=identity['response_identity']
    with pytest.raises(ValueError,match='epoch is stale'):
        policy.assert_policy_epoch(family,live_response)
    # The explicit private bank hook can return a generic empirical research
    # certificate when the response is absent. It must not import the old
    # measured policy or make its old diagnostics solver-usable.
    certificate=policy.qualify_policy(family,None,raw['diagnostics'],bank=bank)
    assert certificate['response_identity']==live_response
    assert certificate['bank_enriched'] is False
    assert 'necessary' not in certificate
    object.__setattr__(model,'measurement_diagnostics',
                       {**raw['diagnostics'],'margin_policy':certificate})
    with pytest.raises(ValueError,match='stale margin-policy response_identity'):
        policy.default_reserves(model,direction='necessary')


@pytest.mark.parametrize('density',[1000.,1025.])
def test_live_scheme2_rows_retain_all_exactly_feasible_synthetic_states(boundary_context,density):
    # These analytic states are unit fixtures, never historical observations
    # relabelled as fresh calibration or production qualification.
    family,model,diagnostics,bank=boundary_context
    model.water_density_kg_m3=density
    model.hull_max_draught_m=9.;model.hull_min_draught_fwd_m=.1
    boundary=bank['entries']['boundary-response']['boundary_list']
    boundary['water_density_kg_m3']=density
    boundary['builder_inputs_hash']=policy._boundary_builder_inputs_hash(boundary)
    zero=policy._zeros(1)
    for side in policy._SIDES:
        diagnostics['signed_errors'][side.replace('unsafe','conservatism')]=deepcopy(zero[side])
    ordinary=qualify_holdout(zero,zero,zero,station_x_m=[0.],calibration_seed=11,
        holdout_seed=12,calibration_samples=128,holdout_samples=64)
    ordinary['direction']='necessary'
    edge=deepcopy(ordinary);edge.update(kind='necessary-domain-edges-v1',holdout_seed=32)
    opposite=deepcopy(boundary)
    opposite.update(direction='necessary',residual_definition='exact outward capacity minus affine capacity',
        measured_optimistic_kgm=opposite.pop('measured_unsafe_kgm'),
        interior_qualification=dict(independent_holdout_passed=True,affine_gm_filter_applied=False,
            calibration_seed=21,holdout_seed=22,calibration_samples=128,holdout_samples=64,
            max_holdout_excess_kgm={side:0. for side in policy._SIDES}))
    opposite['builder_inputs_hash']=policy._boundary_builder_inputs_hash(opposite)
    bank['entries']['boundary-response']['necessary']=dict(format=1,direction='necessary',
        physical_optimistic=zero,reference_transfer=zero,holdout_qualification=ordinary,
        edge_holdout_qualification=edge,boundary_list=opposite)
    certificate=policy.qualify_policy(family,None,diagnostics,bank=bank)
    diagnostics['margin_policy']=certificate
    assert dependencies.LINEAR_DEPENDENCY_HASH_SCHEME_VERSION=='2'
    assert certificate['policy_inputs']['application_identity']==dependencies.dependency_fingerprint('application')
    for direction in ('necessary','conservative'):
        rows=constraints(model,1.,direction=direction)
        assert set(rows.names)=={'gm','trim','list_port','list_starboard','displacement',
                                'draught','draught_aft','draught_forward','sf@0','bm@0'}
        for mass in np.linspace(0.,1.,64):
            # Independent closed-form fixture: KM=10, Mz=9000+10*c,
            # My=c, displacement=1000+c, zero trim/SF/BM, draught=M/1000.
            displacement=1000.+mass
            assert 1000./displacement>=model.gm_min_m
            assert abs(np.arctan(mass/1000.))<model.max_list_rad
            assert model.band.displacement_min_kg<displacement<model.band.displacement_max_kg
            assert .1<displacement/1000.<9.
            values=rows.a_cell@np.array([mass])+rows.const
            assert np.all(values>=rows.lb-1e-7),(density,direction,mass,rows.names)
            assert np.all(values<=rows.ub+1e-7),(density,direction,mass,rows.names)
