"""Necessary securing screens compared with the actual checker geometry.

These synthetic support examples do not certify hydrostatics or lashing loads.
Only the motion inputs are fixed: support replay and the securing load graph
are the real visibility/lashing implementations, without checker doubles.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.checker.lashing import _load_graph
from stowage.checker.visibility import _all_cargo_geometry
from stowage.model import Instance, Placement
from stowage.model.fixtures import build_instance
from stowage.solver.securing_geometry import (
    securing_geometry_violation,
    securing_state_violations,
)


MOTIONS = SimpleNamespace(at=lambda *_: (0., 0., 0.))
OFFSETS = (-1.01e-6, -1e-6, -.99e-6, 0., .99e-6, 1e-6, 1.01e-6)
PROFILES = {
    'standard': ((2.591, 0.), (2.591, 0.)),
    'high-cubes': ((2.896, 0.), (2.896, 0.)),
    'unequal-high-cube': ((2.591, 0.), (2.896, 0.)),
    'oog': ((2.591, .4), (2.591, .4)),
    'unequal-oog': ((2.591, 0.), (2.591, .4)),
    'equal-high-cube-oog': ((2.896, 0.), (2.591, 2.896-2.591)),
}


def supported_long(*, deck, profile='standard', offset=0.):
    data = build_instance().model_dump()
    boxes = data['containers'][:3]
    for box in boxes:
        box.update(dg=None, oog=None, special_stowage=[], pod='THBKK',
                   gross_mass_kg=8000., reefer=False, reefer_setpoint_c=None)
    for box, (height, over_height) in zip(boxes[1:], PROFILES[profile]):
        box.update(iso_type='25G1' if height == 2.896 else '22G1', height_m=height,
                   oog={'over_height_m': over_height} if over_height else None)
    data.update(containers=boxes, load_list=[boxes[0]['id']], discharge_list=[])
    data['vessel']['fixed_loads'] = []
    suffix = 'D' if deck else 'H'
    for stack in data['vessel']['stacks']:
        if stack['id'] == f'00301{suffix}':
            stack['base_z_m'] += offset
    tier = 82 if deck else 2
    data['onboard']['placements'] = [
        {'container_id': box['id'], 'slot': f'{bay:03d}01{tier:02d}'}
        for box, bay in zip(boxes[1:], (1, 3))]
    data['yard']['stacks'] = [
        {'key': 'A-001-01', 'length_ft': 40, 'boxes': data['load_list']}]
    return Instance.model_validate(data), f'00201{tier+2:02d}'


@pytest.mark.parametrize('deck', (False, True), ids=('hold', 'deck'))
@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('offset', OFFSETS)
def test_screen_never_rejects_mixed_supports_accepted_by_real_checker(deck, profile, offset):
    instance, slot = supported_long(deck=deck, profile=profile, offset=offset)
    action = Place(instance.load_list[0], slot)
    expander = Expander(instance)
    reason = securing_geometry_violation(instance, expander, action, include_existing=True)
    ship = instance.onboard.model_copy(deep=True)
    ship.placements.append(Placement(container_id=action.box, slot=slot))
    try:
        loads = _load_graph(instance, ship, MOTIONS)
    except ValueError:
        checker_accepts = False
    else:
        checker_accepts = True
        assert len(loads) == 3
        assert len(_all_cargo_geometry(instance, ship)) == 3
    assert reason is None or not checker_accepts
    # In this simple two-column arrangement the screen has exactly the same
    # geometry and mixed-size rules, so require the converse as well.
    assert (reason is None) == checker_accepts
    prefix = securing_geometry_violation(instance, expander, action)
    if deck:
        assert prefix is None
    else:
        assert prefix == reason
    completed = Expander(instance.model_copy(update={'onboard': ship}))
    assert bool(securing_state_violations(instance, completed)) == (not checker_accepts)


def test_overlapping_long_slots_synchronize_tops_like_real_checker():
    instance, _ = supported_long(deck=True)
    data = instance.model_dump()
    lower_long, lower_twenty, upper_long = data['containers']
    for box, length in ((lower_long, 40), (lower_twenty, 20), (upper_long, 40)):
        box.update(iso_type='42G1' if length == 40 else '22G1',
                   length_ft=length, height_m=2.591)
    data['load_list'] = [upper_long['id']]
    data['yard']['stacks'][0]['boxes'] = data['load_list']
    data['onboard']['placements'] = [
        {'container_id': lower_twenty['id'], 'slot': '0050182'},
        {'container_id': lower_long['id'], 'slot': '0020182'}]
    for stack in data['vessel']['stacks']:
        if stack['id'] == '00301D':
            stack['base_z_m'] = 9.+.99e-6
    template = next(s for s in data['vessel']['stacks'] if s['id'] == '00101D')
    data['vessel']['stacks'].append(dict(template, id='00501D', bay=5,
        base_z_m=9.-.5e-6, cell_ids=['0050182', '0050184'], hatch_cover_id=None))
    for tier in (82, 84):
        cell = next(c for c in data['vessel']['cells'] if c['id'] == f'00101{tier}')
        data['vessel']['cells'].append(dict(cell, id=f'00501{tier}', stack_id='00501D', lcg_m=59.3))
    instance = Instance.model_validate(data)
    action = Place(upper_long['id'], '0040184')
    ship = instance.onboard.model_copy(deep=True)
    ship.placements.insert(0, Placement(container_id=action.box, slot=action.slot))
    # The checker replays by (tier, slot), ignoring insertion order, and the
    # lower long box resets both A/B tops to its first support's roof.
    geometry = _all_cargo_geometry(instance, ship)
    assert geometry[-1].bottom == geometry[0].top
    assert len(_load_graph(instance, ship, MOTIONS)) == 3
    assert securing_geometry_violation(
        instance, Expander(instance), action, include_existing=True) is None
    completed = Expander(instance.model_copy(update={'onboard': ship}))
    assert securing_state_violations(instance, completed) == ()


@pytest.mark.parametrize('deck', (False, True), ids=('hold', 'deck'))
@pytest.mark.parametrize('lower_length', (40, 45))
def test_twenty_over_long_matches_real_checker(deck, lower_length):
    instance, _ = supported_long(deck=deck)
    lower, upper = instance.containers[:2]
    lower.length_ft = lower_length
    lower.iso_type = '42G1' if lower_length == 40 else 'L2G1'
    instance.onboard.placements = [Placement(container_id=lower.id,
        slot='0020182' if deck else '0020102')]
    instance.load_list = [upper.id]
    instance.yard.stacks = []
    action = Place(upper.id, '0010184' if deck else '0010104')
    ship = instance.onboard.model_copy(deep=True)
    ship.placements.append(Placement(container_id=upper.id, slot=action.slot))
    with pytest.raises(ValueError, match='20-foot box over'):
        _load_graph(instance, ship, MOTIONS)
    assert '20-foot box over' in securing_geometry_violation(
        instance, Expander(instance), action, include_existing=True)


@pytest.mark.parametrize('deck', (False, True), ids=('hold', 'deck'))
@pytest.mark.parametrize('lower_length', (40, 45))
@pytest.mark.parametrize('upper_length', (40, 45))
def test_long_over_long_remains_accepted_by_real_checker(deck, lower_length, upper_length):
    instance, slot = supported_long(deck=deck)
    lower, upper = instance.containers[:2]
    for box, length in ((lower, lower_length), (upper, upper_length)):
        box.length_ft = length
        box.iso_type = '42G1' if length == 40 else 'L2G1'
        box.height_m = 2.591
    instance.onboard.placements = [Placement(container_id=lower.id,
        slot='0020182' if deck else '0020102')]
    instance.load_list = [upper.id]
    instance.yard.stacks = []
    action = Place(upper.id, slot)
    ship = instance.onboard.model_copy(deep=True)
    ship.placements.append(Placement(container_id=upper.id, slot=slot))
    assert len(_load_graph(instance, ship, MOTIONS)) == 2
    assert securing_geometry_violation(
        instance, Expander(instance), action, include_existing=True) is None


def test_hold_prefix_defers_until_required_discharge_is_gone():
    instance, slot = supported_long(deck=False)
    action = Place(instance.load_list[0], slot)
    expander = Expander(instance)
    assert securing_geometry_violation(instance, expander, action) is not None
    # A required discharge can remove a supporting blocker; it is only safe
    # to prune irreversible hold prefixes after all such discharges have left.
    instance.discharge_list = [instance.containers[1].id]
    assert securing_geometry_violation(instance, expander, action) is None
