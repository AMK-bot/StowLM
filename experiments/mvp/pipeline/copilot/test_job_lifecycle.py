"""Job rejection recovery and retained-storage regressions, all synthetic."""
from copy import deepcopy
from itertools import count
import gc
from threading import Event
import weakref

import pytest

from experiments.mvp.pipeline.copilot.test_async_safety import confirmed, toolset
from experiments.mvp.pipeline.copilot.jobs import JobManager, native_guard
from experiments.mvp.pipeline.contract.schema import canonical_json


class ControlledJobs:
    """Explicit host scheduler double with native-payload release support."""
    def __init__(self):
        self.jobs = {}
        self._ids = count()

    def start(self, operation, *, identity=None, context=None):
        job_id = f'lifecycle-job-{next(self._ids)}'
        self.jobs[job_id] = dict(operation=operation, identity=identity, status='running')
        return self.poll(job_id, identity=identity)

    def complete(self, job_id):
        job = self.jobs[job_id]
        job['payload'] = job.pop('operation')()
        job['status'] = 'completed'

    def poll(self, job_id, *, identity=None):
        return dict(job_id=job_id, status=self.jobs[job_id]['status'],
                    text='Synthetic lifecycle job status.', elapsed_s=0., passed=False)

    def result(self, job_id, *, identity=None):
        return self.jobs[job_id]['payload']

    def cancel(self, job_id):
        self.jobs[job_id]['status'] = 'stale'

    def forget(self, job_id):
        return self.jobs.pop(job_id, None) is not None


@pytest.fixture(autouse=True)
def synthetic_instruction_audit(monkeypatch):
    monkeypatch.setattr('experiments.mvp.reader.pipeline.intrinsic_violations', lambda *args: [])


@pytest.mark.parametrize('tamper', ['missing_plan', 'missing_report', 'wrong_digest',
                                  'instruction_failed', 'missing_result', 'invalid_result',
                                  'invalid_payload'])
def test_rejected_certificate_is_terminal_and_allows_a_fresh_request(tamper):
    jobs = ControlledJobs()
    tools = toolset(jobs=jobs)
    reading_id = confirmed(tools)
    started = tools.call('replan', {'reading_id': reading_id})
    job_id = started['job_id']
    jobs.complete(job_id)
    payload = jobs.jobs[job_id]['payload']
    if tamper == 'missing_plan':
        payload['plan'] = None
    elif tamper == 'missing_report':
        payload['report'] = None
    elif tamper == 'wrong_digest':
        payload['plan'] = {'different_plan': True}
    elif tamper == 'instruction_failed':
        payload['turn_result']['instruction_recheck']['passed'] = False
    elif tamper == 'missing_result':
        payload.pop('turn_result')
    elif tamper == 'invalid_result':
        payload['turn_result'] = None
    else:
        jobs.jobs[job_id]['payload'] = []
    rejected = tools.call('wait_replan', {'job_id': job_id})
    assert rejected['status'] == 'error' and not rejected['passed']
    assert 'plan_sha256' not in rejected
    assert tools._active_job is None
    assert job_id not in jobs.jobs
    assert tools.call('wait_replan', {'job_id': job_id}) == rejected
    assert tools.call('check_plan', {})['status'] == 'no_checked_plan'
    fresh = tools.call('replan', {'reading_id': reading_id})
    assert fresh['status'] == 'running' and fresh['job_id'] != job_id
    jobs.complete(fresh['job_id'])
    assert tools.call('wait_replan', {'job_id': fresh['job_id']})['passed']


def test_completed_result_access_exception_discards_job_and_recovers(monkeypatch):
    jobs = ControlledJobs()
    tools = toolset(jobs=jobs)
    reading_id = confirmed(tools)
    job_id = tools.call('replan', {'reading_id': reading_id})['job_id']
    jobs.complete(job_id)
    monkeypatch.setattr(jobs, 'result', lambda *args, **kwargs:
                        (_ for _ in ()).throw(ValueError('SYNTHETIC PRIVATE PROVIDER BODY')))
    rejected = tools.call('wait_replan', {'job_id': job_id})
    assert rejected['status'] == 'error' and tools._active_job is None
    assert 'SYNTHETIC PRIVATE' not in repr(rejected)
    assert job_id not in jobs.jobs
    assert tools.call('replan', {'reading_id': reading_id})['job_id'] != job_id


