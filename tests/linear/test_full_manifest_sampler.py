"""Full stack mass can occupy a few bottom tiers instead of every cell."""
from collections import Counter
import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.conventions import make_container_id
from stowage.linear.bands import _sample_whole_columns
from stowage.model import ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_vessel


def saturated(name, *, compact, max_bottom=False, use_forties=True):
    # Layout-only loading is cheap and intentionally performs no hull assembly.
    production = load_vessel(name, with_hull=False)
    original_caps = {stack.id: stack.max_mass_kg for stack in production.stacks}
    # This explicit fixture variant dedicates columns to a known length class.
    # Its local maximum is implied by BOTH the production structural limit and
    # the 30.48 t maximum gross mass of each available physical box position.
    local_caps = {stack.id: min(stack.max_mass_kg, 30480.*len(stack.cell_ids))
                  for stack in production.stacks}
    lookup = {(s.bay, s.row, s.on_deck): s for s in production.stacks}
    selected_pairs = []
    for left in production.stacks:
        if not use_forties or not left.on_deck or (left.bay-1) % 4:
            continue
        right = lookup.get((left.bay+2, left.row, True))
        if right is None:
            continue
        usable = min(len(left.cell_ids), len(right.cell_ids),
                     int(math.floor((left.max_height_m+1e-7)/2.896)),
                     int(math.floor((right.max_height_m+1e-7)/2.896)))
        assert usable > 0
        # One 40 ft box shares its gross mass equally between the two cells.
        cap = min(local_caps[left.id], local_caps[right.id], 30480.*usable/2)
        local_caps[left.id] = local_caps[right.id] = cap
        selected_pairs.append((left.id, right.id, usable))
    vessel = production.model_copy(update={"stacks": [
        stack.model_copy(update={"max_mass_kg": local_caps[stack.id]})
        for stack in production.stacks]})
    by_id = {stack.id: stack for stack in vessel.stacks}
    groups, covered = [], set()
    for left_id, right_id, usable in selected_pairs:
        left, right = by_id[left_id], by_id[right_id]
        n = math.ceil((left.max_mass_kg+right.max_mass_kg)/30480.) if compact else usable
        gross = (left.max_mass_kg+right.max_mass_kg)/n
        groups.append((40, 2.896, gross, "on_deck", n))
        covered.update((left.id, right.id))
    for stack in vessel.stacks:
        if stack.id in covered:
            continue
        n = math.ceil(stack.max_mass_kg/30480.) if compact else len(stack.cell_ids)
        groups.append((20, 2.591, stack.max_mass_kg/n,
                       "on_deck" if stack.on_deck else "under_deck", n))
    if max_bottom:
        recipes=[]
        for length,height,gross,location,n in groups:
            capacity=gross*n
            heavy=int(math.floor((capacity+1e-5)/30480.))
            residual=capacity-heavy*30480.
            if heavy: recipes.append((length,height,30480.,location,heavy))
            if residual>1e-5: recipes.append((length,height,residual,location,1))
        groups=recipes
    prototype = build_instance().containers[1]
    boxes = []
    for length, height, gross, location, n in groups:
        for _ in range(n):
            boxes.append(prototype.model_copy(update={
                "id": make_container_id("QZXU", 10000+len(boxes)),
                "length_ft": length, "height_m": height, "gross_mass_kg": gross,
                "iso_type": "45G1" if length == 40 else "22G1",
                "tare_kg": 4200. if length == 40 else 2200., "dg": None,
                "special_stowage": [location], "oog": None,
            }))
    assert all(box.tare_kg <= box.gross_mass_kg <= 30480.+1e-7 for box in boxes)
    instance = SimpleNamespace(vessel=vessel, original_stack_caps_kg=original_caps,
        onboard=ShipState(vessel_id=vessel.id),
        discharge_list=[], load_list=[b.id for b in boxes], containers=boxes)
    model = SimpleNamespace(cell_ids=tuple(c.id for c in vessel.cells),
                            cell_vcg_m=np.array([c.vcg_base_m+1.2955 for c in vessel.cells]))
    return model, instance


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("compact", [False, True])
def test_complete_known_inventory_saturates_stacks_from_bottom(name, compact):
    model, instance = saturated(name, compact=compact)
    result = _sample_whole_columns(model, instance, np.random.default_rng(3707))
    assert result is not None
    masses, heights = result
    indices = {cid: i for i, cid in enumerate(model.cell_ids)}
    assert masses.sum() == pytest.approx(sum(b.gross_mass_kg for b in instance.containers))
    expected = Counter()
    for box in instance.containers:
        tokens = 1 if box.length_ft == 20 else 2
        expected[round(box.gross_mass_kg/tokens, 7)] += tokens
    actual = Counter(round(float(m), 7) for m in masses if m > 0)
    assert actual == expected
    unused = masses == 0
    np.testing.assert_array_equal(heights[unused], model.cell_vcg_m[unused])
    # With shortened production columns, even the minimum number of boxes may
    # legitimately occupy every tier. Occupancy must equal the actual inventory.
    assert np.count_nonzero(~unused) == sum(expected.values())
    assert np.count_nonzero(unused) == len(model.cell_ids)-sum(expected.values())
    for stack in instance.vessel.stacks:
        rows = np.array([indices[cid] for cid in stack.cell_ids])
        n = np.count_nonzero(masses[rows])
        assert 0 < n <= len(rows)
        assert np.all(masses[rows[:n]] > 0) and np.all(masses[rows[n:]] == 0)
        assert masses[rows].sum() == pytest.approx(stack.max_mass_kg)
        assert masses[rows].sum() <= instance.original_stack_caps_kg[stack.id]+1e-7
        assert stack.max_mass_kg <= instance.original_stack_caps_kg[stack.id]+1e-7
        height = (heights[rows[0]]-stack.base_z_m)*2
        assert n*height <= stack.max_height_m+1e-7
        np.testing.assert_allclose(heights[rows[:n]], stack.base_z_m+(np.arange(n)+.5)*height)


