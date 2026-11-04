"""Synthetic shipment declarations bind to exact contexts, never checker outcomes."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from stowage.checker.rules_imdg import MISSING, STOWAGE, imdg_violations
from stowage.generate import cargo
from stowage.model.cargo import PackingForm
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.scenarios import context_for, provenance_matches
from stowage.rules.imdg.dgl import lookup_dgl
from stowage.rules.imdg.shipment_assessments import assess_sw19
from tests.checker.test_rules_imdg_assessments import _shipment


def _case(un=1333, pg='II'):
    instance, ship, box, _ = _shipment(un, pg)
    box.dg.packing_form = PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS
    box.dg.ctu_closed = True
    key = 'sw19' if un == 3480 else 'h1'
    provenance = dict(document='synthetic shipment fixture', clause='SW19' if key == 'sw19' else 'H1',
                      scenario_id=f'synthetic-{key}-template')
    record = (dict(un_number='3480', battery_condition='intact', transport_purpose='normal',
                   special_provision_applicability={'376': False, '377': False})
              if key == 'sw19' else dict(ctu_weathertight=True, packing_condition='dry'))
    facts = dict(synthetic=True, scope='shipment_entry', template_ids=[provenance['scenario_id']],
                 ctu_closed=True, packing_form=box.dg.packing_form.value,
                 entry_id=lookup_dgl(un, pg).id, un_number=f'{un:04d}', packing_group=pg,
                 **{key: dict(record, provenance=provenance)})
    report = cargo.CargoGenerationReport()
    report.realised = {'n_boxes': 1, 'shipment_scenarios': {box.id: {'primary': facts}}}
    return instance, ship, box, report, key


@pytest.mark.parametrize('un,pg', [(1333, 'II'), (1402, 'II'), (3480, None)])
def test_bound_declarations_clear_real_imdg_path_without_placement_clearance(un, pg):
    instance, ship, box, report, key = _case(un, pg)
    report.realised['shipment_scenarios'][box.id]['primary'] = cargo.declare_entry_scenarios(
        lookup_dgl(un, pg), iso_type=box.iso_type)
    before, audit_before = instance.model_dump_json(), deepcopy(report)
    assert any(key.upper() in violation.message for violation in imdg_violations(instance, ship, call_index=0))
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_shipment_scenarios(instance, report)
    assert bound is not instance
    assert instance.model_dump_json() == before
    assert report == audit_before
    assert imdg_violations(bound, ship, call_index=0) == []
    config = bound.rule_overrides[STOWAGE]
    entry_facts = config['containers'][box.id]['entry_assessments']['primary']
    context = context_for(bound, box, 'primary', ship.placements[0].slot)
    source = entry_facts[key]['provenance']
    record = config['assessment_scenarios'][source['scenario_id']]
    assert provenance_matches(source, context, config['assessment_scenarios'])
    for field, value in asdict(context).items():
        if field != 'slot':
            assert field in record and record[field] == value
    assert record['synthetic'] is True
    assert record['scope'] == 'shipment_entry'
    # Both deck and hold physical footprints are in scope. DG legality remains
    # the checker's job, including H1's actual covered-hold geometry.
    assert {'0010182', '0010102'} <= set(record['allowed_slots'])
    assert 'placement_geometry' not in config
    assert 'voyage' not in entry_facts.get('sw19', {})
    with rule_freeze_v1(enabled=True):
        assert cargo.bind_shipment_scenarios(bound, report).model_dump_json() == bound.model_dump_json()


@pytest.mark.parametrize('key,un,pg', [('h1', 1333, 'II'), ('sw19', 3480, None)])
@pytest.mark.parametrize('field,foreign', [
    ('instance_id', 'other-instance'), ('container_id', 'QZXU9999990'),
    ('entry_path', 'primary.additional.0'), ('vessel_id', 'other-vessel'),
    ('voyage_id', 'other-voyage'), ('call_index', 1), ('un_number', '9999'),
    ('packing_group', 'III'), ('allowed_slots', ['9999982']),
])
def test_foreign_binding_remains_missing_in_real_checker(key, un, pg, field, foreign):
    instance, ship, box, report, _ = _case(un, pg)
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_shipment_scenarios(instance, report)
    config = bound.rule_overrides[STOWAGE]
    source = config['containers'][box.id]['entry_assessments']['primary'][key]['provenance']
    config['assessment_scenarios'][source['scenario_id']][field] = foreign
    assert not provenance_matches(source, context_for(bound, box, 'primary', ship.placements[0].slot),
                                  config['assessment_scenarios'])
    assert any(violation.rule_id == MISSING and key.upper() in violation.message
               for violation in imdg_violations(bound, ship, call_index=0))


def test_off_returns_input_without_reading_manifest():
    instance, _, _, report, _ = _case()
    report.realised['shipment_scenarios'] = 'invalid but inactive'
    with rule_freeze_v1(enabled=False):
        assert cargo.bind_shipment_scenarios(instance, report) is instance


@pytest.mark.parametrize('failure', ['unknown_box', 'unknown_entry', 'no_leg', 'no_slots',
                                    'ctu_conflict', 'existing_facts', 'non_synthetic',
                                    'rejected_report', 'missing_count', 'missing_manifest',
                                    'packing_conflict', 'malformed_h1', 'excess_inventory'])
def test_invalid_binding_raises_without_partial_mutation(failure):
    instance, _, box, report, _ = _case()
    manifest = report.realised['shipment_scenarios']
    if failure == 'unknown_box':
        manifest['QZXU9999990'] = manifest.pop(box.id)
    elif failure == 'unknown_entry':
        manifest[box.id]['primary.additional.0'] = manifest[box.id].pop('primary')
    elif failure == 'no_leg':
        instance.call_index = len(instance.rotation.calls) - 1
    elif failure == 'no_slots':
        instance.vessel.cells = []
    elif failure == 'ctu_conflict':
        manifest[box.id]['primary']['ctu_closed'] = False
    elif failure == 'existing_facts':
        instance.rule_overrides[STOWAGE]['containers'][box.id]['entry_assessments'] = {
            'primary': {'h1': {'packing_condition': 'wet'}}}
    elif failure == 'non_synthetic':
        manifest[box.id]['primary']['synthetic'] = False
    elif failure == 'rejected_report':
        report.rejections = ['vessel cannot support generated bookings']
    elif failure == 'missing_count':
        report.realised.pop('n_boxes')
    elif failure == 'missing_manifest':
        report.realised.pop('shipment_scenarios')
    elif failure == 'packing_conflict':
        manifest[box.id]['primary']['packing_form'] = 'tank_container'
    elif failure == 'malformed_h1':
        manifest[box.id]['primary']['h1'] = 'invalid'
    else:
        report.realised['n_boxes'] = 0
    before = instance.model_dump_json()
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError):
        cargo.bind_shipment_scenarios(instance, report)
    assert instance.model_dump_json() == before


def test_additional_entry_never_inherits_primary_assessment():
    instance, ship, box, report, _ = _case(3480, None)
    box.dg.additional = [box.dg.model_copy(deep=True)]
    with rule_freeze_v1(enabled=True):
        primary_only = cargo.bind_shipment_scenarios(instance, report)
    assert any(v.rule_id == MISSING and 'SW19' in v.message
               for v in imdg_violations(primary_only, ship, call_index=0))
    report.realised['shipment_scenarios'][box.id]['primary.additional.0'] = deepcopy(
        report.realised['shipment_scenarios'][box.id]['primary'])
    with rule_freeze_v1(enabled=True):
        both = cargo.bind_shipment_scenarios(instance, report)
    assert imdg_violations(both, ship, call_index=0) == []
    entries = both.rule_overrides[STOWAGE]['containers'][box.id]['entry_assessments']
    assert entries['primary']['sw19']['provenance']['scenario_id'] != entries['primary.additional.0']['sw19']['provenance']['scenario_id']


@pytest.mark.parametrize('length', [20, 40, 45])
def test_binding_covers_physical_footprints_without_using_dg_legal_shortlist(length):
    instance, _, box, report, _ = _case()
    box.length_ft = length
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = []
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_shipment_scenarios(instance, report)
    record, = bound.rule_overrides[STOWAGE]['assessment_scenarios'].values()
    assert record['allowed_slots']
    assert all(int(slot[:3]) % 2 == (1 if length == 20 else 0) for slot in record['allowed_slots'])
    assert ('0010182' if length == 20 else '0020182') in record['allowed_slots']


@pytest.mark.parametrize('source_un,source_pg,target_un,target_pg', [
    (1333, 'II', 1402, 'II'), (1402, 'I', 1402, 'II'), (1402, 'II', 1402, 'I'),
])
def test_prebinding_foreign_un_or_packing_group_cannot_reauthorize_manifest(
    source_un, source_pg, target_un, target_pg,
):
    instance, _, box, report, _ = _case(source_un, source_pg)
    target = lookup_dgl(target_un, target_pg)
    box.dg.un_number = f'{target_un:04d}'
    box.dg.packing_group = target_pg
    box.dg.imdg_class = target.hazard_class
    box.dg.stowage_category = target.stowage_category
    box.dg.stowage_codes = list(target.stowage_codes)
    box.dg.handling_codes = []
    before = instance.model_dump_json()
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError):
        cargo.bind_shipment_scenarios(instance, report)
    assert instance.model_dump_json() == before


@pytest.mark.parametrize('field', ['entry_id', 'un_number', 'packing_group'])
def test_binding_requires_declared_entry_identity_even_known_null_packing_group(field):
    instance, _, box, report, _ = _case(3480, None)
    report.realised['shipment_scenarios'][box.id]['primary'].pop(field, None)
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError):
        cargo.bind_shipment_scenarios(instance, report)


def _recycling_case():
    instance, ship, box, report, _ = _case(3480, None)
    report.realised['shipment_scenarios'][box.id]['primary'] = cargo.declare_entry_scenarios(
        lookup_dgl(3480), iso_type=box.iso_type, scenario_key='sw19_recycling')
    return instance, ship, box, report


def test_bound_sp377_non_short_voyage_requires_category_c_on_deck():
    instance, ship, box, report = _recycling_case()
    before, report_before = instance.model_dump_json(), deepcopy(report)
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_shipment_scenarios(instance, report)
    assert instance.model_dump_json() == before
    assert report == report_before
    config = bound.rule_overrides[STOWAGE]
    facts = config['containers'][box.id]['entry_assessments']['primary']
    context = context_for(bound, box, 'primary', ship.placements[0].slot)
    for record in (facts['sw19'], facts['sw19']['voyage']):
        assert provenance_matches(record['provenance'], context, config['assessment_scenarios'])
    result = assess_sw19(facts, un_number=box.dg.un_number, context=context,
                        registry=config['assessment_scenarios'])
    assert result.compliant is True and result.category == 'C'
    assert imdg_violations(bound, ship, call_index=0) == []
    ship.placements[0].slot = '0010102'
    assert any(v.rule_id == STOWAGE and 'category C requires on-deck' in v.message
               for v in imdg_violations(bound, ship, call_index=0))
    # The voyage classification is an explicit scenario declaration; the binder
    # does not add or overwrite the model's physical or voyage fields.
    assert next(c for c in bound.containers if c.id == box.id).dg.short_international_voyage is None


def test_bound_recycling_voyage_cannot_be_replayed_from_a_foreign_leg():
    instance, ship, box, report = _recycling_case()
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_shipment_scenarios(instance, report)
    config = bound.rule_overrides[STOWAGE]
    facts = config['containers'][box.id]['entry_assessments']['primary']
    source = facts['sw19']['voyage']['provenance']
    config['assessment_scenarios'][source['scenario_id']]['voyage_id'] = 'another-voyage'
    result = assess_sw19(facts, un_number=box.dg.un_number,
                        context=context_for(bound, box, 'primary', ship.placements[0].slot),
                        registry=config['assessment_scenarios'])
    assert result.compliant is None and result.category is None
    assert any(v.rule_id == MISSING and 'SW19' in v.message
               for v in imdg_violations(bound, ship, call_index=0))


@pytest.mark.parametrize('model_short', [None, True])
def test_bound_sp377_short_voyage_preserves_base_category_and_allows_hold(model_short):
    instance, ship, box, report = _recycling_case()
    box.dg.short_international_voyage = model_short
    declaration = report.realised['shipment_scenarios'][box.id]['primary']
    voyage = declaration['sw19']['voyage']
    previous_id = voyage['provenance']['scenario_id']
    voyage.update(short_international_voyage=True, provenance=dict(
        document='synthetic short-voyage fixture declaration',
        clause='explicit IMDG short international voyage classification',
        scenario_id='synthetic-sp377-short-voyage'))
    declaration['template_ids'] = [voyage['provenance']['scenario_id'] if key == previous_id else key
                                   for key in declaration['template_ids']]
    ship.placements[0].slot = '0010102'
    with rule_freeze_v1(enabled=True):
        bound = cargo.bind_shipment_scenarios(instance, report)
    config = bound.rule_overrides[STOWAGE]
    facts = config['containers'][box.id]['entry_assessments']['primary']
    context = context_for(bound, box, 'primary', ship.placements[0].slot)
    assert provenance_matches(facts['sw19']['voyage']['provenance'], context,
                              config['assessment_scenarios'])
    result = assess_sw19(facts, un_number=box.dg.un_number, context=context,
                        registry=config['assessment_scenarios'])
    assert result.compliant is True
    assert result.category == lookup_dgl(3480).stowage_category == 'A'
    assert imdg_violations(bound, ship, call_index=0) == []
    assert next(c for c in bound.containers if c.id == box.id).dg.short_international_voyage is model_short


@pytest.mark.parametrize('invalid', [
    'not_mapping', 'integer_false', 'string_false', 'missing_provenance', 'missing_classification',
    'contradictory_model', 'contradictory_legacy',
])
def test_recycling_binding_rejects_unknown_or_contradictory_voyage_declarations(invalid):
    instance, _, box, report = _recycling_case()
    record = report.realised['shipment_scenarios'][box.id]['primary']['sw19']
    if invalid == 'not_mapping':
        record['voyage'] = None
    elif invalid == 'integer_false':
        record['voyage']['short_international_voyage'] = 0
    elif invalid == 'string_false':
        record['voyage']['short_international_voyage'] = 'false'
    elif invalid == 'missing_provenance':
        record['voyage'].pop('provenance')
    elif invalid == 'missing_classification':
        record['voyage'].pop('short_international_voyage')
    elif invalid == 'contradictory_model':
        box.dg.short_international_voyage = True
    else:
        instance.rule_overrides[STOWAGE]['containers'][box.id]['short_international_voyage'] = True
    before, report_before = instance.model_dump_json(), deepcopy(report)
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError):
        cargo.bind_shipment_scenarios(instance, report)
    assert instance.model_dump_json() == before
    assert report == report_before


def test_onboard_generated_manifest_binds_final_validated_instance_without_changing_facts():
    from random import Random
    from stowage.model.instance import Instance

    instance, _, _, _, _ = _case()
    params = deepcopy(cargo._params(None))
    equipment = next(row for row in params['equipment'] if row['id'] == 'dry_20')
    params['equipment'] = [equipment]
    equipment['empty_share']['value'] = 0
    params['dg']['share_of_eligible_loaded_boxes']['value'] = 1
    params['dg']['classes'] = [row for row in params['dg']['classes'] if row['id'] == '4.1']
    params['dg']['classes'][0]['un_numbers'] = [{'un_number': '1333', 'share': {'value': 1}}]
    params['special_stowage']['share_of_loaded_boxes']['value'] = 0
    report = cargo.CargoGenerationReport()
    with rule_freeze_v1(enabled=True):
        box, = cargo.generate_onboard_cargo(instance.vessel, 'straits', 0, seed=731,
                                           n_boxes=1, params=params, report=report)
        payload = instance.model_dump(mode='json')
        payload.update(stage='B', containers=[box.model_dump(mode='json')], load_list=[], discharge_list=[],
                       yard={'yard_id': instance.port.yard.id}, rule_overrides={},
                       rotation=cargo._route('straits', params, Random(731))[0].model_dump(mode='json'))
        payload['onboard']['placements'] = [{'slot': '0010182', 'container_id': box.id}]
        payload['onboard']['tank_masses_kg'] = {}  # _shipment's isolated fixture has no tanks.
        finalized = Instance.model_validate(payload)
        facts_before, report_before = finalized.containers[0].model_dump_json(), deepcopy(report)
        bound = cargo.bind_shipment_scenarios(finalized, report)
    assert box.id.startswith('QZYU')
    assert bound.stage == 'B'
    assert bound.containers[0].model_dump_json() == facts_before
    assert report == report_before
    assert imdg_violations(bound, bound.onboard, call_index=0) == []
