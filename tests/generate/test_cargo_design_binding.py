"""Shared authored design references bind complete exact shipment contexts."""
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from stowage.generate import cargo
from stowage.model.vessel import HeatSource, ImdgPlacementGeometry
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg import scenarios
from stowage.rules.imdg.scenarios import context_for, provenance_matches
from tests.checker.test_rules_imdg_assessments import _shipment


STOWAGE = 'imdg.stowage.3_2'


def _design_case():
    instance, ship, box, facts = _shipment(2810, 'III')
    # These are explicit synthetic design inputs, not observed temperatures or
    # a generator-created protection/placement clearance.
    instance.vessel.heat_sources = [HeatSource(
        id=f'synthetic-source-{index}', kind='other', liable_to_exceed_55_c=True,
        x_min_m=0, x_max_m=1, y_min_m=0, y_max_m=1, z_min_m=0, z_max_m=1,
    ) for index in range(2)]

    def evidence(identifier, clause):
        return dict(document='synthetic vessel design fixture', clause=clause,
                    scenario_id=identifier)

    instance.vessel.imdg_placement_geometry = ImdgPlacementGeometry(
        heat_map_complete=True,
        heat_map_provenance=evidence('synthetic-heat-map', 'SW1 design map'),
        living_quarters_map_complete=True,
        living_quarters_map_provenance=evidence('synthetic-living-map', 'SW2 design map'),
        heat_sources=[dict(id=source.id, liable_to_exceed_55_c=True,
                           provenance=evidence(f'synthetic-heat-source-{index}', 'SW1 source applicability'))
                      for index, source in enumerate(instance.vessel.heat_sources)],
    )
    facts['sunlight'] = {'package_inside_ctu': True, 'precautions_required': None}
    return instance, ship, box


def _evidence(metadata):
    return [source for source in (
        metadata.heat_map_provenance, metadata.living_quarters_map_provenance,
        *(row.provenance for row in metadata.heat_sources),
    ) if source is not None]


def _registry(instance):
    return instance.rule_overrides.get(STOWAGE, {}).get('assessment_scenarios', {})


def test_design_binding_registers_all_authored_references_without_changing_design_or_shipment():
    instance, ship, box = _design_case()
    original = instance.model_dump_json()
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_design_scenarios(instance, container_id=box.id)
    assert bound is not instance
    assert instance.model_dump_json() == original
    assert bound.vessel.model_dump_json() == instance.vessel.model_dump_json()
    assert [c.model_dump_json() for c in bound.containers] == [c.model_dump_json() for c in instance.containers]
    assert bound.rule_overrides[STOWAGE]['containers'] == instance.rule_overrides[STOWAGE]['containers']
    context = context_for(bound, box, 'primary', ship.placements[0].slot)
    registry = _registry(bound)
    sources = _evidence(instance.vessel.imdg_placement_geometry)
    assert set(registry) == {source.scenario_id for source in sources}
    for source in sources:
        assert provenance_matches(source, context, registry)
        record = registry[source.scenario_id]
        assert record['document'] == source.document
        assert record['clause'] == source.clause
        for field, value in asdict(context).items():
            if field != 'slot':
                assert field in record and record[field] == value
        assert {'0010182', '0010102'} <= set(record['allowed_slots'])
        assert len(record['allowed_slots']) == len(set(record['allowed_slots']))
    with rule_freeze_v1(enabled=True):
        assert cargo.bind_design_scenarios(bound, container_id=box.id).model_dump_json() == bound.model_dump_json()


def test_off_design_binding_returns_original_without_resolving_context(monkeypatch):
    instance, _, _ = _design_case()

    def forbidden(*args, **kwargs):
        raise AssertionError('off-state binding must not resolve contexts')

    monkeypatch.setattr(scenarios, 'context_for', forbidden)
    with rule_freeze_v1(enabled=False):
        assert cargo.bind_design_scenarios(instance, container_id='unknown', entry_path='invalid') is instance


