"""Adversarial v7 screens using development geometry, never blind documents."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from stowage.model.instance import Instance
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1_enabled
from stowage.rules.imdg.dgl import dangerous_goods_list
from .cargo_gates import validate_cargo_rows


@pytest.fixture
def cargo():
    path = Path('experiments/mvp/frontier/lab/sets/quick_small.txt').read_text().splitlines()[0]
    base = Instance.model_validate_json(Path(path).read_text())
    original = next(b for b in base.containers if b.id in base.load_list and b.length_ft == 20)
    base.rule_overrides['intake.policy'] = {'this_port': base.port.locode, 'vessel': 'SYNTHETIC LANTA',
        'voyage': '2611S', 'max_gross_mass_kg_by_length': {'20': 30480, '40': 32500, '45': 32500},
        'reefer_setpoint_c': {'min': -35, 'max': 30}}
    fact = {'id': original.id, 'iso_type': '22G1', 'pol': base.port.locode,
        'pod': base.rotation.calls[base.call_index + 1].locode, 'gross_mass_kg': 12000., 'tare_kg': 2200.,
        'max_gross_mass_kg': None, 'empty': False, 'reefer': False, 'reefer_setpoint_c': None,
        'dg': None, 'booked_vessel': 'SYNTHETIC LANTA', 'booking_status': 'confirmed'}
    return base, fact


def reasons(result):
    return {q['reason'] for q in result['questions']}


def test_software_derives_ordinary_row_with_unknown_plate(cargo):
    base, fact = cargo
    result = validate_cargo_rows([fact], base)
    assert not result['questions'], result['questions']
    row = result['rows'][0]
    assert row['length_ft'] == 20 and row['height_m'] == 2.591
    assert row['weight_class'] == load_catalogue().weight_classes.classify(12000)
    masskey = f'mass_kg={float(12000).hex()}' if rule_freeze_v1_enabled() else row['weight_class']
    assert row['category'] == f'{fact["pod"]}:22G1:{masskey}:loaded'
    assert row['max_gross_mass_kg'] is None
    assert {'length_ft', 'height_m', 'category', 'weight_class'} <= {s['field'] for s in result['field_sources'] if s.get('derived')}


@pytest.mark.parametrize('changes,reason', [
    ({'pol': 'THLCH'}, 'wrong_pol'),
    ({'pod': 'USLAX'}, 'pod_not_on_rotation'),
    ({'booked_vessel': 'OTHER FEEDER'}, 'wrong_vessel'),
    ({'booking_status': 'rolled'}, 'booking_not_confirmed'),
    ({'booked_voyage': '2612N'}, 'wrong_voyage'),
    ({'gross_mass_kg': 999999}, 'intake_mass_limit'),
    ({'tare_kg': 12000}, 'implausible_tare'),
    ({'empty': True}, 'empty_mass_mismatch'),
    ({'reefer': True, 'reefer_setpoint_c': -18}, 'reefer_iso_mismatch'),
    ({'reefer': True, 'reefer_setpoint_c': 70}, 'reefer_setpoint_range'),
    ({'reefer': True, 'reefer_setpoint_c': 1000}, 'reefer_setpoint_range'),
    ({'reefer': True}, 'missing_reefer_setpoint'),
    ({'reefer_setpoint_c': -18}, 'nonreefer_setpoint'),
    ({'reefer_setpoint_c': -18, 'reefer': True, 'temperature_unit': 'F'}, 'fahrenheit_setpoint'),
    ({'iso_type': '22T1'}, 'unsupported_equipment'),
    ({'special_stowage': ['under_deck']}, 'reader_referral'),
])
def test_independent_screens_stop_plausible_bad_declarations(cargo, changes, reason):
    base, fact = cargo
    fact.update(changes)
    assert reason in reasons(validate_cargo_rows([fact], base))


def test_other_question_never_excuses_mass_temperature_or_pol(cargo):
    base, fact = cargo
    fact.update(booked_vessel='OTHER', pol='THLCH', gross_mass_kg=999999,
                reefer=True, reefer_setpoint_c=1000)
    assert {'wrong_vessel', 'wrong_pol', 'intake_mass_limit', 'reefer_setpoint_range', 'reefer_iso_mismatch'} <= reasons(validate_cargo_rows([fact], base))


def test_yard_membership_does_not_mean_booked(cargo):
    base, fact = cargo
    base.load_list.remove(fact['id'])
    assert 'not_booked' in reasons(validate_cargo_rows([fact], base))


def test_empty_with_tare_mass_is_clean(cargo):
    base, fact = cargo
    fact.update(empty=True, gross_mass_kg=2200)
    assert not validate_cargo_rows([fact], base)['questions']


@pytest.mark.parametrize('gross', [2200, 2350])
def test_loaded_near_tare_stops_for_empty_confirmation(cargo, gross):
    base, fact = cargo
    fact.update(empty=False, gross_mass_kg=gross)
    result = validate_cargo_rows([fact], base)
    assert 'loaded_mass_mismatch' in reasons(result)
    assert not next(s for s in result['field_sources'] if s['field'] == 'category')['accepted']


def test_derived_weight_label_cannot_authenticate_stopped_mass(cargo):
    base, fact = cargo
    fact['gross_mass_kg'] = 999999
    result = validate_cargo_rows([fact], base)
    assert not next(s for s in result['field_sources'] if s['field'] == 'weight_class')['accepted']


def test_supplied_plate_rating_and_policy_both_checked(cargo):
    base, fact = cargo
    fact.update(gross_mass_kg=15000, max_gross_mass_kg=14000)
    assert 'container_mass_limit' in reasons(validate_cargo_rows([fact], base))
    fact.update(gross_mass_kg=31000, max_gross_mass_kg=35000)
    assert 'intake_mass_limit' in reasons(validate_cargo_rows([fact], base))


def test_no_plate_and_no_declared_ceiling_remains_question(cargo):
    base, fact = cargo
    del base.rule_overrides['intake.policy']['max_gross_mass_kg_by_length']
    assert 'missing_mass_limit' in reasons(validate_cargo_rows([fact], base))


def test_clean_reefer_and_nonoperating_reefer(cargo):
    base, fact = cargo
    fact.update(iso_type='22R1', reefer=True, reefer_setpoint_c=-18)
    # Supply plug capability explicitly for this geometry-only fixture.
    for cell in base.vessel.cells:
        cell.reefer_plug = True
    result = validate_cargo_rows([fact], base)
    assert not result['questions'], result['questions']
    assert result['rows'][0]['reefer_setpoint_c'] == -18
    assert result['rows'][0]['category'] is None
    fact.update(reefer=False, reefer_setpoint_c=None)
    assert not validate_cargo_rows([fact], base)['questions']


def dg_declaration(entry, **kwargs):
    return {'un_number': f'{entry.un_number:04d}', 'imdg_class': entry.hazard_class + (entry.compatibility_group or ''),
            'packing_group': entry.packing_group, 'marine_pollutant': False, 'limited_quantity': False, **kwargs}


def test_clean_mixed_class_two_un_lq_marine_pollutant(cargo):
    base, fact = cargo
    # Avoid port direct-delivery classes: fixture test concerns intake declaration.
    candidates = [e for e in dangerous_goods_list() if e.hazard_class in ('3', '8')]
    first = next(e for e in candidates if e.hazard_class == '3')
    second = next(e for e in candidates if e.hazard_class == '8')
    fact['dg'] = dg_declaration(first, limited_quantity=True, marine_pollutant=True,
                                additional=[dg_declaration(second, marine_pollutant=True)])
    result = validate_cargo_rows([fact], base)
    assert not result['questions'], result['questions']
    dg = result['rows'][0]['dg']
    assert dg['marine_pollutant'] and dg['limited_quantity']
    assert dg['additional'][0]['un_number'] == f'{second.un_number:04d}'
    assert dg['additional'][0]['marine_pollutant']
    assert dg['stowage_category'] == first.stowage_category
    assert dg['ctu_closed'] is None and dg['verified_provisions'] is None
    assert result['rows'][0]['category'] is None


@pytest.mark.parametrize('field', ['marine_pollutant', 'limited_quantity', 'packing_group', 'imdg_class'])
def test_dg_shipment_flags_and_identity_are_never_filled_from_defaults(cargo, field):
    base, fact = cargo
    entry = next(e for e in dangerous_goods_list() if e.hazard_class == '3')
    fact['dg'] = dg_declaration(entry)
    del fact['dg'][field]
    result = validate_cargo_rows([fact], base)
    assert any(q['reason'] == 'missing_dg_declaration' and q['field'] == f'dg.{field}' for q in result['questions'])


def test_un_and_class_and_additional_each_checked(cargo):
    base, fact = cargo
    entry = next(e for e in dangerous_goods_list() if e.hazard_class == '3')
    fact['dg'] = dg_declaration(entry, imdg_class='8', additional=[{
        'un_number': '9999', 'packing_group': 'II', 'imdg_class': '3',
        'marine_pollutant': False, 'limited_quantity': False}])
    result = validate_cargo_rows([fact], base)
    assert {'imdg_class_mismatch', 'unknown_un'} <= reasons(result)
    assert any(q['field'] == 'dg.additional.0' for q in result['questions'])


@pytest.mark.parametrize('field', ['id', 'iso_type', 'pol', 'pod', 'gross_mass_kg', 'empty', 'reefer', 'dg'])
def test_missing_business_fact_never_silently_filled(cargo, field):
    base, fact = cargo
    del fact[field]
    result = validate_cargo_rows([fact], base)
    assert any(q['field'] == field and q['reason'] in ('missing_vgm', 'missing_declaration') for q in result['questions'])


def test_numeric_and_boolean_schema_are_strict(cargo):
    base, fact = cargo
    fact.update(gross_mass_kg=True, tare_kg=float('inf'), empty='false', reefer='no')
    assert {'invalid_mass', 'invalid_boolean'} <= reasons(validate_cargo_rows([fact], base))


def test_duplicate_is_question_not_confirmation(cargo):
    base, fact = cargo
    result = validate_cargo_rows([fact, deepcopy(fact)], base)
    assert 'duplicate_id' in reasons(result)
