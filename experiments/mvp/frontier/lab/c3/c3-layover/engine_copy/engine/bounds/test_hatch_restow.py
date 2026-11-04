# Copied from experiments/mvp/frontier/lab/c2/c2-hatch/bound_tests.py; engine integration changes are local.
"""Bounds checked against exhaustive move-engine optima and stored evidence."""
from pathlib import Path

import pytest

from .hatch_restow import lower_bound, _floor_scaled
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.io import load_instance
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


def fixture(*, initially_off=False, current=1, future=1):
    """Three ordinary loads, two deck cells and two cells under one cover.

    Geometry pattern adapted from tests/bounds/test_forced_hold.py; production
    models and exhaustive move engine are reused without modification.
    """
    data = build_instance(layover=True).model_dump()
    boxes = data["containers"][:3]
    for box in boxes:
        box.update(length_ft=20, iso_type="22G1", height_m=2.591,
            tare_kg=2200, gross_mass_kg=12000, max_gross_mass_kg=30000,
            reefer=False, reefer_setpoint_c=None, dg=None, oog=None,
            special_stowage=[], direct_delivery=False, pod="THBKK")
    ids = [box["id"] for box in boxes]
    data.update(containers=boxes, load_list=ids, discharge_list=[], stage="A")
    stacks = [s for s in data["vessel"]["stacks"]
              if s["id"] in {"00101D", "00301D", "00101H"}]
    for stack in stacks:
        stack["hatch_cover_id"] = "HC-02-1"
        stack["cell_ids"] = stack["cell_ids"][:1 if stack["on_deck"] else 2]
    cells = {cell for stack in stacks for cell in stack["cell_ids"]}
    cover = data["vessel"]["hatch_covers"][0]
    cover.update(bays=[1, 3], hold_stack_ids=["00101H"],
                 deck_stack_ids=["00101D", "00301D"])
    data["vessel"].update(stacks=stacks, hatch_covers=[cover], fixed_loads=[])
    data["vessel"]["cells"] = [c for c in data["vessel"]["cells"] if c["id"] in cells]
    block = data["port"]["yard"]["blocks"][0]
    block.update(n_bays=2, n_rows=1, max_tiers=4, allowed_lengths_ft=[20])
    data["port"]["yard"]["blocks"] = [block]
    data["yard"].update(stacks=[dict(key="A-001-01", length_ft=20, boxes=ids)], quay_buffer=[])
    data["onboard"].update(placements=[], covers_off={"HC-02-1": "quay"} if initially_off else {})
    data["objective"].update(hatch_cover=current, future_hatch_cover=future)
    return Instance.model_validate(data)


@pytest.mark.parametrize("freeze", [False, True])
@pytest.mark.parametrize("off,current,future,expected", [
    (False, 1, 1, 4), (True, 1, 1, 3), (False, 1.5, .25, 3.5),
    (False, 0, 1, 2), (False, 1, 0, 2)])
def test_exact_relaxation_matches_exhaustive_oracle(freeze, off, current, future, expected):
    with rule_freeze_v1(enabled=freeze):
        instance = fixture(initially_off=off, current=current, future=future)
        oracle = brute_force_optimum(instance, max_nodes=10000, time_limit_s=None)
        result = lower_bound(instance, budget_s=2)
        assert oracle.status == "optimal"
        assert oracle.objective == pytest.approx(expected)
        assert result.status == "optimal"
        assert result.lower_bound <= oracle.objective
        assert result.lower_bound == pytest.approx(expected)


def test_early_timeout_returns_bound_not_incumbent():
    instance = load_instance("experiments/mvp/corpus/val/small/feeder_800/random_yard/seed-040100011.json")
    result = lower_bound(instance, budget_s=.001)
    assert result.lower_bound >= result.counting_lower_bound
    # A time-limited solver must retain the existing bound and distinguish its
    # lower bound from its incumbent.
    assert result.status in {"unknown", "feasible", "optimal"}
    if result.relaxation_incumbent is not None:
        assert result.hatch_lower_bound <= result.relaxation_incumbent


def test_fractional_objective_rounds_down_exactly():
    from fractions import Fraction
    for value in [0.0004999999999999999, 0.1, 1.001, 1.23456789]:
        assert Fraction(_floor_scaled(value), 1000) <= Fraction(value)


def test_initial_arrival_uses_existing_counting_bound():
    instance = build_instance(layover=True)
    result = lower_bound(instance, budget_s=1)
    assert result.status == "counting_only"
    assert result.lower_bound == result.counting_lower_bound