def finished(manager, job_id):
    for _ in range(20):
        result = manager.wait(job_id, timeout_s=.1)
        if result['status'] != 'running':
            return result
    pytest.fail('Synthetic background operation did not finish')


def test_consumed_publication_releases_native_payload_and_caches_compact_repeat():
    jobs = ControlledJobs()
    bulk = 'NATIVE_REPORT_PAYLOAD' * 10000
    tools = toolset(jobs=jobs, checker=lambda *args: {
        'passed': True, 'count': {'weighted_nonproductive': 7}, 'bulk': bulk})
    reading_id = confirmed(tools)
    job_id = tools.call('replan', {'reading_id': reading_id})['job_id']
    jobs.complete(job_id)
    published = tools.call('wait_replan', {'job_id': job_id})
    assert published['passed'] and tools._report['bulk'] == bulk
    assert job_id not in jobs.jobs
    assert 'NATIVE_REPORT_PAYLOAD' not in canonical_json(tools._job_states)
    assert len(canonical_json(tools._job_states)) < 12000
    assert tools.call('wait_replan', {'job_id': job_id}) == published


def test_repeated_publications_bound_tool_history_and_forget_all_consumed_payloads():
    jobs = ControlledJobs()
    tools = toolset(jobs=jobs)
    tools._job_history_limit = 3
    reading_id = confirmed(tools)
    old_ids = []
    for _ in range(12):
        job_id = tools.call('replan', {'reading_id': reading_id})['job_id']
        old_ids.append(job_id)
        jobs.complete(job_id)
        published = tools.call('wait_replan', {'job_id': job_id})
        assert published['passed']
        assert len(tools._job_states) <= 3
        assert jobs.jobs == {}
    assert tools.call('wait_replan', {'job_id': old_ids[0]})['status'] == 'not_found'
    assert tools.call('wait_replan', {'job_id': old_ids[-1]}) == published


def test_manager_bounds_unconsumed_terminal_history_without_host_polling():
    manager = JobManager()
    manager._history_limit = 3
    ids, handles = [], []
    with native_guard() as acquired:
        assert acquired
        for _ in range(12):
            job_id = manager.start(lambda: {'native_payload': object()})['job_id']
            ids.append(job_id)
            handles.append(manager._get(job_id))
    for job in handles:
        assert job.done.wait(1)
    assert len(manager._jobs) <= 3
    assert manager.poll(ids[0])['status'] == 'not_found'
    assert manager.poll(ids[-1])['status'] == 'completed'


class NativeGraph:
    pass


def test_forget_releases_payload_even_while_dispatcher_is_idle():
    manager = JobManager()
    job_id = manager.start(NativeGraph)['job_id']
    finished(manager, job_id)
    payload = manager.result(job_id)
    reference = weakref.ref(payload)
    del payload
    assert manager.forget(job_id)
    gc.collect()
    assert reference() is None


def test_queued_cancellation_releases_captured_graph_before_worker_can_start():
    manager = JobManager()
    payload = NativeGraph()
    reference = weakref.ref(payload)
    operation = lambda value=payload: value
    with native_guard() as acquired:
        assert acquired
        job_id = manager.start(operation)['job_id']
        manager.cancel(job_id)
        assert manager.forget(job_id)
        del operation, payload
        gc.collect()
        assert reference() is None


def test_stale_executing_job_is_not_forgotten_until_native_work_finishes():
    manager = JobManager()
    entered, release, exited = Event(), Event(), Event()
    def operation():
        entered.set()
        try:
            release.wait(2)
            return NativeGraph()
        finally:
            exited.set()
    job_id = manager.start(operation)['job_id']
    try:
        assert entered.wait(1)
        status = manager.cancel(job_id)
        assert status['status'] == 'stale' and status['native_running']
        assert manager.forget(job_id) is False
    finally:
        release.set()
        assert exited.wait(1)
    with native_guard(blocking=True) as acquired:
        assert acquired
    status = manager.poll(job_id)
    assert status['status'] == 'stale' and not status['result_available']
    assert manager.forget(job_id)


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, None])
def test_history_limits_are_positive_host_owned_integers(limit):
    from experiments.mvp.pipeline.copilot.tools import ToolSet
    from experiments.mvp.reader.test_refusal_evidence import ship
    instance, _ = ship()
    with pytest.raises(ValueError, match='positive integer'):
        JobManager(history_limit=limit)
    with pytest.raises(ValueError, match='positive integer'):
        ToolSet(instance, job_history_limit=limit)
