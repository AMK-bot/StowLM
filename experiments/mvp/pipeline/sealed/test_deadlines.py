"""Owned process-group wall deadlines; no maps, solver, network or GPU."""
import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.sealed import driver
from experiments.mvp.pipeline.sealed.common import atomic_json, read_json


class HungChild:
    pid = 987654321
    returncode = None
    _sealed_owns_session = True

    def __init__(self):
        self.waits = []
        self.stopped = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        assert timeout is not None, 'A deadline must never perform an unbounded wait'
        self.waits.append(timeout)
        if self.stopped:
            self.returncode = -signal.SIGKILL
            return self.returncode
        raise subprocess.TimeoutExpired(['synthetic-owned-child'], timeout)


def mocked_group(monkeypatch):
    child = HungChild()
    signals = []
    def popen(*args, **kwargs):
        assert kwargs['start_new_session'] is True
        return child
    def killpg(pgid, signum):
        assert pgid == child.pid
        signals.append(signum)
        if child.stopped:
            raise ProcessLookupError('owned group is gone')
        if signum == signal.SIGKILL:
            child.stopped = True
    monkeypatch.setattr(driver.subprocess, 'Popen', popen)
    monkeypatch.setattr(driver.os, 'killpg', killpg)
    return child, signals


@pytest.mark.parametrize('audit', [False, True])
def test_planning_and_audit_timeout_stop_the_owned_group_and_retain_elapsed(monkeypatch, tmp_path, audit):
    child, signals = mocked_group(monkeypatch)
    job = dict(map_id='m', cohort='tiny15', arm='pipeline', mode='dry-run', sha256='a'*64)
    raw = dict(job, map_sha256=job['sha256'], status='no_checked_plan', passed=False, plan=None)
    atomic_json(tmp_path/'row.json', raw)
    if audit:
        row = driver.audit_child(job, tmp_path, timeout_s=.01)
        assert row['status'] == 'error' and row['audit_verified'] is False
        prefix, stem = 'audit', 'audit.'
    else:
        row = driver.run_child(job, tmp_path, timeout_s=.01)
        assert row['status'] == 'deadline_exceeded' and row['harness_error'] is False
        assert row['message'] == 'no plan within the time budget'
        prefix, stem = 'child', ''
    assert row[prefix+'_timeout_s'] == .01
    assert row[prefix+'_elapsed_s'] > 0
    assert row[prefix+'_timed_out'] is True
    assert row[prefix+'_cleanup_failed'] is False
    assert signal.SIGKILL in [sig for sig in signals if sig]
    assert child.waits and all(wait is not None for wait in child.waits)
    receipt = read_json(tmp_path/(stem+'pid.json'))
    assert receipt['pid'] == receipt['pgid'] == child.pid
    assert receipt['start_new_session'] is True
    assert read_json(tmp_path/(stem+'process.json'))[prefix+'_timed_out'] is True


def test_signal_denial_is_explicit_and_never_waits_forever(monkeypatch):
    child = HungChild()
    def denied(*args):
        raise PermissionError('synthetic sandbox signal denial')
    monkeypatch.setattr(driver.os, 'killpg', denied)
    started = time.monotonic()
    result = driver._stop_owned_group(child, grace_s=.01)
    assert time.monotonic()-started < .5
    assert result['cleanup_failed'] is True
    assert any('SIGTERM' in error for error in result['cleanup_errors'])
    assert any('SIGKILL' in error for error in result['cleanup_errors'])
    assert all(wait is not None for wait in child.waits)


def test_disappeared_group_and_macos_zombie_lookup_do_not_escape_cleanup(monkeypatch):
    child = HungChild()
    def gone(*args):
        raise ProcessLookupError('owned process already reaped')
    monkeypatch.setattr(driver.os, 'killpg', gone)
    assert driver._stop_owned_group(child)['cleanup_failed'] is False


def test_cleanup_refuses_an_unowned_group(monkeypatch):
    monkeypatch.setattr(driver.os, 'killpg', lambda *args: pytest.fail('unowned process was signalled'))
    with pytest.raises(ValueError, match='not created by this driver'):
        driver._stop_owned_group(SimpleNamespace(pid=1))


