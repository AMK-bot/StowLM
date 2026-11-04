"""Dependency and approximate-tare regressions on development geometry only."""
from copy import deepcopy
import pytest

from .cargo_gates import REPRESENTATIVE_TARE_KG, validate_cargo_rows
from .test_cargo_gates_v7 import cargo, dg_declaration
from stowage.rules.imdg.dgl import dangerous_goods_list


def questions_for(result, field):
    return [q for q in result['questions'] if q['field'] == field]


def sources_for(result, field):
    return [s for s in result['field_sources'] if s['field'] == field]


@pytest.mark.parametrize('flag', [True, None, 'yes', 1])
def test_unsupported_reefer_state_blocks_its_setpoint_even_on_other_question(cargo, flag):
    base, fact = cargo
    fact.update(reefer=flag, reefer_setpoint_c=-18, booked_vessel='OTHER')
    source={'row_id':fact['id'],'field':'reefer_setpoint_c','value':-18,'accepted':True}
    result=validate_cargo_rows([fact],base,field_sources=[source])
    assert any(q['reason']=='reefer_dependency' for q in questions_for(result,'reefer_setpoint_c'))
    assert not sources_for(result,'reefer_setpoint_c')[0]['accepted']
    assert any(q['reason']=='wrong_vessel' for q in result['questions'])


def test_missing_reefer_flag_with_present_or_null_point_is_not_silent(cargo):
    base, fact = cargo
    del fact['reefer']
    for point in (-18,None):
        fact['reefer_setpoint_c']=point
        result=validate_cargo_rows([fact],base)
        assert any(q['reason']=='reefer_dependency' for q in questions_for(result,'reefer_setpoint_c'))


def test_partial_row_does_not_acquire_unsupported_null_setpoint(cargo):
    base, fact = cargo
    del fact['reefer']
    del fact['reefer_setpoint_c']
    result=validate_cargo_rows([fact],base)
    assert 'reefer_setpoint_c' not in result['rows'][0]
    assert 'oog' not in result['rows'][0]
    assert 'reefer' not in result['rows'][0]


@pytest.mark.parametrize('missing', ['limited_quantity','marine_pollutant','packing_group'])
def test_partial_dg_entry_does_not_inject_missing_shipment_or_permission_defaults(cargo, missing):
    base, fact=cargo
    entry=next(e for e in dangerous_goods_list() if e.hazard_class=='3')
    fact['dg']=dg_declaration(entry)
    del fact['dg'][missing]
    sources=[{'row_id':fact['id'],'field':f'dg.{name}','value':value,'accepted':True}
             for name,value in fact['dg'].items()]
    result=validate_cargo_rows([fact],base,field_sources=sources)
    assert any(q['reason']=='incomplete_dg_entry' for q in questions_for(result,'dg'))
    dg=result['rows'][0]['dg']
    assert missing not in dg
    assert 'ctu_closed' not in dg and 'verified_provisions' not in dg
    assert not any(s.get('accepted') for s in result['field_sources'] if s['field'].startswith('dg.'))


def test_incomplete_additional_dg_cannot_gain_defaults_through_its_parent(cargo):
    base,fact=cargo
    entry=next(e for e in dangerous_goods_list() if e.hazard_class=='3')
    extra=dg_declaration(entry)
    del extra['limited_quantity']
    fact['dg']=dg_declaration(entry,additional=[extra])
    result=validate_cargo_rows([fact],base)
    child=result['rows'][0]['dg']['additional'][0]
    assert 'limited_quantity' not in child and 'ctu_closed' not in child
    assert questions_for(result,'dg.additional.0') and questions_for(result,'dg')


def test_unrelated_row_question_keeps_independent_complete_dg_facts(cargo):
    base,fact=cargo
    entry=next(e for e in dangerous_goods_list() if e.hazard_class=='3')
    fact['dg']=dg_declaration(entry)
    fact['booked_vessel']='OTHER'
    result=validate_cargo_rows([fact],base)
    assert not questions_for(result,'dg')
    assert result['rows'][0]['dg']['limited_quantity'] is False
    assert result['rows'][0]['dg']['ctu_closed'] is None


def test_unstated_tare_uses_labelled_synthetic_representative(cargo):
    base,fact=cargo
    del fact['tare_kg']
    result=validate_cargo_rows([fact],base)
    assert not result['questions'],result['questions']
    assert result['rows'][0]['tare_kg']==REPRESENTATIVE_TARE_KG['22G1']
    source=sources_for(result,'tare_kg')[0]
    assert source['derived'] and source['approximate'] and source['accepted']
    assert source['inputs']==['iso_type','length_ft']
    assert source['band_kg']==[1500,4000]
    assert source['policy_source']=='cargo_gates.REPRESENTATIVE_TARE_KG'
    assert 'Not an individual equipment measurement' in source['quote']


@pytest.mark.parametrize('value', [None,0,-1,12000,'2200'])
def test_stated_bad_tare_is_never_replaced(cargo,value):
    base,fact=cargo
    fact['tare_kg']=value
    result=validate_cargo_rows([fact],base)
    assert questions_for(result,'tare_kg')
    assert not any(s.get('derived') for s in sources_for(result,'tare_kg'))


def test_valid_stated_tare_wins_over_representative(cargo):
    base,fact=cargo
    fact['tare_kg']=2300
    result=validate_cargo_rows([fact],base)
    assert not result['questions']
    assert result['rows'][0]['tare_kg']==2300
    assert not sources_for(result,'tare_kg')


def test_derived_values_do_not_accept_blocked_input_sources(cargo):
    base,fact=cargo
    del fact['tare_kg']
    sources=[{'row_id':fact['id'],'field':'iso_type','value':fact['iso_type'],'accepted':False},
             {'row_id':fact['id'],'field':'gross_mass_kg','value':fact['gross_mass_kg'],'accepted':False}]
    result=validate_cargo_rows([fact],base,field_sources=sources)
    for field in ('length_ft','height_m','tare_kg','weight_class','category'):
        assert not sources_for(result,field)[0]['accepted'],field


def test_bad_mass_and_dg_and_reefer_still_all_checked(cargo):
    base,fact=cargo
    entry=next(e for e in dangerous_goods_list() if e.hazard_class=='3')
    fact.update(gross_mass_kg=999999,reefer=True,reefer_setpoint_c=1000,dg=dg_declaration(entry))
    del fact['dg']['limited_quantity']
    result=validate_cargo_rows([fact],base)
    reasons={q['reason'] for q in result['questions']}
    assert {'intake_mass_limit','reefer_iso_mismatch','reefer_dependency','reefer_setpoint_range','incomplete_dg_entry'} <= reasons
