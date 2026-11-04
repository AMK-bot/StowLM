"""Cheap probe tests: no native planning/checking, provider or credentials."""
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.copilot.binding_background_probe import (
    SENTENCE, RULE, make_tools, run_probe,
)
from experiments.mvp.pipeline.copilot.agency_fixtures import FixtureReader
from experiments.mvp.pipeline.copilot.test_tools import tools as synthetic_tools


def test_probe_defaults_use_native_services_and_explicit_stage_ablation():
    t, source = make_tools()
    assert t.checker is None and t.replanner is None and t.job_manager is None
    assert len(t.instance.load_list) == source['load_boxes'] == 13
    assert source['engine_config']['starts'] is False
    assert source['engine_config']['search'] is False
    assert source['engine_config']['finish'] is False
    assert source['engine_config']['bound'] is False
    assert t.planning_options['budget_s'] == 1 and t.planning_options['finalize_s'] == 0
    assert isinstance(t.parser, FixtureReader)
    assert t.parser.answers == {SENTENCE: RULE}


def test_probe_records_responsive_turns_and_keeps_cache_scope_until_worker_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr('experiments.mvp.reader.pipeline.intrinsic_violations', lambda *args: [])
    entered, release, checked = Event(), Event(), Event()
    state = {'cache_active': False, 'cache_closed': False, 'polls': 0}
    @contextmanager
    def cache_scope():
        state['cache_active'] = True
        yield
        assert checked.is_set()
        state.update(cache_active=False, cache_closed=True)
    def replan(*args, **kwargs):
        assert state['cache_active']
        entered.set()
        assert release.wait(2)
        return NS(plan={'native_test_candidate': True}), {'status': 'checked_plan', 'passed': True}
    def check(*args):
        assert state['cache_active']
        checked.set()
        return {'passed': True, 'count': {'weighted_nonproductive': 7}}
    t = synthetic_tools(checker=check, replanner=replan, job_manager=None)
    def pause(seconds):
        state['polls'] += 1
        assert state['cache_active']
        assert entered.wait(1)
        release.set()
        checked.wait(.1)
    record = run_probe(out_dir=tmp_path, tools=t, cache_scope=cache_scope,
                       deadline_s=3, poll_interval_s=.01, pause=pause, announce=lambda _: None)
    assert record['status'] == 'completed' and record['native_completion_proved']
    assert record['accepted_plan'] and record['measured_model_cost_usd'] == '0'
    assert record['native_service_defaults'] is False
    assert state['cache_closed'] and state['polls']
    calls = [call for row in record['turns'] for call in row['turn']['tool_calls']]
    assert [call['name'] for call in calls] == ['read_instruction', 'replan', 'wait_replan', 'wait_replan']
    assert calls[1]['result']['status'] == calls[2]['result']['status'] == 'running'
    assert all(row['wall_s'] < 1 for row in record['turns'])
    assert record['host_confirmation']['planner_confirmed']
    assert (tmp_path / 'started.json').is_file() and (tmp_path / 'result.json').is_file()
    with pytest.raises(FileExistsError):
        run_probe(out_dir=tmp_path, tools=t, cache_scope=cache_scope)


def test_probe_deadline_is_interruption_and_cache_stays_until_cancelled_worker_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr('experiments.mvp.reader.pipeline.intrinsic_violations', lambda *args: [])
    entered, release, finished = Event(), Event(), Event()
    state = {'active': False, 'clock': 0., 'waits': 0}
    @contextmanager
    def cache_scope():
        state['active'] = True
        yield
        assert finished.is_set()
        state['active'] = False
    def replan(*args, **kwargs):
        assert state['active']
        entered.set()
        assert release.wait(2)
        finished.set()
        return None, {'status': 'no_checked_plan', 'passed': False}
    t = synthetic_tools(replanner=replan, job_manager=None)
    def pause(seconds):
        assert entered.wait(1) and state['active']
        state['clock'] += 2
        state['waits'] += 1
        if state['waits'] >= 2:  # First wait expires observation, second drains native work.
            release.set()
            finished.wait(.1)
    record = run_probe(out_dir=tmp_path, tools=t, cache_scope=cache_scope,
                       deadline_s=1, poll_interval_s=.01,
                       clock=lambda: state['clock'], pause=pause, announce=lambda _: None)
    assert record['status'] == 'interrupted' and not record['native_completion_proved']
    assert not record['accepted_plan'] and 'not a proven failure' in record['interruption_reason']
    assert state['waits'] >= 2 and not state['active']
    assert t.job_manager.poll(record['job_id'])['native_running'] is False


@pytest.mark.parametrize('kwargs', [{'deadline_s': 0}, {'deadline_s': float('nan')},
                                  {'poll_interval_s': 46}, {'poll_interval_s': True}])
def test_probe_rejects_unbounded_or_invalid_observation_parameters(tmp_path, kwargs):
    with pytest.raises(ValueError):
        run_probe(out_dir=tmp_path, **kwargs)
    assert not (tmp_path / 'started.json').exists()