def test_real_deadline_records_new_session_and_targets_grandchildren(monkeypatch, tmp_path):
    ready, marker = tmp_path/'ready.json', tmp_path/'grandchild-finished'
    grandchild = 'import pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(.7); pathlib.Path('+repr(str(marker))+').write_text("finished")'
    script = ('import json,os,pathlib,signal,subprocess,sys,time\n'
              'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
              'child=subprocess.Popen([sys.executable,"-c",'+repr(grandchild)+'])\n'
              'pathlib.Path('+repr(str(ready))+').write_text(json.dumps(dict(pid=os.getpid(),pgid=os.getpgrp(),grandchild=child.pid,grandchild_pgid=os.getpgid(child.pid))))\n'
              'time.sleep(1.5)\n')
    original_popen, original_killpg = subprocess.Popen, os.killpg
    owned, signals = [], []
    def popen(command, **kwargs):
        child = original_popen([sys.executable, '-c', script], **kwargs)
        owned.append(child)
        return child
    def killpg(pgid, signum):
        assert owned and pgid == owned[0].pid
        if signum:
            signals.append((pgid, signum))
        return original_killpg(pgid, signum)
    monkeypatch.setattr(driver.subprocess, 'Popen', popen)
    monkeypatch.setattr(driver.os, 'killpg', killpg)
    try:
        started = time.monotonic()
        with pytest.raises(driver.ChildProcessFailure) as caught:
            driver._owned_process(['synthetic-replaced-command'], tmp_path, timeout_s=.2)
        assert time.monotonic()-started < 1.5
        metadata = read_json(ready)
        assert metadata['pid'] == metadata['pgid'] == metadata['grandchild_pgid'] == owned[0].pid
        assert metadata['grandchild'] != metadata['pid']
        assert metadata['pgid'] != os.getpgrp()
        assert signal.SIGKILL in [sig for _, sig in signals]
        metrics = caught.value.metrics
        assert metrics['child_timed_out'] is True
        if any('PermissionError' in error for error in metrics['child_cleanup_errors']):
            assert metrics['child_cleanup_failed'] is True
        else:
            # Killing only the direct parent leaves the grandchild writing this.
            time.sleep(.35)
            assert not marker.exists()
    finally:
        # Sandbox denial is allowed to return, while this tiny owned test child
        # finishes naturally. Never wait without a bound or discover other PIDs.
        for child in owned:
            child.wait(timeout=3)


def test_timeout_failure_row_keeps_metrics_and_later_trial_is_not_retried(monkeypatch, tmp_path):
    from experiments.mvp.pipeline.sealed.test_review import dry_harness
    run = dry_harness(tmp_path, monkeypatch)
    mocked_group(monkeypatch)
    original_child = driver.run_child
    calls = []
    def child(job, target, timeout_s):
        calls.append(job['arm'])
        if job['arm'] == 'pipeline':
            return original_child(job, target, timeout_s=.01)
        row = dict(map_id=job['map_id'], cohort=job['cohort'], arm=job['arm'],
            map_sha256=job['sha256'], status='no_checked_plan', passed=False,
            full_checker_pass=False, plan=None, wall_s=.01)
        atomic_json(target/'row.json', row)
        return row
    monkeypatch.setattr(driver, 'run_child', child)
    monkeypatch.setattr(driver, 'audit_child', lambda job,target: dict(read_json(target/('deadline-row.json' if job.get('deadline_recovery') else 'row.json')),
        audit_verified=True, audit_fresh_process=True))
    result = driver.run(run, admission_wait=lambda _: {'synthetic_admission': True})
    rows = read_json(run/'rows.json')
    assert calls == ['pipeline', 'twostep']
    assert rows[0]['status'] == 'deadline_exceeded' and rows[0]['child_timed_out'] is True
    assert rows[0]['harness_error'] is False and rows[0]['message'] == 'no plan within the time budget'
    assert rows[0]['child_elapsed_s'] > 0 and rows[0]['timeout_s'] == .01
    assert rows[1]['status'] == 'no_checked_plan'
    assert result['provisional'] is True


def test_development_sample_preserves_full_draw_but_filters_jobs_and_report_population(tmp_path):
    maps = [dict(map_id=f'e{i}', cohort='envelope24', path=f'e{i}', sha256='a'*64)
            for i in range(32)]
    maps += [dict(map_id=f'q{i}', cohort='tiny15', path=f'q{i}', sha256='b'*64) for i in range(6)]
    selected = [f'e{i}' for i in range(0,32,4)]+[f'q{i}' for i in range(6)]
    config = dict(mode='dry-run', maps=maps, dry_sample_map_ids=selected,
                  twostep_budget_s=30, twostep_s=15, twostep_finalize_s=15)
    jobs = list(driver.jobs_for(config, tmp_path))
    assert len(config['maps']) == 38
    assert len(driver.report_maps(config)) == 14
    assert len(jobs) == 44
    assert {item['map_id'] for item in jobs} == set(selected)


@pytest.mark.parametrize('mode,selected,error', [
    ('sealed', ['m'], 'sealed run'),
    ('dry-run', ['m','m'], 'unique list'),
    ('dry-run', ['missing'], 'unknown map'),
    ('dry-run', [], 'unique list'),
])
def test_sample_cannot_substitute_sealed_or_unknown_duplicate_populations(mode, selected, error):
    with pytest.raises(driver.HarnessError, match=error):
        driver.report_maps(dict(mode=mode, maps=[dict(map_id='m')], dry_sample_map_ids=selected))
