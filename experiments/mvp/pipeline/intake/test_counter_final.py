"""An absent value is not a declaration; unrelated questions do not excuse one."""
import pytest

from experiments.mvp.pipeline.contract.schema import IntakeResult,Question
from .campaign_v7 import business_fields,metrics


A,B='QZXU0000009','QZXU0000014'
DG={'un_number':'1263','imdg_class':'3','packing_group':'III',
    'limited_quantity':False,'marine_pollutant':False}


def q(field=None,row=0):
    return Question(id=f'{row}:{field}',row=row,field=field,reason='fixture_question',prompt='Synthetic blocked reading')


def score(rows,truth,questions=(),status='needs_answers'):
    result=IntakeResult(status=status,rows=rows,questions=list(questions),field_sources=[])
    return metrics(result,{'rows':truth,'traps':[],'clean_control_ids':[]},name='counter regression')


def test_business_fields_never_manufactures_non_dg_for_an_id_only_row():
    assert business_fields({'id':A})=={'id':A}
    assert business_fields({'id':A,'dg':None})=={'id':A,'dg':None}


def test_business_fields_never_manufactures_an_absent_setpoint():
    assert business_fields({'id':A,'reefer':True})=={'id':A,'reefer':True}
    assert business_fields({'id':A,'reefer':True,'reefer_setpoint_c':None})['reefer_setpoint_c'] is None


def test_two_id_only_schema_rejections_do_not_become_two_silent_non_dg_claims():
    scored=score([{'id':A},{'id':B}],[{'id':A,'dg':DG},{'id':B,'dg':DG}],
                 [q(None,None)],status='rejected')
    assert scored['silent_wrong_values']==0
    assert scored['silent_wrong_details']==[]
    assert scored['boxes_stopped']==2 and scored['rows_exact']==0


@pytest.mark.parametrize('question',[q(None),q('pod'),q('sheet:vgm',None)])
def test_explicit_wrong_non_dg_is_still_counted_despite_other_questions(question):
    scored=score([{'id':A,'dg':None}],[{'id':A,'dg':DG}],[question])
    assert scored['silent_wrong_values']==1
    assert scored['silent_wrong_details']==[{'row_id':A,'field':'dg','value':None,
                                            'expected':'not declared by ground truth'}]


def test_absent_dg_does_not_inflate_a_real_wrong_mass_on_a_questioned_row():
    scored=score([{'id':A,'gross_mass_kg':13000}],
                 [{'id':A,'gross_mass_kg':12000,'dg':DG}],[q(None)])
    assert scored['silent_wrong_values']==1
    assert scored['silent_wrong_details']==[{'row_id':A,'field':'gross_mass_kg','value':13000,'expected':12000}]


def test_an_exact_dg_question_blocks_that_explicit_declaration_only():
    scored=score([{'id':A,'dg':None,'gross_mass_kg':13000}],
                 [{'id':A,'dg':DG,'gross_mass_kg':12000}],[q('dg')])
    assert scored['silent_wrong_values']==1
    assert scored['silent_wrong_details'][0]['field']=='gross_mass_kg'


def test_absent_dg_is_missing_coverage_while_correct_explicit_non_dg_is_coverage():
    omitted=score([{'id':A}],[{'id':A,'dg':None}],[q(None)])
    explicit=score([{'id':A,'dg':None}],[{'id':A,'dg':None}],[q(None)])
    assert omitted['silent_wrong_values']==explicit['silent_wrong_values']==0
    assert business_fields({'id':A}).get('dg','missing')=='missing'
    assert 'dg' in business_fields({'id':A,'dg':None})
