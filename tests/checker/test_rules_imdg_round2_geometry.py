"""Checker verdicts for the declared synthetic feeders' round-two solids."""
import pytest

from stowage.checker.rules_imdg import MISSING, STOWAGE, imdg_violations
from stowage.model.fixtures import build_instance
from stowage.model.cargo import DangerousGoods, PackingForm
from stowage.model.ship import Placement, ShipState
from stowage.rules.imdg.dgl import lookup_dgl
from stowage.rules.imdg.placement_assessments import BoxEnvelope, envelope_distance
from stowage.rules.imdg.scenarios import context_for
from tests.rules.test_imdg_model_geometry import FLEETS, register_metadata
from tests.rules.test_imdg_round2_geometry import round2_vessel


def round2_shipment(name, *, near=False):
    instance = build_instance(with_dg_doc=True)
    instance.id = f'round2-named-catalog:{name}'
    instance.schema_version = '1.9'
    instance.vessel = round2_vessel(name).model_copy(deep=True)
    instance.onboard = ShipState(vessel_id=instance.vessel.id)
    box = instance.containers[1]
    entry = lookup_dgl(2810, 'III')
    box.dg = DangerousGoods(un_number='2810', imdg_class=entry.hazard_class,
        packing_group='III', stowage_category=entry.stowage_category,
        subsidiary_risks=list(entry.subsidiary_risks), stowage_codes=list(entry.stowage_codes),
        segregation_codes=list(entry.segregation_codes), ctu_closed=True,
        packing_form=PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS)
    candidates = []
    cells = {c.id: c for c in instance.vessel.cells}
    for stack in instance.vessel.stacks:
        if not stack.on_deck:
            continue
        c = cells[stack.cell_ids[0]]
        envelope = BoxEnvelope(c.lcg_m-3.029, c.lcg_m+3.029, c.tcg_m-1.219,
            c.tcg_m+1.219, stack.base_z_m, stack.base_z_m+box.height_m)
        distance = min(envelope_distance(envelope, BoxEnvelope(**r.bounds.model_dump()))
                       for r in instance.vessel.exclusion_regions)
        candidates.append((distance, c.id))
    distance, slot = min(candidates) if near else max(candidates)
    assert distance < 3 if near else distance >= 3
    context = context_for(instance, box, 'primary', slot)
    instance.rule_overrides = {STOWAGE: {'containers': {box.id: {}},
        'assessment_scenarios': register_metadata(context, instance.vessel.imdg_placement_geometry)}}
    ship = ShipState(vessel_id=instance.vessel.id,
                     placements=[Placement(slot=slot, container_id=box.id)])
    return instance, ship, box, slot


@pytest.mark.parametrize('name', FLEETS)
@pytest.mark.parametrize('state', ['positive', 'negative', 'unknown'])
def test_checker_consumes_actual_complete_eight_region_catalog(name, state):
    instance, ship, _, _ = round2_shipment(name, near=state == 'negative')
    metadata = instance.vessel.imdg_placement_geometry
    assert metadata.living_quarters_map_complete is True
    assert len(metadata.living_quarters_sources) == len(instance.vessel.exclusion_regions) == 8
    if state == 'unknown':
        metadata.living_quarters_map_provenance.scenario_id = 'unregistered-map'
    out = imdg_violations(instance, ship, call_index=0)
    if state == 'positive':
        assert out == []
    else:
        assert any(v.rule_id == (STOWAGE if state == 'negative' else MISSING)
                   and 'SW2' in v.message for v in out)


@pytest.mark.parametrize('name', FLEETS)
def test_complete_flag_cannot_hide_missing_named_region_reference(name):
    instance, ship, _, _ = round2_shipment(name)
    instance.vessel.imdg_placement_geometry.living_quarters_sources.pop()
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and 'SW2' in v.message for v in out)


@pytest.mark.parametrize('name', FLEETS)
def test_reference_cannot_point_to_a_different_existing_named_region(name):
    instance, ship, _, _ = round2_shipment(name)
    metadata = instance.vessel.imdg_placement_geometry
    metadata.living_quarters_sources[0].region_id = metadata.living_quarters_sources[1].region_id
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and 'SW2' in v.message for v in out)


@pytest.mark.parametrize('name', FLEETS)
@pytest.mark.parametrize('near', [False, True])
def test_actual_fleet_tank_sections_feed_sw1_checker_verdict(name, near):
    from tests.checker.test_rules_imdg_model_geometry import _fleet_shipment
    instance, ship, _, _, _ = _fleet_shipment(name, 'SW1', near=near, full=True)
    assert {s.actual_geometry.shape for s in instance.vessel.heat_sources
            if s.kind == 'heated_tank'} == {'hull_sections'}
    out = imdg_violations(instance, ship, call_index=0)
    if near:
        assert any(v.rule_id == STOWAGE and 'SW1' in v.message for v in out)
    else:
        assert out == []


def test_unsampled_tank_space_cannot_turn_former_bbox_rejection_into_clearance():
    from tests.checker.test_rules_imdg_model_geometry import _fleet_shipment
    instance, _, box, _, facts = _fleet_shipment('feeder_1700', 'SW1', full=True)
    slot = '0330108'
    stack = next(s for s in instance.vessel.stacks if slot in s.cell_ids)
    lower_ids = stack.cell_ids[:stack.cell_ids.index(slot)]
    supports = [b for b in instance.containers if b.id != box.id]
    assert len(supports) >= len(lower_ids)
    placements = []
    for support, lower_slot in zip(supports, lower_ids):
        support.dg = None
        support.iso_type, support.length_ft, support.height_m = '22G1', 20, 2.591
        placements.append(Placement(slot=lower_slot, container_id=support.id))
    placements.append(Placement(slot=slot, container_id=box.id))
    ship = ShipState(vessel_id=instance.vessel.id, placements=placements)
    context = context_for(instance, box, 'primary', slot)
    instance.rule_overrides[STOWAGE]['assessment_scenarios'] = register_metadata(
        context, instance.vessel.imdg_placement_geometry)
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and 'SW1' in v.message for v in out)
    assert not any(v.rule_id == STOWAGE and 'SW1' in v.message for v in out)
