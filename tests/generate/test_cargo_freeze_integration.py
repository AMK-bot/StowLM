"""Frozen booking policy: explicit demand losses and seeded off-state bytes."""
from copy import deepcopy
import hashlib
import json
from random import Random

import pytest

from stowage.generate.cargo import (
    CargoGenerationReport, UnsupportedCargoError, _params, _route, generate_load_list,
)
from stowage.rules.catalogue import rule_freeze_v1


def test_legacy_seeded_output_bytes_are_unchanged():
    samples = {}
    with rule_freeze_v1(enabled=False):
        for route in ('straits', 'vietnam_straits', 'south_china_vietnam'):
            for seed in (1, 71, 250526):
                audit = CargoGenerationReport()
                boxes = generate_load_list(route, 0, 400, seed=seed, report=audit)
                samples[f'{route}:{seed}'] = {
                    'boxes': [b.model_dump(mode='json') for b in boxes],
                    'report': audit.__dict__,
                    'rotation': _route(route, _params(None), Random(seed))[0].model_dump(mode='json'),
                }
    assert hashlib.sha256(json.dumps(samples, sort_keys=True).encode()).hexdigest() == (
        '057d8166d99691812758a64d30658ace74dc0df94fe5022e237eb59a0e9c032e'
    )


def dg_profile(*classes):
    p = deepcopy(_params(None))
    equipment = next(e for e in p['equipment'] if e['id'] == 'dry_20')
    p['equipment'] = [equipment]
    equipment['empty_share']['value'] = 0
    p['dg']['share_of_eligible_loaded_boxes']['value'] = 1
    p['dg']['classes'] = [c for c in p['dg']['classes'] if c['id'] in classes]
    for row in p['dg']['classes']:
        row['share']['value'] = 1
    return p


def test_refused_demand_redraw_is_counted_without_relabelling():
    audit = CargoGenerationReport()
    with rule_freeze_v1(enabled=True):
        boxes = generate_load_list('straits', 0, 30, seed=17,
                                   params=dg_profile('7', '3'), report=audit)
    assert len(boxes) == 30
    assert all(b.dg and b.dg.imdg_class == '3' and b.dg.un_number == '1263' for b in boxes)
    assert audit.realised['refused_by_class']['7'] > 0
    assert audit.realised['unsupported_demand'] == audit.realised['refused_by_class']['7']
    assert audit.realised['refused_by_class'].get('3', 0) == 0
    assert audit.generator_version == '8.0.0'
    assert all(b.dg.shipment_provenance.versions['cargo_generator'] == '8.0.0' for b in boxes)


def test_all_refused_demand_fails_explicitly_and_keeps_counts():
    audit = CargoGenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(UnsupportedCargoError, match='unsupported demand'):
        generate_load_list('straits', 0, 1, seed=17, params=dg_profile('7'), report=audit)
    assert audit.realised == {}
    assert audit.requested['unsupported_demand'] > 0
    assert audit.requested['refused_by_class'] == {'7': audit.requested['unsupported_demand']}
    assert audit.rejections


def test_switch_off_does_not_remove_refused_classes():
    audit = CargoGenerationReport()
    with rule_freeze_v1(enabled=False):
        boxes = generate_load_list('straits', 0, 3, seed=17, params=dg_profile('7'), report=audit)
    assert all(b.dg.imdg_class == '7' for b in boxes)
    assert 'refused_by_class' not in audit.realised


def test_activation_lookup_failure_resets_reused_audit_before_io(monkeypatch):
    from stowage.generate import cargo

    audit = CargoGenerationReport()
    generate_load_list('straits', 0, 1, seed=1, report=audit)

    def unavailable_catalogue():
        raise OSError('catalogue unavailable')

    monkeypatch.setattr(cargo, 'effective_cargo_generator_version', unavailable_catalogue)
    with pytest.raises(OSError, match='catalogue unavailable'):
        generate_load_list('straits', 0, 1, seed=2, report=audit)
    assert audit.seed == 2
    assert audit.realised == {}
    assert audit.rejections == ['catalogue unavailable']


