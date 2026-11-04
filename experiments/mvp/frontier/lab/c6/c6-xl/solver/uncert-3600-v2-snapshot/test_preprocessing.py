from types import SimpleNamespace as NS
from pathlib import Path
import sys
import numpy as np
import pytest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from e8_static import fast_static, install
from sight_mask import restrict
from stowage.model import ShipState, Placement
from stowage.conventions import stack_id, make_container_id
from stowage.checker.rules_special import special_violations
from stowage.rules.catalogue import rule_freeze_v1
from xl_sight import Model as SightModel


def heat(x0,x1,y0,y1,z0,z1):
    return NS(id='heat',kind='engine_room',tank_id=None,x_min_m=x0,x_max_m=x1,
              y_min_m=y0,y_max_m=y1,z_min_m=z0,z_max_m=z1)


def toy(heats,clearance=1.,demand=501):
    cells=[]; stacks=[]
    for bay,x,base in ((1,10.,7.),(3,16.058,8.)):
        sid=stack_id(bay,1,True)
        ids=[f'{bay:03d}01{tier:02d}' for tier in (82,84)]
        cells.extend(NS(id=cid,stack_id=sid,lcg_m=x,tcg_m=-2.,vcg_base_m=base+2.6*k) for k,cid in enumerate(ids))
        stacks.append(NS(id=sid,bay=bay,row=1,on_deck=True,cell_ids=ids,base_z_m=base,
                         max_mass_kg=100000.,max_height_m=20.,max_mass_20_kg=None,max_mass_40_kg=None))
    boxes=[NS(id=make_container_id('ABCU',i),length_ft=length,height_m=height,oog=None,
              special_stowage=['away_from_heat'] if i<4 else [])
           for i,(length,height) in enumerate([(20,2.591),(20,2.896),(40,2.591),(40,2.896),(20,2.591)])]
    vessel=NS(cells=cells,stacks=stacks,heat_sources=heats,tanks=[])
    inst=NS(vessel=vessel,containers=boxes,load_list=list(range(demand)),onboard=ShipState(vessel_id='toy'),
            rule_overrides={'expert.e8.away_from_heat':{'minimum_clearance':clearance}},
            port=NS(port_rules=[],quay_crane=NS(max_on_deck_tiers=20)),rotation=NS(calls=[]),call_index=0)
    slots=[c.id for c in cells]+['0020182','0020184']
    static=np.random.default_rng(9).random((5,6))>.15
    T=NS(box_ids=[b.id for b in boxes],slot_ids=slots,heat=np.array([True]*4+[False]),static=static)
    return NS(p=NS(instance=inst,boxes={b.id:b for b in boxes}),T=T,boxes=list(range(5)))


def original_mask(model):
    inst,T=model.p.instance,model.T
    out=T.static.copy()
    for b in model.boxes:
        if not T.heat[b]:continue
        for s in np.flatnonzero(out[b]):
            probe=inst.onboard.model_copy(update={'placements':[Placement.model_construct(container_id=T.box_ids[b],slot=T.slot_ids[s])]})
            found=special_violations(inst,probe,call_index=0,focus_container_id=T.box_ids[b])
            if any('away_from_heat' in str(v.rule_id) for v in found):out[b,s]=False
        if not out[b].any():out[b]=T.static[b]
    return out


@pytest.mark.parametrize('clearance',[0.,1.,2.,None])
def test_exact_e8_against_native_many_envelopes_and_boundaries(clearance):
    rng=np.random.default_rng(245100000)
    probes=[heat(10.+3.029+d,12.+3.029+d,-3.,-1.,7.,9.591)
            for d in (-1e-8,-1e-9,0.,1e-9,1e-8,.5,1.,2.)]
    probes.extend(heat(float(x),float(x+4),-4.,0.,float(z),float(z+2)) for x,z in rng.uniform([0,0],[25,15],size=(8,2)))
    with rule_freeze_v1(enabled=False):
        for source in probes:
            m=toy([source],clearance)
            np.testing.assert_array_equal(fast_static(m),original_mask(m))


