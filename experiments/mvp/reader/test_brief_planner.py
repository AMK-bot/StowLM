from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pytest
from experiments.mvp.reader.public_brief import VoyageBrief, plan_brief, read_brief, execute_brief, Intersection
from experiments.mvp.reader.schema import OnePortPerHatch
from experiments.mvp.reader.pipeline import ParserOutputError
from llm.instructions.schema import CloseBay, Refusal


def parse_fixture(answers):
    return NS(parse=Mock(side_effect=[(a, {'parser':'fixture','adapter_sha256':'a'*64}) if not isinstance(a, Exception) else a for a in answers]))


def test_standing_is_soft_voyage_is_hard():
    parser = parse_fixture([OnePortPerHatch(type='one_port_per_hatch'), CloseBay(type='close_bay', bay=1)])
    result = NS(plan='checked')
    instance = {'id':'x'}
    compiled = NS(instance=instance, forbids=lambda *a: None, violations=lambda p: [])
    with patch('experiments.mvp.reader.public_brief.context_from_instance', return_value={'bays':[1], 'hatch_covers':['A']}), \
         patch('experiments.mvp.reader.public_brief.compile_constraint', return_value=(compiled, {})) as compile_, \
         patch('experiments.mvp.reader.select_binding.per_box_slot_screen', return_value=[]), \
         patch('experiments.mvp.reader.public_brief.plan_and_check', return_value=(result, {})) as plan:
        got, evidence = plan_brief(instance, VoyageBrief('One port per hatch.', ('Close bay 1.',)), parser=parser)
    assert got is result
    assert parser.parse.call_count == 2
    assert compile_.call_count == 1
    assert isinstance(compile_.call_args.args[1], CloseBay)
    assert plan.call_args.kwargs['enable_port_blocked'] is True
    assert isinstance(plan.call_args.args[1], Intersection)
    assert evidence['sentences'][0]['binds'] is False
    assert evidence['sentences'][1]['binds'] is True
    assert evidence['sentences'][1]['parser']['adapter_sha256'] == 'a'*64


def test_refusal_and_malformed_do_not_bind_or_invent_constraint():
    parser = parse_fixture([Refusal(code='not_an_instruction',reason='Empty.'), ParserOutputError({'raw':'invalid'})])
    with patch('experiments.mvp.reader.public_brief.context_from_instance', return_value={}), \
         patch('experiments.mvp.reader.public_brief.compile_constraint') as compile_, \
         patch('experiments.mvp.reader.public_brief.plan_and_check', return_value=(None, {})) as plan:
        _, evidence = plan_brief({'id':'x'}, VoyageBrief('', ('Unclear sentence',)), parser=parser)
    compile_.assert_not_called()
    plan.assert_not_called()
    assert evidence['status'] == 'needs_confirmation'
    assert evidence['plan_released'] is False
    assert [s['status'] for s in evidence['sentences']] == ['refused','malformed']
    assert not any(s['binds'] for s in evidence['sentences'])


def test_empty_brief_still_reads_before_planning():
    parser = parse_fixture([Refusal(code='not_an_instruction', reason='Empty')])
    with patch('experiments.mvp.reader.public_brief.context_from_instance', return_value={}), \
         patch('experiments.mvp.reader.public_brief.plan_and_check', return_value=(None, {})):
        plan_brief({'id':'x'}, VoyageBrief(''), parser=parser)
    parser.parse.assert_called_once_with('', {})
    with pytest.raises(ValueError, match='reader-produced'):
        execute_brief({'id':'x'}, {})


def test_hard_constraints_intersect_and_unavailable_never_falls_back():
    from experiments.mvp.reader.constraints import InfeasibleInstruction
    instance = {'id':'x'}
    parser = parse_fixture([OnePortPerHatch(type='one_port_per_hatch'), CloseBay(type='close_bay',bay=1)])
    with patch('experiments.mvp.reader.public_brief.context_from_instance', return_value={'bays':[1], 'hatch_covers':['A']}), \
         patch('experiments.mvp.reader.public_brief.compile_constraint', side_effect=InfeasibleInstruction('fixed conflict')), \
         patch('experiments.mvp.reader.public_brief.plan_and_check') as plan:
        got, evidence = plan_brief(instance, VoyageBrief('One port per hatch.', ('Close 1',)), parser=parser)
    assert got is None and evidence['status'] == 'constraint_unavailable'
    plan.assert_not_called()
    combined = Intersection(instance, [NS(forbids=lambda *a:'A', violations=lambda p:['A']),
                                       NS(forbids=lambda *a:'B', violations=lambda p:['B'])])
    assert combined.forbids('x','slot') == 'A'
    assert combined.violations('plan') == ['A','B']


def test_parsed_static_impossibility_returns_failure_without_search():
    from llm.instructions.schema import CloseBay
    instance = {'id':'x'}
    compiled = NS(instance=instance, capacity_shortfall=lambda:'no open cells')
    parser = parse_fixture([OnePortPerHatch(type='one_port_per_hatch'), CloseBay(type='close_bay',bay=1)])
    with patch('experiments.mvp.reader.public_brief.context_from_instance', return_value={'bays':[1], 'hatch_covers':['A']}), \
         patch('experiments.mvp.reader.public_brief.compile_constraint', return_value=(compiled, {'method':'static'})), \
         patch('experiments.mvp.reader.public_brief.plan_and_check') as plan:
        brief = read_brief(instance, VoyageBrief(voyage=('Close bay 1',)), parser=parser)
        got, evidence = execute_brief(instance, brief)
    assert got is None and evidence['status'] == 'constraint_unavailable'
    assert evidence['compilation'][0]['method'] == 'static'
    assert evidence['compilation'][0]['source_sentence'] == 'Close bay 1'
    plan.assert_not_called()
