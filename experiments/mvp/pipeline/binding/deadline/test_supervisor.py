"""Hard-wall result gates and exact owned groups; no engine work is launched."""
import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from experiments.mvp.pipeline.binding.deadline import supervisor


def checked_event(*, lifts=7., wall_s=.03, received_wall_s=.04, plan_id='checked'):
    return dict(status='checked_plan', passed=True, instruction_recheck_passed=True,
        checker={'passed': True}, rules=[{'instruction_recheck': {'passed': True, 'violations': []}}],
        validated_by='binding_original_instance_full_checker_and_rule_audit',
        plan={'id': plan_id}, lifts=lifts, wall_s=wall_s, received_wall_s=received_wall_s,
        engine_version='0.13.0',
        engine_manifest_sha256='d4febd15222fd783584ddbffb8003d5e281baa7f7de95cb4aa5be7d536f0705b')


def test_predeadline_original_checked_and_rule_audited_event_is_eligible():
    assert supervisor.valid_event(checked_event(), deadline_s=.1)


@pytest.mark.parametrize('field,value', [
    ('status', 'relaxed_plan'), ('passed', False), ('instruction_recheck_passed', False),
    ('checker', {'passed': False}), ('plan', None),
    ('validated_by', None), ('validated_by', 'unchecked_worker_claim'),
    ('rules', [{'instruction_recheck': {'passed': False, 'violations': ['instruction']}}]),
    ('wall_s', .101), ('received_wall_s', .101),
])
def test_late_or_unchecked_or_instruction_failing_events_cannot_be_retained(field, value):
    event = checked_event()
    event[field] = value
    assert not supervisor.valid_event(event, deadline_s=.1)


def test_deadline_retains_cheapest_valid_plan_and_first_valid_received_time():
    events = [checked_event(lifts=2., wall_s=1., received_wall_s=2., plan_id='unaudited'),
              checked_event(lifts=9., wall_s=20., received_wall_s=40., plan_id='first-valid'),
              checked_event(lifts=7., wall_s=50., received_wall_s=60., plan_id='best-valid'),
              checked_event(lifts=1., wall_s=800., received_wall_s=900.1, plan_id='late')]
    events[0]['validated_by'] = None
    row = supervisor.deadline_row({'job_id': 'synthetic-hard-wall'}, events, elapsed_s=900.)
    assert row['status'] == 'deadline_exceeded' and row['passed'] is True
    assert row['plan']['id'] == 'best-valid' and row['lifts'] == 7.
    assert row['first_checked_wall_s'] == 40.


@pytest.mark.parametrize('events', [[], [dict(passed=True, plan={'id': 'unchecked'})]])
def test_deadline_without_valid_incumbent_is_no_live_budget_pass(events):
    row = supervisor.deadline_row({'job_id': 'synthetic-hard-wall'}, events, elapsed_s=900.)
    assert row['status'] == 'deadline_exceeded' and row['passed'] is False
    assert row['live_outcome'] == 'no_plan_inside_live_budget'
    assert row.get('plan') is None and row.get('first_checked_wall_s') is None
    assert not row.get('proved_infeasible', False)


def test_ready_jobs_preserves_ten_existing_rows_and_never_reruns_finished_or_busy_cases(tmp_path):
    medium = [dict(cell='medium', path=f'public-dev-{i}.json', sha256=str(i)*64) for i in range(8)]
    opportunistic = [dict(cell=cell, path=f'public-{cell}.json', sha256='a'*64)
                     for cell in ('tiny', 'small')]
    manifest = dict(selected_medium=medium, opportunistic=opportunistic)
    finished = [dict(cell=record['cell'], map=record['path'], rule_type=kind,
                     passed=bool(index % 2), historical_immutable=True)
                for record in medium[:2] for index, kind in enumerate(supervisor.KINDS)]
    assert len(finished) == 10
    frozen = []
    for i, row in enumerate(finished):
        path = tmp_path / f'historical-{i}.json'
        path.write_text(json.dumps(row, sort_keys=True))
        frozen.append((path, path.read_bytes()))
    busy = [('medium', medium[2]['path'], 'baseline')]
    ready = list(supervisor.ready_jobs(manifest, finished, busy))
    forbidden = {(row['cell'], row['map'], row['rule_type']) for row in finished} | set(busy)
    assert ready and all((row['cell'], row['map'], row['rule_type']) not in forbidden for row in ready)
    assert all(row['cell'] == 'medium' and row['rule_type'] == 'baseline' for row in ready)
    assert len(ready) == 5  # Six missing baselines, one already owned by another slot.
    assert all(path.read_bytes() == original for path, original in frozen)
    assert [json.loads(path.read_bytes()) for path, _ in frozen] == finished
    # Only after the forty medium map/type cells finish can optional sizes enter.
    complete_medium = [dict(cell='medium', map=record['path'], rule_type=kind)
                       for record in medium for kind in supervisor.KINDS]
    optional = list(supervisor.ready_jobs(manifest, complete_medium))
    assert {row['cell'] for row in optional} == {'tiny', 'small'}
    assert all(row['rule_type'] == 'baseline' for row in optional)