@pytest.mark.parametrize("freeze,expected", [(False, 4), (True, 0)])
def test_e6_bottom_witness_matches_static_oracle(freeze, expected):
    data = fixture().model_dump()
    data["containers"] = data["containers"][:2]
    for box in data["containers"]:
        box["gross_mass_kg"] = 22000
    data["load_list"] = [b["id"] for b in data["containers"]]
    data["yard"]["stacks"][0]["boxes"] = data["load_list"]
    with rule_freeze_v1(enabled=freeze):
        instance = Instance.model_validate(data)
        oracle = brute_force_optimum(instance, max_nodes=10000, time_limit_s=None,
                                    check_static_rules=True)
        result = lower_bound(instance, budget_s=2)
        assert oracle.status == "optimal"
        assert result.lower_bound <= oracle.objective
        assert result.lower_bound == pytest.approx(expected)
        assert ("0 safe bottom-slot" in result.note) == freeze


def test_lateral_oog_disables_e6_cuts():
    data = fixture().model_dump()
    data["containers"] = data["containers"][:2]
    for box in data["containers"]:
        box["gross_mass_kg"] = 22000
    data["containers"][0]["oog"] = {"over_width_port_m": .1}
    data["load_list"] = [b["id"] for b in data["containers"]]
    data["yard"]["stacks"][0]["boxes"] = data["load_list"]
    with rule_freeze_v1(enabled=False):
        result = lower_bound(Instance.model_validate(data), budget_s=2)
    assert "0 safe bottom-slot" in result.note
    assert result.lower_bound == 0


@pytest.mark.parametrize('pods', [
    ('THBKK', 'THBKK', 'THBKK'),
    ('THBKK', 'THSGZ', 'THSGZ'),
    ('THSGZ', 'THBKK', 'THBKK'),
    ('THSGZ', 'THSGZ', 'THBKK'),
])
@pytest.mark.parametrize('restow_weight', [0, .125, 1])
def test_fixed_deck_hold_closure_matches_real_projection(pods, restow_weight):
    """A deck box required through two columns/covers still costs once/port."""
    from stowage.feasibility.tables import build_tables
    from stowage.model import Placement, ShipState
    from stowage.moves import project_future_moves
    data = fixture().model_dump()
    for box, pod in zip(data['containers'], pods):
        box['pod'] = pod
    data['objective']['future_restow'] = restow_weight
    instance = Instance.model_validate(data)
    tables = build_tables(instance)
    slots = ['0010102', '0010182', '0030182']
    assignment = dict(zip([b['id'] for b in data['containers']], slots))
    tables.static[:] = False
    for bid, slot in assignment.items():
        tables.static[tables.box_index[bid], tables.slot_index[slot]] = True
    ship = instance.onboard.model_copy(update={"placements": [Placement(container_id=bid, slot=slot)
                                for bid, slot in assignment.items()]})
    future = project_future_moves(instance, ship, from_call=instance.call_index)
    expected_restow = sum(f.restow_lifts for f in future) * restow_weight
    expected_hatch = 2 + sum(f.hatch_cover_lifts for f in future)
    result = lower_bound(instance, tables=tables, budget_s=2)
    assert result.status == 'optimal'
    assert result.witness_restow == pytest.approx(expected_restow)
    assert result.witness_hatch == pytest.approx(expected_hatch)
    assert result.lower_bound == pytest.approx(expected_restow + expected_hatch)


def spanning_fixture(*, both_targets=False):
    """Three deck columns, two overlapping long boxes carry closure sideways."""
    from copy import deepcopy
    data = build_instance(layover=True).model_dump()
    boxes = data['containers'][:6]
    for index, box in enumerate(boxes):
        box.update(length_ft=40 if index in (3, 5) else 20,
                   iso_type='42G1' if index in (3, 5) else '22G1',
                   height_m=2.591, tare_kg=2200, gross_mass_kg=12000,
                   max_gross_mass_kg=30000, reefer=False,
                   reefer_setpoint_c=None, dg=None, oog=None,
                   special_stowage=[], direct_delivery=False,
                   pod='THBKK' if index == 0 or both_targets and index == 1 else 'THSGZ')
    data.update(containers=boxes, load_list=[b['id'] for b in boxes],
                discharge_list=[], stage='A')
    originals = {s['id']: s for s in data['vessel']['stacks']}
    original_cells = {c['id']: c for c in data['vessel']['cells']}
    stacks, cells = [], []
    for old, bay in [('00101D', 1), ('00301D', 3), ('00102D', 5)]:
        stack = deepcopy(originals[old])
        stack['id'] = f'{bay:03d}01D'
        stack.update(bay=bay, row=1)
        stack['cell_ids'] = [f'{bay:03d}01{tier:02d}' for tier in (82, 84, 86)]
        stack['max_height_m'] = 10
        for new_id in stack['cell_ids']:
            cell = deepcopy(original_cells[originals[old]['cell_ids'][0]])
            cell.update(id=new_id, stack_id=stack['id'], accepts_20=True,
                        accepts_40=True, accepts_45=True)
            cells.append(cell)
        stacks.append(stack)
    cover = deepcopy(data['vessel']['hatch_covers'][0])
    cover.update(bays=[1, 3, 5], hold_stack_ids=[],
                 deck_stack_ids=[s['id'] for s in stacks])
    for stack in stacks:
        stack['hatch_cover_id'] = cover['id']
    data['vessel'].update(stacks=stacks, cells=cells, hatch_covers=[cover], fixed_loads=[])
    data['onboard'].update(placements=[], covers_off={})
    block = data['port']['yard']['blocks'][0]
    block.update(n_bays=3, n_rows=1, max_tiers=8, allowed_lengths_ft=[20, 40])
    data['port']['yard']['blocks'] = [block]
    data['yard'].update(stacks=[dict(key='A-001-01', length_ft=20,
        boxes=[b['id'] for b in boxes if b['length_ft'] == 20]),
        dict(key='A-002-01', length_ft=40,
        boxes=[b['id'] for b in boxes if b['length_ft'] == 40])], quay_buffer=[])
    return Instance.model_validate(data), ['0010182', '0030182', '0050182',
                                          '0020184', '0050184', '0040186']


