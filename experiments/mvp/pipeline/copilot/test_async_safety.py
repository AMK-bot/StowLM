"""Async publication regressions. All checker/planner services are test doubles.

No native engine, native checker, paid model, or evaluation cohort is used here.
"""
from copy import deepcopy
import json
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from llm.instructions.schema import CloseBay, Refusal
from experiments.mvp.pipeline.contract.schema import Mode, sha256_json
from experiments.mvp.pipeline.copilot.agent import Copilot
from experiments.mvp.pipeline.copilot.authorization import ActionGrant, TurnRequest
from experiments.mvp.pipeline.copilot.jobs import JobManager
from experiments.mvp.pipeline.copilot.safety import finish_turn, numbers_supported
from experiments.mvp.pipeline.copilot.tools import ToolSet
from experiments.mvp.reader.test_refusal_evidence import ship


class Scripted:
    mode = Mode.recorded

    def __init__(self, *actions):
        self.actions = iter(actions)
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(next(self.actions))


class DeferredJobs:
    """Explicit synthetic scheduler; complete() runs only cheap injected doubles."""

    def __init__(self):
        self.jobs = {}
        self._next_job = 0

    def start(self, operation, *, identity=None, context=None):
        job_id = 'synthetic-job-' + str(self._next_job)
        self._next_job += 1
        self.jobs[job_id] = {'identity': identity, 'operation': operation, 'status': 'running'}
        return self.poll(job_id, identity=identity)

    def poll(self, job_id, *, identity=None):
        job = self.jobs[job_id]
        assert identity == job['identity']
        return {'job_id': job_id, 'status': job['status'], 'elapsed_s': 1.25,
                'text': 'Synthetic job status. Measured elapsed time: 1.25 seconds.',
                'passed': False}

    def complete(self, job_id):
        job = self.jobs[job_id]
        job['payload'] = job['operation']()
        job['status'] = 'completed'

    def result(self, job_id, *, identity=None):
        job = self.jobs[job_id]
        assert identity == job['identity']
        assert job['status'] == 'completed'
        return job['payload']

    def cancel(self, job_id):
        self.jobs[job_id]['status'] = 'stale'

    def forget(self, job_id):
        return self.jobs.pop(job_id, None) is not None


def toolset(*, jobs=None, checker=None, replanner=None):
    instance, _ = ship()  # Existing tiny synthetic unit-test vessel, not a cohort.
    answer = CloseBay(type='close_bay', bay=1)
    empty = Refusal(code='not_an_instruction', reason='Synthetic empty preference')
    reader = SimpleNamespace(parse=Mock(side_effect=lambda sentence, context:
        (answer if sentence else empty, {'parser': 'synthetic-test-reader', 'adapter_sha256': None})))
    return ToolSet(instance, parser=reader, plan={'previous': True},
                   checker=checker or (lambda *args: {'passed': True, 'count': {'weighted_nonproductive': 7}}),
                   replanner=replanner or (lambda *args, **kwargs:
                       (SimpleNamespace(plan={'new': True}), {'status': 'checked_plan', 'passed': True})),
                   envelope=lambda instance: {'in_envelope': True, 'numbers': {}, 'reasons': []},
                   job_manager=jobs if jobs is not None else JobManager())


def confirmed(tools):
    reading = tools.call('read_instruction', {'sentence': 'Keep bay 1 clear'})
    reading_id = reading['proposed_reading']['reading_id']
    assert tools.confirm_reading(reading_id)['status'] == 'confirmed'
    return reading_id


def checked_result():
    return {'status': 'checked_plan', 'passed': True, 'plan_sha256': 'a' * 64,
            'checker': {'passed': True, 'screen': 'full'},
            'text': 'The previous test plan passed with 7 weighted lifts.'}


