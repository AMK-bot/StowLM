"""Swapping the reader does not change the instruction compiler/planner."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from llm.instructions.schema import Refusal
from experiments.mvp.reader.schema import OnePortPerHatch
from experiments.mvp.reader.instruction_planner import plan_sentence


def test_one_sentence_derives_hatch_mapping_without_caller_parameters():
    instance = NS(id='fixture')
    answer = OnePortPerHatch(type='one_port_per_hatch')
    parser = NS(parse=Mock(return_value=(answer, {'backend': 'future-reader'})))
    constrained, result = object(), object()
    with patch('experiments.mvp.reader.instruction_planner.context_from_instance', return_value={'bays': [1]}) as context, \
         patch('experiments.mvp.reader.instruction_planner.compile_constraint', return_value=(constrained, {'cover_ports': {'A':'SGSIN'}})) as compiler, \
         patch('experiments.mvp.reader.instruction_planner.plan_and_check', return_value=(result, {'audit': True})) as planner:
        got, record = plan_sentence(instance, 'One port per hatch.', parser=parser)
    assert got is result and record['passed']
    assert record['status'] == 'checked_plan'
    assert record['sentence_compliance_verified'] is False
    assert record['claim_scope'] == 'parsed_constraint_execution'
    parser.parse.assert_called_once_with('One port per hatch.', {'bays': [1]})
    compiler.assert_called_once_with(instance, answer, workers=1, seed=0)
    assert planner.call_args.args == (instance, constrained)


def test_refusal_never_runs_compiler_or_frontier():
    parser = NS(parse=Mock(return_value=(Refusal(code='loosening', reason='No relaxation'), {})))
    with patch('experiments.mvp.reader.instruction_planner.context_from_instance', return_value={}), \
         patch('experiments.mvp.reader.instruction_planner.compile_constraint') as compiler, \
         patch('experiments.mvp.reader.instruction_planner.plan_and_check') as planner:
        got, record = plan_sentence(NS(), 'Allow mixed ports again.', parser=parser)
    assert got is None and record['refused']
    compiler.assert_not_called()
    planner.assert_not_called()


def test_no_checked_plan_returns_no_fallback():
    parser = NS(parse=Mock(return_value=(OnePortPerHatch(type='one_port_per_hatch'), {})))
    with patch('experiments.mvp.reader.instruction_planner.context_from_instance', return_value={}), \
         patch('experiments.mvp.reader.instruction_planner.compile_constraint', return_value=(object(), {})), \
         patch('experiments.mvp.reader.instruction_planner.plan_and_check', return_value=(None, {'starts': []})) as planner:
        got, record = plan_sentence(NS(), 'One port per hatch.', parser=parser)
    assert got is None and record['passed'] is False
    assert record['status'] == 'no_checked_plan' and record['repaired'] is False
    assert record['planner'] == {'starts': []}
    planner.assert_called_once()


def test_incompatible_fixed_cargo_stops_before_planner():
    from experiments.mvp.reader.constraints import InfeasibleInstruction
    parser = NS(parse=Mock(return_value=(OnePortPerHatch(type='one_port_per_hatch'), {})))
    with patch('experiments.mvp.reader.instruction_planner.context_from_instance', return_value={}), \
         patch('experiments.mvp.reader.instruction_planner.compile_constraint', side_effect=InfeasibleInstruction('retained cargo conflicts')), \
         patch('experiments.mvp.reader.instruction_planner.plan_and_check') as planner:
        got, record = plan_sentence(NS(), 'One port per hatch.', parser=parser)
    assert got is None and record['status'] == 'constraint_unavailable'
    assert record['error'] == 'retained cargo conflicts' and record['passed'] is False
    planner.assert_not_called()
