"""C5/A3 later-call destination screening, independently of current handling."""

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.model import Placement, ShipState
from stowage.model.port import PortCall, PortRuleRef
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1
from tests.checker.test_rules_fixtures import build_instance


def case():
    instance = build_instance()
    box = instance.containers[0]
    # Mutate synthetic facts to isolate this port screen from the IMDG checker,
    # whose independent declaration validation may also reject this shipment.
    box.iso_type, box.length_ft = '22G1', 20
    box.dg = next(b.dg for b in instance.containers if b.dg is not None).model_copy(deep=True)
    box.dg.imdg_class = '7'
    box.pod = 'THLCH'
    instance.port = load_port('singapore_like')
    instance.rotation.calls = [
        PortCall(index=0, locode=instance.port.locode, archetype=instance.port.archetype),
        PortCall(index=1, locode='THLCH', archetype='laem_chabang_like'),
    ]
    ship = ShipState(vessel_id=instance.vessel.id,
                     placements=[Placement(container_id=box.id, slot='0010182')])
    return instance, box, ship


def check(instance, box, ship, mode):
    if mode == 'full':
        return check_static_rules(instance, ship, call_index=0).violations
    return placement_violations(instance, ship.model_copy(update={'placements': []}),
                                box, '0010182', call_index=0)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_destination_refusal_is_hard_and_staged(enabled, mode):
    instance, box, ship = case()
    with rule_freeze_v1(enabled=enabled):
        out = [v for v in check(instance, box, ship, mode) if v.rule_id == 'port.dg.refused_class']
    assert bool(out) is enabled
    if enabled:
        assert all(v.severity == 'hard' and v.call_index == 0 for v in out)
        assert out[0].containers == [box.id]


@pytest.mark.parametrize('enabled', [False, True])
def test_current_port_and_unhandled_transit_refusal_not_duplicated(enabled):
    instance, box, ship = case()
    box.pod = 'SGSIN'
    instance.rotation.calls.append(PortCall(index=2, locode='SGSIN', archetype='singapore_like'))
    instance.port.port_rules.append(PortRuleRef(rule_id='port.dg.refused_class', params={'classes': ['7']}))
    with rule_freeze_v1(enabled=enabled):
        assert not any(v.rule_id == 'port.dg.refused_class' for v in check(instance, box, ship, 'full'))


@pytest.mark.parametrize('enabled', [False, True])
def test_primary_class_only_no_subsidiary_or_compound_expansion(enabled):
    instance, box, ship = case()
    additional = box.dg.model_copy(deep=True)
    box.dg.imdg_class = '3'
    box.dg.subsidiary_risks = ['7']
    box.dg.additional = [additional]
    with rule_freeze_v1(enabled=enabled):
        assert not any(v.rule_id == 'port.dg.refused_class' for v in check(instance, box, ship, 'full'))


@pytest.mark.parametrize('enabled', [False, True])
def test_unknown_destination_profile_fails_closed_only_when_active(enabled):
    instance, box, ship = case()
    box.pod = 'ZZUNK'
    instance.rotation.calls[1] = PortCall(index=1, locode='ZZUNK')
    with rule_freeze_v1(enabled=enabled):
        out = check(instance, box, ship, 'full')
    assert any(v.rule_id == 'checker.missing_vessel_data' and 'destination port rules' in v.message for v in out) is enabled


def test_repeated_locode_uses_first_destination_and_explicit_profile():
    instance, box, ship = case()
    instance.rotation.calls[1].archetype = 'singapore_like'  # Deliberate inconsistent metadata.
    instance.rotation.calls.append(PortCall(index=2, locode='THLCH', archetype='laem_chabang_like'))
    with rule_freeze_v1():
        out = check(instance, box, ship, 'full')
    assert any(v.rule_id == 'checker.missing_vessel_data' and 'destination port rules' in v.message for v in out)


def test_inherited_destination_refusal_remains_hard():
    instance, box, ship = case()
    instance.stage, instance.onboard = 'B', ship.model_copy(deep=True)
    with rule_freeze_v1():
        out = [v for v in check(instance, box, ship, 'full') if v.rule_id == 'port.dg.refused_class']
    assert len(out) == 1 and out[0].severity == 'hard' and out[0].inherited


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('primary', ['7', '3', None])
def test_public_later_refusal_predicate_matches_static_and_incremental(enabled, primary):
    from stowage.checker import rules

    instance, box, ship = case()
    if primary is None:
        box.dg = None
    else:
        box.dg.imdg_class = primary
    with rule_freeze_v1(enabled=enabled):
        calls = rules.refused_later_handling_calls(
            instance.rotation, 0, box.pod, primary, current_port=instance.port)
        for mode in ('full', 'incremental'):
            out = check(instance, box, ship, mode)
            assert bool(calls) == any(v.rule_id == 'port.dg.refused_class' for v in out)
    assert calls == ([1] if enabled and primary == '7' else [])


def test_public_later_refusal_is_authoritative_for_both_checker_apis(monkeypatch):
    from stowage.checker import rules

    instance, box, ship = case()
    used = []

    def refusal(rotation, call_index, pod, primary_class, *, current_port=None):
        used.append((call_index, pod, primary_class))
        return [1]

    monkeypatch.setattr(rules, 'refused_later_handling_calls', refusal)
    with rule_freeze_v1():
        for mode in ('full', 'incremental'):
            assert any(v.rule_id == 'port.dg.refused_class' for v in check(instance, box, ship, mode))
    assert used == [(0, box.pod, '7'), (0, box.pod, '7')]
