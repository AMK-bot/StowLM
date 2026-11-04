"""Bounded test-worker transport and a hard subprocess wall for spawn tests.

Queue.get(timeout) bounds readiness, not an in-progress partial pipe frame.
An atomic file has no feeder/receive locks and cannot expose a partial message.
The outer guard also bounds native cleanup, admission and interpreter shutdown.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
_GUARD_ENV = 'STOWLM_BINDING_ISOLATED_TEST_NODE'


@dataclass
class ResultFile:
    path: Path

    def put(self, message):
        path = Path(self.path)
        temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(message))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def close(self):
        pass  # There is no background feeder or connection to join.


def receive_result(worker, output, *, timeout_s):
    deadline = time.monotonic()+timeout_s
    path = Path(output.path)
    while not path.exists():
        exitcode = worker.exitcode
        if exitcode is not None:
            # Publish-before-exit may race the first existence check.
            if path.exists():
                break
            raise AssertionError(f'worker {worker.pid} exited with code {exitcode} without publishing a result')
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f'worker {worker.pid} did not publish a result within {timeout_s:g}s')
        worker.join(timeout=min(.05, remaining))
    message = json.loads(path.read_text())
    worker.join(timeout=max(0., deadline-time.monotonic()))
    if worker.is_alive():
        raise TimeoutError(f'worker {worker.pid} published a result but did not exit within {timeout_s:g}s')
    if worker.exitcode != 0:
        raise AssertionError(f'worker {worker.pid} exited with code {worker.exitcode}: {message.get("error", message)}')
    return message


def stop_worker(worker):
    """Only the exact Process handle created by the calling test."""
    if worker.is_alive():
        worker.terminate()
        worker.join(timeout=.5)
    if worker.is_alive():
        worker.kill()
        worker.join(timeout=1.)
    if worker.is_alive():
        raise AssertionError(f'worker {worker.pid} survived bounded terminate/kill cleanup')


def run_bounded(command, *, timeout_s, log_path, env=None):
    """Own a fresh session so a deadline retires this test and its children."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    with log_path.open('wb') as log:
        worker = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            code = worker.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired as error:
            os.killpg(worker.pid, signal.SIGKILL)  # Exactly this started session.
            worker.wait(timeout=3.)
            raise TimeoutError(f'test subprocess {worker.pid} exceeded hard deadline {timeout_s:g}s; log: {log_path}') from error
    return code, time.monotonic()-began


def subprocess_guard(*, timeout_s):
    """Execute a real-spawn case in a bounded pytest subprocess, fixtures intact."""
    def decorate(function):
        node = str(Path(inspect.getsourcefile(function)).resolve())+'::'+function.__name__
        @wraps(function)
        def guarded(*args, **kwargs):
            if os.environ.get(_GUARD_ENV) == node:
                return function(*args, **kwargs)
            token = function.__name__+'-'+uuid.uuid4().hex
            directory = HERE/'pytest-spawn-guards'/token
            directory.mkdir(parents=True)
            environment = dict(os.environ, **{_GUARD_ENV:node, 'PYTHONDONTWRITEBYTECODE':'1',
                'OMP_NUM_THREADS':'1', 'OPENBLAS_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1',
                'VECLIB_MAXIMUM_THREADS':'1', 'STOWAGE_IN_PORT_WORKERS':'1'})
            command = [sys.executable, '-m', 'pytest', node, '-q', '-m', '',
                '--import-mode=importlib', '--basetemp='+str(directory/'tmp'),
                '-o', 'cache_dir='+str(directory/'cache')]
            log = directory/'pytest.log'
            code, elapsed = run_bounded(command, timeout_s=timeout_s, log_path=log, env=environment)
            assert code == 0, f'isolated worker test failed ({code}, {elapsed:.1f}s): {log}\n'+log.read_text()[-5000:]
        return guarded
    return decorate


class FakeProcess:
    def __init__(self, *, exitcode=None, pid=123):
        self.exitcode, self.pid = exitcode, pid
        self.joins = []
    def join(self, timeout):
        self.joins.append(timeout)
    def is_alive(self):
        return self.exitcode is None


def test_dead_worker_without_result_fails_immediately(tmp_path):
    process = FakeProcess(exitcode=7)
    began = time.monotonic()
    with pytest.raises(AssertionError, match='exited with code 7 without publishing'):
        receive_result(process, ResultFile(tmp_path/'result.json'), timeout_s=300)
    assert time.monotonic()-began < 1.
    assert not process.joins


def test_partial_unpublished_result_does_not_block_on_a_pipe_frame(tmp_path):
    output = ResultFile(tmp_path/'result.json')
    (tmp_path/'result.json.interrupted.tmp').write_text('{"incomplete":')
    with pytest.raises(AssertionError, match='without publishing'):
        receive_result(FakeProcess(exitcode=-9), output, timeout_s=300)


def test_worker_without_message_has_one_absolute_deadline(tmp_path):
    process = FakeProcess()
    with pytest.raises(TimeoutError, match='did not publish'):
        receive_result(process, ResultFile(tmp_path/'result.json'), timeout_s=.01)
    assert process.joins and all(0 <= timeout <= .01 for timeout in process.joins)


def test_published_result_still_requires_worker_exit_before_deadline(tmp_path):
    output = ResultFile(tmp_path/'result.json')
    output.put({'checked': True})
    with pytest.raises(TimeoutError, match='published a result but did not exit'):
        receive_result(FakeProcess(), output, timeout_s=.01)


def test_clean_exited_worker_returns_complete_result(tmp_path):
    output = ResultFile(tmp_path/'result.json')
    output.put({'checked': True})
    assert receive_result(FakeProcess(exitcode=0), output, timeout_s=.1) == {'checked': True}
    assert not list(tmp_path.glob('*.tmp'))


def test_hard_subprocess_deadline_stops_its_own_sleeping_child(tmp_path):
    with pytest.raises(TimeoutError, match='exceeded hard deadline'):
        run_bounded([sys.executable, '-c', 'import time; time.sleep(60)'],
                    timeout_s=.1, log_path=tmp_path/'child.log')


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX partial multiprocessing frame')
def test_dead_child_partial_queue_frame_outlives_get_timeout_but_not_outer_wall(tmp_path):
    # Reproduce the unsafe transport mechanism without touching an external
    # process: a dead sender leaves one frame byte, while the parent's retained
    # writer prevents EOF. Queue.get's timeout only covered initial readiness.
    script = '''
import multiprocessing,os,struct
queue=multiprocessing.get_context('fork').Queue()
pid=os.fork()
if pid==0:
    os.write(queue._writer.fileno(),struct.pack('!i',10000)+b'{')
    os._exit(7)
_,status=os.waitpid(pid,0)
assert os.waitstatus_to_exitcode(status)==7
print('dead sender; entering Queue.get(timeout=.01)',flush=True)
queue.get(timeout=.01)
raise AssertionError('incomplete frame unexpectedly returned')
'''
    log = tmp_path/'partial-frame.log'
    with pytest.raises(TimeoutError, match='exceeded hard deadline'):
        run_bounded([sys.executable, '-c', script], timeout_s=2., log_path=log)
    assert 'dead sender; entering Queue.get(timeout=.01)' in log.read_text()
