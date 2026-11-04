"""Finish reader v3 from the stopped weights, without touching a hashed file.

The stopped run died at row 1,200 because `evaluate.main` calls `check_admission`,
which raises under load, in the child process of a held-out check. This launcher
wraps the frozen recipe rather than editing it:

- admission waits at every check, here in the parent, and a denied child exits
  RETRY_EXIT so the parent waits and tries again;
- the start is adapter-v3 (read only) with a fresh Adam, on exactly the rows the
  stopped run had not reached, in the seed-199 order, same accumulation groups;
- output goes to adapter-v3-final/, checks-final/ and results-final/;
- the hard stop counts active seconds: admission waits don't spend it.

`_v2.run_train` is the frozen loop, unchanged. It sees only the tail rows, so it
counts from zero; this file converts everything it logs to whole-epoch numbers.
Usage: python -m experiments.mvp.reader.v3.finish_run
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from experiments.mvp.reader import train_v2 as _v2
from experiments.mvp.reader.gpu_run import fixed_sample, plateaued
from experiments.mvp.reader.v3 import evaluate, train
from experiments.mvp.reader.v3.gpu_run import ENV, SAMPLE, freeze
from experiments.mvp.reader.v3.train import HERE, ROOT

START = HERE / 'adapter-v3'
FINAL = HERE / 'adapter-v3-final'
CHECKS = HERE / 'checks-final'
RESULTS = HERE / 'results-final'
START_SHA256 = '887d53c23d68130e28c7ed90141f21d2631dc6fedc0430f3662725942e179ea7'
ROWS_DONE = 1200
ROWS_TOTAL = 4121
EARLIER_CHECKS = (0.7625, 0.8875)  # stopped run, iterations 400 and 800; the plateau policy spans the epoch
RETRY_EXIT = 75  # EX_TEMPFAIL: child was denied admission and did no work
ADMISSION_ERRORS = (OSError, ValueError, KeyError, TypeError)  # includes a half-written state file


def log(**fields):
    with (RESULTS / 'gpu-run.jsonl').open('a') as stream:
        stream.write(json.dumps({'at': time.time(), **fields}) + '\n')


def wait_for_admission(check=None, sleep=time.sleep, now=time.monotonic,
                       note=lambda **_: None, poll=10, heartbeat=300):
    """Block until admitted, however long; return the seconds waited. Never raises."""
    check = check or train.check_admission
    began = now()
    next_note = began
    while True:
        try:
            check()
            return now() - began
        except ADMISSION_ERRORS as error:
            if now() >= next_note:
                note(event='admission-wait', waited_seconds=now() - began, denied=str(error))
                next_note = now() + heartbeat
            sleep(poll)


class ActiveClock:
    """time.monotonic minus admission waits, so waiting never spends the hard stop."""

    def __init__(self):
        self.paused = 0.0

    def monotonic(self):
        return time.monotonic() - self.paused

    def wait(self, **options):
        waited = wait_for_admission(note=log, **options)
        self.paused += waited
        return waited


def tail_rows(rows, accumulation, seed, done):
    """Rows the stopped run never reached, in epoch order, same group boundaries."""
    if done % accumulation:
        raise ValueError('continuation must start on a group boundary')
    groups = train.epoch_groups(len(rows), accumulation, seed)
    return [rows[index] for group in groups[done // accumulation:] for index in group]


def sequential_groups(count, accumulation, _seed):
    """Stands in for epoch_groups: tail_rows has already applied the seeded shuffle."""
    return [list(range(i, min(i + accumulation, count))) for i in range(0, count, accumulation)]


class Checker:
    """The frozen Checker's policy with whole-epoch numbers, new paths and waiting."""

    def __init__(self, clock, run=subprocess.run):
        self.clock = clock
        self.run = run
        self.history = list(EARLIER_CHECKS)

    def on_progress(self, *, iteration, optimizer_updates, **fields):
        log(event='train', iteration=iteration + ROWS_DONE,
            optimizer_updates=optimizer_updates + ROWS_DONE // 4, **fields)

    def score(self, folder, output, deadline):
        """Exit code of the check child, or None if the hard stop passed."""
        command = [sys.executable, '-m', 'experiments.mvp.reader.v3.finish_run', '--check-child',
                   '--data', str(SAMPLE), '--backend', 'glimmer', '--adapter', str(folder),
                   '--output', str(output)]
        while True:
            self.clock.wait()
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                return None
            try:
                code = self.run(command, cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL,
                                timeout=remaining).returncode
            except subprocess.TimeoutExpired:
                return None
            if code != RETRY_EXIT:
                return code

    def __call__(self, *, model, iteration, val_loss, deadline):
        absolute = iteration + ROWS_DONE
        log(event='val-loss', iteration=absolute, val_loss=val_loss)
        folder = CHECKS / f'{absolute:06d}'
        folder.mkdir(parents=True, exist_ok=False)
        shutil.copy(FINAL / 'adapter_config.json', folder / 'adapter_config.json')
        train.save_weights(model, folder / 'adapters.safetensors')
        output = folder / 'valid-sample.json'
        code = self.score(folder, output, deadline)
        if code is None:
            return 'hard stop: active seconds spent during held-out check'
        if code:
            # A failed check costs no training: the weights are saved. Go on, and say so.
            log(event='valid-check-failed', iteration=absolute, exit_code=code)
            return None
        result = json.loads(output.read_text())
        if result.get('decoding_policy') != evaluate.DECODING_POLICY or not result.get('schema_constrained'):
            raise ValueError('validation did not use the frozen schema mask')
        self.history.append(result['exact_match'])
        log(event='valid-check', iteration=absolute, exact=result['exact_match'], val_loss=val_loss)
        if plateaued(self.history):
            return 'passed 95 percent and no improvement across two checks'
        return None


def whole_epoch_report(report, admission_wait):
    """run_train counted tail rows; restate its report for the whole epoch."""
    rows = ROWS_DONE + report['rows_completed']
    return {**report, 'rows_completed': rows, 'rows_planned': ROWS_TOTAL,
            'full_epoch_completed': rows == ROWS_TOTAL,
            'optimizer_updates': ROWS_DONE // 4 + report['optimizer_updates'],
            'validation_checks': [{**check, 'iteration': check['iteration'] + ROWS_DONE}
                                  for check in report['validation_checks']],
            'continuation': {'rows_from_stopped_run': ROWS_DONE, 'rows_this_process': report['rows_completed'],
                             'admission_wait_seconds': admission_wait,
                             'elapsed_seconds_counts': 'active seconds of this process only',
                             'optimizer': 'fresh Adam at row 1,200; the stopped run\'s optimizer state was lost'}}


def write_report(path, report):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(path)


def require_start():
    if train.sha256(START / 'adapters.safetensors') != START_SHA256:
        raise ValueError('stopped adapter changed')


def main():
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / 'run.pid').open('x') as stream:  # exclusive: a second launch fails here
        stream.write(f'{os.getpid()}\n')
    clock = ActiveClock()
    clock.wait()
    freeze('recipe', check=True)
    if (HERE / 'results/final-freeze.json').exists():
        raise ValueError('sealed phase started; retraining is forbidden')
    require_start()
    earlier = [json.loads(line) for line in (HERE / 'results/gpu-run.jsonl').read_text().splitlines()]
    last = [row for row in earlier if row.get('event') == 'train'][-1]
    if (last['iteration'], last['optimizer_updates']) != (ROWS_DONE, ROWS_DONE // 4):
        raise ValueError('stopped run did not end on the expected group boundary')
    cfg, model, rows, valid, manifest = train.preflight(destination=FINAL)
    if len(rows) != ROWS_TOTAL:
        raise ValueError('training row count changed')
    expected = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in fixed_sample(valid, n=80, seed=199))
    if SAMPLE.read_text() != expected:
        raise ValueError('fixed validation sample changed')
    tail = tail_rows(rows, cfg['grad_accumulation_steps'], cfg['seed'], ROWS_DONE)
    manifest['continuation'] = {
        'start_adapter': str(START), 'start_adapter_sha256': START_SHA256,
        'rows_skipped': ROWS_DONE, 'groups_skipped': ROWS_DONE // cfg['grad_accumulation_steps'],
        'rows_remaining': len(tail), 'optimizer_policy': 'fresh Adam; stopped-run optimizer state does not exist',
        'hard_stop_policy': f"{cfg['hard_stop_seconds']} active seconds of this process; admission waits excluded",
        'launcher_sha256': train.sha256(__file__)}
    _v2.epoch_groups = sequential_groups
    _v2.time = clock
    checker = Checker(clock)
    log(event='start', **manifest['continuation'])
    report = train.run_train(tail, valid, model, START, FINAL, cfg, manifest, callback=checker,
                             started=clock.monotonic())
    report = whole_epoch_report(report, clock.paused)
    write_report(FINAL / 'STOPPED.json', report)
    if report['full_epoch_completed']:
        write_report(FINAL / 'DONE.json', report)
    freeze('recipe', check=True)
    require_start()
    log(event='stopped', **report)
    print(json.dumps(report, indent=2))


def check_child(arguments):
    """evaluate.main with a denied admission as a clean retry, not a crash."""
    def admit():
        try:
            return train.check_admission()
        except ADMISSION_ERRORS:
            raise SystemExit(RETRY_EXIT)
    evaluate.check_admission = admit
    sys.argv = ['evaluate', *arguments]
    evaluate.main()


if __name__ == '__main__':
    if sys.argv[1:2] == ['--check-child']:
        check_child(sys.argv[2:])
    else:
        main()
