"""Positive-control projection and motion envelope regression tests."""
import json
from pathlib import Path

import numpy as np
import pytest

from packing import build_model, attach_rows
from physics import hull_reference, motion_floor
from stowage.checker import lashing
from stowage.conventions import cells_of_slot, parse_slot
from stowage.io import load_instance

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]


@pytest.mark.parametrize('number', [0,1])
def test_known_legal_plan_projects_into_outer_model(number):
    comparison = json.loads((LAB/'c2/c2-layout/comparison.json').read_text())
    row = json.loads(Path(comparison['maps'][number]['evidence']).read_text())
    instance = load_instance(row['input'])
    model = build_model(instance)
    bounds = attach_rows(model,instance)
    from rod_bound import attach_rod_rows
    attach_rod_rows(model,instance)
    x = np.zeros(model.n_variables)
    classes = {bid:k for k,c in enumerate(model.metadata['classes']) for bid in c['members']}
    slots = {(a['class_index'],tuple(tuple(s) for s in a['signature'])):j
             for j,a in enumerate(model.allocations)}
    cells = {c.id:c for c in instance.vessel.cells}
    stacks = {s.id:s for s in instance.vessel.stacks}
    for bid,slot in row['assignment'].items():
        if bid not in classes:
            continue
        signature = []
        for cid in cells_of_slot(slot):
            stack = stacks[cells[cid].stack_id]
            signature.append((parse_slot(cid).bay,bool(stack.on_deck),stack.cell_ids.index(cid),1))
        j = slots[classes[bid],tuple(sorted(signature))]
        x[j] += 1.
    tanks = row['plan'].get('ballast')
    masses = tanks['tank_masses_kg'] if tanks else instance.onboard.tank_masses_kg
    for t in bounds['tanks']:
        x[t['variable']] = masses.get(t['id'],instance.onboard.tank_masses_kg.get(t['id'],0.))
    lhs = model.A@x
    assert np.all(x>=np.asarray(model.variable_lower)-1e-7)
    assert np.all(x<=np.asarray(model.variable_upper)+1e-7)
    for value,lo,hi,name in zip(lhs,model.lower,model.upper,model.row_names):
        assert value>=lo-1e-5, (name,value,lo)
        assert value<=hi+1e-5, (name,value,hi)


def test_motion_envelope_against_checker():
    path = (LAB/'sets/quick_medium.txt').read_text().splitlines()[0]
    instance = load_instance(path)
    _,_,cb = hull_reference(instance)
    hull = instance.vessel.hull
    for lower in (0.,.25,.6,1.5):
        for zlower in (0.,hull.depth_m/2,hull.depth_m+12.):
            floor = motion_floor(instance,gm_lower=lower,z_lower=zlower)
            for gm in (max(lower,1e-4),max(lower,2.),hull.max_gm_lashing_m):
                if gm<lower:
                    continue
                for draft in (.1,hull.design_draught_m,100.):
                    actual = lashing._motions(hull,draft,gm,cb).at(.45*hull.lpp_m,0.,zlower+1.)
                    for key,value in zip(('al_m_s2','at_m_s2','av_m_s2'),actual):
                        assert floor[key]<=value+1e-9,(lower,zlower,key,floor[key],value)
