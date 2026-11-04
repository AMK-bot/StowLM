"""Final physical ISO assertions: independent of other row questions or sources."""
import pytest

from .cargo_gates import validate_cargo_rows
from .test_cargo_gates_v7 import cargo


def source(fact,field,value,**extra):
    return {'row_id':fact['id'],'field':field,'value':value,'document':'physical-declaration',
            'source_kind':'paper','accepted':True,**extra}


def fields(result,reason='iso_declaration_conflict'):
    return {q['field'] for q in result['questions'] if q['reason']==reason}


@pytest.mark.parametrize('field,value', [('length_ft',40),('height_m',2.896),
                                       ('tank',True),('equipment_reefer',True)])
def test_explicit_physical_facts_cannot_be_overwritten_by_iso_derivation(cargo,field,value):
    base,fact=cargo
    fact[field]=value
    fact['booked_vessel']='OTHER'
    originals=[source(fact,'iso_type',fact['iso_type']),source(fact,field,value)]
    result=validate_cargo_rows([fact],base,field_sources=originals)
    assert {'iso_type',field} <= fields(result)
    assert any(q['reason']=='wrong_vessel' for q in result['questions'])
    for item in result['field_sources']:
        if item['field'] in ('iso_type',field,'length_ft','height_m','category'):
            assert not item.get('accepted'),item
    assert any(str(value) in str(q['candidates']) for q in result['questions'] if q['field']==field)


def test_operating_reefer_on_dry_iso_questions_both_facts_and_dependent_setpoint(cargo):
    base,fact=cargo
    fact.update(reefer=True,reefer_setpoint_c=-18)
    originals=[source(fact,k,fact[k]) for k in ('iso_type','reefer','reefer_setpoint_c')]
    result=validate_cargo_rows([fact],base,field_sources=originals)
    assert {'iso_type','reefer'} <= fields(result)
    assert any(q['field']=='reefer_setpoint_c' and q['reason']=='reefer_dependency' for q in result['questions'])
    assert not any(s['accepted'] for s in result['field_sources'] if s['field'] in ('iso_type','reefer','reefer_setpoint_c'))


def test_nonoperating_reefer_is_schema_valid_but_explicit_not_reefer_equipment_conflicts(cargo):
    base,fact=cargo
    fact.update(iso_type='22R1',reefer=False,reefer_setpoint_c=None)
    assert not validate_cargo_rows([fact],base)['questions']
    fact['equipment_reefer']=False
    result=validate_cargo_rows([fact],base)
    assert {'iso_type','equipment_reefer'} <= fields(result)


def test_matching_length_height_and_dry_equipment_flags_remain_clean(cargo):
    base,fact=cargo
    fact.update(length_ft=20,height_m=2.591,tank=False,equipment_reefer=False)
    result=validate_cargo_rows([fact],base)
    assert not result['questions'],result['questions']
    assert result['rows'][0]['length_ft']==20 and result['rows'][0]['height_m']==2.591


@pytest.mark.parametrize('field,value', [('length_ft',40),('height_m',2.896),('tank',True),('equipment_reefer',True)])
def test_grounded_source_disagreement_is_checked_when_value_was_withheld_from_fact(cargo,field,value):
    base,fact=cargo
    result=validate_cargo_rows([fact],base,field_sources=[source(fact,field,value,accepted=False,evidence='supplementary_only')])
    assert {'iso_type',field} <= fields(result)


def test_conflicted_iso_sources_are_audited_even_when_iso_missing_from_resolved_fact(cargo):
    base,fact=cargo
    del fact['iso_type']
    fact.update(length_ft=40,height_m=2.591)
    evidence=[source(fact,'iso_type','22G1',accepted=False),source(fact,'iso_type','42G1',accepted=False)]
    result=validate_cargo_rows([fact],base,field_sources=evidence)
    assert {'iso_type','length_ft'} <= fields(result)
    assert any(q['reason']=='cross_document_conflict' and q['field']=='iso_type' for q in result['questions'])
    assert not any(s['accepted'] for s in result['field_sources'] if s['field']=='iso_type')


def test_uncorroborated_grounded_photo_still_surfaces_physical_contradiction(cargo):
    base,fact=cargo
    evidence=[source(fact,'height_m',2.896,accepted=False,source_kind='image')]
    assert {'iso_type','height_m'} <= fields(validate_cargo_rows([fact],base,field_sources=evidence))


@pytest.mark.parametrize('marker', ['unsupported_paper','unsupported_source','unknown_contextual_assessment',
                                   'rejected_model','unverified_transcription','wrong_booking_scope'])
def test_rejected_model_or_wrong_booking_claim_cannot_manufacture_iso_conflict(cargo,marker):
    base,fact=cargo
    evidence=[source(fact,'height_m',2.896,accepted=False,evidence=marker)]
    assert not validate_cargo_rows([fact],base,field_sources=evidence)['questions']


def test_planner_superseded_claim_is_not_resurrected(cargo):
    base,fact=cargo
    fact['length_ft']=20
    evidence=[source(fact,'length_ft',40,accepted=False,superseded_by_planner=True),
              source(fact,'length_ft',20,source_kind='planner',evidence='planner_confirmation')]
    assert not validate_cargo_rows([fact],base,field_sources=evidence)['questions']


def test_closed_world_nonoperating_flag_does_not_claim_physical_dry_equipment(cargo):
    base,fact=cargo
    fact.update(iso_type='22R1',reefer=False,reefer_setpoint_c=None)
    evidence=[source(fact,'reefer',False,source_kind='closed_world',derived=True,evidence='closed_world_declaration')]
    assert not validate_cargo_rows([fact],base,field_sources=evidence)['questions']


def test_bad_iso_relation_does_not_skip_independent_mass_gate(cargo):
    base,fact=cargo
    fact.update(length_ft=40,gross_mass_kg=999999)
    result=validate_cargo_rows([fact],base)
    assert {'iso_type','length_ft'} <= fields(result)
    assert any(q['reason']=='intake_mass_limit' for q in result['questions'])
