"""Cross-parent scorecard admission; native search and checker stay unchanged.

Two independent scorecard pools may each spawn two workers. Only a worker
holding a common file-lock slot enters score.one; imports and work of the native
one-map runner happen inside that slot. A returning BENCH marker prevents new
second-slot admissions; already admitted calls drain cooperatively.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time

DIRECTORY_ENV = 'FRONTIER_ADMISSION_DIR'
ARM_ENV = 'FRONTIER_ADMISSION_ARM'
MARKER_ENV = 'FRONTIER_ADMISSION_MARKER'
SLOTS = 2
_score_module = None


def _event(directory, value):
    """One append under a short journal lock; no process enumeration."""
    with (directory/'events.jsonl').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            stream.write(json.dumps(value, sort_keys=True)+'\n')
            stream.flush()
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def admitted(directory, *, arm, marker, input_path=None, poll_s=.25):
    """Hold one common slot for a whole map; time waiting is outside its budget."""
    if not 0. < poll_s <= 60.:
        raise ValueError('admission polling must be positive and at most 60 seconds')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    marker = Path(marker) if marker else None
    waiting = time.monotonic()
    lease = None
    reservation = None
    slot = None
    while lease is None:
        limit = 1 if marker is not None and marker.exists() else SLOTS
        for candidate in range(limit):
            stream = (directory/f'slot-{candidate}.lock').open('a+')
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                stream.close()
                continue
            # Recheck after acquisition: a marker can appear during the scan.
            if candidate and marker is not None and marker.exists():
                fcntl.flock(stream, fcntl.LOCK_UN)
                stream.close()
                continue
            if not candidate and marker is not None and marker.exists():
                # Wait for a running second slot to drain, then reserve it.
                # Merely skipping that slot would still admit alongside its
                # older occupant after the benchmark marker returns.
                second = (directory/'slot-1.lock').open('a+')
                try:
                    fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    second.close()
                    fcntl.flock(stream, fcntl.LOCK_UN)
                    stream.close()
                    continue
                reservation = second
            lease, slot = stream, candidate
            break
        if lease is None:
            time.sleep(poll_s)
    record = dict(pid=os.getpid(), parent_pid=os.getppid(), arm=arm, slot=slot,
                  input=input_path, queue_wait_s=time.monotonic()-waiting,
                  entered_unix_s=time.time(), entered_monotonic_s=time.monotonic(),
                  marker_at_admission=bool(marker is not None and marker.exists()),
                  admission_cap=1 if marker is not None and marker.exists() else SLOTS)
    try:
        _event(directory, dict(record, event='enter'))
        yield record
    finally:
        record.update(left_unix_s=time.time(), left_monotonic_s=time.monotonic())
        try:
            _event(directory, dict(record, event='leave'))
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)
            lease.close()
            if reservation is not None:
                fcntl.flock(reservation, fcntl.LOCK_UN)
                reservation.close()


def outcome_row(row):
    """Expose the engine's tier evidence without changing native acceptance."""
    engine = row.get('method_metadata', {}).get('engine')
    if not isinstance(engine, dict):
        return row
    for key in ('completed', 'best_safe_partial', 'rules_broken', 'level', 'winner_lane'):
        row[key] = engine.get(key)
    if row.get('passed') is True and (not row['completed'] or row['completed'].get('level') != 0):
        # Only the unchanged scorer's check can make this ordinary T5 pass.
        row['completed'] = dict(level=0, column='T5', boxes_left_ashore=[],
                                full_checker_passed=True)
    elif row.get('passed') is not True and row['completed'] and row['completed'].get('level') == 0:
        row['completed'] = None
        row['outcome_reporting_warning'] = 'engine T5 claim failed independent native scorecard check'
    return row


def one(job):
    """Spawn-picklable replacement for score.one; calls its unchanged original."""
    global _score_module
    if _score_module is None:
        import score
        install(score)
    module = _score_module
    original = module._engine_admission_original_one
    directory = os.environ.get(DIRECTORY_ENV)

    def run():
        row, cached = original(job)
        outcome_row(row)
        return row, cached

    if not directory:
        row, cached = run()
    else:
        marker = os.environ.get(MARKER_ENV)
        with admitted(directory, arm=os.environ.get(ARM_ENV, 'unspecified'),
                      marker=marker, input_path=str(job[0])) as lease:
            row, cached = run()
        row['admission'] = dict(lease)
    # score.one already writes its native row. Add reporting fields only.
    target = Path(job[1])/f'{row["instance_id"]}.json'
    module.write_json(target, row)
    return row, cached


def install(module):
    """Idempotent, module-level setup also runs when score workers spawn."""
    global _score_module
    if not hasattr(module, '_engine_admission_original_one'):
        module._engine_admission_original_one = module.one
    module.one = one
    _score_module = module
    return module


def journal(directory):
    """Summarize observed gate intervals; system load is a separate proxy."""
    path = Path(directory)/'events.jsonl'
    if not path.exists():
        return dict(events=[], owned_worker_pids=[], max_admitted=0, overlap_s=0.)
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        try:
            events = [json.loads(line) for line in stream if line.strip()]
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
    active, peak, overlap = {}, 0, 0.
    previous = None
    for event in sorted(events, key=lambda row: row.get('left_monotonic_s')
                        if row['event'] == 'leave' else row['entered_monotonic_s']):
        now = event['left_monotonic_s'] if event['event'] == 'leave' else event['entered_monotonic_s']
        if previous is not None and len({row['arm'] for row in active.values()}) > 1:
            overlap += max(0., now-previous)
        identity = (event['pid'], event['entered_monotonic_s'])
        if event['event'] == 'enter':
            active[identity] = event
        else:
            active.pop(identity, None)
        peak = max(peak, len(active))
        previous = now
    return dict(events=events, owned_worker_pids=sorted({row['pid'] for row in events}),
                max_admitted=peak, unfinished_leases=list(active.values()),
                overlap_s=overlap,
                scope='Shared two-slot map admissions; overlap is simultaneous admitted arms, not per-map matched load')
