"""Regression cases from the code and logic reviews of the DG assessors."""
import pytest

from stowage.checker.rules_imdg import MISSING, STOWAGE, imdg_violations
from stowage.rules.catalogue import rule_freeze_v1
from tests.checker.test_rules_imdg_assessments import _shipment


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('schema', ['1.8', '1.9'])
@pytest.mark.parametrize('key,un,pg,legacy', [
    ('h1', 1333, 'II', {'dry': True}),
    ('sw19', 3480, None, {'special_provisions': []}),
])
@pytest.mark.parametrize('entry_scoped', [False, True])
def test_present_null_assessment_never_restores_legacy_clearance(enabled, schema, key, un, pg, legacy, entry_scoped):
    instance, ship, box, facts = _shipment(un, pg)
    instance.schema_version = schema
    box.dg.ctu_closed = True
    facts.update(legacy)
    if entry_scoped:
        facts['entry_assessments'] = {'primary': {key: None}}
    else:
        facts[key] = None
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and key.upper() in v.message for v in out)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('field,un,pg,code', [
    ('dry', 1333, 'II', 'H1'), ('protected_from_heat', 1361, 'III', 'SW1'),
    ('clear_living_quarters', 2810, 'III', 'SW2'),
])
def test_schema_19_box_boolean_is_not_placement_permission(enabled, field, un, pg, code):
    instance, ship, box, facts = _shipment(un, pg)
    instance.schema_version = '1.9'
    box.dg.ctu_closed = True
    facts.update({field: True, 'verified_provisions': ['H2', 'SW27']})
    setattr(box.dg, field, True)
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    assert any(code in v.message and v.rule_id in {MISSING, STOWAGE} for v in out)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('un,code', [(1361, 'SW1'), (2810, 'SW2')])
def test_unregistered_vessel_scenario_cannot_clear_placement(enabled, un, code):
    from tests.checker.test_rules_imdg_assessments import _geometry
    instance, ship, box, facts = _shipment(un, 'III')
    instance.schema_version = '1.8'
    box.dg.ctu_closed = True
    facts['verified_provisions'] = ['H2', 'SW27']
    instance.vessel.heat_sources = []
    geometry = _geometry()
    instance.rule_overrides[STOWAGE]['placement_geometry'] = geometry
    facts['sunlight'] = {'0010182': {'package_inside_ctu': True, 'precautions_required': False,
                                    'provenance': geometry['heat_map_provenance']}}
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    assert any(code in v.message and v.rule_id == MISSING for v in out)


def test_contradictory_ctu_sunlight_declaration_cannot_bypass_precautions():
    from tests.checker.test_rules_imdg_assessments import _geometry, _register
    instance, ship, box, facts = _shipment(1361, 'III', slot='0010102')
    box.dg.ctu_closed = True
    facts['verified_provisions'] = ['H2', 'SW27']
    geometry = _geometry()
    instance.rule_overrides[STOWAGE]['placement_geometry'] = geometry
    facts['sunlight'] = {'package_inside_ctu': False, 'precautions_required': True,
                        'provenance': geometry['heat_map_provenance']}
    _register(instance, box, '0010102', facts, geometry)
    stack = next(s for s in instance.vessel.stacks if '0010102' in s.cell_ids)
    ship.covers_off[stack.hatch_cover_id] = 'quay'
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and 'SW1' in v.message for v in out)


def test_h1_handling_instructions_are_emitted_outside_the_verdict():
    from stowage.checker.rules_imdg import imdg_handling_instructions
    instance, ship, box, facts = _shipment(1333, 'II')
    # Unknown declarations still need operational precautions; they do not pass.
    before = instance.model_dump_json()
    instructions = imdg_handling_instructions(instance)
    assert instructions[0]['container_id'] == box.id
    assert instructions[0]['code'] == 'H1'
    assert 'dry' in ' '.join(instructions[0]['instructions'])
    assert instance.model_dump_json() == before
    assert any('H1' in v.message for v in imdg_violations(instance, ship, call_index=0))


def _assessed_case(code, slot='0010182'):
    from tests.checker.test_rules_imdg_assessments import _geometry, _register
    from tests.rules.test_imdg_shipment_assessments import h1_facts, sw19_facts
    un, pg = {'H1': (1333, 'II'), 'SW19': (3480, None),
              'SW1': (1361, 'III'), 'SW2': (2810, 'III')}[code]
    instance, ship, box, facts = _shipment(un, pg, slot=slot)
    box.dg.ctu_closed = True
    if code == 'H1':
        facts.update(h1_facts())
    elif code == 'SW19':
        facts.update(sw19_facts())
    else:
        facts['verified_provisions'] = ['H2', 'SW27']
        geometry = _geometry()
        instance.rule_overrides[STOWAGE]['placement_geometry'] = geometry
        if code == 'SW1':
            facts['sunlight'] = {'package_inside_ctu': True, 'precautions_required': False,
                                'provenance': geometry['heat_map_provenance']}
        _register(instance, box, slot, geometry)
    _register(instance, box, slot, facts)
    return instance, ship, box, facts


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('code', ['H1', 'SW19', 'SW1', 'SW2'])
@pytest.mark.parametrize('field', ['instance_id', 'container_id', 'entry_path', 'un_number',
                                  'packing_group', 'vessel_id', 'voyage_id', 'call_index',
                                  'allowed_slots', 'document', 'clause'])
