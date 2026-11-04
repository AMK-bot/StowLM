"""Evidence references must resolve to the shipment and placement assessed."""

from dataclasses import asdict, replace

import pytest

from stowage.model.fixtures import build_instance
from stowage.rules.imdg.scenarios import (
    BindingContext, context_for, provenance_matches, voyage_identity,
)


def context():
    return BindingContext(instance_id='instance-1', container_id='QZXU0000020',
                          entry_path='primary', un_number='3480', packing_group=None,
                          vessel_id='vessel-1', voyage_id='voyage-1', call_index=0, slot='0010182')


def evidence():
    return {'document': 'synthetic record', 'clause': '7.1.5 SW19', 'scenario_id': 'scenario-1'}


def registry(expected=None):
    record = asdict(expected or context())
    record.pop('slot')
    record.update(document=evidence()['document'], clause=evidence()['clause'], allowed_slots=['0010182'])
    return {'scenario-1': record}


def test_exact_registered_provenance_matches():
    assert provenance_matches(evidence(), context(), registry()) is True


@pytest.mark.parametrize('field,value', [
    ('instance_id', 'other-instance'), ('container_id', 'QZXU0000035'),
    ('entry_path', 'primary.additional.0'), ('un_number', '3090'),
    ('packing_group', 'II'), ('vessel_id', 'other-vessel'), ('voyage_id', 'other-voyage'),
    ('call_index', 1), ('slot', '0030182'),
])
def test_registered_evidence_cannot_be_replayed_in_another_scope(field, value):
    assert provenance_matches(evidence(), replace(context(), **{field: value}), registry()) is False


@pytest.mark.parametrize('field', ['document', 'clause', 'scenario_id'])
def test_provenance_fields_must_resolve_exactly(field):
    source = evidence()
    source[field] = 'unrelated'
    assert provenance_matches(source, context(), registry()) is False


@pytest.mark.parametrize('bad', [None, {}, [], '', {'scenario-1': None}, {'scenario-1': []}])
def test_missing_or_malformed_registry_fails_closed(bad):
    assert provenance_matches(evidence(), context(), bad) is False


@pytest.mark.parametrize('bad', [None, {}, [], '', {'document': 'x', 'clause': 'y', 'scenario_id': 'unregistered'}])
def test_missing_malformed_and_unregistered_provenance_fail_closed(bad):
    assert provenance_matches(bad, context(), registry()) is False


@pytest.mark.parametrize('field', list(asdict(context())))
def test_registry_requires_every_scope_field_including_known_null_packing_group(field):
    scenarios = registry()
    scenarios['scenario-1'].pop('allowed_slots' if field == 'slot' else field)
    assert provenance_matches(evidence(), context(), scenarios) is False


@pytest.mark.parametrize('slots', [None, '*', [], ['*'], ['0010182', '*'], [None], ['0010182', '0010182']])
def test_placement_scope_is_an_explicit_valid_nonempty_set(slots):
    scenarios = registry()
    scenarios['scenario-1']['allowed_slots'] = slots
    assert provenance_matches(evidence(), context(), scenarios) is False


@pytest.mark.parametrize('value', [True, None, -1, '0'])
def test_malformed_call_index_never_matches(value):
    scenarios = registry()
    scenarios['scenario-1']['call_index'] = value
    assert provenance_matches(evidence(), replace(context(), call_index=value), scenarios) is False


def test_missing_context_fails_closed():
    assert provenance_matches(evidence(), None, registry()) is False


@pytest.mark.parametrize('field,value', [
    ('instance_id', None), ('container_id', ''), ('entry_path', '*'),
    ('entry_path', 'primary.additional.-1'), ('un_number', 'UN3480'),
    ('packing_group', []), ('vessel_id', None), ('voyage_id', ''),
    ('call_index', True), ('slot', '0010183'),
])
def test_even_identically_malformed_context_and_registry_cannot_grant_permission(field, value):
    expected = replace(context(), **{field: value})
    assert provenance_matches(evidence(), expected, registry(expected)) is False


def test_evidence_objects_resolve_to_the_same_registry():
    from stowage.rules.imdg.placement_assessments import PlacementEvidence
    assert provenance_matches(PlacementEvidence(**evidence()), context(), registry()) is True


def test_context_constructor_binds_actual_entry_and_rotation_leg():
    instance = build_instance()
    box = next(b for b in instance.containers if b.dg is not None)
    expected = context_for(instance, box, 'primary', '0010182')
    assert expected.instance_id == instance.id
    assert expected.container_id == box.id
    assert expected.un_number == box.dg.un_number
    assert expected.packing_group == box.dg.packing_group
    assert expected.vessel_id == instance.vessel.id
    assert expected.voyage_id == voyage_identity(instance, instance.call_index)
    assert context_for(instance, box, 'primary.additional.0', '0010182') is None
    assert context_for(instance, box, 'primary', '*') is None
    box.dg.additional = [box.dg.model_copy(deep=True)]
    child = context_for(instance, box, 'primary.additional.0', '0010182')
    assert child.entry_path == 'primary.additional.0'
    assert child.un_number == box.dg.additional[0].un_number


def test_declared_rotation_voyage_identity_changes_with_actual_leg():
    instance = build_instance()
    first = voyage_identity(instance, 0)
    assert first is not None and first == voyage_identity(instance, 0)
    changed = instance.model_copy(deep=True)
    changed.rotation.calls[1].locode = 'SGSIN'
    assert voyage_identity(changed, 0) != first
    assert voyage_identity(instance, 999) is None


def test_voyage_identity_binds_both_declared_condition_identities():
    from stowage.model.voyage import VoyageConditionIdentity, VoyageSpec
    instance = build_instance()
    instance.voyage = VoyageSpec(**{
        phase: VoyageConditionIdentity(
            id=f'condition-{phase}', phase=phase, call_index=index,
            locode=instance.rotation.calls[index].locode, environment_id='environment-1',
            density_id='density-1', water_density_kg_m3=1025.0)
        for index, phase in enumerate(('departure', 'arrival'))})
    original = voyage_identity(instance, 0)
    assert original is not None
    changed = instance.model_copy(deep=True)
    changed.voyage.arrival.id = 'other-arrival'
    assert voyage_identity(changed, 0) != original
    changed = instance.model_copy(deep=True)
    changed.voyage.departure.id = 'other-departure'
    assert voyage_identity(changed, 0) != original