def test_nearby_but_nonintegral_inventory_is_not_accepted():
    model, instance = saturated("feeder_500", compact=True)
    for i,delta in enumerate((1.,2.,-3.)):
        box=instance.containers[i]
        instance.containers[i]=box.model_copy(update={"gross_mass_kg": box.gross_mass_kg+delta})
    assert _sample_whole_columns(model, instance, np.random.default_rng(1)) is None


def test_wrong_region_and_excess_height_fail_closed():
    model, instance = saturated("feeder_500", compact=True)
    instance.containers = [box.model_copy(update={"special_stowage": ["under_deck"]})
                           for box in instance.containers]
    assert _sample_whole_columns(model, instance, np.random.default_rng(1)) is None
    model, instance = saturated("feeder_500", compact=True)
    instance.containers = [box.model_copy(update={"height_m": 100.}) for box in instance.containers]
    assert _sample_whole_columns(model, instance, np.random.default_rng(1)) is None


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("use_forties", [False, True])
def test_exact_maximum_bottom_and_residual_top_recipes(name, use_forties):
    model, instance = saturated(name, compact=True, max_bottom=True, use_forties=use_forties)
    result = _sample_whole_columns(model, instance, np.random.default_rng(3707))
    assert result is not None
    masses,heights=result
    expected=Counter()
    for box in instance.containers:
        tokens=1 if box.length_ft==20 else 2
        expected[round(box.gross_mass_kg/tokens,7)]+=tokens
    assert Counter(round(float(m),7) for m in masses if m>0)==expected
    index={cid:i for i,cid in enumerate(model.cell_ids)}
    for stack in instance.vessel.stacks:
        rows=np.array([index[cid] for cid in stack.cell_ids])
        occupied=masses[rows][masses[rows]>0]
        assert occupied.sum()==pytest.approx(stack.max_mass_kg)
        assert occupied.sum()<=instance.original_stack_caps_kg[stack.id]+1e-7
        assert stack.max_mass_kg<=instance.original_stack_caps_kg[stack.id]+1e-7
        assert np.all(np.diff(occupied)<=1e-7)
        assert np.all(masses[rows[:len(occupied)]]>0)
    np.testing.assert_array_equal(heights[masses==0],model.cell_vcg_m[masses==0])


def test_mixed_two_and_three_tier_equal_capacity_columns_fill_restrictive_first():
    base=build_instance()
    selected=[s for s in base.vessel.stacks if not s.on_deck and s.row==1]
    selected=sorted(selected,key=lambda s:s.bay)
    left,right=selected
    cells=[c for c in base.vessel.cells if c.stack_id in (left.id,right.id)]
    top=next(c for c in cells if c.id==right.cell_ids[-1])
    extra=top.model_copy(update={'id':top.id[:-2]+'06','vcg_base_m':top.vcg_base_m+2.591})
    cells.append(extra)
    stacks=[left.model_copy(update={'max_mass_kg':60_000.,'max_height_m':2*2.591}),
            right.model_copy(update={'cell_ids':[*right.cell_ids,extra.id],
                                     'max_mass_kg':60_000.,'max_height_m':3*2.591})]
    vessel=SimpleNamespace(id=base.vessel.id,cells=cells,stacks=stacks)
    prototype=base.containers[1]
    boxes=[prototype.model_copy(update={'id':make_container_id('QZXU',20000+i),
            'height_m':2.591,'length_ft':20,'gross_mass_kg':gross,'oog':None,
            'special_stowage':['under_deck']})
           for i,gross in enumerate([30_000.,30_000.,20_000.,20_000.,20_000.])]
    instance=SimpleNamespace(vessel=vessel,containers=boxes,load_list=[b.id for b in boxes],
        discharge_list=[],onboard=ShipState(vessel_id=vessel.id))
    ordered=sorted(cells,key=lambda c:c.id)
    model=SimpleNamespace(cell_ids=tuple(c.id for c in ordered),
        cell_vcg_m=np.array([c.vcg_base_m+2.591/2 for c in ordered]))
    indices={cid:i for i,cid in enumerate(model.cell_ids)}
    for seed in range(20):
        result=_sample_whole_columns(model,instance,np.random.default_rng(seed))
        assert result is not None
        masses,heights=result
        np.testing.assert_allclose(masses[[indices[c] for c in stacks[0].cell_ids]],30_000.)
        np.testing.assert_allclose(masses[[indices[c] for c in stacks[1].cell_ids]],20_000.)
        assert masses.sum()==pytest.approx(sum(s.max_mass_kg for s in stacks))
