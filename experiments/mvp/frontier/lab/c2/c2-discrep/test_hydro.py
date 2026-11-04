"""Focused coefficient and report regression on approved lab map lists."""
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from stowage.io import load_instance
from stowage.conventions import parse_slot,format_slot
from stowage.model import CheckReport,PartReport
from stowage.model.ship import Placement
from stowage.checker.stability import _context
from stowage.checker.stability_loading import loading_condition
from hydro import HydroFeatures,checker_margins

HERE=Path(__file__).resolve().parent
LAB=HERE.parents[1]


def moment_vector(c):
    return c.mass_kg/1000.*np.array([1.,c.lcg_m,c.tcg_m,c.kg_m])


def verify(path):
    instance=load_instance(path)
    geometry,params=_context(instance.vessel)
    def condition(ship):
        return loading_condition(instance,ship,geometry=geometry,ship_params=params,
                                 precomputed_soundings=True,small_tank_exemption=True)
    bg=condition(instance.onboard)
    assert not bg.errors,bg.errors
    tested=0
    boxes=list(instance.containers)
    for length in (20,40):
        available=[k for k,b in enumerate(boxes) if b.id in instance.load_list and b.length_ft==length]
        if len(available)<2: continue
        stack=next((s for s in instance.vessel.stacks if len(s.cell_ids)>=2 and
                    all((c:=next(c for c in instance.vessel.cells if c.id==cid)).accepts_20
                        for cid in s.cell_ids[:2])),None)
        if length==20:
            if stack is None: continue
            slots=stack.cell_ids[:2]
        else:
            slots=None
            cells={c.id:c for c in instance.vessel.cells}
            for candidate in instance.vessel.stacks:
                if len(candidate.cell_ids)<2: continue
                found=[]
                for cid in candidate.cell_ids[:2]:
                    q=parse_slot(cid)
                    partner=format_slot(q.bay+2,q.row,q.tier)
                    if partner not in cells or not cells[cid].accepts_40 or not cells[partner].accepts_40:
                        break
                    found.append(format_slot(q.bay+1,q.row,q.tier))
                if len(found)==2:
                    slots=found;stack=candidate;break
            if slots is None: continue
        chosen=tuple(sorted(available,key=lambda k:-boxes[k].height_m)[:2])
        table=SimpleNamespace(box_ids=[b.id for b in boxes],slot_ids=list(slots),
                              stack_base_z=np.array([stack.base_z_m]))
        page=SimpleNamespace(stacks=(0,),slots=(0,1))
        model=SimpleNamespace(p=SimpleNamespace(instance=instance),T=table,pages=[page])
        hydro=HydroFeatures(model)
        vector=hydro.vector(SimpleNamespace(page=0,boxes=chosen))
        ship=instance.onboard.model_copy(update={'placements':[
            Placement(container_id=boxes[b].id,slot=slots[tier]) for tier,b in enumerate(chosen)]})
        actual=condition(ship)
        assert not actual.errors,actual.errors
        expected=moment_vector(actual)-moment_vector(bg)
        np.testing.assert_allclose(vector[:4],expected,rtol=1e-11,atol=1e-7)
        np.testing.assert_allclose(sum(vector[5:]),vector[0],rtol=1e-12)
        tested+=1
    assert tested, 'no two-box physical page available'
    print(instance.id,'page sizes checked',tested)


if __name__=='__main__':
    for approved_set in ('quick_small','medium12'):
        path=next(line for line in (LAB/'sets'/f'{approved_set}.txt').read_text().splitlines()
                  if line.strip() and not line.startswith('#'))
        verify(path)
    report=CheckReport(passed=False,parts={
        'condition:a:lashing':PartReport(name='lashing',margins={'max_racking_ratio':-.2}),
        'stability':PartReport(name='stability',margins={'condition:a.trim_m':-.4,'gm_m':.1,
                              'containership_area_0_30_mrad':-.01})})
    got=checker_margins(report)['margins']
    assert got['securing_ratio']==-.2 and got['trim_m']==-.4 and got['second_check_area_mrad']==-.01
    assert got['sight_m'] is None
    print('typed worst-condition margin extraction checked')