@pytest.mark.parametrize('missing', ['all', 'one_source'])
@pytest.mark.parametrize('global_binding', [False, True])
def test_missing_design_provenance_remains_unknown(missing, global_binding):
    instance, _, box = _design_case()
    geometry = instance.vessel.imdg_placement_geometry
    geometry.heat_map_complete = None
    geometry.heat_sources[0].provenance = None
    if missing == 'all':
        geometry.living_quarters_map_complete = None
        geometry.heat_map_provenance = None
        geometry.living_quarters_map_provenance = None
        geometry.heat_sources[1].provenance = None
    before = instance.model_dump_json()
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_design_scenarios(instance, container_id=None if global_binding else box.id)
    assert instance.model_dump_json() == before
    assert bound.vessel.model_dump_json() == instance.vessel.model_dump_json()
    assert set(_registry(bound)) == {source.scenario_id for source in _evidence(geometry)}
    assert bound.vessel.imdg_placement_geometry.heat_sources[0].provenance is None


def test_distinct_boxes_promote_shared_design_ids_to_complete_context_lists():
    instance, ship, first = _design_case()
    second = next(box for box in instance.containers if box.dg and box.id != first.id)
    original = instance.model_dump_json()
    with rule_freeze_v1(enabled=True):
        left = cargo.bind_design_scenarios(instance, container_id=first.id)
        right = cargo.bind_design_scenarios(instance, container_id=second.id)
    left_context = context_for(left, first, 'primary', ship.placements[0].slot)
    right_context = context_for(right, second, 'primary', ship.placements[0].slot)
    for source in _evidence(instance.vessel.imdg_placement_geometry):
        assert provenance_matches(source, left_context, _registry(left))
        assert provenance_matches(source, right_context, _registry(right))
        assert not provenance_matches(source, right_context, _registry(left))
        assert not provenance_matches(source, left_context, _registry(right))
    assert instance.model_dump_json() == original
    left_before = left.model_dump_json()
    with rule_freeze_v1(enabled=True):
        both = cargo.bind_design_scenarios(left, container_id=second.id)
    assert left.model_dump_json() == left_before
    for source in _evidence(instance.vessel.imdg_placement_geometry):
        records = _registry(both)[source.scenario_id]
        assert records == [_registry(left)[source.scenario_id], _registry(right)[source.scenario_id]]
        assert provenance_matches(source, left_context, _registry(both))
        assert provenance_matches(source, right_context, _registry(both))
    with rule_freeze_v1(enabled=True):
        assert cargo.bind_design_scenarios(both, container_id=first.id).model_dump_json() == both.model_dump_json()
        assert cargo.bind_design_scenarios(both, container_id=second.id).model_dump_json() == both.model_dump_json()


def _recursive_design_case():
    instance, ship, box = _design_case()
    child = box.dg.model_copy(deep=True)
    child.additional = [box.dg.model_copy(deep=True)]
    box.dg.additional = [child, box.dg.model_copy(deep=True)]
    paths = ['primary', 'primary.additional.0', 'primary.additional.0.additional.0',
             'primary.additional.1']
    expected = [(box.id, path) for path in paths]
    expected += [(other.id, 'primary') for other in instance.containers
                 if other.dg is not None and other.id != box.id]
    return instance, ship, box, expected


def _assert_complete_design_records(bound, expected):
    boxes = {box.id: box for box in bound.containers}
    sources = _evidence(bound.vessel.imdg_placement_geometry)
    assert set(_registry(bound)) == {source.scenario_id for source in sources}
    for source in sources:
        records = _registry(bound)[source.scenario_id]
        records = records if isinstance(records, list) else [records]
        assert len(records) == len(expected)
        assert {(row['container_id'], row['entry_path']) for row in records} == set(expected)
        for identifier, path in expected:
            slots = cargo._physical_footprints(bound.vessel, boxes[identifier])
            context = context_for(bound, boxes[identifier], path, slots[0])
            row, = [row for row in records if (row['container_id'], row['entry_path']) == (identifier, path)]
            assert row['document'] == source.document and row['clause'] == source.clause
            assert row['allowed_slots'] == slots
            for field, value in asdict(context).items():
                if field != 'slot':
                    assert field in row and row[field] == value
            assert all(provenance_matches(source, replace(context, slot=slot), _registry(bound))
                       for slot in slots)


