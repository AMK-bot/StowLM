import functools
import itertools
import json
import subprocess
from types import SimpleNamespace

import pytest

from experiments.mvp.reader.v3 import evaluate, finish_run, train


def write_state(path, **overrides):
    path.write_text(json.dumps({'mask': 255, 'load1': 10, 't': 1000, **overrides}))


def test_admission_waits_through_every_denial_then_returns(tmp_path):
    state = tmp_path / 'core-state.json'
    # Overloaded, stale, too few cores, half-written, missing: each must wait, not raise.
    denials = [lambda: write_state(state, load1=90), lambda: write_state(state, t=1),
               lambda: write_state(state, mask=7), lambda: state.write_text('{"mask"'), state.unlink]
    denials[0]()
    slept, notes = [], []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) < len(denials):
            denials[len(slept)]()
        else:
            write_state(state)

    check = functools.partial(train.check_admission, state, 1000)
    with pytest.raises(ValueError):
        check()  # the frozen check still raises; only the wrapper waits
    clock = itertools.count(0, 100)
    waited = finish_run.wait_for_admission(check, sleep=sleep, now=lambda: next(clock),
                                           note=lambda **fields: notes.append(fields))
    assert len(slept) == len(denials) and waited > 0
    assert notes and notes[0]['event'] == 'admission-wait'


def test_active_clock_excludes_admission_waits(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(finish_run.time, 'monotonic', lambda: now[0])
    clock = finish_run.ActiveClock()

    def admission(check=None, **_):
        now[0] += 500
        return 500

    monkeypatch.setattr(finish_run, 'wait_for_admission', admission)
    clock.wait()
    assert clock.monotonic() == 100.0


def test_tail_covers_each_row_once_with_the_original_groups():
    rows = list(range(4121))
    order = [row for group in train.epoch_groups(4121, 4, 199) for row in group]
    tail = finish_run.tail_rows(rows, 4, 199, 1200)
    assert tail == order[1200:]
    assert sorted(order[:1200] + tail) == rows
    groups = finish_run.sequential_groups(len(tail), 4, 199)
    assert [tail[i] for i in groups[0]] == train.epoch_groups(4121, 4, 199)[300]
    assert len(groups[-1]) == 1 and sum(map(len, groups)) == 2921
    with pytest.raises(ValueError):
        finish_run.tail_rows(rows, 4, 199, 1201)


def test_child_denied_admission_exits_for_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(train, 'check_admission', lambda: (_ for _ in ()).throw(ValueError('denied')))
    monkeypatch.setattr(evaluate, 'check_admission', evaluate.check_admission)
    monkeypatch.setattr(evaluate, 'read_jsonl', lambda _: pytest.fail('denied child must do no work'))
    with pytest.raises(SystemExit) as stop:
        finish_run.check_child(['--data', str(tmp_path / 'x.jsonl'), '--backend', 'predictions',
                                '--predictions', str(tmp_path / 'p.jsonl'), '--output', str(tmp_path / 'o.json')])
    assert stop.value.code == finish_run.RETRY_EXIT


@pytest.fixture
def checker(monkeypatch, tmp_path):
    monkeypatch.setattr(finish_run, 'RESULTS', tmp_path)
    monkeypatch.setattr(finish_run, 'CHECKS', tmp_path / 'checks')
    monkeypatch.setattr(finish_run, 'FINAL', tmp_path)
    (tmp_path / 'adapter_config.json').write_text('{}')
    monkeypatch.setattr(train, 'save_weights', lambda _model, path: path.write_text('weights'))
    waits = []
    clock = SimpleNamespace(wait=lambda: waits.append(1), monotonic=lambda: 0.0)
    made = finish_run.Checker(clock)
    made.waits = waits
    return made


def events(path):
    return [json.loads(line) for line in (path / 'gpu-run.jsonl').read_text().splitlines()]


def good_result(output, exact):
    output.write_text(json.dumps({'decoding_policy': evaluate.DECODING_POLICY,
                                  'schema_constrained': True, 'exact_match': exact}))


def test_check_retries_after_denied_child_and_logs_whole_epoch_iteration(checker, tmp_path):
    codes = [finish_run.RETRY_EXIT, 0]

    def run(command, **_):
        code = codes.pop(0)
        if not code:
            good_result(tmp_path / 'checks/001600/valid-sample.json', .9)
        return SimpleNamespace(returncode=code)

    checker.run = run
    assert checker(model=None, iteration=400, val_loss=.01, deadline=100) is None
    assert len(checker.waits) == 2 and not codes
    assert [e for e in events(tmp_path) if e['event'] == 'valid-check'][0]['iteration'] == 1600


def test_failed_check_keeps_training_and_timeout_stops(checker, tmp_path):
    checker.run = lambda *_, **__: SimpleNamespace(returncode=1)
    assert checker(model=None, iteration=0, val_loss=.01, deadline=100) is None
    assert events(tmp_path)[-1]['event'] == 'valid-check-failed'

    def expire(*_, **__):
        raise subprocess.TimeoutExpired('child', 1)

    checker.run = expire
    assert 'hard stop' in checker(model=None, iteration=400, val_loss=.01, deadline=100)


def test_plateau_spans_the_stopped_run(checker, tmp_path):
    scores = iter([.96, .95, .96])

    def run(command, **_):
        good_result(tmp_path / 'checks' / folder / 'valid-sample.json', next(scores))
        return SimpleNamespace(returncode=0)

    checker.run = run
    outcomes = []
    for iteration in (0, 400, 800):
        folder = f'{iteration + 1200:06d}'
        outcomes.append(checker(model=None, iteration=iteration, val_loss=.01, deadline=100))
    assert outcomes[:2] == [None, None] and 'passed 95 percent' in outcomes[2]


def test_report_is_restated_for_the_whole_epoch():
    report = finish_run.whole_epoch_report(
        {'rows_completed': 2921, 'rows_planned': 2921, 'full_epoch_completed': True, 'optimizer_updates': 731,
         'validation_checks': [{'iteration': 400, 'val_loss': .01}], 'adapter_sha256': 'a'}, 60.0)
    assert (report['rows_completed'], report['rows_planned'], report['optimizer_updates']) == (4121, 4121, 1031)
    assert report['full_epoch_completed'] and report['validation_checks'][0]['iteration'] == 1600
    assert report['continuation']['admission_wait_seconds'] == 60.0
    stopped = finish_run.whole_epoch_report(
        {'rows_completed': 400, 'optimizer_updates': 100, 'validation_checks': []}, 0.0)
    assert not stopped['full_epoch_completed'] and stopped['rows_completed'] == 1600
