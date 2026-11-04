"""Synthetic grounding regressions; no evaluation documents or model calls."""
import pytest

from .paper import baseline_paper, grounded, normalize

A = 'MSCU1234567'
B = 'MSCU7654321'


def claim(quote, field, value, **extra):
    return {'row_id': A, 'field': field, 'value': value, 'document': 'paper', 'quote': quote, **extra}


def text(quote, *lines):
    return {'id': 'paper', 'kind': 'text', 'text': '\n'.join((quote, *lines))}


@pytest.mark.parametrize('field,value,quote', [
    ('gross_mass_kg', 12300, f'{B} VGM 12300 kg'),
    ('pod', 'SGSIN', f'{B} POD SGSIN'),
    ('dg.un_number', '1263', f'{B} UN 1263'),
    ('reefer', True, f'{B} REEFER YES'),
])
def test_other_row_cannot_lend_cargo_fact_through_row_quote(field, value, quote):
    anchor = f'{A} received'
    observation = claim(quote, field, value, row_quote=anchor)
    assert not grounded(observation, text(quote, anchor))


def test_container_quote_cannot_mix_another_identity():
    quote = f'{A} and {B} VGM 12300 kg'
    assert not grounded(claim(quote, 'gross_mass_kg', 12300), text(quote))


def test_image_transcription_requires_its_own_row_identity():
    quote = f'{B} VGM 12300 kg'
    observation = claim(quote, 'gross_mass_kg', 12300, row_quote=f'{A} received')
    assert not grounded(observation, {'id':'paper', 'kind':'image'})


@pytest.mark.parametrize('field,value,header', [
    ('booked_vessel', 'SYNTHETIC LANTA', 'Vessel: SYNTHETIC LANTA'),
    ('booking_status', 'booked', 'Status: CONFIRMED'),
])
def test_document_wide_booking_header_can_use_real_single_row_anchor(field, value, header):
    anchor = f'{A} booking line'
    assert grounded(claim(header, field, value, row_quote=anchor), text(header, anchor))
    assert not grounded(claim(header, field, value, row_quote=f'{B} booking line'), text(header, anchor))
    assert not grounded(claim(header, field, value, row_quote=anchor), text(header))


@pytest.mark.parametrize('field,value,label', [
    ('gross_mass_kg', 2200, 'VGM 12000 kg; TARE 2200 kg'),
    ('tare_kg', 12000, 'VGM 12000 kg; TARE 2200 kg'),
    ('gross_mass_kg', 9800, 'VGM 12000 kg; NET 9800 kg'),
    ('net_mass_kg', 12000, 'VGM 12000 kg; NET 9800 kg'),
    ('gross_mass_kg', 30480, 'MAX GROSS MASS 30480 kg'),
    ('max_gross_mass_kg', 12000, 'VGM 12000 kg'),
])
def test_recognized_mass_labels_cannot_borrow_neighboring_number(field, value, label):
    quote = f'{A} {label}'
    assert not grounded(claim(quote, field, value), text(quote))


@pytest.mark.parametrize('field,value,label', [
    ('gross_mass_kg', 12000, 'VGM 12000 kg; TARE 2200 kg'),
    ('tare_kg', 2200, 'VGM 12000 kg; TARE 2200 kg'),
    ('net_mass_kg', 9800, 'VGM 12000 kg; NET 9800 kg'),
    ('max_gross_mass_kg', 30480, 'MAX GROSS MASS 30480 kg; VGM 12000 kg'),
    ('gross_mass_kg', 12000, 'น้ำหนักรวม 12000 kg; น้ำหนักตู้เปล่า 2200 kg'),
])
def test_recognized_mass_labels_keep_correct_reading(field, value, label):
    quote = f'{A} {label}'
    assert grounded(claim(quote, field, value), text(quote))


def test_contradictory_labels_in_one_quote_cannot_endorse_either_value():
    quote = f'{A} VGM 12000 kg; VGM 12300 kg'
    assert not grounded(claim(quote, 'gross_mass_kg', 12000), text(quote))
    assert not grounded(claim(quote, 'gross_mass_kg', 12300), text(quote))


def test_pounds_raw_number_remains_supported_and_converted_once():
    quote = f'{A} VGM 22000 lb; TARE 2200 kg'
    observation = claim(quote, 'gross_mass_kg', 22000, unit='lb')
    assert grounded(observation, text(quote))
    assert normalize(observation) == 9979.03214
    assert next(o['value'] for o in baseline_paper(text(quote)) if o['field']=='gross_mass_kg') == 9979.03214
    assert not grounded(claim(quote, 'gross_mass_kg', 2200, unit='kg'), text(quote))


def test_unknown_thai_wording_stays_literal_for_downstream_corroboration():
    quote = f'{A} ยอดชั่งล่าสุด 12000 kg'
    assert grounded(claim(quote, 'gross_mass_kg', 12000), text(quote))
    assert not grounded(claim(quote, 'gross_mass_kg', 12001), text(quote))


def test_baseline_cannot_call_a_csc_rating_or_tare_vgm():
    quote = f'{A} MAX GROSS MASS 30480 kg; TARE 2200 kg'
    observations = baseline_paper(text(quote))
    assert not any(o['field'] == 'gross_mass_kg' for o in observations)
    assert next(o['value'] for o in observations if o['field'] == 'tare_kg') == 2200


@pytest.mark.parametrize('field,label', [('dg.limited_quantity','LQ'), ('dg.additional.0.marine_pollutant','MP')])
@pytest.mark.parametrize('value', [True,False])
def test_mixed_un_quote_cannot_lend_first_shipment_flag_to_another_entry(field,label,value):
    quote=f'{A} UN 1263 CLASS 3 {label} NO; UN 1824 CLASS 8 {label} YES'
    assert not grounded(claim(quote,field,value),text(quote))


@pytest.mark.parametrize('field,label', [('dg.limited_quantity','LQ'), ('dg.additional.0.marine_pollutant','MP')])
def test_individual_un_entry_excerpt_with_row_identity_keeps_explicit_flag(field,label):
    quote=f'{A} UN 1824 CLASS 8 {label} YES'
    assert grounded(claim(quote,field,True),text(quote))
    assert not grounded(claim(quote,field,False),text(quote))


@pytest.mark.parametrize('field,positive,negative', [
    ('dg.marine_pollutant','เป็นมลพิษทางทะเล','ไม่เป็นมลพิษทางทะเล'),
    ('dg.limited_quantity','ปริมาณจำกัด','ไม่ใช่ปริมาณจำกัด'),
])
def test_distinct_thai_dg_flags_cannot_be_collapsed_by_negative_substring(field,positive,negative):
    mixed=f'{A} UN 1263 {negative}; UN 1824 {positive}'
    assert not grounded(claim(mixed,field,False),text(mixed))
    assert not grounded(claim(mixed,field,True),text(mixed))
    single=f'{A} UN 1263 {negative}'
    assert grounded(claim(single,field,False),text(single))
    assert not grounded(claim(single,field,True),text(single))
