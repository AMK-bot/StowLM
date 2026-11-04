"""Plain SG class requirements must not demand unrelated shipment verification."""
from types import SimpleNamespace

import pytest

from stowage.checker import rules_imdg
from stowage.model.cargo import DangerousGoods
from stowage.rules.catalogue import rule_freeze_v1
from tests.checker.test_rules_imdg import _case, _dg


def _pair(instance, ship, a, b):
    return rules_imdg._pair(instance, a, ship.placements[0].slot, b,
        ship.placements[1].slot, 0, {c.id: c for c in instance.vessel.cells})


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('code,target', [('SG17', '5.1'), ('SG25', '2.1'), ('SG25', '3')])
@pytest.mark.parametrize('far', [False, True])
def test_plain_sg_class_requires_level_two_only_for_matching_class(monkeypatch, enabled, code, target, far):
    instance, ship, a, b = _case(2, far=far)
    # Synthetic isolated entries: no sampled DGL code or general-table level can
    # conceal whether this particular code dispatches to the ship-table check.
    a.dg = DangerousGoods(un_number='9998', imdg_class='4.1',
                           stowage_category='A', segregation_codes=[code])
    b.dg = DangerousGoods(un_number='9999', imdg_class=target, stowage_category='A')
    monkeypatch.setattr(rules_imdg, 'general_segregation', lambda *_: SimpleNamespace(value='0'))
    with rule_freeze_v1(enabled=enabled):
        out = _pair(instance, ship, a, b)
    if far:
        assert out == []
    else:
        assert len(out) == 1
        assert out[0].rule_id == rules_imdg.SHIP
        assert 'level 2' in out[0].message


@pytest.mark.parametrize('code', ['SG17', 'SG25'])
@pytest.mark.parametrize('target', ['2.2', '4.1', '8', '9'])
def test_plain_sg_does_not_restrict_unrelated_class(monkeypatch, code, target):
    instance, ship, a, b = _case(2)
    a.dg = DangerousGoods(un_number='9998', imdg_class='4.1',
                           stowage_category='A', segregation_codes=[code])
    b.dg = DangerousGoods(un_number='9999', imdg_class=target, stowage_category='A')
    monkeypatch.setattr(rules_imdg, 'general_segregation', lambda *_: SimpleNamespace(value='0'))
    assert _pair(instance, ship, a, b) == []


@pytest.mark.parametrize('code,target', [('SG17', '5.1'), ('SG25', '2.1'), ('SG25', '3')])
def test_plain_sg_applies_to_subsidiary_class_and_either_pair_order(monkeypatch, code, target):
    instance, ship, a, b = _case(2)
    a.dg = DangerousGoods(un_number='9998', imdg_class='4.1',
                           stowage_category='A', segregation_codes=[code])
    b.dg = DangerousGoods(un_number='9999', imdg_class='9', subsidiary_risks=[target],
                           stowage_category='A')
    monkeypatch.setattr(rules_imdg, 'general_segregation', lambda *_: SimpleNamespace(value='0'))
    for left, right, left_slot, right_slot in ((a, b, ship.placements[0].slot, ship.placements[1].slot),
            (b, a, ship.placements[1].slot, ship.placements[0].slot)):
        out = rules_imdg._pair(instance, left, left_slot, right, right_slot, 0,
                              {c.id: c for c in instance.vessel.cells})
        assert len(out) == 1
        assert out[0].rule_id == rules_imdg.SHIP
        assert 'level 2' in out[0].message


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('far', [False, True])
def test_un1333_with_paint_obeys_sg25_and_additional_sg26(enabled, far):
    instance, ship, a, b = _case(2, far=far)
    a.dg, b.dg = _dg(1333, 'II'), _dg(1263, 'II')
    if far:
        # SG26 independently demands two spaces athwartships, even when the
        # fore/aft level-two distance is already satisfied.
        for cell in instance.vessel.cells:
            if cell.id.startswith('003'):
                cell.tcg_m += 100
    with rule_freeze_v1(enabled=enabled):
        out = _pair(instance, ship, a, b)
    if far:
        assert out == []
    else:
        assert {v.rule_id for v in out} == {rules_imdg.SHIP}
        assert any('level 2' in v.message for v in out)
        assert any('SG26' in v.message for v in out)


@pytest.mark.parametrize('far', [False, True])
def test_un1333_with_acid_has_no_spurious_conditional_violation(far):
    instance, ship, a, b = _case(2, far=far)
    a.dg, b.dg = _dg(1333, 'II'), _dg(1789, 'II')
    assert _pair(instance, ship, a, b) == []


@pytest.mark.parametrize('code,target', [('SG17', '5.1'), ('SG25', '3')])
def test_plain_sg_unknown_geometry_stays_hard_missing(monkeypatch, code, target):
    instance, ship, a, b = _case(2, far=True)
    a.dg = DangerousGoods(un_number='9998', imdg_class='4.1',
                           stowage_category='A', segregation_codes=[code])
    b.dg = DangerousGoods(un_number='9999', imdg_class=target, stowage_category='A')
    monkeypatch.setattr(rules_imdg, 'general_segregation', lambda *_: SimpleNamespace(value='0'))
    del instance.rule_overrides[rules_imdg.SHIP]['ship_type']
    out = _pair(instance, ship, a, b)
    assert len(out) == 1
    assert out[0].rule_id == rules_imdg.MISSING
    assert out[0].severity == 'hard'


@pytest.mark.parametrize('code', ['SG17', 'SG25'])
def test_plain_sg_does_not_relax_a_stronger_general_requirement(monkeypatch, code):
    instance, ship, a, b = _case(2)
    a.dg = DangerousGoods(un_number='9998', imdg_class='4.1',
                           stowage_category='A', segregation_codes=[code])
    b.dg = DangerousGoods(un_number='9999', imdg_class='3', stowage_category='A')
    monkeypatch.setattr(rules_imdg, 'general_segregation', lambda *_: SimpleNamespace(value='4'))
    out = _pair(instance, ship, a, b)
    assert len(out) == 1
    assert out[0].rule_id == rules_imdg.SHIP
    assert 'level 4' in out[0].message


def test_unknown_group_marker_cannot_clear_even_with_a_verification_flag():
    instance, ship, a, b = _case(2, far=True)
    a.dg = DangerousGoods(un_number='9998', imdg_class='2.2',
                           stowage_category='A', segregation_codes=['SGG999'])
    b.dg = DangerousGoods(un_number='9999', imdg_class='2.2', stowage_category='A')
    instance.rule_overrides[rules_imdg.STOWAGE]['containers'][a.id]['verified_segregation_codes'] = ['SGG999']
    out = _pair(instance, ship, a, b)
    assert len(out) == 1
    assert out[0].rule_id == rules_imdg.GENERAL
    assert 'unclassified segregation-group marker' in out[0].message
