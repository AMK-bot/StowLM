"""The checker evaluates named fleet regions at the actual CTU envelope."""
import pytest

from stowage.checker.rules_imdg import (
    MISSING, STOWAGE, _placement_envelope, imdg_placement_violations, imdg_violations,
)
from stowage.model.fixtures import build_instance
from stowage.model.cargo import DangerousGoods, PackingForm
from stowage.model.ship import Placement, ShipState
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.dgl import lookup_dgl
from stowage.rules.imdg.placement_assessments import BoxEnvelope, envelope_distance
from stowage.rules.imdg.scenarios import context_for
from stowage.vessel.fleet import load_vessel
from tests.rules.test_imdg_model_geometry import FLEETS, fleet_case, register_metadata


def _bounds(source):
    return BoxEnvelope(**{f'{axis}_{side}_m': getattr(source, f'{axis}_{side}_m')
                          for axis in 'xyz' for side in ('min', 'max')})


def _fleet_shipment(name, code='SW2', *, near=False, full=False):
    """Place a registered synthetic shipment in unchanged actual fleet solids."""
    instance = build_instance(with_dg_doc=True)
    instance.schema_version = '1.9'
    instance.id = f'named-fleet-assessment:{name}:{code}'
    instance.vessel = load_vessel(name) if full else fleet_case(name)
    instance.onboard = ShipState(vessel_id=instance.vessel.id)
    box = instance.containers[1]
    entry = lookup_dgl(2810 if code == 'SW2' else 1361, 'III')
    box.dg = DangerousGoods(un_number=f'{entry.un_number:04d}', imdg_class=entry.hazard_class,
        packing_group='III', stowage_category=entry.stowage_category,
        subsidiary_risks=list(entry.subsidiary_risks), stowage_codes=list(entry.stowage_codes),
        segregation_codes=list(entry.segregation_codes), ctu_closed=True,
        packing_form=PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS,
        verified_provisions=['H2', 'SW27'])
    cells = {c.id: c for c in instance.vessel.cells}
    candidates = []
    sources = ([r.bounds for r in instance.vessel.exclusion_regions]
               if code == 'SW2' else [_bounds(s) for s in instance.vessel.heat_sources])
    for stack in instance.vessel.stacks:
        if not stack.on_deck:
            continue
        cell = cells[stack.cell_ids[0]]
        bounds = BoxEnvelope(cell.lcg_m-3.029, cell.lcg_m+3.029,
            cell.tcg_m-1.219, cell.tcg_m+1.219, stack.base_z_m, stack.base_z_m+box.height_m)
        distances = [envelope_distance(bounds, b if isinstance(b, BoxEnvelope) else _bounds(b))
                     for b in sources]
        candidates.append((min(distances), cell.id))
    distance, slot = min(candidates) if near else max(candidates)
    assert distance < (3 if code == 'SW2' else 2.4) if near else distance > 3
    ship = ShipState(vessel_id=instance.vessel.id,
                     placements=[Placement(slot=slot, container_id=box.id)])
    metadata = instance.vessel.imdg_placement_geometry
    facts = {}
    if code == 'SW1':
        facts['sunlight'] = dict(package_inside_ctu=True, precautions_required=False,
                                provenance=metadata.heat_map_provenance.model_dump())
    context = context_for(instance, box, 'primary', slot)
    registry = register_metadata(context, metadata)
    instance.rule_overrides = {STOWAGE: {'containers': {box.id: facts}, 'assessment_scenarios': registry}}
    return instance, ship, box, slot, facts


@pytest.mark.parametrize('name', FLEETS)
@pytest.mark.parametrize('code', ['SW1', 'SW2'])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('state', ['positive', 'negative', 'unknown'])
def test_named_fleet_checker_positive_negative_unknown(name, code, enabled, state):
    instance, ship, _, _, _ = _fleet_shipment(name, code, near=state == 'negative')
    if state == 'unknown':
        prefix = 'heat' if code == 'SW1' else 'living_quarters'
        setattr(instance.vessel.imdg_placement_geometry, prefix+'_map_provenance', None)
        setattr(instance.vessel.imdg_placement_geometry, prefix+'_map_complete', None)
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    if state == 'positive':
        assert out == []
    else:
        rule = STOWAGE if state == 'negative' else MISSING
        assert any(v.rule_id == rule and code in v.message for v in out)