@pytest.mark.parametrize('both_targets', [False, True])
def test_spanning_upward_closure_and_duplicate_blocker_once(both_targets):
    from stowage.feasibility.tables import build_tables
    from stowage.model import Placement
    from stowage.moves import project_future_moves
    instance, slots = spanning_fixture(both_targets=both_targets)
    assignment = dict(zip(instance.load_list, slots))
    tables = build_tables(instance)
    tables.static[:] = False
    for bid, slot in assignment.items():
        tables.static[tables.box_index[bid], tables.slot_index[slot]] = True
    ship = instance.onboard.model_copy(update={'placements': [
        Placement(container_id=bid, slot=slot) for bid, slot in assignment.items()]})
    future = project_future_moves(instance, ship, from_call=instance.call_index)
    assert future[0].restowed_boxes == 2
    result = lower_bound(instance, tables=tables, budget_s=2)
    assert result.status == 'optimal'
    assert result.lower_bound == 4
    assert result.witness_restow == sum(f.restow_lifts for f in future)
    # The far spanning box does not touch the sole early target's left column.
    assert set(result.projected_required['1']) == {
        instance.load_list[k] for k in ([0, 1, 3, 5] if both_targets else [0, 3, 5])}


@pytest.mark.parametrize('freeze', [False, True])
def test_homogeneous_hold_cut_agrees_with_full_checker_geometry(freeze):
    """Checker's securing graph rejects mixed lengths under both rule modes."""
    from types import SimpleNamespace
    from stowage.checker.lashing import _load_graph
    from stowage.feasibility.tables import build_tables
    from stowage.model import Placement
    instance, slots = spanning_fixture()
    data = instance.model_dump()
    def hold_slot(slot):
        return slot[:-2] + f'{int(slot[-2:])-80:02d}'
    for stack in data['vessel']['stacks']:
        stack['id'] = stack['id'][:-1] + 'H'
        stack['on_deck'] = False
        stack['cell_ids'] = list(map(hold_slot, stack['cell_ids']))
    for cell in data['vessel']['cells']:
        cell['id'] = hold_slot(cell['id'])
        cell['stack_id'] = cell['stack_id'][:-1] + 'H'
    cover = data['vessel']['hatch_covers'][0]
    cover.update(deck_stack_ids=[], hold_stack_ids=[s['id'] for s in data['vessel']['stacks']])
    instance = Instance.model_validate(data)
    slots = list(map(hold_slot, slots))
    assignment = dict(zip(instance.load_list, slots))
    ship = instance.onboard.model_copy(update={'placements': [
        Placement(container_id=bid, slot=slot) for bid, slot in assignment.items()]})
    with rule_freeze_v1(enabled=freeze):
        with pytest.raises(ValueError, match='mixed 20/long hold stacks'):
            _load_graph(instance, ship, SimpleNamespace(at=lambda *args: (0., 0., 1.)))
        tables = build_tables(instance)
        tables.static[:] = False
        for bid, slot in assignment.items():
            tables.static[tables.box_index[bid], tables.slot_index[slot]] = True
        result = lower_bound(instance, tables=tables, budget_s=2)
        assert result.status == 'infeasible'


@pytest.mark.parametrize("supplied_tables", [False, True])
def test_inactive_dg_uses_optimistic_unary_without_mutating_native_tables(monkeypatch, supplied_tables):
    from . import hatch_restow
    from stowage.feasibility.tables import build_tables
    data = fixture().model_dump()
    reference = next(box.dg for box in build_instance().containers if box.dg is not None)
    data["containers"][0]["dg"] = reference.model_dump()
    instance = Instance.model_validate(data)
    with rule_freeze_v1(enabled=False):
        native = build_tables(instance)
        before = native.static.copy()
        monkeypatch.setattr(hatch_restow, "build_tables", lambda _: native)
        result = lower_bound(instance, budget_s=.01,
                             tables=native if supplied_tables else None)
    assert result.status != "counting_only"
    assert (native.static == before).all()
    assert "DG unary rows widened" in result.note
    assert result.unary_dg_widened is True
