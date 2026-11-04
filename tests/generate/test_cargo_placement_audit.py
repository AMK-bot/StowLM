"""SW1/SW2 await actual geometry; cargo never declares their conclusions."""
from copy import deepcopy

import pytest

from stowage.generate import cargo
from stowage.generate.cargo_shipments import shipment_requirements
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.dgl import entries_for_un


CASES = [('2.1', 'SW2'), ('2.3', 'SW2'), ('6.1', 'SW2'),
         ('4.2', 'SW1'), ('5.1', 'SW1'), ('5.2', 'SW1')]


def _profile(class_id):
    profile = deepcopy(load_param_file('cargo', 'feeder_export'))
    equipment = next(row for row in profile['equipment'] if row['id'] == 'dry_20')
    equipment['share']['value'], equipment['empty_share']['value'] = 1, 0
    profile['equipment'] = [equipment]
    profile['dg']['classes'] = [row for row in profile['dg']['classes'] if row['id'] == class_id]
    profile['dg']['share_of_eligible_loaded_boxes']['value'] = 1
    profile['special_stowage']['share_of_loaded_boxes']['value'] = 0
    return profile


@pytest.mark.parametrize('class_id,code', CASES)
@pytest.mark.parametrize('enabled', [False, True])
def test_all_placement_classes_remain_raw_demand_without_clearance_or_redraw(class_id, code, enabled):
    report = cargo.CargoGenerationReport()
    with rule_freeze_v1(enabled=enabled):
        boxes = cargo.generate_load_list('straits', 0, 8, seed=731, params=_profile(class_id), report=report)
    assert len(boxes) == 8
    assert {box.dg.imdg_class for box in boxes} == {class_id}
    for box in boxes:
        dg = box.dg
        assert dg.protected_from_heat is None and dg.clear_living_quarters is None
        assert dg.shaded is None and dg.air_circulation is None
        assert dg.handling_compliant is None and dg.verified_provisions is None
        assert dg.verified_segregation_codes is None
        entry = next(row for row in entries_for_un(int(dg.un_number)) if row.packing_group == dg.packing_group)
        baseline = set(shipment_requirements(entry))
        actual = set(report.shipment_requirements.get(box.id, []))
        if enabled:
            assert actual == baseline - {'protected_from_heat', 'clear_living_quarters'}
            assert report.requested['placement_requirements'][box.id] == [code]
            assert report.realised['placement_requirements'][box.id] == [code]
        else:
            assert actual == baseline
    if enabled:
        flow = report.realised['booking_audit']
        assert flow['requested']['dg_by_class'] == flow['drawn']['dg_by_class'] == flow['emitted']['dg_by_class'] == {class_id: 8}
        assert flow['redrawn']['n_boxes'] == flow['refused']['n_boxes'] == 0
        assert report.requested['placement_requirements'] is not report.realised['placement_requirements']
        # No geometry or entry declarations are manufactured for these classes.
        assert report.realised['shipment_scenarios'] == {}
    else:
        assert 'placement_requirements' not in report.requested
        assert 'placement_requirements' not in report.realised


def test_placement_audit_is_reset_between_reused_reports():
    report = cargo.CargoGenerationReport()
    with rule_freeze_v1(enabled=True):
        cargo.generate_load_list('straits', 0, 1, seed=731, params=_profile('2.1'), report=report)
        assert report.requested['placement_requirements']
        cargo.generate_load_list('straits', 0, 1, seed=731, params=_profile('2.2'), report=report)
        assert report.requested['placement_requirements'] == report.realised['placement_requirements'] == {}
    with rule_freeze_v1(enabled=False):
        cargo.generate_load_list('straits', 0, 1, seed=731, params=_profile('2.1'), report=report)
    assert 'placement_requirements' not in report.requested
    assert 'placement_requirements' not in report.realised


@pytest.mark.parametrize('class_id', ['2.1', '2.3', '6.1'])
def test_generated_sw2_demand_uses_assessor_geometry_without_clearance_flags(class_id):
    from stowage.checker.rules_imdg import MISSING, STOWAGE, imdg_violations
    from stowage.rules.imdg.scenarios import context_for
    from tests.checker.test_rules_imdg_model_geometry import _fleet_shipment
    from tests.rules.test_imdg_model_geometry import register_metadata

    with rule_freeze_v1(enabled=True):
        box, = cargo.generate_load_list('straits', 0, 1, seed=731, params=_profile(class_id))
        # Explicit hypothetical complete test design from the assessor owner.
        # Actual fleet completeness is unchanged; no production data is promoted.
        instance, ship, _, slot, _ = _fleet_shipment('feeder_500', 'SW2')
        instance.containers = [box]
        ship.placements[0].container_id = box.id
        context = context_for(instance, box, 'primary', slot)
        instance.rule_overrides[STOWAGE] = {'containers': {}, 'assessment_scenarios':
            register_metadata(context, instance.vessel.imdg_placement_geometry)}
        assert box.dg.clear_living_quarters is None
        assert imdg_violations(instance, ship, call_index=0) == []
        instance.rule_overrides[STOWAGE]['assessment_scenarios'] = {}
        assert any(v.rule_id == MISSING and 'SW2' in v.message
                   for v in imdg_violations(instance, ship, call_index=0))
        assert box.dg.clear_living_quarters is None
