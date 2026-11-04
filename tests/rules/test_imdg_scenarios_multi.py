"""A shared design ID can register several separate, exact evidence scopes."""
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from stowage.rules.imdg.scenarios import BindingContext, provenance_matches


SOURCE = {'document': 'synthetic shared design', 'clause': 'IMDG 42-24 7.1.2 SW2',
          'scenario_id': 'unchanged-shared-design-id'}
BASE = BindingContext('instance-a', 'QZXU0000020', 'primary', '2810', 'III',
                      'vessel-a', 'voyage-a', 0, '0010182')
CHANGES = [
    ('instance_id', 'instance-b'), ('container_id', 'QZXU0000035'),
    ('entry_path', 'primary.additional.0'), ('un_number', '3480'),
    ('packing_group', None), ('vessel_id', 'vessel-b'), ('voyage_id', 'voyage-b'),
    ('call_index', 1), ('slot', '0030182'),
]


def row(context, *, source=SOURCE):
    record = asdict(context)
    record.pop('slot')
    return record | {'document': source['document'], 'clause': source['clause'],
                     'allowed_slots': [context.slot]}


@pytest.mark.parametrize('field,value', CHANGES)
@pytest.mark.parametrize('collection', [list, tuple])
def test_each_whole_registered_use_of_one_unchanged_scenario_id_passes(field, value, collection):
    other = replace(BASE, **{field: value})
    registry = {SOURCE['scenario_id']: collection([row(BASE), row(other)])}
    assert provenance_matches(SOURCE, BASE, registry) is True
    assert provenance_matches(SOURCE, other, registry) is True


@pytest.mark.parametrize('field,value', CHANGES)
def test_registered_rows_cannot_be_spliced_into_a_third_scope(field, value):
    other = replace(BASE, **dict(CHANGES))
    mixed = replace(BASE, **{field: value})
    registry = {SOURCE['scenario_id']: [row(BASE), row(other)]}
    assert mixed != BASE and mixed != other
    assert provenance_matches(SOURCE, mixed, registry) is False


def test_document_and_clause_must_match_the_same_whole_row():
    first, second = row(BASE), row(BASE)
    first['clause'] = 'other clause'
    second['document'] = 'other document'
    assert provenance_matches(SOURCE, BASE, {SOURCE['scenario_id']: [first, second]}) is False


def test_placement_permission_cannot_be_borrowed_from_a_different_shipment_row():
    first = row(BASE)
    first['allowed_slots'] = ['0030182']
    second = row(replace(BASE, container_id='QZXU0000035'))
    assert provenance_matches(SOURCE, BASE, {SOURCE['scenario_id']: [first, second]}) is False


@pytest.mark.parametrize('malformed', [None, {}, [], '', True, [row(BASE)], (row(BASE),)])
def test_malformed_sibling_cannot_grant_permission_but_does_not_erase_a_valid_row(malformed):
    other = replace(BASE, container_id='QZXU0000035')
    registry = {SOURCE['scenario_id']: [malformed, row(other)]}
    assert provenance_matches(SOURCE, BASE, registry) is False
    assert provenance_matches(SOURCE, other, registry) is True


@pytest.mark.parametrize('field', list(row(BASE)))
def test_incomplete_siblings_cannot_be_combined_to_complete_a_record(field):
    incomplete = row(BASE)
    removed = incomplete.pop(field)
    fragment = {field: removed}
    registry = {SOURCE['scenario_id']: [incomplete, fragment]}
    assert provenance_matches(SOURCE, BASE, registry) is False


@pytest.mark.parametrize('slots', [None, [], '*', ['*'], ['0010182', '*'], ['0010182', '0010182']])
def test_malformed_slot_list_in_one_row_never_uses_a_sibling_slot_list(slots):
    malformed = row(BASE)
    malformed['allowed_slots'] = slots
    other = row(replace(BASE, container_id='QZXU0000035'))
    assert provenance_matches(SOURCE, BASE, {SOURCE['scenario_id']: [malformed, other]}) is False


