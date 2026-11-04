"""Design-GM masking is an intentionally stronger policy than actual-GM IRS.

This is an arithmetic counterexample to treating screen rejection as proof that
no ballast/loading condition could pass. It does not establish a hydrostatic or
stability-approved complete plan. Full checker acceptance remains independent.
"""
from types import SimpleNamespace

import pytest

from stowage.checker import lashing,visibility
from stowage.model import Cell,Placement,ShipState
from stowage.model.fixtures import build_instance
from stowage.solver.securing_screen import DeckSecuringScreen,NecessaryDeckSecuringScreen
from stowage.vessel.fleet import load_vessel


def test_design_policy_can_reject_actual_condition_securable_stack(monkeypatch):
    instance = build_instance()
    physical = load_vessel('feeder_500')
    stacks,cells = [],[]
    for bay in (1,3):
        original = next(s for s in instance.vessel.stacks if s.id==f'{bay:03}01D')
        source = next(c for c in instance.vessel.cells if c.id==original.cell_ids[0])
        ids = [f'{bay:03}01{82+2*j}' for j in range(4)]
        stacks.append(original.model_copy(update={'cell_ids':ids,'max_mass_kg':200000.,'max_height_m':30.}))
        cells.extend(Cell(**{**source.model_dump(),'id':cid,'vcg_base_m':9+2.591*j}) for j,cid in enumerate(ids))
    boxes = [b.model_copy(update={'length_ft':40,'gross_mass_kg':10000.,'height_m':2.591,'oog':None,'dg':None})
             for b in instance.containers[:4]]
    vessel = instance.vessel.model_copy(update={'hull':physical.hull,'cells':cells,'stacks':stacks,
        'fixed_loads':[],'fixed_load_cells':[],'fixed_load_stacks':[],'fixed_load_hatch_covers':[]})
    instance = instance.model_copy(update={'vessel':vessel,'containers':boxes})
    placements = [Placement(container_id=b.id,slot=f'00201{82+2*j}') for j,b in enumerate(boxes)]
    screen = DeckSecuringScreen(instance)
    design = screen.evaluate(placements)
    assert not design.passed
    assert NecessaryDeckSecuringScreen(instance).evaluate(placements).passed
    assert design.worst_ratio == pytest.approx(1.6737736280229165,rel=1e-10)
    condition = SimpleNamespace(**{**vars(screen.condition),'gm_m':.5,'t_aft_m':4.,'t_fwd_m':4.})
    monkeypatch.setattr(visibility,'_loading_condition',lambda *_:condition)
    ship = ShipState(vessel_id=vessel.id,placements=placements)
    actual = lashing.check_lashing(instance,ship,call_index=0)
    assert actual.ran and not actual.violations
    worst = max(v for k,v in actual.metrics.items() if k.startswith('max_') and k.endswith('_ratio'))
    assert worst == pytest.approx(.8562481310362382,rel=1e-10)