def test_bound_assessments_cannot_be_replayed_in_another_scope(enabled, code, field):
    instance, ship, box, facts = _assessed_case(code)
    with rule_freeze_v1(enabled=enabled):
        assert imdg_violations(instance, ship, call_index=0) == []
        for row in instance.rule_overrides[STOWAGE]['assessment_scenarios'].values():
            row[field] = ['0030182'] if field == 'allowed_slots' else 1 if field == 'call_index' else 'other'
        out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and code in v.message for v in out)


@pytest.mark.parametrize('enabled', [False, True])
def test_schema_19_true_legacy_flag_next_to_actual_accommodation_fails_sw2(enabled):
    from tests.checker.test_rules_imdg_assessments import _component
    from stowage.checker.visibility import _fixed_loads
    from stowage.rules.imdg.placement_assessments import geometry_from_vessel
    from stowage.rules.imdg.scenarios import context_for
    instance, ship, box, facts = _assessed_case('SW2')
    facts['clear_living_quarters'] = True
    box.dg.clear_living_quarters = True
    geometry = instance.rule_overrides[STOWAGE]['placement_geometry']
    cell = next(c for c in instance.vessel.cells if c.id == '0010182')
    _component(instance, geometry, dict(x_min_m=cell.lcg_m-1, x_max_m=cell.lcg_m+1,
        y_min_m=cell.tcg_m-1, y_max_m=cell.tcg_m+1, z_min_m=9, z_max_m=13), kind='accommodation')
    registry = instance.rule_overrides[STOWAGE]['assessment_scenarios']
    derived = geometry_from_vessel(instance.vessel, geometry,
        context=context_for(instance, box, 'primary', '0010182'), registry=registry)
    visible = _fixed_loads(instance.vessel)[0].silhouette
    assert derived.living_quarters_regions[0].bounds.x_min_m == visible.x_min_m
    assert derived.living_quarters_regions[0].bounds.z_max_m == visible.z_max_m
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == STOWAGE and 'SW2' in v.message for v in out)


@pytest.mark.parametrize('covered', [True, False])
def test_sw1_shading_is_derived_from_secured_hatch_covers(covered):
    instance, ship, box, facts = _assessed_case('SW1', slot='0010102')
    facts['sunlight']['precautions_required'] = True
    if not covered:
        stack = next(s for s in instance.vessel.stacks if '0010102' in s.cell_ids)
        ship.covers_off[stack.hatch_cover_id] = 'quay'
    out = imdg_violations(instance, ship, call_index=0)
    assert (out == []) is covered
    if not covered:
        assert any(v.rule_id == STOWAGE and 'SW1' in v.message for v in out)


def test_malformed_packing_with_sunlight_is_unknown_instead_of_an_exception():
    instance, ship, box, facts = _assessed_case('SW1')
    box.dg.ctu_closed = None
    facts['ctu_closed'] = None
    facts['packing_form'] = []
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING for v in out)


def test_aliased_additional_entries_still_require_distinct_scope_bindings():
    from tests.checker.test_rules_imdg_assessments import _register
    from tests.rules.test_imdg_shipment_assessments import sw19_facts
    instance, ship, box, facts = _assessed_case('SW19')
    shared = box.dg.model_copy(update={'additional': []})
    box.dg.additional = [shared, shared]
    extra = sw19_facts()
    extra['sw19']['provenance']['scenario_id'] += '-extra'
    facts['entry_assessments'] = {'primary.additional.0': extra}
    _register(instance, box, '0010182', extra, entry_path='primary.additional.0')
    out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and 'SW19' in v.message for v in out)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('code', ['SW1', 'SW2'])
def test_null_placement_assessment_dominates_saved_legacy_flag(enabled, code):
    instance, ship, box, facts = _shipment(1361 if code == 'SW1' else 2810, 'III')
    instance.schema_version = '1.8'
    box.dg.ctu_closed = True
    facts['verified_provisions'] = ['H2', 'SW27']
    if code == 'SW1':
        facts.update(protected_from_heat=True, sunlight=None)
    else:
        facts['clear_living_quarters'] = True
        instance.rule_overrides[STOWAGE]['placement_geometry'] = None
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and code in v.message for v in out)