@pytest.mark.parametrize('name', FLEETS)
@pytest.mark.parametrize('code', ['SW1', 'SW2'])
def test_unrelated_unregistered_map_cannot_poison_valid_named_assessment(name, code):
    instance, ship, _, _, _ = _fleet_shipment(name, code)
    unrelated = ('living_quarters_map_provenance' if code == 'SW1' else 'heat_map_provenance')
    source = getattr(instance.vessel.imdg_placement_geometry, unrelated)
    source.scenario_id = 'unregistered-unrelated-map'
    assert imdg_violations(instance, ship, call_index=0) == []


@pytest.mark.parametrize('name', FLEETS)
def test_checker_full_fleet_sw1_uses_actual_heated_tank_links(name):
    instance, ship, _, _, _ = _fleet_shipment(name, 'SW1', full=True)
    assert instance.vessel.imdg_placement_geometry.heat_map_complete is True
    assert any(tank.heated for tank in instance.vessel.tanks)
    assert imdg_violations(instance, ship, call_index=0) == []


@pytest.mark.parametrize('name', FLEETS)
def test_named_sw2_missing_actual_support_floor_is_unknown(name):
    instance, _, box, _, _ = _fleet_shipment(name)
    stacks = {s.id: s for s in instance.vessel.stacks}
    cells = {c.id: c for c in instance.vessel.cells}
    stack = next(s for s in stacks.values() if s.on_deck and len(s.cell_ids) > 1)
    slot = stack.cell_ids[1]
    context = context_for(instance, box, 'primary', slot)
    instance.rule_overrides[STOWAGE]['assessment_scenarios'] = register_metadata(
        context, instance.vessel.imdg_placement_geometry)
    empty = ShipState(vessel_id=instance.vessel.id)
    assert _placement_envelope(instance, empty, box, slot, cells, stacks) is None
    out = imdg_placement_violations(instance, empty, box, slot)
    assert any(v.rule_id == MISSING and 'SW2' in v.message for v in out)


@pytest.mark.parametrize('name', FLEETS)
def test_named_actual_envelope_uses_high_cube_support_not_cell_nominal_z(name):
    instance, _, box, _, _ = _fleet_shipment(name)
    stack = next(s for s in instance.vessel.stacks if s.on_deck and len(s.cell_ids) > 1)
    lower = instance.containers[2]
    lower.dg = None
    lower.iso_type, lower.height_m = '25G1', 2.896
    ship = ShipState(vessel_id=instance.vessel.id,
        placements=[Placement(slot=stack.cell_ids[0], container_id=lower.id)])
    slot = stack.cell_ids[1]
    cells = {c.id: c for c in instance.vessel.cells}
    envelope = _placement_envelope(instance, ship, box, slot, cells,
                                    {s.id: s for s in instance.vessel.stacks})
    assert envelope.z_min_m == pytest.approx(stack.base_z_m + lower.height_m)
    assert abs(envelope.z_min_m - cells[slot].vcg_base_m) > .2
    assert envelope.x_max_m-envelope.x_min_m == pytest.approx(6.058)
    assert envelope.y_max_m-envelope.y_min_m == pytest.approx(2.438)


@pytest.mark.parametrize('name', FLEETS)
def test_checker_rejects_surface_breach_even_when_container_centre_is_clear(name):
    instance, ship, box, slot, _ = _fleet_shipment(name, near=True)
    envelope = _placement_envelope(instance, ship, box, slot,
        {c.id: c for c in instance.vessel.cells}, {s.id: s for s in instance.vessel.stacks})
    x = (envelope.x_min_m+envelope.x_max_m)/2
    y = (envelope.y_min_m+envelope.y_max_m)/2
    z = (envelope.z_min_m+envelope.z_max_m)/2
    centre = BoxEnvelope(x, x, y, y, z, z)
    assert min(envelope_distance(centre, _bounds(r.bounds))
               for r in instance.vessel.exclusion_regions) > 3
    assert any(v.rule_id == STOWAGE and 'SW2' in v.message
               for v in imdg_violations(instance, ship, call_index=0))
