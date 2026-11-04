from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import pytest
from llm.instructions.schema import CloseBay,Refusal
from experiments.mvp.reader.pipeline import ParserOutputError
from experiments.mvp.reader.refusal_flow import review_sentence,review_answer


def test_ambiguous_sentence_explains_question_without_guess_or_plan():
    parser=NS(parse=Mock(return_value=(Refusal(code='ambiguous',reason='Unclear'),{'parser':'fixture'})))
    proof={'status':'ambiguous','facts':[],'clarifications':[{'id':'q','text':'Which bay?'}]}
    explainer=NS(explain=Mock(return_value=('Which bay?',{'validated':True})))
    with patch('experiments.mvp.reader.refusal_flow.context_from_instance',return_value={}), \
         patch('experiments.mvp.reader.refusal_flow.build_refusal_evidence',return_value=proof):
        record=review_sentence(NS(), 'Farther away.',parser=parser,explainer=explainer)
    assert record['status']=='ambiguous' and not record['binds']
    assert not record['plan_allowed'] and record['requires_reading_resolution']
    assert record['explanation']=='Which bay?' and record['no_planner_called']


def test_impossible_instruction_stays_hard_and_never_applies_alternative():
    explainer=NS(explain=Mock(return_value=('Needs 23, has 0.',{'validated':True})))
    proof={'status':'static_impossible','alternatives':[{'id':'a','applied':False}]}
    with patch('experiments.mvp.reader.refusal_flow.context_from_instance',return_value={'bays':[2]}), \
         patch('experiments.mvp.reader.refusal_flow.build_refusal_evidence',return_value=proof):
        r=review_answer(NS(),CloseBay(type='close_bay',bay=2),parser_evidence={},explainer=explainer)
    assert r['binds'] and not r['plan_allowed'] and not r['alternative_applied']


def test_unknown_loosened_rule_is_asked_for_not_invented():
    explainer=NS(explain=Mock())
    with patch('experiments.mvp.reader.refusal_flow.context_from_instance',return_value={}):
        r=review_answer(NS(),Refusal(code='loosening',reason='No'),parser_evidence={},explainer=explainer)
    assert r['status']=='needs_rule_details' and not r['binds']
    assert not r['plan_allowed'] and r['requires_reading_resolution']
    assert 'Which existing rule' in r['question']
    explainer.explain.assert_not_called()


def test_malformed_output_does_not_bind_or_acquire_semantics():
    parser=NS(parse=Mock(side_effect=ParserOutputError({'parser':'fixture','raw':'bad'})))
    with patch('experiments.mvp.reader.refusal_flow.context_from_instance',return_value={}):
        r=review_sentence(NS(),'unclear',parser=parser,explainer=NS())
    assert r['status']=='malformed' and not r['binds'] and 'parsed' not in r
    assert not r['plan_allowed'] and r['requires_reading_resolution']
    assert r['parser']=={'parser':'fixture','raw':'bad'}


@pytest.mark.parametrize('code',['ambiguous','unsupported','loosening','not_an_instruction'])
def test_every_rejected_voyage_reading_blocks_new_plan_release(code):
    answer=Refusal(code=code,reason='Reader output remains unchanged.')
    provider={'parser':'fixture','adapter_sha256':'unchanged'}
    parser=NS(parse=Mock(return_value=(answer,provider)))
    explainer=NS(explain=Mock(return_value=('Grounded reason.',{'validated':True})))
    proof={'status':code,'facts':[]}
    with patch('experiments.mvp.reader.refusal_flow.context_from_instance',return_value={}), \
         patch('experiments.mvp.reader.refusal_flow.build_refusal_evidence',return_value=proof):
        record=review_sentence(NS(),'Requested voyage restriction.',parser=parser,
            explainer=explainer,rule_details={'name':'Known rule','description':'Actual supplied rule.'})
    assert record['parsed']['refusal']==answer.model_dump()
    assert record['parser'] is provider
    assert record['plan_allowed'] is False and record['binds'] is False
    assert record['no_planner_called']
    assert record['release_condition']=='explicit_sentence_correction_or_removal'
    assert not record['automatically_applied'] and not record['alternative_applied']


def test_empty_standing_brief_remains_allowed_after_reader_refusal():
    parser=NS(parse=Mock(return_value=(Refusal(code='not_an_instruction',reason='Empty standing text.'),{})))
    explainer=NS(explain=Mock(return_value=('No standing preference.',{})))
    with patch('experiments.mvp.reader.refusal_flow.context_from_instance',return_value={}), \
         patch('experiments.mvp.reader.refusal_flow.build_refusal_evidence',return_value={'status':'not_an_instruction'}):
        record=review_sentence(NS(),'',parser=parser,explainer=explainer,role='standing')
    parser.parse.assert_called_once_with('',{})
    assert record['plan_allowed'] is True and record['binds'] is False
    assert 'requires_reading_resolution' not in record


def test_empty_standing_parse_error_does_not_block_optional_preferences():
    parser=NS(parse=Mock(side_effect=ParserOutputError({'raw':'bad'})))
    with patch('experiments.mvp.reader.refusal_flow.context_from_instance',return_value={}):
        record=review_sentence(NS(),'',parser=parser,explainer=NS(),role='standing')
    assert record['plan_allowed'] is True and record['role']=='standing'


def test_explicit_correction_is_reviewed_as_new_constraint_without_releasing_rejected_record():
    parser=NS(parse=Mock(side_effect=[ParserOutputError({'raw':'bad'}),
                                     (CloseBay(type='close_bay',bay=1),{'parser':'fixture'})]))
    with patch('experiments.mvp.reader.refusal_flow.context_from_instance',return_value={'bays':[1]}), \
         patch('experiments.mvp.reader.refusal_flow.build_refusal_evidence',return_value={'status':'screen_passed'}):
        rejected=review_sentence(NS(),'unclear',parser=parser,explainer=NS())
        corrected=review_sentence(NS(),'Do not load bay 1.',parser=parser,explainer=NS())
    assert rejected['plan_allowed'] is False
    assert corrected['plan_allowed'] is True and corrected['binds'] is True
    assert corrected['parsed']['constraint']=={'type':'close_bay','bay':1}


def test_invalid_role_cannot_bypass_release_gate_on_parser_error():
    parser=NS(parse=Mock(side_effect=ParserOutputError({})))
    with pytest.raises(ValueError,match='Unknown brief role'):
        review_sentence(NS(),'Close a bay.',parser=parser,explainer=NS(),role='unknown')
    parser.parse.assert_not_called()