@pytest.mark.parametrize('status', ['running', 'queued', 'pending', 'stale'])
def test_async_transition_invalidates_earlier_same_turn_acceptance(status):
    previous = checked_result()
    transition = {'status': status, 'passed': False, 'elapsed_s': 1.25,
                  'text': 'Background work has no accepted result. Elapsed 1.25 seconds.'}
    calls = [{'name': 'check_plan', 'arguments': {}, 'result': previous},
             {'name': 'wait_replan', 'arguments': {'job_id': 'synthetic'}, 'result': transition}]
    turn = finish_turn(previous['text'], calls, [previous, transition], Mode.recorded)
    assert turn.plan_sha256 is None
    assert turn.say == transition['text']
    assert '7 weighted lifts' not in turn.say
    assert 'plan_sha256' not in turn.tool_calls[0]['result']
    assert turn.tool_calls[0]['result_sha256'] == sha256_json(turn.tool_calls[0]['result'])
    assert numbers_supported(turn.say, [transition])


@pytest.mark.parametrize('tool_name,arguments', [
    ('replan', {'reading_id': 'reader-test'}), ('wait_replan', {'job_id': 'job-test'}),
    ('try_ladder', {}),
])
def test_loop_stops_on_running_and_drops_earlier_checked_hash(tool_name, arguments):
    running = {'status': 'running', 'job_id': 'job-test', 'passed': False,
               'text': 'Synthetic background replan is running. Elapsed 1.25 seconds.', 'elapsed_s': 1.25}

    class SequenceTools:
        def __init__(self):
            self.calls = []

        def context(self):
            return {'synthetic': True}

        def call(self, name, args):
            self.calls.append(name)
            return checked_result() if name == 'check_plan' else running

    tools = SequenceTools()
    model = Scripted({'tool': 'check_plan', 'arguments': {}},
                     {'tool': tool_name, 'arguments': arguments},
                     {'say': 'I approve the unchecked background plan with 999 lifts.'})
    turn = Copilot(tools, model).turn('Check the old plan, then request a fresh replan.',
        request=TurnRequest(actions=(ActionGrant('check_plan', {}), ActionGrant(tool_name, arguments))))
    assert tools.calls == ['check_plan', tool_name]
    assert len(model.prompts) == 2
    assert turn.plan_sha256 is None
    assert turn.say == running['text']
    assert '999' not in turn.say


def test_later_full_check_can_reestablish_acceptance_after_running_barrier():
    running = {'status': 'running', 'passed': False, 'text': 'Work is running.'}
    checked = checked_result()
    turn = finish_turn(checked['text'], [], [running, checked], Mode.recorded)
    assert turn.plan_sha256 == checked['plan_sha256']


@pytest.mark.parametrize('second_completed', [False, True])
def test_old_published_job_cannot_accept_while_new_job_awaits_publication(monkeypatch, second_completed):
    monkeypatch.setattr('experiments.mvp.reader.pipeline.intrinsic_violations', lambda *args: [])
    jobs = DeferredJobs()
    tools = toolset(jobs=jobs)
    reading_id = confirmed(tools)
    first = tools.call('replan', {'reading_id': reading_id})
    jobs.complete(first['job_id'])
    published = tools.call('wait_replan', {'job_id': first['job_id']})
    assert published['passed'] is True
    second = tools.call('replan', {'reading_id': reading_id})
    if second_completed:
        jobs.complete(second['job_id'])
    old_status = tools.call('wait_replan', {'job_id': first['job_id']})
    assert old_status['status'] == 'running'
    assert old_status['job_id'] == second['job_id']
    assert old_status['passed'] is False
    assert 'plan_sha256' not in old_status
    assert tools._active_job == second['job_id']