def test_boolean_call_index_is_not_an_integer_scope_even_in_a_multi_registry():
    context = replace(BASE, call_index=1)
    malformed = row(context)
    malformed['call_index'] = True
    assert provenance_matches(SOURCE, context, {SOURCE['scenario_id']: [malformed]}) is False


@pytest.mark.parametrize('empty', [[], ()])
def test_empty_multi_registry_has_no_registered_use(empty):
    assert provenance_matches(SOURCE, BASE, {SOURCE['scenario_id']: empty}) is False


def test_legacy_single_object_keeps_exact_scope_behavior():
    registry = {SOURCE['scenario_id']: row(BASE)}
    assert provenance_matches(SOURCE, BASE, registry) is True
    assert provenance_matches(SOURCE, replace(BASE, container_id='QZXU0000035'), registry) is False


def test_single_and_multi_records_do_not_modify_provenance_context_or_registry():
    source = deepcopy(SOURCE)
    registry = {source['scenario_id']: [row(BASE), row(replace(BASE, call_index=1))]}
    original_source, original_registry = deepcopy(source), deepcopy(registry)
    original_context = asdict(BASE)
    provenance_matches(source, BASE, registry)
    provenance_matches(source, replace(BASE, slot='0030182'), registry)
    assert source == original_source
    assert registry == original_registry
    assert asdict(BASE) == original_context


@pytest.mark.parametrize('enabled', [False, True])
def test_two_shipments_share_unchanged_sw2_design_id_with_independent_bindings(enabled):
    from stowage.checker.rules_imdg import MISSING, STOWAGE, _placement_envelope, imdg_violations
    from stowage.model.ship import Placement
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.rules.imdg.placement_assessments import BoxEnvelope, envelope_distance
    from stowage.rules.imdg.scenarios import context_for
    from tests.checker.test_rules_imdg_round2_geometry import round2_shipment
    from tests.rules.test_imdg_model_geometry import register_metadata

    instance, ship, first, _ = round2_shipment('feeder_500')
    second = next(box for box in instance.containers if box.id != first.id and box.length_ft == 20)
    second.dg = first.dg.model_copy(deep=True)
    cells = {cell.id: cell for cell in instance.vessel.cells}
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    first_slot = ship.placements[0].slot
    for stack in instance.vessel.stacks:
        if not stack.on_deck or stack.cell_ids[0] == first_slot:
            continue
        candidate = stack.cell_ids[0]
        bounds = _placement_envelope(instance, ship, second, candidate, cells, stacks)
        if bounds is not None and all(envelope_distance(bounds, BoxEnvelope(**region.bounds.model_dump())) >= 3
                                      for region in instance.vessel.exclusion_regions):
            second_slot = candidate
            break
    else:
        raise AssertionError('No second physically clear deck placement in the synthetic fixture')
    ship.placements.append(Placement(slot=second_slot, container_id=second.id))
    config = instance.rule_overrides[STOWAGE]
    config['containers'][second.id] = {}
    second_registry = register_metadata(context_for(instance, second, 'primary', second_slot),
                                        instance.vessel.imdg_placement_geometry)
    first_registry = config['assessment_scenarios']
    assert first_registry.keys() == second_registry.keys()
    scenario_id = instance.vessel.imdg_placement_geometry.living_quarters_map_provenance.scenario_id
    config['assessment_scenarios'] = {key: [first_registry[key], second_registry[key]] for key in first_registry}
    with rule_freeze_v1(enabled=enabled):
        assert imdg_violations(instance, ship, call_index=0) == []
    # Removing one use leaves the source ID and other shipment's binding intact.
    config['assessment_scenarios'][scenario_id].pop()
    with rule_freeze_v1(enabled=enabled):
        out = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and 'SW2' in v.message and second.id in v.containers for v in out)
    assert not any('SW2' in v.message and first.id in v.containers for v in out)
    assert instance.vessel.imdg_placement_geometry.living_quarters_map_provenance.scenario_id == scenario_id
