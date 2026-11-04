"""Synthetic fixture-builder cases for IMDG 42-24 rules and ship-table levels."""
import pytest

from stowage.checker.rules_imdg import (
    GENERAL, MISSING, SHIP, STOWAGE, imdg_placement_violations, imdg_violations,
)
from stowage.model.cargo import DangerousGoods
from tests.checker.test_rules_fixtures import build_instance
from stowage.model.ship import Placement, ShipState
from stowage.rules.imdg.dgl import lookup_dgl


def _dg(un, pg=None):
    e = lookup_dgl(un, pg)
    return DangerousGoods(un_number=f'{un:04d}', imdg_class=e.hazard_class,
                          subsidiary_risks=list(e.subsidiary_risks), packing_group=pg,
                          stowage_category=e.stowage_category, stowage_codes=list(e.stowage_codes),
                          segregation_codes=list(e.segregation_codes))


def _case(level=2, on=True, far=False):
    instance = build_instance(with_dg_doc=True)
    instance.schema_version = '1.8'  # This fixture exercises saved legacy assessment flags.
    a, b = instance.containers[1:3]
    un_b, pg_b = {1: (1325, 'II'), 2: (1202, 'III'), 3: (2814, None), 4: (2814, None)}[level]
    a.dg = _dg(1202, 'III') if level == 3 else _dg(1057)
    b.dg = _dg(un_b, pg_b)
    tier = '82' if on else '02'
    sa, sb = f'00101{tier}', f'00301{tier}'
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = ['2.1', '3', '4.1', '6.2', '8', '9']
    for area in instance.vessel.dg_doc_areas:
        area.classes_allowed = ['2.1', '3', '4.1', '6.1', '6.2', '8', '9']
    if far:
        for cell in instance.vessel.cells:
            if cell.id.startswith('003'):
                cell.lcg_m += 100
    pair = '|'.join(sorted((sa, sb)))
    instance.rule_overrides[SHIP] = {
        'ship_type': 'closed_holds',
        'slots': {sa: {'closed': level != 1, 'hold_id': 'aft'},
                  sb: {'closed': level != 1, 'hold_id': 'fore' if far else 'aft'}},
        'pairs': {pair: {'bulkheads': 1 if far else 0,
                          'barriers_resistant_to_fire_and_liquids': True,
                          'distance_from_intervening_bulkhead_m': 6 if far else 0}},
    }
    facts = dict(packing_form='packaged_goods_in_closed_freight_containers', clear_living_quarters=True, dry=True, handling_compliant=True,
                 verified_segregation_codes=['SG50', 'SG72'])
    if level == 1:
        facts['packing_form'] = 'open_freight_container'
        for area in instance.vessel.dg_doc_areas:
            area.packing_forms_allowed.append('open_freight_container')
    instance.rule_overrides[STOWAGE] = {'containers': {a.id: dict(facts), b.id: dict(facts)}}
    ship = ShipState(vessel_id=instance.vessel.id,
                     placements=[Placement(slot=sa, container_id=a.id), Placement(slot=sb, container_id=b.id)])
    return instance, ship, a, b


@pytest.mark.parametrize('level', [1, 2, 3, 4])
@pytest.mark.parametrize('on', [True, False], ids=['deck', 'hold'])
def test_ship_segregation_level_pass(level, on):
    instance, ship, _, _ = _case(level, on, far=True)
    assert imdg_violations(instance, ship, call_index=0) == []


@pytest.mark.parametrize('level', [1, 2, 3, 4])
@pytest.mark.parametrize('on', [True, False], ids=['deck', 'hold'])
def test_ship_segregation_level_fail(level, on):
    instance, ship, _, _ = _case(level, on)
    out = imdg_violations(instance, ship, call_index=0)
    assert {v.rule_id for v in out} == {SHIP}
    assert any(f'level {level}' in v.message for v in out)


def test_general_segregation_pass():
    instance, ship, _, _ = _case(2, far=True)
    assert not any(v.rule_id == GENERAL for v in imdg_violations(instance, ship, call_index=0))