def test_global_design_binding_registers_all_actual_dg_entries_recursively_and_is_idempotent():
    instance, _, _, expected = _recursive_design_case()
    original = instance.model_dump_json()
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_design_scenarios(instance)
        repeated = cargo.bind_design_scenarios(bound, container_id=None)
    _assert_complete_design_records(bound, expected)
    assert repeated.model_dump_json() == bound.model_dump_json()
    assert instance.model_dump_json() == original
    assert bound.vessel.model_dump_json() == instance.vessel.model_dump_json()
    assert [box.model_dump_json() for box in bound.containers] == [box.model_dump_json() for box in instance.containers]
    assert bound.rule_overrides[STOWAGE]['containers'] == instance.rule_overrides[STOWAGE]['containers']


def test_global_design_binding_with_no_dg_registers_no_design_evidence():
    instance, _, _ = _design_case()
    for box in instance.containers:
        box.dg = None
    original = instance.model_dump_json()
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_design_scenarios(instance)
    assert _registry(bound) == {}
    assert bound.model_dump_json() == original
    assert instance.model_dump_json() == original


@pytest.mark.parametrize('entry_path', ['primary.additional.0', 'invalid', None])
def test_global_design_binding_rejects_entry_selection_without_a_container(entry_path):
    instance, _, _ = _design_case()
    original = instance.model_dump_json()
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError):
        cargo.bind_design_scenarios(instance, container_id=None, entry_path=entry_path)
    assert instance.model_dump_json() == original


@pytest.mark.parametrize('existing_shape', ['flat', 'list', 'tuple'])
def test_design_binding_preserves_foreign_records_without_scope_or_slot_splicing(existing_shape):
    instance, ship, box = _design_case()
    with rule_freeze_v1(enabled=True):
        registered = cargo.bind_design_scenarios(instance, container_id=box.id)
    source = _evidence(instance.vessel.imdg_placement_geometry)[0]
    actual = deepcopy(_registry(registered)[source.scenario_id])
    foreign = actual | {'instance_id': 'foreign-instance', 'allowed_slots': ['9999982']}
    existing = foreign if existing_shape == 'flat' else [foreign]
    if existing_shape == 'tuple':
        existing = tuple(existing)
    instance.rule_overrides[STOWAGE]['assessment_scenarios'] = {source.scenario_id: existing}
    original = instance.model_dump_json()
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_design_scenarios(instance, container_id=box.id)
    assert _registry(bound)[source.scenario_id] == [foreign, actual]
    assert instance.model_dump_json() == original
    context = context_for(bound, box, 'primary', ship.placements[0].slot)
    assert provenance_matches(source, context, _registry(bound))
    assert provenance_matches(source, replace(context, instance_id='foreign-instance', slot='9999982'), _registry(bound))
    assert not provenance_matches(source, replace(context, slot='9999982'), _registry(bound))
    assert not provenance_matches(source, replace(context, instance_id='foreign-instance'), _registry(bound))
    with rule_freeze_v1(enabled=True):
        assert cargo.bind_design_scenarios(bound, container_id=box.id).model_dump_json() == bound.model_dump_json()


