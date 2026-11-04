"""Evidence paths do not weaken independent authorization/unknown guards."""
import pytest
from dataclasses import asdict

from stowage.checker.rules_imdg import MISSING, STOWAGE, imdg_violations
from stowage.rules.catalogue import rule_freeze_v1
from tests.rules.test_imdg_followup import _lone_dg_case


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('label, mismatch', [('1.4G', False), ('1.3G', True), ('unresolved', True)])
def test_class_one_compares_division_but_retains_doc_label(enabled, label, mismatch):
    instance, ship, box = _lone_dg_case(336)
    box.dg.imdg_class = label
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    assert any('disagree with the DGL' in v.message for v in out) is mismatch
    assert any('Class-1 stowage' in v.message for v in out)
    if label != '1.4G':
        assert any('Compliance' in v.message for v in out)


def _shipment(un, pg=None, slot='0010182'):
    from tests.checker.test_rules_imdg import _case, _dg
    instance, ship, box, _ = _case(2, far=True)
    instance.schema_version = '1.9'
    # Isolated design fixture: sources/tanks are added explicitly by each case.
    instance.vessel.heat_sources = []
    instance.vessel.tanks = []
    instance.vessel.bridge = None  # No remote deckhouse in this local-geometry unit fixture.
    box.dg = _dg(un, pg)
    ship.placements = [ship.placements[0].model_copy(update={'slot': slot})]
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = [box.dg.imdg_class]
    for area in instance.vessel.dg_doc_areas:
        area.classes_allowed = [box.dg.imdg_class]
    facts = instance.rule_overrides[STOWAGE]['containers'][box.id]
    facts.pop('dry', None)
    facts.pop('clear_living_quarters', None)
    return instance, ship, box, facts


def _register(instance, box, slot, *records, entry_path='primary'):
    """The fixture author explicitly declares evidence scope, not the checker."""
    from stowage.rules.imdg.scenarios import context_for
    context = context_for(instance, box, entry_path, slot)
    registry = instance.rule_overrides[STOWAGE].setdefault('assessment_scenarios', {})
    def visit(value):
        if not isinstance(value, dict):
            if isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)
            return
        source = value.get('provenance')
        if isinstance(source, dict) and {'scenario_id', 'document', 'clause'} <= source.keys():
            row = asdict(context)
            row.pop('slot')
            registry[source['scenario_id']] = row | {'document': source['document'],
                'clause': source['clause'], 'allowed_slots': [slot]}
        for key, child in value.items():
            if key.endswith('_provenance'):
                visit({'provenance': child})
            else:
                visit(child)
    for record in records:
        visit(record)
    return registry


def _component(instance, geometry, bounds, kind='air_intake'):
    from stowage.model.vessel import FixedLoad, FixedLoadSilhouette
    index = len(instance.vessel.fixed_loads)
    instance.vessel.fixed_loads.append(FixedLoad(
        x_m=(bounds['x_min_m']+bounds['x_max_m'])/2,
        y_m=(bounds['y_min_m']+bounds['y_max_m'])/2,
        z_m=(bounds['z_min_m']+bounds['z_max_m'])/2,
        mass_kg=1, kind='non_cargo', silhouette=FixedLoadSilhouette(**bounds)))
    geometry['living_quarters_sources'].append({'id': f'{kind}-{index}', 'kind': kind, 'fixed_load_index': index})


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('state', ['positive', 'negative', 'unknown'])
def test_h1_evidence_at_actual_placement(enabled, state):
    from tests.rules.test_imdg_shipment_assessments import h1_facts
    instance, ship, box, facts = _shipment(1333, 'II')
    facts.update(h1_facts())
    _register(instance, box, '0010182', facts)
    if state == 'negative':
        facts['h1']['packing_condition'] = 'wet'
    elif state == 'unknown':
        del facts['h1']['provenance']
        facts['dry'] = True  # partial new evidence cannot inherit an old clearance
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    if state == 'positive':
        assert out == []
    else:
        assert any(v.rule_id == (MISSING if state == 'unknown' else STOWAGE) and 'H1' in v.message for v in out)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('state', ['positive', 'negative', 'unknown', 'long_voyage'])
