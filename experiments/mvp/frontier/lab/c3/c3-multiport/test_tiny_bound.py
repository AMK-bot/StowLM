"""Exact six-cell relaxation checks; no native solver, replay or checker."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tiny_bound import tiny_global_bound


@pytest.fixture(autouse=True)
def legacy_mode(monkeypatch):
    monkeypatch.setattr('stowage.rules.catalogue.rule_freeze_v1_enabled', lambda: False)


def fixture(pods=('P1', 'P2', 'P3', 'P4', 'P5', 'P5')):
    cells = [NS(id=f'{bay:03}02{tier:02}', stack_id=f'{bay:03}02{side}')
             for bay in (1, 3) for side, levels in (('H', (2, 4)), ('D', (82,)))
             for tier in levels]
    stacks = [NS(id=f'{bay:03}02{side}', on_deck=side == 'D', hatch_cover_id='C',
                 cell_ids=[c.id for c in cells if c.stack_id == f'{bay:03}02{side}'])
              for bay in (1, 3) for side in ('H', 'D')]
    boxes = [NS(id=f'b{i}', pod=pod, length_ft=20) for i, pod in enumerate(pods)]
    return NS(load_list=[b.id for b in boxes], containers=boxes, call_index=0,
              onboard=NS(placements=[], covers_off={}),
              rotation=NS(calls=[NS(locode=p) for p in ('LOAD', 'P1', 'P2', 'P3', 'P4', 'P5')]),
              vessel=NS(cells=cells, stacks=stacks, hatch_covers=[NS(id='C')]),
              objective=NS(hatch_cover=1., future_hatch_cover=1., future_restow=1.,
                           future_restow_lifts_per_box=2))


def test_exact_five_port_global_bound_is_not_fixed_assignment_bound():
    instance = fixture()
    answer = tiny_global_bound(instance)
    assert answer['assignments_enumerated'] == answer['assignments_expected'] == 720
    assert answer['minimum_restow_lifts'] == 0
    assert answer['minimum_joint_weighted'] == 8
    assert answer['minimum_joint_weighted_exact'] == '8'
    assert set(answer['joint_witness']) == set(instance.load_list)
    assert set(answer['joint_witness'].values()) == {c.id for c in instance.vessel.cells}


def test_same_port_has_no_overstow_and_one_future_hatch_opening():
    answer = tiny_global_bound(fixture(('P1',) * 6))
    assert answer['minimum_restowed_boxes'] == 0
    assert answer['minimum_hatch_later'] == 2
    assert answer['mandatory_hatch_now'] == 2
    assert answer['minimum_joint_weighted'] == 4


def test_refuses_activated_projection(monkeypatch):
    monkeypatch.setattr('stowage.rules.catalogue.rule_freeze_v1_enabled', lambda: True)
    with pytest.raises(ValueError, match='legacy'):
        tiny_global_bound(fixture())


def test_joint_bound_uses_weights_and_can_trade_more_restows_for_fewer_hatches():
    instance = fixture(('P1', 'P1', 'P2', 'P2', 'P3', 'P3'))
    instance.objective.future_restow = 0
    instance.objective.future_hatch_cover = 3
    answer = tiny_global_bound(instance)
    assert answer['minimum_restow_lifts'] == 0
    assert answer['minimum_hatch_later'] == 4
    assert answer['minimum_joint_weighted'] == 14


@pytest.mark.parametrize('change', ('partial', 'long', 'onboard', 'open', 'unknown_pod', 'gap'))
def test_refuses_shapes_outside_exhaustive_family(change):
    instance = deepcopy(fixture())
    if change == 'partial':
        instance.load_list.pop()
    elif change == 'long':
        instance.containers[0].length_ft = 40
    elif change == 'onboard':
        instance.onboard.placements = [NS(container_id='resident')]
    elif change == 'open':
        instance.onboard.covers_off = {'C': 'quay'}
    elif change == 'unknown_pod':
        instance.containers[0].pod = 'MISSING'
    else:
        cell = instance.vessel.cells[1]
        old, cell.id = cell.id, '0010206'
        stack = next(s for s in instance.vessel.stacks if s.id == cell.stack_id)
        stack.cell_ids = [cell.id if c == old else c for c in stack.cell_ids]
    with pytest.raises(ValueError):
        tiny_global_bound(instance)
