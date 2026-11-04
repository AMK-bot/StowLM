"""Review regressions: tiny direct batches and fail-closed liquid arithmetic."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from stowage.checker import stability_direct_liquid_batch as batch
from stowage.checker.stability_loading import CorrectionTank,LoadingCondition,liquid_cg
from stowage.checker.stability_tank_transfer import _direct_liquid
from stowage.vessel.hull_tanks import SectionTankGeometry
from stowage.vessel.tanks import TankGeometry


def _solid():
    solid=TankGeometry.from_params(dict(shape='hull_sections',x_m=[.5,1.5],
        integration_weights_m=[1.,1.],sections_yz_m=[[[[0.,0.],[2.,0.],[2.,1.],[0.,1.]]]]*2,
        x_min_m=0.,x_max_m=2.,y_min_m=0.,y_max_m=2.,z_min_m=0.,z_max_m=1.))
    solid._liquid_cache.clear()
    return solid


@pytest.fixture(autouse=True)
def clear_batch_caches():
    batch._CACHE.clear();batch._CENTROID_CACHE.clear()
    yield
    batch._CACHE.clear();batch._CENTROID_CACHE.clear()


def test_direct_batch_tiny_apt_port_retains_scalar_centroid():
    from stowage.vessel.fleet import load_ship_params
    params=load_ship_params('feeder_500')
    solid=TankGeometry.from_params(next(t for t in params['tanks'] if t['id']=='APT-P'),ship_params=params)
    volume=1e-13
    tank=CorrectionTank('APT-P',1025.,volume,0.,solid)
    condition=LoadingCondition(8e6,50.,0.,6.,0.,0.,actual_tanks=(tank,))
    moved=np.asarray(solid.liquid(volume,.2).centroid_m)
    upright=np.asarray(solid.liquid(volume).centroid_m)
    expected=np.array([50.,0.,6.])+1025.*volume/8e6*(moved-upright)
    actual=np.asarray(liquid_cg(condition,.2))
    assert np.isfinite(actual).all()
    np.testing.assert_array_equal(actual,expected)
    centres=batch.centroids(batch.prepare(condition),.2,0.)
    np.testing.assert_array_equal(centres[0],moved)
    np.testing.assert_array_equal(batch.centroids(batch.prepare(condition),.2,0.),centres)


@pytest.mark.parametrize('field',['volume_nan','volume_neginf','moment_nan','moment_inf','area','inertia','level'])
def test_liquid_rejects_corrupt_tiny_results_before_empty_convention(monkeypatch,field):
    solid=_solid()
    def corrupt(self,volume,normal,*,surface=False,distribution=False):
        totals=np.zeros(4);area=inertia=level=0.
        if field=='volume_nan':totals[0]=np.nan
        elif field=='volume_neginf':totals[0]=-np.inf
        elif field=='moment_nan':totals[1]=np.nan
        elif field=='moment_inf':totals[2]=np.inf
        elif field=='area':area=np.nan
        elif field=='inertia':inertia=np.inf
        else:level=np.nan
        return ((totals,area,inertia) if surface else totals),level
    monkeypatch.setattr(SectionTankGeometry,'_tiny_liquid',corrupt)
    with pytest.raises(ArithmeticError,match='finite'):
        solid.liquid(5e-15,.2)


@pytest.mark.parametrize('field',['volume_nan','volume_neginf','moment_nan','moment_inf','level'])
@pytest.mark.parametrize('polynomial',[False,True])
def test_direct_centroid_rejects_corrupt_tiny_results_before_empty_convention(monkeypatch,field,polynomial):
    solid=_solid()
    def corrupt(self,volume,normal,*,surface=False,distribution=False):
        totals=np.zeros(4)
        if field=='volume_nan':totals[0]=np.nan
        elif field=='volume_neginf':totals[0]=-np.inf
        elif field=='moment_nan':totals[1]=np.nan
        elif field=='moment_inf':totals[2]=np.inf
        return totals,np.nan if field=='level' else 0.
    monkeypatch.setattr(SectionTankGeometry,'_tiny_liquid',corrupt)
    with pytest.raises(ArithmeticError,match='finite'):
        _direct_liquid(solid,5e-15,.2,0.,polynomial=polynomial)


def test_finite_unresolved_tiny_result_still_uses_exact_empty_convention(monkeypatch):
    solid=_solid()
    empty=solid.liquid(0.,.2)
    def unresolved(self,volume,normal,*,surface=False,distribution=False):
        return ((np.zeros(4),0.,0.) if surface else np.zeros(4)),0.
    monkeypatch.setattr(SectionTankGeometry,'_tiny_liquid',unresolved)
    assert solid.liquid(5e-15,.2)==empty
    centre,level=_direct_liquid(solid,5e-15,.2,0.)
    np.testing.assert_array_equal(centre,empty.centroid_m)
    assert level==empty.plane_level_m


def test_direct_environment_manual_c1_tiny_residue_passes_all_states(monkeypatch):
    from stowage.checker.full import check
    from stowage.model import BallastCondition
    root=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('review_liquid_manual_corpus',root/'experiments/checker/perf-1028/corpus.py')
    corpus=importlib.util.module_from_spec(spec);spec.loader.exec_module(corpus)
    instance,plan=corpus.manual_case('feeder_500','C1')
    masses=dict(plan.ballast.tank_masses_kg)
    assert masses['APT-P']==0.
    masses['APT-P']=1e-10
    plan=plan.model_copy(update=dict(ballast=BallastCondition(tank_masses_kg=masses,source='planner')))
    monkeypatch.setenv('STOWAGE_STABILITY_LIQUID_DIRECT','1')
    report=check(instance,plan)
    assert report.passed,[(v.rule_id,v.message) for v in report.violations]
    sampled=[part for key,part in report.parts.items() if key.startswith('condition:') and key.endswith(':stability')]
    assert len(sampled)==3
    assert all(part.ran and part.error is None for part in sampled)