def test_general_segregation_unknown_class_fail():
    instance, ship, a, _ = _case(2, far=True)
    a.dg.imdg_class = 'unresolved'
    assert GENERAL in {v.rule_id for v in imdg_violations(instance, ship, call_index=0)}


def test_stowage_category_pass():
    instance, ship, a, _ = _case(2, on=True, far=True)
    a.dg = _dg(1789, 'II')  # C: cargo ship on deck only; SG36/49 have no target here.
    assert imdg_violations(instance, ship, call_index=0) == []


def test_stowage_category_fail():
    instance, ship, a, _ = _case(2, on=False, far=True)
    a.dg = _dg(1789, 'II')
    assert any(v.rule_id == STOWAGE and 'on-deck' in v.message
               for v in imdg_violations(instance, ship, call_index=0))


def test_doc_class_fail():
    instance, ship, _, _ = _case(2, far=True)
    instance.vessel.stacks[0].dg_classes_allowed = []
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = []
    assert any(v.rule_id == STOWAGE and 'Compliance' in v.message
               for v in imdg_violations(instance, ship, call_index=0))


def test_dgl_provision_pass_and_fail():
    instance, ship, a, _ = _case(2, far=True)
    assert imdg_violations(instance, ship, call_index=0) == []
    instance.rule_overrides[STOWAGE]['containers'][a.id]['clear_living_quarters'] = False
    assert any(v.rule_id == STOWAGE and 'SW2' in v.message
               for v in imdg_violations(instance, ship, call_index=0))


def test_dgl_declared_category_cannot_relax_source():
    instance, ship, a, _ = _case(2, on=False, far=True)
    a.dg = _dg(1789, 'II')
    a.dg.stowage_category = 'A'
    out = imdg_violations(instance, ship, call_index=0)
    assert any('disagree' in v.message for v in out)
    assert any('requires on-deck' in v.message for v in out)


def test_missing_geometry_is_hard():
    instance, ship, _, _ = _case(2)
    del instance.rule_overrides[SHIP]['ship_type']
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and v.severity == 'hard' for v in out)


def test_missing_provision_is_hard():
    instance, ship, a, _ = _case(2, far=True)
    del instance.rule_overrides[STOWAGE]['containers'][a.id]['clear_living_quarters']
    assert any(v.rule_id == MISSING and 'SW2' in v.message
               for v in imdg_violations(instance, ship, call_index=0))


def test_vertical_deck_barrier_alternative():
    instance, ship, a, b = _case(2)
    ship.placements[1].slot = '0010102'
    b.dg.ctu_closed = True
    key = '0010102|0010182'
    instance.rule_overrides[SHIP]['pairs'][key] = {
        'segregated_by_deck': True, 'barriers_resistant_to_fire_and_liquids': True,
    }
    assert imdg_violations(instance, ship, call_index=0) == []
    instance.rule_overrides[SHIP]['pairs'][key]['barriers_resistant_to_fire_and_liquids'] = False
    assert SHIP in {v.rule_id for v in imdg_violations(instance, ship, call_index=0)}


def test_sg35_overrides_general_x_cell():
    instance, ship, a, b = _case(2)
    a.dg, b.dg = _dg(1689, 'I'), _dg(1789, 'II')
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = ['6.1', '8']
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == SHIP and 'level 2' in v.message for v in out)


def test_incremental_matches_full_and_does_not_mutate():
    instance, ship, a, b = _case(2)
    candidate_slot = ship.placements[1].slot
    partial = ship.model_copy(update={'placements': ship.placements[:1]})
    before = partial.model_dump_json()
    assert imdg_placement_violations(instance, partial, b, candidate_slot) == imdg_violations(instance, ship, call_index=0)
    assert partial.model_dump_json() == before


def test_conditional_sg_not_silently_ignored_for_lone_dg_box():
    instance, ship, a, b = _case(3, far=True)
    ship.placements = ship.placements[1:]
    instance.rule_overrides[STOWAGE]['containers'][b.id]['verified_segregation_codes'] = []
    assert any(v.rule_id == GENERAL and 'SG50' in v.message
               for v in imdg_violations(instance, ship, call_index=0))