@pytest.mark.skipif(not hasattr(os, 'fork') or not hasattr(os, 'killpg'), reason='POSIX owned process groups')
def test_terminate_group_signals_only_the_exact_started_session_and_waits(monkeypatch, tmp_path):
    pid_file = tmp_path / 'owned-pids.json'
    script = (
        'import json,os,time,sys\n'
        'child=os.fork()\n'
        'if child==0:\n'
        ' time.sleep(.2)\n'
        ' os._exit(0)\n'
        'with open(sys.argv[1]+".tmp","w") as stream:\n'
        ' json.dump({"parent":os.getpid(),"child":child,"group":os.getpgrp()},stream)\n'
        'os.replace(sys.argv[1]+".tmp",sys.argv[1])\n'
        'time.sleep(.2)\n'
        'os.waitpid(child,0)\n'
    )
    process = subprocess.Popen([sys.executable, '-c', script, str(pid_file)],
                               start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owned_pid = process.pid
    real_killpg = os.killpg
    calls = []
    def killpg(group, sig):
        calls.append((group, sig))
        return real_killpg(group, sig)
    monkeypatch.setattr(supervisor.os, 'killpg', killpg)
    try:
        limit = time.monotonic()+2.
        while not pid_file.exists() and process.poll() is None and time.monotonic() < limit:
            time.sleep(.005)
        pids = json.loads(pid_file.read_text())
        assert pids['parent'] == pids['group'] == owned_pid
        assert os.getpgid(pids['child']) == owned_pid
        assert owned_pid != os.getpgrp()
        supervisor.terminate_group(process)
        assert process.poll() is not None and process.returncode == -signal.SIGKILL
        assert calls and all(group == owned_pid and sig == signal.SIGKILL for group, sig in calls)
    finally:
        if process.poll() is None:
            real_killpg(owned_pid, signal.SIGKILL)
        process.wait(timeout=2.)


@pytest.mark.skipif(not hasattr(os, 'killpg'), reason='POSIX owned process groups')
def test_daemon_deadline_timer_kills_exact_owned_group_while_main_waits(monkeypatch):
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.2)'],
                               start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    original = os.killpg
    parent_thread = threading.get_ident()
    calls = []
    def killpg(group, sig):
        calls.append((group, sig, threading.get_ident()))
        return original(group, sig)
    monkeypatch.setattr(supervisor.os, 'killpg', killpg)
    timer, state = supervisor.start_deadline(process, .05)
    try:
        assert timer.daemon
        process.wait(timeout=1.)  # No main-loop polling is needed for enforcement.
        timer.join(timeout=1.)
        assert state['fired'] and state['error'] is None
        assert process.returncode == -signal.SIGKILL
        assert calls == [(process.pid, signal.SIGKILL, timer.ident)]
        assert timer.ident != parent_thread
    finally:
        timer.cancel()
        if process.poll() is None:
            original(process.pid, signal.SIGKILL)
        process.wait(timeout=2.)


@pytest.mark.skipif(not hasattr(os, 'killpg'), reason='POSIX owned process groups')
def test_cancelled_timer_never_signals_the_finished_owned_process(monkeypatch):
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.2)'],
                               start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    original = os.killpg
    calls = []
    def killpg(group, sig):
        calls.append((group, sig))
        return original(group, sig)
    monkeypatch.setattr(supervisor.os, 'killpg', killpg)
    timer, state = supervisor.start_deadline(process, .15)
    try:
        timer.cancel()
        timer.join(timeout=1.)
        process.wait(timeout=1.)
        assert process.returncode == 0
        assert not state['fired'] and state['error'] is None and calls == []
    finally:
        timer.cancel()
        if process.poll() is None:
            original(process.pid, signal.SIGKILL)
        process.wait(timeout=2.)
