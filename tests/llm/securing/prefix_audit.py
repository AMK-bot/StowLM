"""Small reproducible IRS-only mixed-support audit; one CPU, no model.

Run: uv run python tests/llm/securing/prefix_audit.py
No recovery in this finite grid is empirical evidence, never a monotonicity proof.
C1 gross-mass ordering is recorded separately from IRS force-only feasibility.
"""
import itertools
import json
import time

from stowage.conventions import make_container_id,SYNTHETIC_OWNER_CODES
from stowage.model import Cell,Placement
from stowage.model.fixtures import build_instance
from stowage.solver.securing_screen import DeckSecuringScreen
from stowage.vessel.fleet import load_vessel


def audit():
    fixture = build_instance()
    hull = load_vessel('feeder_500').hull
    started = time.perf_counter()
    counts = dict(cases=0,failing_prefix=0,recovered=0,c1_gross_order_cases=0,
                  c1_gross_order_failing_prefix=0,c1_gross_order_recovered=0,
                  c1_half_mass_cases=0,c1_half_mass_recovered=0)
    examples = []
    for tiers,height,left_mass,right_mass,long_mass in itertools.product(
            (1,2),(2.591,4.,6.),(4000.,10000.,20000.,30000.),
            (4000.,10000.,20000.,30000.),(4000.,10000.,20000.,30000.)):
        stacks,cells = [],[]
        for bay in (1,3):
            original = next(s for s in fixture.vessel.stacks if s.id==f'{bay:03}01D')
            source = next(c for c in fixture.vessel.cells if c.id==original.cell_ids[0])
            ids = [f'{bay:03}01{82+2*j}' for j in range(tiers+1)]
            stacks.append(original.model_copy(update={'cell_ids':ids,'max_mass_kg':200000.,'max_height_m':30.}))
            cells.extend(Cell(**{**source.model_dump(),'id':cid,'vcg_base_m':9+height*j}) for j,cid in enumerate(ids))
        boxes,placements = [],[]
        for j in range(tiers):
            for bay,mass in ((1,left_mass),(3,right_mass)):
                box = fixture.containers[len(boxes)].model_copy(update={'length_ft':20,'gross_mass_kg':mass,'height_m':height,'oog':None,'dg':None})
                boxes.append(box)
                placements.append(Placement(container_id=box.id,slot=f'{bay:03}01{82+2*j}'))
        vessel = fixture.vessel.model_copy(update={'hull':hull,'cells':cells,'stacks':stacks,
            'fixed_loads':[],'fixed_load_cells':[],'fixed_load_stacks':[],'fixed_load_hatch_covers':[]})
        prefix_instance = fixture.model_copy(update={'vessel':vessel,'containers':boxes})
        prefix = DeckSecuringScreen(prefix_instance).evaluate(placements)
        upper = fixture.containers[0].model_copy(update={'id':make_container_id(SYNTHETIC_OWNER_CODES[0],99),'length_ft':40,'gross_mass_kg':long_mass,'height_m':2.591,'oog':None,'dg':None})
        extended_instance = prefix_instance.model_copy(update={'containers':[*boxes,upper]})
        extension = DeckSecuringScreen(extended_instance).evaluate([*placements,Placement(container_id=upper.id,slot=f'00201{82+2*tiers}')])
        gross_order = long_mass <= min(left_mass,right_mass)
        half_order = long_mass/2 <= min(left_mass,right_mass)
        recovered = not prefix.passed and extension.passed
        counts['cases'] += 1
        counts['failing_prefix'] += not prefix.passed
        counts['recovered'] += recovered
        counts['c1_gross_order_cases'] += gross_order
        counts['c1_gross_order_failing_prefix'] += gross_order and not prefix.passed
        counts['c1_gross_order_recovered'] += gross_order and recovered
        counts['c1_half_mass_cases'] += half_order
        counts['c1_half_mass_recovered'] += half_order and recovered
        if recovered:
            examples.append(dict(tiers=tiers,height=height,left_mass=left_mass,right_mass=right_mass,
                long_mass=long_mass,prefix_ratio=prefix.worst_ratio,extended_ratio=extension.worst_ratio,
                c1_gross_order=gross_order,c1_half_mass_order=half_order))
    return dict(**counts,examples=examples,wall_s=time.perf_counter()-started)


if __name__=='__main__':
    print(json.dumps(audit(),sort_keys=True))