def test_sw19_battery_assessment_controls_category(enabled, state):
    from tests.rules.test_imdg_shipment_assessments import sw19_facts
    instance, ship, box, facts = _shipment(3480, slot='0010102')
    box.dg.ctu_closed = True
    facts.update(sw19_facts('damaged_defective', short=state != 'long_voyage'))
    _register(instance, box, '0010102', facts)
    if state == 'negative':
        facts['sw19']['transport_in_accordance'] = False
    elif state == 'unknown':
        del facts['sw19']['battery_condition']
        facts['special_provisions'] = []
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    if state == 'positive':
        assert out == []
    elif state == 'long_voyage':
        assert any('category C' in v.message for v in out)
    else:
        assert any(v.rule_id == (MISSING if state == 'unknown' else STOWAGE) and 'SW19' in v.message for v in out)


def _geometry():
    return {
        'heat_sources': [], 'heat_map_complete': True,
        'heat_map_provenance': {'document': 'synthetic-map', 'clause': '7.1.2 SW1', 'scenario_id': 'map-heat-1'},
        'living_quarters_sources': [], 'living_quarters_map_complete': True,
        'living_quarters_map_provenance': {'document': 'synthetic-map', 'clause': '7.1.2 SW2', 'scenario_id': 'map-living-1'},
    }


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('code,un,pg', [('SW1', 1361, 'III'), ('SW2', 2810, 'III')])
@pytest.mark.parametrize('state', ['positive', 'negative', 'unknown'])
def test_placement_assessment_uses_box_bounds(enabled, code, un, pg, state):
    instance, ship, box, facts = _shipment(un, pg)
    facts.update(verified_provisions=['H2', 'SW27'])
    geometry = _geometry()
    instance.vessel.heat_sources = []
    instance.rule_overrides[STOWAGE]['placement_geometry'] = geometry
    facts['sunlight'] = {'package_inside_ctu': True, 'precautions_required': False,
                        'provenance': geometry['heat_map_provenance']}
    prefix = 'heat' if code == 'SW1' else 'living_quarters'
    if state == 'unknown':
        geometry[prefix + '_map_complete'] = None
        facts['protected_from_heat' if code == 'SW1' else 'clear_living_quarters'] = True
    if state == 'negative':
        cell = next(c for c in instance.vessel.cells if c.id == '0010182')
        bounds = dict(x_min_m=cell.lcg_m-1, x_max_m=cell.lcg_m+1,
                      y_min_m=cell.tcg_m-1, y_max_m=cell.tcg_m+1,
                      z_min_m=cell.vcg_base_m, z_max_m=cell.vcg_base_m+1)
        if code == 'SW2':
            _component(instance, geometry, bounds)
        else:
            from stowage.model.vessel import BoxSourceGeometry, HeatSource
            instance.vessel.heat_sources = [HeatSource(id='tank', kind='heated_tank',
                actual_geometry=BoxSourceGeometry(**bounds), **bounds)]
            geometry['heat_sources'] = [{'id': 'tank', 'liable_to_exceed_55_c': True,
                                         'provenance': geometry['heat_map_provenance']}]
    _register(instance, box, '0010182', facts, geometry)
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    if state == 'positive':
        assert out == []
    else:
        assert any(v.rule_id == (MISSING if state == 'unknown' else STOWAGE) and code in v.message for v in out)


