"""The live catalogue GM floor cannot reuse a stale coefficient-family limit."""
from dataclasses import replace
from types import SimpleNamespace as NS

import numpy as np
import pytest

from stowage.linear import cache,reusable
from stowage.linear.model import LinearErrors


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
    import stowage.rules.catalogue as catalogue
    from stowage.model import ShipState
    from stowage.model.vessel import Cell,Stack
    state={"minimum":.2}
    def get_rule(name):
        assert name=="iscode.a2_2.initial_gm"
        return NS(parameters={"minimum":NS(value=state["minimum"])})
    monkeypatch.setattr(catalogue,"get_rule",get_rule)
    monkeypatch.setattr(cache,"_DEFAULT_ROOT",tmp_path)
    monkeypatch.setattr(reusable,"_FAMILY_MEMORY",{})
    monkeypatch.setattr(physics,"_geometry_from_params",lambda params:BoxHull())
    monkeypatch.setattr(physics,"_maximum_tank_fsms",lambda *args:np.array([]))
    hull=NS(lpp_m=10.,beam_m=2.,depth_m=10.,min_draught_aft_m=.1,min_draught_fwd_m=.1,
        max_draught_m=8.,max_trim_m=.5,max_list_rad=.1,min_gm_m=.15,
        lightship_mass_kg=20000.,lightship_lcg_m=5.,lightship_tcg_m=0.,lightship_vcg_m=1.,
        lightship_distribution=[(0.,10.,20000.)],
        stations=[NS(x_m=x,max_shear_n=1e8,min_shear_n=-1e8,max_bending_nm=1e9,min_bending_nm=-1e9)
                  for x in (0.,2.,5.,8.,10.)])
    cell=Cell(id="0010102",stack_id="00101H",lcg_m=4.,tcg_m=0.,vcg_base_m=1.)
    stack=Stack(id="00101H",bay=1,row=1,on_deck=False,cell_ids=[cell.id],max_mass_kg=30000.,
                max_height_m=6.,base_z_m=1.,hatch_cover_id="HC-h1")
    class Vessel(NS):
        def model_dump(self,**kwargs):
            return dict(id=self.id,cells=[c.model_dump(mode="json") for c in self.cells],
                stacks=[s.model_dump(mode="json") for s in self.stacks],tanks=self.tanks,
                fixed_loads=self.fixed_loads,hull={**vars(self.hull),"stations":[vars(s) for s in self.hull.stations]})
    vessel=Vessel(id="gm-box",hull=hull,cells=[cell],stacks=[stack],tanks=[],fixed_loads=[],slice_of=None)
    instance=NS(vessel=vessel,port=NS(water_density_kg_m3=1000.,max_draught_m=8.),
        onboard=ShipState(vessel_id="gm-box"),containers=[],load_list=[],discharge_list=[],rule_overrides={})
    return instance,{"id":"gm-box","tanks":[]},state


def assemble(family,instance):
    n=len(family.template.station_x_m)
    errors=LinearErrors(0.,0.,0.,0.,np.zeros(n),np.zeros(n))
    return reusable.assemble_instance(family,instance,replace(family.template.band),
                                       errors=errors,diagnostics={"samples":192})


def test_catalogue_floor_change_rejects_warm_family_and_rebuilds_gm_row(context):
    from stowage.linear.operations import constraints
    instance,params,state=context
    first=reusable.coefficient_family(instance,params)
    assert reusable.coefficient_family(instance,params) is not None
    old_model=assemble(first,instance)
    old_rows=constraints(old_model,1.,direction="conservative")
    state["minimum"]=.4
    assert cache.coefficient_family_key(instance,params,first.template.band)!=first.cache_key
    with pytest.raises(ValueError,match="differ from coefficient family"):
        assemble(first,instance)
    second=reusable.coefficient_family(instance,params)
    assert second.cache_key!=first.cache_key
    assert second.template.gm_min_m==pytest.approx(.4)
    current=assemble(second,instance)
    assert current.gm_min_m==pytest.approx(.4)
    rows=constraints(current,1.,direction="conservative")
    gm=rows.names.index("gm");old_gm=old_rows.names.index("gm")
    np.testing.assert_allclose(rows.a_cell[gm]-old_rows.a_cell[old_gm],.2)
    assert rows.const[gm]-old_rows.const[old_gm]==pytest.approx(.2*current.const_mass_kg)


def test_hull_gm_floor_remains_binding_when_catalogue_is_lower(context):
    instance,params,state=context
    state["minimum"]=.1
    family=reusable.coefficient_family(instance,params)
    assert assemble(family,instance).gm_min_m==pytest.approx(instance.vessel.hull.min_gm_m)
