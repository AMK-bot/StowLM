import os
from pathlib import Path
import signal
import sys

import pytest
from experiments.mvp.hybrid import processes
from experiments.mvp.hybrid.processes import run_owned


@pytest.mark.parametrize('mode', ['plan', 'audit'])
def test_watchdog_bounds_each_arm_stage_and_only_owned_group(mode, tmp_path):
    events = []
    policy = dict(planner_s=.1,audit_s=.1,terminate_grace_s=2,kill_grace_s=2)
    with (tmp_path/'output.log').open('w') as stream:
        row = run_owned([sys.executable,'-c','import time;time.sleep(20)'], cwd=tmp_path,
                        env=dict(os.environ), stream=stream, mode=mode, policy=policy,
                        record=lambda name, **data:events.append((name,data)))
    owned = events[0][1]
    assert row['timed_out'] is True and row['returncode'] == -signal.SIGTERM
    assert row['elapsed_s'] < 5 and row['pgid'] == row['pid'] == owned['pid']
    assert all(data.get('pid', owned['pid']) == owned['pid'] for _,data in events)


def test_denied_termination_is_bounded_and_never_targets_an_unowned_group(tmp_path,monkeypatch):
    class Process:
        pid = 99991
        def wait(self,timeout):
            assert timeout is not None
            raise processes.subprocess.TimeoutExpired('owned-test',timeout)
        def poll(self):
            return None
    signals,events = [],[]
    clock = iter(range(100))
    monkeypatch.setattr(processes.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(processes.subprocess,'Popen',lambda *a,**k:Process())
    def denied(group,sig):
        signals.append((group,sig))
        raise PermissionError('sandbox denied exact owned group')
    monkeypatch.setattr(processes.os,'killpg',denied)
    with (tmp_path/'denied.log').open('w') as stream:
        result = run_owned(['owned-test'],cwd=tmp_path,env={},stream=stream,mode='plan',
            policy=dict(planner_s=.1,audit_s=.1,terminate_grace_s=.1,kill_grace_s=.1),
            record=lambda kind,**data:events.append((kind,data)))
    assert result['undrained'] and result['timed_out'] and result['returncode'] is None
    assert signals == [(99991,signal.SIGTERM),(99991,signal.SIGKILL)]
    assert 'PermissionError' in result['termination_error']
