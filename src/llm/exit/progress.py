"""Durable generation progress and honest reports, including incomplete runs.

Phase walls may overlap. Worker totals are deliberately kept separate from wall
times, and an unmeasured phase is ``None`` rather than an invented zero.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
import json
import os
from pathlib import Path
import resource
import sys
import threading
import time


def _current_rss_bytes():
    """Current process only: no subprocesses and no child-worker memory."""
    try:
        if sys.platform == 'darwin':
            # PROC_PIDTASKINFO begins with virtual and resident sizes (uint64).
            # A generous raw buffer avoids depending on the rest of its ABI.
            library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
            library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                            ctypes.c_void_p, ctypes.c_int]
            library.proc_pidinfo.restype = ctypes.c_int
            buffer = (ctypes.c_uint64 * 32)()
            size = library.proc_pidinfo(os.getpid(), 4, 0, buffer, ctypes.sizeof(buffer))
            return int(buffer[1]) if size >= 16 else None
        if sys.platform.startswith('linux'):
            resident_pages = int(Path('/proc/self/statm').read_text().split()[1])
            return resident_pages * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    return None


def _lifetime_peak_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


class _PhaseMemory:
    """One idle sampler per overlapping phase, with bounded disk updates."""

    interval_s = 1.
    persist_every_s = 5.

    def __init__(self, tracker, phase):
        self.tracker, self.phase = tracker, phase
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name='exit-phase-rss', daemon=True)
        self.sample()
        phase['rss_entry_bytes'] = phase['rss_last_bytes']
        phase['process_lifetime_peak_rss_entry_bytes'] = _lifetime_peak_rss_bytes()

    def sample(self):
        value = _current_rss_bytes()
        with self.tracker._lock:
            self.phase['rss_last_bytes'] = value
            self.phase['rss_sampled_at'] = _stamp()
            self.phase['rss_samples'] = self.phase.get('rss_samples', 0) + (value is not None)
            peak = self.phase.get('rss_sampled_peak_bytes')
            self.phase['rss_sampled_peak_bytes'] = max(peak or 0, value) if value is not None else peak

    def _run(self):
        persisted = time.monotonic()
        while not self.stop.wait(self.interval_s):
            self.sample()
            if time.monotonic() - persisted >= self.persist_every_s:
                self.tracker._save()
                persisted = time.monotonic()

    def finish(self):
        self.stop.set()
        self.thread.join()
        self.sample()
        self.phase['rss_exit_bytes'] = self.phase['rss_last_bytes']
        self.phase['process_lifetime_peak_rss_exit_bytes'] = _lifetime_peak_rss_bytes()


_RSS_NOTE = ('Bytes for the resident loop process only; CPU workers excluded. Phase peak is the maximum '
             'observed at entry, exit and 1-second samples, not a guaranteed instantaneous peak. '
             'Process lifetime high-water RSS is separately labeled and is not a phase peak.')


def _utc_now():
    return datetime.now(timezone.utc)


def _stamp(value=None):
    value = _utc_now() if value is None else value
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('progress timestamps must have a timezone')
    return value.astimezone(timezone.utc).isoformat()


def _elapsed(start, end):
    return max(0., (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


class GenerationProgress:
    """Single-writer phase recorder; resuming never resets the generation clock."""

    def __init__(self, out, number, *, now=None, **metadata):
        self._lock = threading.RLock()
        if number < 0:
            raise ValueError('generation number must be nonnegative')
        self.out, self.number = Path(out), number
        self.path = self.out / f'gen-{number:03d}' / 'phase-status.json'
        stamp = _stamp(now)
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            if self.state['generation'] != number:
                raise ValueError('progress generation mismatch')
            for phase in self.state['phases']:
                if phase['status'] == 'running':
                    phase.update(status='interrupted', ended_at=stamp,
                                 observed_elapsed_s=_elapsed(phase['started_at'], stamp))
            self.state['resumes'] = self.state.get('resumes', 0) + 1
        else:
            self.state = dict(generation=number, started_at=stamp, phases=[], counters={}, resumes=0)
        self.state.update(status='running', updated_at=stamp)
        self.state.pop('ended_at', None)
        self.state.setdefault('metadata', {}).update(metadata)
        self._save(stamp)
        if number == 0:
            marker = self.out / 'STARTED'
            marker.parent.mkdir(parents=True, exist_ok=True)
            try:
                with marker.open('x') as stream:
                    stream.write(self.state['started_at'] + '\n')
            except FileExistsError:
                pass

    def _save(self, stamp=None):
        with self._lock:
            stamp = _stamp(stamp)
            self.state['updated_at'] = stamp
            self.state['elapsed_s'] = _elapsed(self.state['started_at'], stamp)
            _write(self.path, self.state)

    def update(self, **counters):
        with self._lock:
            self.state['counters'].update(counters)
            self._save()

    @contextmanager
    def phase(self, name, **metadata):
        if not name:
            raise ValueError('phase name must not be empty')
        phase = dict(name=name, started_at=_stamp(), status='running', metadata=metadata)
        with self._lock:
            self.state['phases'].append(phase)
        memory = _PhaseMemory(self, phase)
        started = time.monotonic()
        self._save()
        memory.thread.start()
        try:
            yield phase
        except BaseException as error:
            with self._lock:
                phase.update(status='failed', error=f'{type(error).__name__}: {error}')
            raise
        else:
            with self._lock:
                phase['status'] = 'completed'
        finally:
            memory.finish()
            with self._lock:
                phase.update(ended_at=_stamp(), wall_s=max(0., time.monotonic() - started))
                self._save()

    def finish(self, status='completed', **metadata):
        with self._lock:
            self.state.update(status=status, ended_at=_stamp())
            self.state['metadata'].update(metadata)
            self._save(self.state['ended_at'])


def start_generation(out, number, *, now=None, **metadata):
    """Call inside GPU admission; generation0 begins with its baseline audit."""
    return GenerationProgress(out, number, now=now, **metadata)


def _read(path, errors):
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        errors.append(f'{path.name}: {type(error).__name__}: {error}')
        return None


def _phase_wall(phases, name):
    values = [p['wall_s'] for p in phases if p['name'] == name and 'wall_s' in p]
    return sum(values) if values else None


def _sum_known(values):
    values = [value for value in values if value is not None]
    return sum(values) if values else None


def _log_tail(path, *, max_bytes=16384, max_lines=40):
    """Bound report I/O even when a long-running generation has a large log."""
    with Path(path).open('rb') as stream:
        stream.seek(0, 2)
        end = stream.tell()
        stream.seek(max(0, end - max_bytes))
        data = stream.read(max_bytes).decode('utf-8', errors='replace')
    return '\n'.join(data.splitlines()[-max_lines:])


def build_generation_report(out, number=0, *, now=None):
    """Read only ExIt artifacts; usable while GPU/CPU work is still in flight."""
    out = Path(out)
    directory = out / f'gen-{number:03d}'
    errors = []
    summary = _read(directory / 'summary.json', errors) or {}
    progress = _read(directory / 'phase-status.json', errors) or {}
    phases = progress.get('phases', [])
    stamp = _stamp(now)
    start = progress.get('started_at')
    end = progress.get('ended_at', stamp) if progress.get('status') != 'running' else stamp
    elapsed = _elapsed(start, end) if start else summary.get('timings', {}).get('wall_s')
    counters = progress.get('counters', {})
    timing = {**counters.get('timings', {}),
              **{key: value for key, value in counters.items() if key.endswith('_s')},
              **summary.get('timings', {})}
    active = [dict(name=p['name'], started_at=p['started_at'],
                   elapsed_s=_elapsed(p['started_at'], stamp), metadata=p.get('metadata', {}),
                   rss={key: value for key, value in p.items() if 'rss_' in key})
              for p in phases if p['status'] == 'running']
    for phase in phases:
        if phase.get('error'):
            errors.append(f'{phase["name"]}: {phase["error"]}')
    child_rows = summary.get('children')
    if child_rows is None:
        child_rows = [_read(path, errors) or {} for path in sorted(directory.glob('child-*-ready.json'))]
    dpo_rows = [row.get('preference_training', {}) for row in child_rows]
    # Completed DPO artifacts are useful before summary publication too.
    for path in sorted(directory.glob('child-*/preference/dpo_run.json')):
        child_dir = path.parent.parent
        if not any(str(child_dir / 'preference') == row.get('adapter') and row.get('preference_training')
                   for row in child_rows):
            dpo_rows.append(_read(path, errors) or {})
    for row in child_rows:
        if row.get('preference_error'):
            errors.append(row['preference_error'])
    for path in (directory / 'error.json', directory / 'generation-error.json',
                 out / 'generation-error.json'):
        error = _read(path, errors)
        if error:
            errors.append(error)
    selections = [_read(path, errors) or {} for path in sorted(directory.glob('cases/*/selection.json'))]
    details = summary.get('winners_by_source_detail')
    if details is None:
        from collections import Counter
        details = dict(Counter(row.get('winner_source_detail', row.get('source'))
                               for row in selections if row.get('passed')))
    winners = dict(model_alone=details.get('model', 0),
                   model_plus_polish=details.get('polished_from_model', 0),
                   gold=details.get('gold', 0),
                   two_step=details.get('baseline_twostep', 0),
                   polished_gold=details.get('polished_from_gold', 0),
                   polished_two_step=details.get('polished_from_baseline_twostep', 0),
                   all_source_details=details)
    def wall(key, phase):
        return timing[key] if key in timing else _phase_wall(phases, phase)
    sft = {key.removesuffix('_train_s'): value for key, value in timing.items() if key.endswith('_train_s')}
    if not sft:
        sft = {p['name']: _phase_wall(phases, p['name']) for p in phases
               if p['name'].endswith('_train') and 'wall_s' in p}
    pairs = summary.get('preference_pairs')
    if pairs is None and (directory / 'preferences.jsonl').exists():
        try:
            pairs = sum(bool(line.strip()) for line in (directory / 'preferences.jsonl').read_text().splitlines())
        except OSError as error:
            errors.append(f'preferences.jsonl: {error}')
    verify_intervals = [(row.get('verification_started_monotonic'), row.get('verification_finished_monotonic'))
                        for row in selections]
    verify_intervals = [(a, b) for a, b in verify_intervals if a is not None and b is not None]
    # Envelope is not a sum of parallel tasks; label the distinction explicitly.
    verify_envelope = max(b for _, b in verify_intervals) - min(a for a, _ in verify_intervals) if verify_intervals else None
    phase_wall = dict(decode_s=timing.get('decode_wall_s', wall('decode_s', 'decode')),
        decode_verify_pipeline_s=wall('decode_verify_pipeline_s', 'decode_verify_pipeline'),
        verify_envelope_s=timing.get('verification_span_wall_s', verify_envelope),
        verify_drain_s=timing.get('verification_tail_wall_s', wall('verify_drain_s', 'verify_drain')),
        dataset_s=wall('dataset_s', 'dataset'), sft_by_child_s=sft,
        dpo_reference_s=_sum_known(row.get('reference_prepass_s') for row in dpo_rows),
        dpo_update_s=_sum_known(row.get('train_s') for row in dpo_rows),
        dpo_total_s=_sum_known(value for key, value in timing.items() if key.endswith('_dpo_s')),
        fitness_s=wall('fitness_s', 'fitness'), audit_s=wall('audit_s', 'audit'))
    worker = {key: value for key, value in timing.items() if key.endswith('_worker_s')}
    sizing = _read(out / 'sizing-after-gen0.json', errors) if number == 0 else None
    baseline_timing = _read(out / 'baseline-timing.json', errors)
    audit_binding = _read(out / 'audit-baseline' / 'baseline-binding.json', errors) or {}
    baseline_rows = _read(out / 'audit-baseline' / 'summary.json', errors)
    from llm.exit.outcomes import checker_unavailable, outcome_counts, selection_checker_rows
    baseline_counts = outcome_counts(baseline_rows) if baseline_rows is not None else None
    baseline_pass_moves = [r['weighted_moves'] for r in baseline_rows or []
                           if r.get('passed') is True and not checker_unavailable(r)]
    baseline_result = dict(counts=baseline_counts,
        mean_weighted_moves=(sum(baseline_pass_moves)/len(baseline_pass_moves)
                             if baseline_pass_moves else None))
    verification_counts=summary.get('verification_counts') or outcome_counts(
        [check for selection in selections for check in selection_checker_rows(selection)])
    controller = _read(out / 'deployment-status.json', errors) or {}
    if controller.get('state') in ('failed', 'generation_failed', 'smoke_failed'):
        errors.append(dict(controller=controller))
    if not summary and (progress.get('status') == 'failed'
                        or controller.get('state') in ('failed', 'generation_failed')):
        log = out / 'generation.log'
        if log.exists():
            try:
                errors.append(dict(log_path=str(log), log_tail=_log_tail(log)))
            except OSError as error:
                errors.append(f'generation.log: {error}')
    complete = bool(summary)
    return dict(generation=number, generated_at=stamp, started_at=start,
        status=('completed' if complete else progress.get('status', 'not_started')),
        elapsed_s=elapsed, over_120_minutes=bool(elapsed is not None and elapsed > 7200 and not complete),
        active_phases=active, counters=counters, phase_wall_s=phase_wall, worker_totals_s=worker,
        phase_rss=[dict(name=p['name'], status=p['status'], started_at=p['started_at'],
                       **{key: value for key, value in p.items() if 'rss_' in key})
                   for p in phases], rss_note=_RSS_NOTE,
        timing_note='Wall phases may overlap; worker totals are not elapsed wall time. Null means unmeasured.',
        pairs=dict(admitted=pairs, filtered_over_length=summary.get('preference_over_length_pairs'),
                   by_child=[dict(adapter=row.get('adapter'), method=row.get('method'),
                                  pairs=row.get('preference_training', {}).get('pairs'),
                                  skipped=row.get('preference_skipped')) for row in child_rows]),
        winners=winners, fitness=summary.get('fitness'), audit=summary.get('audit'),
        baseline_audit_s=baseline_timing.get('audit_s') if baseline_timing else None,
        baseline_audit_result=baseline_result, verification_counts=verification_counts,
        audit_outcome_epoch=audit_binding.get('audit_outcome_epoch'),
        greedy_fitness_counts=summary.get('greedy_fitness_counts'),
        generation_1_sizing=sizing, errors=errors,
        instance_count=summary.get('instance_count'), completed_selections=len(selections))


def write_generation_report(out, number=0, *, now=None):
    report = build_generation_report(out, number, now=now)
    target = Path(out) / f'generation-{number}-report'
    _write(target.with_suffix('.json'), report)
    def shown(value):
        return 'unmeasured' if value is None else str(value)
    lines = [f'Generation {number}: {report["status"]}', '',
             f'Start: {shown(report["started_at"])}',
             f'Elapsed: {shown(report["elapsed_s"])} seconds',
             f'Over 120 minutes while unfinished: {report["over_120_minutes"]}', '',
             report['timing_note'], '', 'Phase wall seconds:']
    lines += [f'- {name}: {shown(value)}' for name, value in report['phase_wall_s'].items()]
    lines += ['', f'Worker totals: {json.dumps(report["worker_totals_s"])}',
              f'Phase RSS: {json.dumps(report["phase_rss"])}', report['rss_note'],
              f'Active phases: {json.dumps(report["active_phases"])}',
              f'Live counters: {json.dumps(report["counters"])}',
              f'Pairs: {json.dumps(report["pairs"])}',
              f'Winners: {json.dumps(report["winners"])}',
              f'Generation 1 sizing: {json.dumps(report["generation_1_sizing"])}',
              f'Baseline audit seconds (before generation): {shown(report["baseline_audit_s"])}',
              f'Errors: {json.dumps(report["errors"])}', '']
    target.with_suffix('.md').write_text('\n'.join(lines))
    return report
