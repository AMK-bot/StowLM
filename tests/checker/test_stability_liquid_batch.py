"""Batched mass-CG queries preserve individual certified interpolants exactly."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker.stability_liquid_batch import prepare,evaluate
from stowage.checker.stability_liquid_tables import LiquidTable
from stowage.checker.stability_loading import CorrectionTank,LoadingCondition


def condition(*,different_axes=False,prismatic=False,fractions=(.17,.61,.83)):
    rng=np.random.default_rng(314159)
    tanks=[];tables={};references={}
    for index,fraction in enumerate(fractions):
        capacity=100.+index*17.
        fills=np.linspace(0.,1.,9+index)
        heels=np.linspace(-.9,.9,7+int(different_axes and index>0))
        trims=np.linspace(-.04,.04,5)
        reference=np.array([30.+index,-3.+index,1.+.1*index])
        # Random three-dimensional nodal data exercises the tensor regrouping
        # independently of any smooth or symmetric physical special case.
        moments=rng.normal(size=(len(heels),len(trims),len(fills),3))
        moments += reference[None,None,None,:]*capacity*fills[None,None,:,None]
        moments[:,:,0,:]=0.
        solid=SimpleNamespace(sounding=lambda volume,ref=reference:SimpleNamespace(
            lcg_m=ref[0],tcg_m=ref[1],vcg_m=ref[2]))
        tank=CorrectionTank(f'tank-{index}',900.+37*index,fraction*capacity,0.,solid)
        tanks.append(tank)
        references[tank.id]=reference
        tables[tank.id]=LiquidTable(fills,heels,trims,moments,capacity,reference,
                                   prismatic=prismatic and index==0)
    return LoadingCondition(3e6,52.,.1,6.,0.,0.,actual_tanks=tuple(tanks),
                            liquid_tables=tables),references


def individual(c,references,heel,trim):
    result=np.array([c.lcg_m,c.tcg_m,c.kg_m])
    for tank in c.actual_tanks:
        moved=np.array(c.liquid_tables[tank.id].centroid(tank.volume_m3,heel,trim))
        result += tank.density_kg_m3*tank.volume_m3/c.mass_kg*(moved-references[tank.id])
    return result


@pytest.mark.parametrize('different_axes',[False,True])
@pytest.mark.parametrize('fractions',[(.17,.61,.83),(0.,1.,.5),(.001,.997,.32)])
def test_grouped_queries_equal_individual_tensor_queries(different_axes,fractions):
    c,refs=condition(different_axes=different_axes,fractions=fractions)
    groups=prepare(c)
    assert len(groups)==(2 if different_axes else 1)
    rng=np.random.default_rng(271828)
    for heel,trim in rng.uniform([-.9,-.04],[.9,.04],size=(30,2)):
        result=evaluate(groups,(c.lcg_m,c.tcg_m,c.kg_m),heel,trim)
        assert result==pytest.approx(individual(c,refs,heel,trim),abs=2e-14)


def test_complete_table_coverage_is_required_even_for_an_empty_actual_tank():
    c,_=condition(fractions=(0.,.4,.7))
    tables=dict(c.liquid_tables);del tables['tank-0']
    assert prepare(replace(c,liquid_tables=tables)) is None
    assert prepare(replace(c,liquid_tables=None)) is None


def test_no_actual_tanks_preserves_base_cg():
    c,_=condition(fractions=())
    groups=prepare(c)
    assert groups==()
    assert evaluate(groups,(52.,.1,6.),.3,.01)==(52.,.1,6.)


@pytest.mark.parametrize('heel,trim',[(-.901,0.),(.901,0.),(0.,-.041),
                                    (0.,.041),(float('nan'),0.),(0.,float('inf'))])
def test_outside_angular_grid_raises_for_ordinary_direct_fallback(heel,trim):
    c,_=condition()
    with pytest.raises(ValueError,match='outside certified grid'):
        evaluate(prepare(c),(52.,.1,6.),heel,trim)


@pytest.mark.parametrize('trim',[-.001,.001])
def test_prismatic_restriction_matches_individual_queries(trim):
    c,_=condition(prismatic=True)
    with pytest.raises(ValueError,match='prismatic'):
        evaluate(prepare(c),(52.,.1,6.),.3,trim)


def test_prismatic_zero_trim_is_supported():
    c,refs=condition(prismatic=True)
    actual=evaluate(prepare(c),(52.,.1,6.),.3,0.)
    assert actual==pytest.approx(individual(c,refs,.3,0.),abs=2e-14)


def test_fill_interpolation_and_upright_lookup_happen_only_during_preparation(monkeypatch):
    c,refs=condition()
    groups=prepare(c)
    for tank in c.actual_tanks:
        tank.geometry.sounding=lambda *a:pytest.fail('upright lookup repeated')
        c.liquid_tables[tank.id].centroid=lambda *a:pytest.fail('per-tank query repeated')
    for heel in (-.5,0.,.5):
        result=evaluate(groups,(52.,.1,6.),heel,.01)
        assert np.isfinite(result).all()


@pytest.mark.parametrize('volume',[float('nan'),-1.,1e10])
def test_invalid_volume_refuses_preparation(volume):
    c,_=condition()
    tank=replace(c.actual_tanks[0],volume_m3=volume)
    c=replace(c,actual_tanks=(tank,*c.actual_tanks[1:]))
    assert prepare(c) is None