@pytest.mark.parametrize('empty_manifest', [False, True])
def test_shipment_binding_also_registers_all_actual_design_contexts_even_without_shipment_facts(empty_manifest):
    from tests.generate.test_cargo_scenario_binding import _case

    instance, _, box, report, _ = _case()
    design, _, _ = _design_case()
    instance.vessel.imdg_placement_geometry = design.vessel.imdg_placement_geometry.model_copy(deep=True)
    instance.vessel.heat_sources = deepcopy(design.vessel.heat_sources)
    box.dg.additional = [box.dg.model_copy(deep=True)]
    if empty_manifest:
        report.realised['shipment_scenarios'] = {}
    before, audit_before = instance.model_dump_json(), deepcopy(report)
    expected = [(other.id, 'primary') for other in instance.containers if other.dg is not None]
    expected.append((box.id, 'primary.additional.0'))
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_shipment_scenarios(instance, report)
        repeated = cargo.bind_shipment_scenarios(bound, report)
    # Shipment provenance has its own ID, while design IDs retain all contexts.
    design_registry = {source.scenario_id: _registry(bound)[source.scenario_id]
                       for source in _evidence(bound.vessel.imdg_placement_geometry)}
    design_only = bound.model_copy(deep=True)
    design_only.rule_overrides[STOWAGE]['assessment_scenarios'] = design_registry
    _assert_complete_design_records(design_only, expected)
    assert repeated.model_dump_json() == bound.model_dump_json()
    assert instance.model_dump_json() == before and report == audit_before
    assert bound.vessel.model_dump_json() == instance.vessel.model_dump_json()
    assert [other.model_dump_json() for other in bound.containers] == [other.model_dump_json() for other in instance.containers]


def test_explicit_additional_entry_design_binding_has_its_own_context():
    instance, ship, box = _design_case()
    box.dg.additional = [box.dg.model_copy(deep=True)]
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_design_scenarios(instance, container_id=box.id, entry_path='primary.additional.0')
    additional = context_for(bound, box, 'primary.additional.0', ship.placements[0].slot)
    primary = context_for(bound, box, 'primary', ship.placements[0].slot)
    for source in _evidence(bound.vessel.imdg_placement_geometry):
        assert provenance_matches(source, additional, _registry(bound))
        assert not provenance_matches(source, primary, _registry(bound))


@pytest.mark.parametrize('field,value', [('voyage_id', 'foreign-voyage'), ('slot', '9999982')])
def test_design_binding_rejects_foreign_voyage_or_unlisted_physical_slot(field, value):
    instance, ship, box = _design_case()
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_design_scenarios(instance, container_id=box.id)
    context = context_for(bound, box, 'primary', ship.placements[0].slot)
    foreign = replace(context, **{field: value})
    for source in _evidence(bound.vessel.imdg_placement_geometry):
        assert provenance_matches(source, context, _registry(bound))
        assert not provenance_matches(source, foreign, _registry(bound))


def test_actual_fleet_geometry_only_design_registers_living_map_without_inventing_heat_evidence(monkeypatch):
    from stowage.vessel.fleet import load_vessel

    monkeypatch.setenv('STOWAGE_VESSEL_CACHE', '0')
    instance, _, box = _design_case()
    instance.vessel = load_vessel('feeder_500', with_hull=False)
    geometry = instance.vessel.imdg_placement_geometry
    assert geometry.living_quarters_map_provenance is not None
    assert geometry.heat_map_complete is None and geometry.heat_map_provenance is None
    assert geometry.heat_sources == []
    before = instance.vessel.model_dump_json()
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_design_scenarios(instance, container_id=box.id)
    assert bound.vessel.model_dump_json() == before
    sources = _evidence(geometry)
    assert set(_registry(bound)) == {source.scenario_id for source in sources}
    record = _registry(bound)[geometry.living_quarters_map_provenance.scenario_id]
    context = context_for(bound, box, 'primary', record['allowed_slots'][0])
    assert provenance_matches(geometry.living_quarters_map_provenance, context, _registry(bound))
    # This is an evidence-registration assertion; no hull physics, sunlight
    # assessment, full producer admission or positive SW2 clearance is claimed.


@pytest.fixture(scope='module')
def actual_feeder_500():
    from stowage.vessel.fleet import load_vessel

    # Retain the actual hull, tanks, thermal inventory and named exclusions.
    # These tests assess IMDG placements, not whole-vessel physics acceptance.
    return load_vessel('feeder_500')