def test_publication_and_repeat_status_do_no_checker_or_instruction_work(monkeypatch):
    caller = get_ident()
    phases = []
    entered, release = Event(), Event()

    def planner(instance, receipt, **kwargs):
        phases.append(('planner', get_ident()))
        entered.set()
        release.wait(2.)
        return SimpleNamespace(plan={'new': True}), {'status': 'checked_plan', 'passed': True}

    def checker(instance, plan):
        phases.append(('checker', get_ident()))
        return {'passed': True, 'count': {'weighted_nonproductive': 7}}

    def audit(*args):
        phases.append(('instruction_audit', get_ident()))
        return []

    monkeypatch.setattr('experiments.mvp.reader.pipeline.intrinsic_violations', audit)
    tools = toolset(checker=checker, replanner=planner)
    reading_id = confirmed(tools)
    phases.clear()  # Reader static-screen work is separate from the native job.
    old_plan = tools.plan
    progress = tools.call('replan', {'reading_id': reading_id})
    try:
        assert entered.wait(1.)
        assert tools.call('check_plan', {})['status'] == 'running'
        assert phases == [('planner', phases[0][1])]
        assert tools.plan is old_plan and tools._report is None
    finally:
        release.set()
    for _ in range(20):
        progress_state = tools.job_manager.wait(progress['job_id'], timeout_s=.1)
        if progress_state['status'] != 'running':
            break
    assert progress_state['status'] == 'completed'
    assert tools.plan is old_plan
    worker_phases = list(phases)
    assert {name for name, _ in phases} == {'planner', 'checker', 'instruction_audit'}
    assert all(thread != caller for _, thread in phases)
    assert len({thread for _, thread in phases}) == 1
    result = tools.call('wait_replan', {'job_id': progress['job_id']})
    assert result['passed'] is True
    assert tools.call('wait_replan', {'job_id': progress['job_id']}) == result
    assert phases == worker_phases


@pytest.mark.parametrize('tamper', ['plan', 'report', 'digest', 'instruction', 'screen'])
def test_incomplete_background_certificate_cannot_install_plan(monkeypatch, tamper):
    monkeypatch.setattr('experiments.mvp.reader.pipeline.intrinsic_violations', lambda *args: [])
    jobs = DeferredJobs()
    tools = toolset(jobs=jobs)
    reading_id = confirmed(tools)
    previous = tools.plan
    progress = tools.call('replan', {'reading_id': reading_id})
    jobs.complete(progress['job_id'])
    payload = jobs.jobs[progress['job_id']]['payload']
    if tamper in ('plan', 'report'):
        payload[tamper] = None
    elif tamper == 'digest':
        payload['plan'] = {'different': True}
    elif tamper == 'instruction':
        payload['turn_result']['instruction_recheck']['passed'] = False
    else:
        payload['turn_result']['checker']['screen'] = 'fast'
    result = tools.call('wait_replan', {'job_id': progress['job_id']})
    assert result['passed'] is False
    assert 'plan_sha256' not in result
    assert tools.plan is previous and tools._report is None


def test_unresolved_new_reading_blocks_finished_old_job_and_numeric_claim(monkeypatch):
    monkeypatch.setattr('experiments.mvp.reader.pipeline.intrinsic_violations', lambda *args: [])
    jobs = DeferredJobs()
    tools = toolset(jobs=jobs)
    reading_id = confirmed(tools)
    previous = tools.plan
    progress = tools.call('replan', {'reading_id': reading_id})
    jobs.complete(progress['job_id'])
    tools.call('read_instruction', {'sentence': 'A new draft: keep bay 1 clear'})
    model = Scripted({'tool': 'wait_replan', 'arguments': {'job_id': progress['job_id']}},
                     {'say': 'The background plan passed with 999 lifts.'})
    turn = Copilot(tools, model).turn('Inspect the selected old job status.',
        request=TurnRequest(actions=(ActionGrant('wait_replan', {'job_id': progress['job_id']}),)))
    assert turn.plan_sha256 is None
    assert turn.tool_calls[0]['result']['status'] == 'stale'
    assert turn.say == turn.tool_calls[0]['result']['text']
    assert numbers_supported(turn.say, [turn.tool_calls[0]['result']])
    assert tools.plan is previous and tools._report is None
    assert progress['job_id'] not in jobs.jobs


def test_elapsed_numbers_need_current_turn_job_result_not_history():
    running = {'status': 'running', 'elapsed_s': 1.25,
               'text': 'Background job is running. Elapsed 1.25 seconds.', 'passed': False}
    assert finish_turn('Elapsed 999 seconds.', [], [running], Mode.recorded).say == running['text']
    no_source = finish_turn('Elapsed 1.25 seconds.', [], [], Mode.recorded)
    assert '1.25' not in no_source.say
    assert no_source.plan_sha256 is None
