from copy import deepcopy
from types import SimpleNamespace as NS
import numpy as np
import pytest
from geometry_cache import GeometryCache, install
from stowage.feasibility import _engine

NEAREST=_engine.IncrementalEngine._nearest_cells
OOG=_engine.IncrementalEngine._oog_zones


def engine():
    cells=[NS(id=f'c{i}',stack_id=f's{i}',lcg_m=float(x),tcg_m=0.,vcg_base_m=2.) for i,x in enumerate((0,20,40,60))]
    stacks=[NS(id=f's{i}',bay=i,row=0,on_deck=True,cell_ids=[f'c{i}'],base_z_m=2.,max_height_m=9.) for i in range(4)]
    T=NS(cell_ids=[c.id for c in cells],slot_ids=['q0','q1','q2'],stack_ids=[s.id for s in stacks],
         slot_cells=np.array([[0,-1],[1,2],[3,-1]],dtype=np.int64),
         slot_stacks=np.array([[0,-1],[1,2],[3,-1]],dtype=np.int64))
    o=NS(over_width_port_m=0.,over_width_starboard_m=0.,over_length_fore_m=0.,over_length_aft_m=0.)
    instance=NS(vessel=NS(cells=cells,stacks=stacks),containers=[NS(oog=o)],
                port=NS(port_rules=[]),rule_overrides={'expert.e9.over_width_length':{'oog_clearance':0.}})
    return NS(T=T,instance=instance,_slot_xy=np.array([[0.,0.],[30.,0.],[60.,0.]]))


@pytest.mark.parametrize('k',[0,1,2,5])
def test_native_first_and_identical_cached_donors(k):
    e=engine();cache=GeometryCache();calls=[]
    def native(self,n):calls.append(n);return NEAREST(self,n)
    expected=NEAREST(e,k)
    np.testing.assert_array_equal(cache.nearest(e,k,native),expected)
    np.testing.assert_array_equal(cache.nearest(deepcopy(e),k,native),expected)
    assert calls==[k]
    assert cache.stats['nearest_hits']==1


def test_native_arrays_and_outer_zones_cannot_mutate_cache():
    e=engine();cache=GeometryCache()
    expected=NEAREST(e,2)
    first=cache.nearest(e,2,NEAREST);first[:]=777
    second=cache.nearest(e,2,NEAREST);np.testing.assert_array_equal(second,expected);second[:]=999
    np.testing.assert_array_equal(cache.nearest(e,2,NEAREST),expected)
    expected_zones=OOG(e)
    first_zones=cache.oog(e,OOG);first_zones.append((777,));first_zones[0]=(999,)
    assert cache.oog(e,OOG)==expected_zones


@pytest.mark.parametrize('change',['coordinates','cell_order','slot_order','footprint','stack_indices','stack_geometry','k'])
def test_full_geometry_or_k_change_forces_native_call(change):
    e=engine();cache=GeometryCache();calls=[]
    def native(self,n):calls.append(n);return NEAREST(self,n)
    cache.nearest(e,2,native)
    changed=deepcopy(e);k=2
    if change=='coordinates':changed.instance.vessel.cells[0].lcg_m=.125
    elif change=='cell_order':changed.T.cell_ids.reverse()
    elif change=='slot_order':changed.T.slot_ids.reverse()
    elif change=='footprint':changed.T.slot_cells[0,0]=1
    elif change=='stack_indices':changed.T.slot_stacks[0,0]=2
    elif change=='stack_geometry':changed.instance.vessel.stacks[0].base_z_m=2.125
    elif change=='k':k=1
    np.testing.assert_array_equal(cache.nearest(changed,k,native),NEAREST(changed,k))
    assert len(calls)==2


@pytest.mark.parametrize('change',['radius_overhang','radius_clearance','slot_xy','slot_stack'])
def test_oog_native_radius_and_complete_arrays_force_miss(change):
    e=engine();cache=GeometryCache();calls=[]
    def native(self):calls.append(1);return OOG(self)
    first=cache.oog(e,native)
    assert cache.oog(deepcopy(e),native)==first
    changed=deepcopy(e)
    if change=='radius_overhang':changed.instance.containers[0].oog.over_width_port_m=8.
    elif change=='radius_clearance':changed.instance.rule_overrides['expert.e9.over_width_length']['oog_clearance']=20.
    elif change=='slot_xy':changed._slot_xy[0,0]=29.
    elif change=='slot_stack':changed.T.slot_stacks[0,0]=2
    assert cache.oog(changed,native)==OOG(changed)
    assert len(calls)==2


def test_lru_bound_and_native_exceptions_are_not_cached():
    cache=GeometryCache(max_entries=1);e=engine()
    cache.nearest(e,1,NEAREST);cache.nearest(e,2,NEAREST);cache.nearest(e,1,NEAREST)
    assert len(cache.entries)==1 and cache.stats['nearest_misses']==3
    with pytest.raises(ValueError):cache.nearest(e,-1,NEAREST)
    assert len(cache.entries)==1


def test_installer_source_assertion_and_idempotent_native_wrappers(monkeypatch):
    # Restore automatically after the test; original methods stay in the gate.
    monkeypatch.setattr(_engine.IncrementalEngine,'_nearest_cells',NEAREST)
    monkeypatch.setattr(_engine.IncrementalEngine,'_oog_zones',OOG)
    install();first=_engine.IncrementalEngine._nearest_cells
    assert first._c6_original is NEAREST
    assert _engine.IncrementalEngine._oog_zones._c6_original is OOG
    install();assert _engine.IncrementalEngine._nearest_cells is first
