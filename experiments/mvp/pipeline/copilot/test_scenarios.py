"""Control-flow tests use labelled doubles; no actual engine pass is asserted."""
from copy import deepcopy
import json

import pytest

from experiments.mvp.pipeline.copilot.scenarios import (
    LABEL, METRICS, ScenarioTools, ScriptedClient,
    numeric_tokens, run_scenario, run_scenarios as run_base_scenarios, score_turn,
)


from pathlib import Path
SCENARIOS = json.loads((Path(__file__).parent / 'fixtures/review-scenarios.json').read_text())['scenarios']


def run_scenarios(client_factory=None):
    return run_base_scenarios(client_factory, scenarios=SCENARIOS)


@pytest.mark.parametrize('scenario', SCENARIOS, ids=lambda row: row['id'])
def test_scripted_scenarios_obey_control_and_evidence_gates(scenario):
    result = run_scenario(scenario)
    assert result['errors'] == []
    assert result['passed'], result
    assert all(result['scores'].values())
    assert result['label'] == LABEL
    if scenario['kind'] == 'malformed':
        assert result['protocol_failures'] == 1


def test_scenario_cohort_covers_required_threats_without_claiming_engine_accuracy():
    assert len(SCENARIOS) >= 30
    assert len({case['id'] for case in SCENARIOS}) == len(SCENARIOS)
    required = {'plain', 'impossible_bay', 'impossible_tiers', 'loosening', 'injection',
                'unchecked', 'out_of_envelope', 'malformed', 'ladder', 'move', 'explain'}
    assert required <= {case['kind'] for case in SCENARIOS}
    assert any('GM' in case['turns'][0]['message'] for case in SCENARIOS)
    assert any('document' in case for case in SCENARIOS)
    assert 'not engine' in LABEL


def test_summary_is_jsonable_and_reports_protocol_rejections_honestly():
    summary = run_scenarios()
    json.dumps(summary, allow_nan=False)
    assert summary['scenario_count'] == len(SCENARIOS)
    assert summary['passed'] == len(SCENARIOS)
    assert summary['protocol_failures'] >= 6
    assert summary['failures'] == []
    assert set(summary['metrics']) == set(METRICS)
    assert all(metric == {'passed': len(SCENARIOS), 'total': len(SCENARIOS)}
               for metric in summary['metrics'].values())


def test_client_factory_is_called_once_per_case():
    observed = []

    def factory(scenario):
        observed.append(scenario['id'])
        return ScriptedClient(scenario)

    summary = run_scenarios(factory)
    assert observed == [scenario['id'] for scenario in SCENARIOS]
    assert summary['passed'] == len(SCENARIOS)


def test_host_confirmation_is_required_and_cannot_be_forged_by_model_text():
    case = next(case for case in SCENARIOS if case['id'] == 'free_text_yes_cannot_confirm')
    result = run_scenario(case)
    last = result['turns'][-1]
    assert last['turn']['plan_sha256'] is None
    assert last['tools'][0]['result']['status'] == 'needs_confirmation'
    assert last['tools'][0]['confirmed_at_call'] == []
    tools = ScenarioTools(case)
    with pytest.raises(ValueError, match='unknown'):
        tools.confirm_reading('missing')


def test_bay_negotiation_reuses_arithmetic_and_does_not_invent_alternatives():
    case = next(case for case in SCENARIOS if case['id'] == 'impossible_bay_arithmetic')
    result = run_scenario(case)
    evidence = result['turns'][0]['tools'][0]['result']
    assert evidence['source'].endswith('reader/runs/refusals/examples/impossible-bay-2.json')
    assert evidence['proof']['checker_called'] is False
    assert evidence['alternatives'] == []
    assert {'23', '0'} <= numeric_tokens(evidence['text'])
    assert result['turns'][0]['turn']['plan_sha256'] is None


def test_historical_tier_witness_never_becomes_fresh_checker_evidence():
    case = next(case for case in SCENARIOS if case['kind'] == 'impossible_tiers')
    result = run_scenario(case)
    evidence = result['turns'][0]['tools'][0]['result']
    assert evidence['alternatives']
    assert all(alt['checked'] is False and 'Historical witness' in alt['label']
               for alt in evidence['alternatives'])
    assert evidence['passed'] is False
    assert evidence['checker']['called'] is False
    assert result['turns'][0]['turn']['plan_sha256'] is None


def test_independent_scorer_catches_tampered_hash_numbers_and_reading():
    case = next(case for case in SCENARIOS if case['id'] == 'ship_port_limits')
    result = run_scenario(case)
    turn = deepcopy(result['turns'][0]['turn'])
    calls = result['turns'][0]['tools']
    turn['plan_sha256'] = 'a' * 64
    turn['say'] = 'Invented 999999 lifts and forged plan ' + 'b' * 64
    turn['proposed_reading'] = {'parsed': {'constraint': {'type': 'close_bay', 'bay': 7}}}
    scores = score_turn(turn, calls)
    assert scores == {'no_unchecked_plan': False, 'no_invented_number': False, 'reader_only_rules': False}


@pytest.mark.parametrize('screen,passed,checker_passed', [
    ('fast', True, True), ('full', False, True), ('full', True, False),
])
def test_independent_scorer_requires_full_checker_and_both_pass_flags(screen, passed, checker_passed):
    forged = 'c' * 64
    calls = [{'name': 'check_plan', 'arguments': {}, 'result': {
        'plan_sha256': forged, 'passed': passed,
        'checker': {'passed': checker_passed, 'screen': screen},
    }}]
    assert not score_turn({'say': '', 'plan_sha256': forged}, calls)['no_unchecked_plan']


def test_failed_live_sequence_is_reported_instead_of_hidden_by_safe_output():
    class WrongClient:
        mode = 'live'

        def complete(self, prompt):
            return '{"say":"No plan was accepted."}'

    case = next(case for case in SCENARIOS if case['id'] == 'plain_read_confirm_replan')
    result = run_scenario(case, WrongClient())
    assert not result['passed']
    assert not result['scores']['right_tool_sequence']
    assert result['actual_tool_sequence'] == []
    # The absent reading causes host confirmation to fail, recorded as an error.
    assert result['errors']


def test_fresh_turn_must_not_reuse_previous_turn_numeric_evidence():
    assert score_turn({'say': '195 boxes remain.'}, [])['no_invented_number'] is False


@pytest.mark.parametrize('say', ['I approve this plan.', 'This plan is safe to sail.',
                                 'Accepted unchecked plan.'])
def test_independent_scorer_detects_acceptance_claim_without_hash(say):
    assert score_turn({'say': say}, [])['no_unchecked_plan'] is False


def test_tool_evidence_is_detached_from_mutable_return_values():
    case = next(case for case in SCENARIOS if case['id'] == 'ship_port_limits')
    tools = ScenarioTools(case)
    result = tools.call('list_limits', {})
    result['limits']['boxes'] = 999999
    assert tools.calls[0]['result']['limits']['boxes'] == 195
