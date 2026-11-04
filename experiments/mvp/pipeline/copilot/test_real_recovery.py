"""Cheap scripted recovery tests; injected services are not native evidence."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from experiments.mvp.pipeline.copilot import real_recovery as recovery
from experiments.mvp.pipeline.copilot.agency_fixtures import FixtureReader
from experiments.mvp.pipeline.copilot.tools import ToolSet
from experiments.mvp.reader.test_refusal_evidence import ship


class Scripted:
    mode = 'recorded'

    def __init__(self, *actions):
        self.actions, self.calls = iter(actions), []

    def complete(self, prompt):
        self.calls.append({'status': 'completed', 'cost_usd': '0'})
        return json.dumps(next(self.actions))


@contextmanager
def no_native_cache():
    yield


def fixture(case, *, failed=False, miss=False, fault=False):
    from stowage.model import MovePlan
    instance, _ = ship()
    good = MovePlan(id='synthetic-good', instance_id=instance.id, call_index=0, moves=[])
    bad = MovePlan(id='synthetic-bad', instance_id=instance.id, call_index=0, moves=[])
    def checker(current_instance, plan):
        return {'passed': plan.id != bad.id,
                'count': {'weighted_nonproductive': 7} if plan.id != bad.id else None}
    seed = {'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'close_bay', 'bay': 1}}
    cls = recovery.AdmittedTools if fault else ToolSet
    kwargs = {'admit': lambda: {'synthetic_admission': True}, 'cache_scope': no_native_cache,
              'fail_explainer_once': True} if fault else {}
    toolset = cls(instance, parser=FixtureReader({recovery.SEED: seed}), plan=bad if failed else good,
        checker=checker, lattice=lambda *args: None if miss else {'plan': good},
        sandbox=lambda *args: {'legal': True, 'screen': 'full', 'broken': [], 'gauges': {
            'label': 'Full checker', 'approximation': False, 'full_check_pending': False,
            'checker_passed': True, 'diagnostic_only': False, 'replay_legal': True,
            'replay_executed': 1, 'candidate_moves': 1,
            'frames': [{'error': None, 'stability_passed': True, 'strength_passed': True}]}},
        explainer=lambda *args: {'text': 'Grounded test explanation.',
                                 'statements': [{'id': 'test-statement', 'text': 'Test only.'}]},
        envelope=lambda instance: {'in_envelope': True, 'numbers': {}, 'reasons': []}, **kwargs)
    # Explicit cheap instruction-audit seam: these tests measure orchestration.
    toolset._audit = lambda plan: []
    read = toolset.call('read_instruction', {'sentence': recovery.SEED})
    reading_id = read['proposed_reading']['reading_id']
    assert toolset.confirm_reading(reading_id)['status'] == 'confirmed'
    return toolset, {'native_evidence': False, 'label': 'Explicit unit-test service doubles',
        'references': {'box': 'a', 'slot': '0010102', 'reading_id': reading_id,
                       'rules': toolset.context()['confirmed_rules']}}


def test_recovery_cases_are_goal_based_without_prescribed_routes():
    assert len(recovery.CASES) == 4
    assert len({case['id'] for case in recovery.CASES}) == 4
    assert all('expected_tools' not in case and 'expected_tool_sequence' not in case for case in recovery.CASES)
    assert {'miss_then_verified_evidence', 'failed_check_then_checked_replacement',
            'explainer_fault_then_native_diagnosis', 'two_checked_alternatives_compared'} == {
                case['goal'] for case in recovery.CASES}


def test_model_can_recover_from_lookup_miss_by_checking_existing_plan():
    case = recovery.CASES[0]
    tools, meta = fixture(case, miss=True)
    rules = tools.context()['confirmed_rules']
    result = recovery.run_case(case, Scripted(
        {'tool': 'plan_lookup', 'arguments': {'rules': rules}},
        {'tool': 'check_plan', 'arguments': {}},
        {'say': 'The plan passed the full checker with 7 weighted lifts.'}), factory=lambda ignored: (tools, meta))
    assert result['goal']['achieved']
    assert result['fixture']['native_evidence'] is False
    assert result['goal']['scoring'].endswith('no required tool sequence.')


def test_model_can_choose_explanation_as_another_lookup_miss_recovery():
    case = recovery.CASES[0]
    tools, meta = fixture(case, miss=True)
    result = recovery.run_case(case, Scripted(
        {'tool': 'plan_lookup', 'arguments': {'rules': tools.context()['confirmed_rules']}},
        {'tool': 'explain_box', 'arguments': {'box': 'a'}},
        {'say': 'Grounded test explanation.'}), factory=lambda ignored: (tools, meta))
    assert result['goal']['achieved']
    assert result['turns'][0]['turn']['tool_calls'][-1]['name'] == 'explain_box'


def test_failed_plan_check_can_recover_through_stored_checked_replacement():
    case = recovery.CASES[1]
    tools, meta = fixture(case, failed=True)
    result = recovery.run_case(case, Scripted(
        {'tool': 'check_plan', 'arguments': {}},
        {'tool': 'plan_lookup', 'arguments': {'rules': tools.context()['confirmed_rules']}},
        {'say': 'The replacement passed the full checker with 7 weighted lifts.'}), factory=lambda ignored: (tools, meta))
    assert result['goal']['failure_observed']
    assert result['goal']['recovery_observed']
    assert result['goal']['achieved']


def test_declared_explainer_fault_can_recover_with_full_move_and_real_service_retry():
    case = recovery.CASES[2]
    tools, meta = fixture(case, fault=True)
    result = recovery.run_case(case, Scripted(
        {'tool': 'explain_box', 'arguments': {'box': 'a'}},
        {'tool': 'try_move', 'arguments': {'box': 'a', 'slot': '0010102'}},
        {'tool': 'explain_box', 'arguments': {'box': 'a'}},
        {'say': 'Grounded test explanation.'}), factory=lambda ignored: (tools, meta))
    assert result['goal']['achieved']
    assert result['goal']['details']['full_move_verdict_observed']
    assert any(row['event'] == 'declared_synthetic_fault' for row in result['service_events'])
    assert result['turns'][0]['turn']['tool_calls'][0]['result']['status'] == 'error'


def test_success_without_observed_failure_does_not_meet_recovery_goal():
    case = recovery.CASES[0]
    tools, meta = fixture(case)
    result = recovery.run_case(case, Scripted(
        {'tool': 'check_plan', 'arguments': {}},
        {'say': 'The plan passed with 7 weighted lifts.'}), factory=lambda ignored: (tools, meta))
    assert not result['goal']['achieved']
    assert not result['goal']['failure_observed']


def test_postgate_safe_output_does_not_hide_unsupported_raw_recovery_claim():
    case = recovery.CASES[0]
    tools, meta = fixture(case, miss=True)
    result = recovery.run_case(case, Scripted(
        {'tool': 'plan_lookup', 'arguments': {'rules': tools.context()['confirmed_rules']}},
        {'tool': 'check_plan', 'arguments': {}},
        {'say': 'I approve this plan with 999 lifts.'}), factory=lambda ignored: (tools, meta))
    assert result['goal']['failure_observed'] and result['goal']['recovery_observed']
    assert not result['goal']['achieved']
    assert not result['goal']['behavior_supported']


def test_incomplete_model_turn_is_not_a_recovery_success():
    case = recovery.CASES[0]
    tools, meta = fixture(case, miss=True)
    class Incomplete(Scripted):
        def complete(self, prompt):
            self.calls.append({'status': 'incomplete', 'cost_usd': '0'})
            raise RuntimeError('Synthetic incomplete response')
    result = recovery.run_case(case, Incomplete(), factory=lambda ignored: (tools, meta))
    assert not result['goal']['achieved']
    assert result['behavior']['not_applicable'] == 1


def test_missing_last_load_constructs_bad_input_without_checking_or_changing_original():
    from stowage.model import MovePlan
    plan = MovePlan.model_validate({'id': 'synthetic', 'instance_id': 'synthetic', 'call_index': 0,
        'moves': [{'kind': 'load', 'call_index': 0, 'container_id': 'QZXU0000009', 'src': 'B001-001-01-01', 'dst': '0010102'},
                  {'kind': 'load', 'call_index': 0, 'container_id': 'QZXU0000014', 'src': 'B001-001-01-02', 'dst': '0030102'}]})
    before = plan.model_dump(mode='json')
    broken = recovery.missing_last_load(plan)
    assert plan.model_dump(mode='json') == before
    assert len(broken.moves) == 1 and broken.actions is None and broken.action_space is None
    assert broken.id.endswith('.omitted-last-load')


def test_store_copy_rejects_unverified_prepared_entry(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.mkdir()
    key = 'a' * 64
    (source / 'catalogue.json').write_text(json.dumps({key: 'b' * 64}))
    (source / (key + '.json')).write_text('{}')
    with pytest.raises(recovery.NativeUnavailable, match='hash'):
        recovery._copy_store(source, destination)


def test_missing_native_gate_blocks_only_dependent_cases_before_paid_client(tmp_path, monkeypatch):
    import llm.spark.client as paid
    paid_client = Mock(side_effect=AssertionError('Unavailable native evidence must not spend'))
    monkeypatch.setattr(paid, 'SparkClient', paid_client)
    value = recovery.run(output_dir=tmp_path / 'out', prepared_dir=tmp_path / 'absent')
    assert value['complete'] and value['goals_achieved'] == 0
    assert len(value['unavailable']) == 4
    assert value['measured_cost_usd'] == '0'
    paid_client.assert_not_called()


def test_existing_recovery_result_replays_without_paid_or_native_calls(tmp_path, monkeypatch):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / 'results.json').write_text('{"complete":true,"replay_only":true}')
    monkeypatch.setattr(recovery, 'make_native_tools', Mock(side_effect=AssertionError('No native replay')))
    value = recovery.run(output_dir=tmp_path)
    assert value == {'complete': True, 'replay_only': True}


def test_started_one_shot_cannot_restart_after_interruption(tmp_path, monkeypatch):
    recovery.freeze(tmp_path)
    (tmp_path / 'started.json').write_text('{}')
    factory = Mock(side_effect=AssertionError('Spent trial must not restart'))
    monkeypatch.setattr(recovery, 'make_native_tools', factory)
    with pytest.raises(FileExistsError):
        recovery.run(output_dir=tmp_path)
    factory.assert_not_called()


def test_background_search_is_explicitly_unavailable_in_this_native_campaign():
    case = recovery.CASES[0]
    tools, _ = fixture(case, fault=True)
    result = tools.call('replan', {'reading_id': 'anything'})
    assert result['status'] == 'unavailable' and not result['passed']
    assert tools._active_job is None


@pytest.mark.parametrize('mutation', ['input_error', 'diagnostic_only', 'missing_frames', 'missing_checker'])
def test_full_label_without_complete_native_diagnostics_is_not_a_goal_success(mutation):
    case = recovery.CASES[2]
    tools, meta = fixture(case, fault=True)
    original = tools.sandbox
    def invalid(*args):
        result = original(*args)
        gauges = result['gauges']
        if mutation == 'input_error':
            gauges['input_error'] = True
        elif mutation == 'diagnostic_only':
            gauges['diagnostic_only'] = True
        elif mutation == 'missing_frames':
            gauges['frames'] = []
        else:
            gauges.pop('checker_passed')
        return result
    tools.sandbox = invalid
    result = recovery.run_case(case, Scripted(
        {'tool': 'explain_box', 'arguments': {'box': 'a'}},
        {'tool': 'try_move', 'arguments': {'box': 'a', 'slot': '0010102'}},
        {'tool': 'explain_box', 'arguments': {'box': 'a'}},
        {'say': 'Grounded test explanation.'}), factory=lambda ignored: (tools, meta))
    assert not result['goal']['achieved']
    assert not result['goal']['details']['full_move_verdict_observed']


def test_failed_native_store_gate_uses_a_real_empty_catalogue_instead_of_skipping_case():
    ready, label = recovery._store_ready(recovery.CASES[1],
        {'baseline': {'lattice_passed': False}}, False, allow_pending_store=False)
    assert ready is False and label == 'native_gate_failed_real_empty_catalogue'


def test_pending_native_gate_requires_explicit_host_empty_catalogue_decision():
    with pytest.raises(recovery.NativeUnavailable, match='pending'):
        recovery._store_ready(recovery.CASES[1], {}, False, allow_pending_store=False)
    ready, label = recovery._store_ready(recovery.CASES[1], {}, False, allow_pending_store=True)
    assert not ready and label == 'native_gate_pending_explicit_real_empty_catalogue'


def test_successful_store_gate_does_not_require_unrelated_costly_gate():
    ready, _ = recovery._store_ready(recovery.CASES[1],
        {'baseline': {'lattice_passed': True}}, True, allow_pending_store=False)
    assert ready