def test_all_rejected_restores_original_row():
    with rule_freeze_v1(enabled=False):
        m=toy([heat(-100.,100.,-100.,100.,-100.,100.)])
        np.testing.assert_array_equal(fast_static(m),m.T.static)
        np.testing.assert_array_equal(fast_static(m),original_mask(m))


def test_fallbacks_and_small_original_dispatch(monkeypatch):
    import pages_base
    calls=[]
    monkeypatch.setattr(pages_base.Model,'_static',lambda self:calls.append(self) or 'native')
    install()
    with rule_freeze_v1(enabled=False):
        small=toy([],demand=500)
        assert fast_static(small) is None
        assert pages_base.Model._static(small)=='native' and calls[-1] is small
        occupied=toy([])
        occupied.p.instance.onboard=occupied.p.instance.onboard.model_copy(update={'placements':[Placement.model_construct(container_id=occupied.T.box_ids[0],slot=occupied.T.slot_ids[0])]})
        assert fast_static(occupied) is None
        invalid=toy([]);invalid.p.instance.vessel.stacks[0].cell_ids=[]
        assert fast_static(invalid) is None
        oog=toy([]);oog.p.boxes[oog.T.box_ids[0]].oog=NS(over_width_port_m=.2)
        assert fast_static(oog) is None
    with rule_freeze_v1(enabled=True):
        assert fast_static(toy([])) is None


def old_sight_mask(m,out,minimum):
    for page in m.pages:
        if not page.deck:continue
        base=max(float(m.T.stack_base_z[j]) for j in page.stacks)
        for tier,slot in enumerate(page.slots):
            for b in m.boxes:
                if out[b,slot] and base+tier*minimum+float(m.T.height[b])>m.sight_limit(page,tier,b)+1e-8:
                    out[b,slot]=False
    return out


def sight_model():
    m=SightModel.__new__(SightModel)
    lengths=[20,20,40,40,45,40]
    heights=np.array([2.591,2.896,2.591,2.896,2.591,3.1])
    m.boxes=list(range(6));m.T=NS(box_ids=[make_container_id('ABCU',i) for i in range(6)],height=heights,stack_base_z=np.array([7.,8.,9.]))
    boxes={name:NS(length_ft=lengths[i],height_m=heights[i],oog=None) for i,name in enumerate(m.T.box_ids)}
    boxes[m.T.box_ids[-1]].oog=NS(over_length_aft_m=.3,over_length_fore_m=.5,over_width_starboard_m=.1,over_width_port_m=.2)
    m.p=NS(boxes=boxes)
    m.pages=[NS(deck=True,stacks=(j,),slots=tuple(range(4*j,4*j+4)),xy=(20.+20*j,-2.+2*j)) for j in range(3)]
    m._slot_xy=lambda page,tier:page.xy
    m.sight_cache={};m.sight_roof_margin_m=.15
    m.sight_frames=[dict(eye=(0.,0.,18.),normal=(0.,0.,1.),along=(1.,0.,0.),across=(0.,1.,0.),aft_draft_m=7.,bow_range_m=90.)]
    return m


def test_sight_grouping_exact_native_roof_caps_and_masks():
    rng=np.random.default_rng(245100001)
    for _ in range(20):
        m=sight_model();mask=rng.random((6,12))<.8
        minimum=float(min(m.T.height))
        np.testing.assert_array_equal(restrict(m,mask.copy(),minimum),old_sight_mask(m,mask.copy(),minimum))


def test_sight_exact_threshold_arithmetic():
    for delta in (-1e-8,-1e-9,0.,1e-9,1e-8):
        m=sight_model()
        m.sight_limit=lambda page,tier,b:7.+tier*2.591+2.591+delta
        mask=np.ones((6,12),bool)
        np.testing.assert_array_equal(restrict(m,mask.copy(),2.591),old_sight_mask(m,mask.copy(),2.591))