@pytest.mark.parametrize('through,expected_refusal', [(False, True), (True, False)])
def test_generator_refuses_later_handling_but_allows_untouched_through_port(through, expected_refusal):
    from stowage.model.port import PortCall, Rotation

    profile = dg_profile('7')
    ports = ['SGSIN', 'THLCH', 'VNSGN']
    route = Rotation(service='straits', calls=[PortCall(index=i, locode=p) for i, p in enumerate(ports)])
    destination = 'VNSGN' if through else 'THLCH'
    pod = deepcopy(profile['rotations'][0]['pods'][0])
    pod['port'] = destination
    profile['rotations'][0]['pods'] = [pod]
    audit = CargoGenerationReport()
    with rule_freeze_v1(enabled=True):
        if expected_refusal:
            with pytest.raises(UnsupportedCargoError, match='unsupported demand'):
                generate_load_list(route, 0, 1, seed=17, params=profile, report=audit)
            assert all(row['refused_call_indexes'] == [1] for row in audit.requested['refused_bookings'])
            assert all(row['un_number'] == '2915' and row['pod'] == 'THLCH' and row['iso_type'] == '22G1'
                       for row in audit.requested['refused_bookings'])
        else:
            box, = generate_load_list(route, 0, 1, seed=17, params=profile, report=audit)
            assert box.dg.imdg_class == '7'
            assert box.pod == 'VNSGN'
            assert audit.realised['unsupported_demand'] == 0


def _refusal_sequence(monkeypatch, classes):
    from stowage.generate import cargo
    from stowage.model.cargo import DangerousGoods

    draws = iter(classes)
    observed = []

    def draw(*args, **kwargs):
        primary = next(draws)
        observed.append(primary)
        return DangerousGoods(un_number='2915' if primary == '7' else '1263',
                              imdg_class=primary, stowage_category='A',
                              packing_group=None if primary == '7' else 'II')

    monkeypatch.setattr(cargo, '_dg', draw)
    return observed


def test_refusal_guard_fails_after_exactly_256_consecutive_bookings(monkeypatch):
    observed = _refusal_sequence(monkeypatch, ['7'] * 256 + ['3'])
    audit = CargoGenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(UnsupportedCargoError, match='unsupported demand'):
        generate_load_list('straits', 0, 1, seed=17, params=dg_profile('7', '3'), report=audit)
    assert observed == ['7'] * 256
    assert audit.realised == {}
    assert audit.requested['unsupported_demand'] == 256
    assert audit.requested['refused_by_class'] == {'7': 256}
    assert len(audit.requested['refused_bookings']) == 256
    assert all(row['un_number'] == '2915' and row['primary_class'] == '7'
               and row['pol'] == 'THLCH' and row['iso_type'] == '22G1'
               and row['refused_call_indexes'] == [0]
               for row in audit.requested['refused_bookings'])


def test_refusal_guard_resets_after_success_and_reused_audit_clears_counts(monkeypatch):
    sequence = ['7'] * 255 + ['3'] + ['7'] * 255 + ['3']
    observed = _refusal_sequence(monkeypatch, sequence)
    audit = CargoGenerationReport()
    with rule_freeze_v1(enabled=True):
        boxes = generate_load_list('straits', 0, 2, seed=17, params=dg_profile('7', '3'), report=audit)
        assert observed == sequence
        assert [box.dg.imdg_class for box in boxes] == ['3', '3']
        assert audit.requested['unsupported_demand'] == 510
        assert audit.requested['refused_by_class'] == {'7': 510}
        assert len(audit.requested['refused_bookings']) == 510
        assert audit.realised['unsupported_demand'] == 510
        assert audit.realised['refused_by_class'] == {'7': 510}
        assert generate_load_list('straits', 0, 0, seed=18, report=audit) == []
    assert audit.seed == 18
    assert audit.requested['unsupported_demand'] == 0
    assert audit.requested['refused_by_class'] == {}
    assert audit.requested['refused_bookings'] == []
    assert audit.realised['unsupported_demand'] == 0
    assert audit.realised['refused_by_class'] == {}
