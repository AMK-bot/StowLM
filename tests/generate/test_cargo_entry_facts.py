"""D59 entry-scoped declarations are authored, then bound to actual instances."""
from copy import deepcopy

import pytest

from stowage.generate import cargo
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1


def single_class_profile(class_id):
    p = deepcopy(load_param_file('cargo', 'feeder_export'))
    e = next(e for e in p['equipment'] if e['id'] == 'dry_20')
    p['equipment'] = [e]
    e['empty_share']['value'] = 0
    p['dg']['share_of_eligible_loaded_boxes']['value'] = 1
    p['dg']['classes'] = [c for c in p['dg']['classes'] if c['id'] == class_id]
    p['special_stowage']['share_of_loaded_boxes']['value'] = 0
    return p


@pytest.mark.parametrize('class_id,code', [('4.1','h1'),('4.3','h1'),('9','sw19')])
@pytest.mark.parametrize('enabled', [False,True])
def test_generated_entry_facts_and_class_flow_audit(enabled,class_id,code):
    report = cargo.CargoGenerationReport()
    with rule_freeze_v1(enabled=enabled):
        boxes = cargo.generate_load_list('straits',0,3,seed=731,
                                         params=single_class_profile(class_id),report=report)
    assert len(boxes) == 3
    if not enabled:
        assert 'shipment_scenarios' not in report.requested
        assert 'shipment_scenarios' not in report.realised
        return
    declared = report.realised['shipment_scenarios']
    assert set(declared) == {box.id for box in boxes}
    assert report.requested['shipment_scenarios'] == declared
    assert report.requested['shipment_scenarios'] is not declared
    for box in boxes:
        facts = declared[box.id]['primary']
        assert facts['synthetic'] is True
        assert facts['scope'] == 'shipment_entry'
        assert code in facts
        assert {'document','clause','scenario_id'} <= facts[code]['provenance'].keys()
        assert box.dg.dry is None
        assert box.dg.protected_from_heat is None and box.dg.clear_living_quarters is None
        assert box.dg.short_international_voyage is None
        if code == 'h1':
            assert facts['h1']['ctu_weathertight'] is True
            assert facts['h1']['packing_condition'] == 'dry'
            assert 'dry' not in report.shipment_requirements.get(box.id,[])
        else:
            assert facts['sw19']['battery_condition'] == 'intact'
            assert facts['sw19']['transport_purpose'] == 'normal'
            assert facts['sw19']['special_provision_applicability'] == {'376':False,'377':False}
            assert 'voyage' not in facts['sw19']
            assert box.dg.special_provisions is None
            assert 'short_international_voyage' not in report.shipment_requirements.get(box.id,[])
    flow = report.realised['booking_audit']
    assert all(flow[stage]['dg_by_class'] == {class_id:3} for stage in ('requested','drawn','emitted'))


@pytest.mark.parametrize('label,accepted', [('1.4S',True),('1.4G',False)])
def test_generator_uses_class_one_division_accessor_without_losing_group(label,accepted):
    p = single_class_profile('1.4')
    p['dg'].pop('pending_rule_freeze_v1',None)
    cls = p['dg']['classes'][0]
    cls['id'] = label
    cls['un_numbers'][0]['un_number'] = '0012'
    with rule_freeze_v1(enabled=True):
        if not accepted:
            with pytest.raises(cargo.UnsupportedCargoError,match='DGL has no supported entry'):
                cargo.generate_load_list('straits',0,1,seed=731,params=p)
        else:
            box, = cargo.generate_load_list('straits',0,1,seed=731,params=p)
            assert box.dg.imdg_class == '1.4S'
            assert box.direct_delivery


@pytest.mark.parametrize('class_id', ['4.1','4.3','9'])
def test_generated_manifest_binds_and_passes_isolated_imdg_rules(class_id):
    from random import Random
    from stowage.checker.rules_imdg import imdg_violations
    from stowage.model.instance import Instance
    from stowage.model.ship import Placement
    from stowage.model.yard import YardState
    from tests.checker.test_rules_imdg_assessments import _shipment

    report = cargo.CargoGenerationReport()
    p = single_class_profile(class_id)
    with rule_freeze_v1(enabled=True):
        box, = cargo.generate_load_list('straits',0,1,seed=731,params=p,report=report)
        instance, ship, _, _ = _shipment(int(box.dg.un_number),box.dg.packing_group)
        # A small declared-design fixture isolates IMDG; no full-physics or
        # whole-plan acceptance is asserted by this test.
        instance.containers = [box]
        instance.rotation, _ = cargo._route('straits',cargo._params(p),Random(731))
        instance.voyage = None
        instance.stage = 'B'
        instance.load_list = []
        instance.discharge_list = []
        instance.yard = YardState(yard_id=instance.port.yard.id)
        ship.placements = [Placement(slot='0010182',container_id=box.id)]
        ship.tank_masses_kg = {}
        instance.onboard = ship
        instance = Instance.model_validate(instance.model_dump(mode='python'))
        bound = cargo.bind_shipment_scenarios(instance,report)
        assert imdg_violations(bound,ship,call_index=0) == []


def test_off_preserves_legacy_rejection_of_qualified_custom_class_labels():
    p = single_class_profile('1.4')
    p['dg'].pop('pending_rule_freeze_v1',None)
    p['dg']['classes'][0]['id'] = '1.4S'
    p['dg']['classes'][0]['un_numbers'][0]['un_number'] = '0012'
    with rule_freeze_v1(enabled=False), pytest.raises(cargo.UnsupportedCargoError,match='DGL has no supported entry'):
        cargo.generate_load_list('straits',0,1,seed=731,params=p)
