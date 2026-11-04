"""Arrival bounds versus the unchanged exhaustive physical move engine."""
import importlib.util
from pathlib import Path
import sys

import pytest

from stowage.bounds.bruteforce import brute_force_optimum
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1

spec = importlib.util.spec_from_file_location('layover_bound_tested', Path(__file__).with_name('bound.py'))
bound = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bound
spec.loader.exec_module(bound)


def fixture(*, kind='retained', restow=1, future=1, initially_off=False):
    """Small geometry adapted from c2/c2-hatch/bound_tests.py:fixture."""
    data = build_instance(layover=True).model_dump()
    boxes = data['containers'][:3]
    for box in boxes:
        box.update(length_ft=20, iso_type='22G1', height_m=2.591,
                   tare_kg=2200, gross_mass_kg=12000, max_gross_mass_kg=30000,
                   reefer=False, reefer_setpoint_c=None, dg=None, oog=None,
                   special_stowage=[], direct_delivery=False, pod='THBKK')
    ids = [box['id'] for box in boxes]
    stacks = [s for s in data['vessel']['stacks'] if s['id'] in {'00101D', '00301D', '00101H'}]
    for stack in stacks:
        stack['hatch_cover_id'] = 'HC-02-1'
        stack['cell_ids'] = stack['cell_ids'][:1 if stack['on_deck'] else 2]
    cells = {cell for stack in stacks for cell in stack['cell_ids']}
    cover = data['vessel']['hatch_covers'][0]
    cover.update(bays=[1, 3], hold_stack_ids=['00101H'], deck_stack_ids=['00101D', '00301D'])
    data['vessel'].update(stacks=stacks, hatch_covers=[cover], fixed_loads=[])
    data['vessel']['cells'] = [c for c in data['vessel']['cells'] if c['id'] in cells]
    block = data['port']['yard']['blocks'][0]
    block.update(n_bays=2, n_rows=1, max_tiers=4, allowed_lengths_ft=[20])
    data['port']['yard']['blocks'] = [block]
    data.update(containers=boxes, load_list=[], discharge_list=[], stage='B')
    data['yard'].update(stacks=[], quay_buffer=[])
    placements = [(ids[0], '0010102')]
    if kind in {'upper', 'blocked_cover'}:
        boxes[0]['pod'] = data['port']['locode']
        data['discharge_list'] = [ids[0]]
        placements += [(ids[1], '0010104' if kind == 'upper' else '0010182')]
    elif kind == 'tradeoff':
        boxes[0]['pod'] = 'THSGZ'
        boxes[1]['pod'] = 'THBKK'
        placements = [(ids[0], '0010182'), (ids[1], '0010102')]
    elif kind == 'load_hold':
        # Deck arrival blocks the only cover, while two new loads exceed the
        # one other deck cell: opening and moving the arrival box is necessary.
        placements = [(ids[0], '0010182')]
        data['load_list'] = ids[1:]
        data['yard']['stacks'] = [dict(key='A-001-01', length_ft=20, boxes=ids[1:])]
    data['onboard'].update(placements=[dict(container_id=bid, slot=slot) for bid, slot in placements],
                           covers_off={'HC-02-1': 'quay'} if initially_off else {})
    data['objective'].update(restow=restow, future_restow=future)
    return Instance.model_validate(data)


@pytest.mark.parametrize('freeze', [False, True])
@pytest.mark.parametrize('kind', ['retained', 'upper', 'blocked_cover', 'load_hold', 'tradeoff'])
def test_arrival_bound_below_exhaustive_moves(freeze, kind):
    with rule_freeze_v1(enabled=freeze):
        instance = fixture(kind=kind)
        result = bound.lower_bound(instance, budget_s=2)
        oracle = brute_force_optimum(instance, max_nodes=10000, time_limit_s=5,
                                     check_static_rules=True)
    assert result.status == 'optimal'
    assert oracle.status == 'optimal'
    assert result.lower_bound <= oracle.objective + 1e-9
    assert result.lower_bound >= result.counting_lower_bound


def test_retained_hold_needs_no_current_opening():
    result = bound.lower_bound(fixture(), budget_s=2)
    assert result.lower_bound == 2
    assert result.witness_hatch == 2
    assert result.witness_current_restow == 0
    assert result.moved_arrival == []


@pytest.mark.parametrize('kind', ['upper', 'blocked_cover'])
def test_discharge_clearance_prices_box_once(kind):
    result = bound.lower_bound(fixture(kind=kind), budget_s=2)
    assert result.witness_current_restow == 1
    assert len(result.moved_arrival) == 1
    assert result.lower_bound == 3


@pytest.mark.parametrize('restow,future', [(0, 1), (.125, 1), (8, .25)])
def test_arrival_future_tradeoff_never_double_counts(restow, future):
    instance = fixture(kind='tradeoff', restow=restow, future=future)
    result = bound.lower_bound(instance, budget_s=2)
    oracle = brute_force_optimum(instance, max_nodes=10000, time_limit_s=5,
                                 check_static_rules=True)
    assert oracle.status == 'optimal'
    assert result.lower_bound <= oracle.objective + 1e-9
    # Doing nothing is legal in the physical model and costs one future cover
    # pair plus one blocker off/on. The bound must allow this alternative.
    assert result.lower_bound <= 2 + 2 * future


def test_empty_departure_initially_off_cover_closes_once():
    data = fixture(initially_off=True).model_dump()
    data['onboard']['placements'] = []
    result = bound.lower_bound(Instance.model_validate(data), budget_s=2)
    assert result.lower_bound == 1


def test_inherited_soft_weight_inversion_is_not_a_mandatory_repair():
    data = fixture(kind='upper', restow=20).model_dump()
    data['discharge_list'] = []
    for index, box in enumerate(data['containers']):
        box['pod'] = 'THBKK'
        box['gross_mass_kg'] = 8000 if index == 0 else 22000
    with rule_freeze_v1(enabled=False):
        from stowage.checker.rules import check_static_rules
        instance = Instance.model_validate(data)
        violations = check_static_rules(instance, instance.onboard,
                                        call_index=instance.call_index).violations
        assert any(v.rule_id == 'expert.e1.heavy_bottom' and v.severity == 'tolerated'
                   for v in violations)
        result = bound.lower_bound(instance, budget_s=2)
    assert result.lower_bound == 2
    assert result.witness_current_restow == 0


def test_dg_state_dependent_unary_does_not_restrict_certificate():
    from stowage.feasibility.tables import build_tables
    data = fixture().model_dump()
    # Use fixture DG metadata for the otherwise ordinary retained box.
    dg = next(box.dg for box in build_instance(layover=True).containers if box.dg)
    data['containers'][0]['dg'] = dg.model_dump()
    instance = Instance.model_validate(data)
    tables = build_tables(instance)
    tables.static[:] = False
    result = bound.lower_bound(instance, tables=tables, budget_s=2)
    assert result.status == 'optimal'
    assert result.lower_bound <= 2