def _generated_fleet_instance(vessel, class_id):
    from random import Random
    from stowage.model.instance import Instance
    from stowage.model.ship import ShipState
    from stowage.model.yard import YardState
    from stowage.ports import load_port
    from tests.generate.test_cargo_placement_audit import _profile

    params = _profile(class_id)
    report = cargo.CargoGenerationReport()
    box, = cargo.generate_load_list('straits', 0, 1, seed=731, params=params, report=report)
    rotation, _ = cargo._route('straits', params, Random(731))
    port = load_port('laem_chabang_like')
    return Instance(
        id=f'design.binding.feeder500.{class_id}', stage='A', size_class='full', seed=731,
        vessel=vessel, rotation=rotation, port=port, call_index=0, containers=[box],
        load_list=[box.id], yard=YardState(yard_id=port.yard.id, quay_buffer=[box.id]),
        onboard=ShipState(vessel_id=vessel.id),
    )


@pytest.mark.parametrize('class_id', ['2.1', '2.3', '6.1'])
def test_generated_sw2_entry_uses_actual_fleet_design_at_a_ground_slot(actual_feeder_500, class_id):
    from stowage.checker.rules_imdg import MISSING, imdg_placement_violations

    with rule_freeze_v1(enabled=True):
        instance = _generated_fleet_instance(actual_feeder_500, class_id)
        box, = instance.containers
        box_before = box.model_dump_json()
        assert box.dg.clear_living_quarters is None
        bound = cargo.bind_design_scenarios(instance, container_id=box.id)
        # Ground addresses require no invented lower cargo or support geometry.
        grounds = sorted(stack.cell_ids[0] for stack in bound.vessel.stacks if stack.cell_ids)
        results = {slot: imdg_placement_violations(bound, bound.onboard, box, slot) for slot in grounds}
        accepted = next((slot for slot, violations in results.items() if not violations), None)
        assert accepted is not None, sorted({violation.message for violations in results.values()
                                             for violation in violations})
        source = bound.vessel.imdg_placement_geometry.living_quarters_map_provenance
        assert provenance_matches(source, context_for(bound, box, 'primary', accepted), _registry(bound))
        _registry(bound).pop(source.scenario_id)
        missing = imdg_placement_violations(bound, bound.onboard, box, accepted)
    assert any(violation.rule_id == MISSING and 'SW2' in violation.message for violation in missing)
    assert box.model_dump_json() == box_before
    assert next(c for c in bound.containers if c.id == box.id).dg.clear_living_quarters is None


def test_actual_fleet_sw1_parser_receives_complete_authored_thermal_inventory_without_sunlight_claim(actual_feeder_500):
    from stowage.rules.imdg.placement_assessments import geometry_from_vessel

    with rule_freeze_v1(enabled=True):
        instance = _generated_fleet_instance(actual_feeder_500, '2.1')
        box, = instance.containers
        bound = cargo.bind_design_scenarios(instance, container_id=box.id)
    metadata = bound.vessel.imdg_placement_geometry
    source = metadata.heat_map_provenance
    slot = _registry(bound)[source.scenario_id]['allowed_slots'][0]
    context = context_for(bound, box, 'primary', slot)
    authored = {row.id: row for row in metadata.heat_sources}
    actual = {row.id: row for row in bound.vessel.heat_sources}
    assert authored.keys() == actual.keys()
    assert provenance_matches(source, context, _registry(bound))
    assert all(provenance_matches(row.provenance, context, _registry(bound)) for row in authored.values())
    geometry = geometry_from_vessel(bound.vessel, metadata, context=context,
                                    registry=_registry(bound), provision='SW1')
    assert geometry is not None and geometry.heat_map_complete is True
    parsed = {row.id: row for row in geometry.heat_sources}
    assert parsed.keys() == actual.keys()
    for identifier, row in parsed.items():
        assert row.liable_to_exceed_55_c is actual[identifier].liable_to_exceed_55_c
        assert row.provenance.scenario_id == authored[identifier].provenance.scenario_id
        assert provenance_matches(row.provenance, context, _registry(bound))
    assert not bound.rule_overrides[STOWAGE].get('containers')
    assert box.dg.protected_from_heat is None and box.dg.shaded is None
    # Receiving the thermal inventory does not establish SW1 sunlight facts,
    # placement compliance, a full checker pass, or whole-producer admission.