def test_sw2_uses_actual_high_cube_support_and_incremental_matches_full():
    from stowage.checker.rules_imdg import imdg_placement_violations
    from stowage.model.ship import Placement
    instance, ship, box, facts = _shipment(2810, 'III', slot='0010184')
    lower = instance.containers[2]
    lower.dg = None
    box.dg.ctu_closed = True
    lower.iso_type, lower.height_m = '25G1', 2.896
    ship.placements.insert(0, Placement(slot='0010182', container_id=lower.id))
    geometry = _geometry()
    cell = next(c for c in instance.vessel.cells if c.id == '0010184')
    # Nominal floor gives >3m; the actual HC support raises the CTU within 3m.
    _component(instance, geometry, dict(x_min_m=cell.lcg_m-1, x_max_m=cell.lcg_m+1,
                       y_min_m=cell.tcg_m-1, y_max_m=cell.tcg_m+1,
                       z_min_m=17.3, z_max_m=18))
    instance.rule_overrides[STOWAGE]['placement_geometry'] = geometry
    _register(instance, box, '0010184', geometry)
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == STOWAGE and 'SW2' in v.message for v in out)
    partial = ship.model_copy(update={'placements': ship.placements[:1]})
    assert imdg_placement_violations(instance, partial, box, '0010184') == out
    partial.placements = []
    out = imdg_placement_violations(instance, partial, box, '0010184')
    assert any(v.rule_id == MISSING and 'SW2' in v.message for v in out)


def test_same_un_additional_battery_requires_its_own_assessment():
    from tests.rules.test_imdg_shipment_assessments import sw19_facts
    instance, ship, box, facts = _shipment(3480, slot='0010102')
    box.dg.ctu_closed = True
    box.dg.additional = [box.dg.model_copy(deep=True)]
    facts.update(sw19_facts())
    _register(instance, box, '0010102', facts)
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and 'SW19' in v.message for v in out)
    extra = sw19_facts()
    extra['sw19']['provenance']['scenario_id'] += '-additional'
    facts['entry_assessments'] = {'primary.additional.0': extra}
    _register(instance, box, '0010102', extra, entry_path='primary.additional.0')
    assert imdg_violations(instance, ship, call_index=0) == []


def test_actual_envelope_includes_support_and_candidate_oog():
    from stowage.checker.rules_imdg import _placement_envelope
    from stowage.model.cargo import OutOfGauge
    from stowage.model.ship import Placement
    instance, ship, box, _ = _shipment(2810, 'III', slot='0010184')
    lower = instance.containers[2]
    lower.oog = OutOfGauge(over_height_m=0.5)
    box.oog = OutOfGauge(over_height_m=0.2, over_width_port_m=0.3)
    ship.placements = [Placement(slot='0010182', container_id=lower.id)]
    envelope = _placement_envelope(instance, ship, box, '0010184',
                                   {c.id: c for c in instance.vessel.cells},
                                   {s.id: s for s in instance.vessel.stacks})
    assert envelope.z_min_m == pytest.approx(9 + lower.height_m + 0.5)
    assert envelope.z_max_m == pytest.approx(envelope.z_min_m + box.height_m + 0.2)
    assert envelope.y_max_m - envelope.y_min_m == pytest.approx(2.438 + 0.3)


def test_existing_static_stowage_call_signature_remains_supported():
    from stowage.checker.rules_imdg import _stowage, _doc_areas
    instance, ship, box, _ = _shipment(3480, slot='0010102')
    # rules_weight._compatible_slots calls this internal shared static query.
    out = _stowage(instance, box, '0010102', 0,
                   {c.id: c for c in instance.vessel.cells},
                   {s.id: s for s in instance.vessel.stacks}, _doc_areas(instance))
    assert any(v.rule_id == MISSING and 'SW19' in v.message for v in out)


def test_static_hold_screen_defers_unknown_vertical_geometry_to_actual_placement():
    from stowage.checker.rules_imdg import imdg_placement_violations
    from stowage.checker.rules_weight import _compatible_slots
    from stowage.model.ship import Placement
    instance, ship, box, _ = _shipment(2810, 'III', slot='0010104')
    box.dg.ctu_closed = True
    lower = instance.containers[2]
    lower.dg = None
    ship.placements = [Placement(slot='0010102', container_id=lower.id)]
    instance.rule_overrides[STOWAGE]['placement_geometry'] = _geometry()
    _register(instance, box, '0010104', instance.rule_overrides[STOWAGE]['placement_geometry'])
    assert '0010104' in _compatible_slots(instance, box)
    assert imdg_placement_violations(instance, ship, box, '0010104') == []
    ship.placements = []
    assert any(v.rule_id == MISSING and 'SW2' in v.message
               for v in imdg_placement_violations(instance, ship, box, '0010104'))
