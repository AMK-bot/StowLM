"""D109 measured verification sizing, separate from hard fleet admission.

The GPU-saturated machine delivered about 12 useful CPU cores in the leader's
probe (experiments/mvp/speed/cpu-capacity/NOTE.md). Idle-core estimates cannot
justify adding workers. Six is the explicit starting setting; only comparable
measured verification throughput can justify a different setting. This module
does not reserve cores: resources.reservation still enforces the fleet ledger.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from statistics import median

DEFAULT_VERIFY_WORKERS = 6
GPU_EFFECTIVE_CPU_CORES = 12
HARD_FLEET_CPU_CORES = 16


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f'{label} must be a positive integer')
    return value


def observation(*, workers, completed_maps, wall_s, background_workers=None,
                gpu_saturated=True):
    """Describe a verification-only interval, never a whole decode/train cycle.

    background_workers identifies comparable load, e.g. 8 for gold=6 + corpus=2
    and 6 once corpus finishes. Unknown load remains a separate comparison group.
    """
    workers = _positive_integer(workers, 'workers')
    completed_maps = _positive_integer(completed_maps, 'completed_maps')
    if isinstance(wall_s, bool) or not math.isfinite(wall_s) or wall_s <= 0:
        raise ValueError('wall_s must be positive and finite')
    if background_workers is not None:
        if (isinstance(background_workers, bool) or not isinstance(background_workers, int)
                or background_workers < 0):
            raise ValueError('background_workers must be a nonnegative integer or None')
    if not isinstance(gpu_saturated, bool):
        raise ValueError('gpu_saturated must be a boolean')
    return dict(workers=workers, completed_maps=completed_maps, wall_s=float(wall_s),
                maps_per_s=completed_maps / wall_s,
                background_workers=background_workers, gpu_saturated=gpu_saturated,
                effective_cpu_cores=GPU_EFFECTIVE_CPU_CORES if gpu_saturated else None)


def gpu_busy_observation(selections, *, workers, decode_finished,
                         background_workers=None, decode_started=None,
                         min_completed_maps=None, min_busy_s=1.):
    """Measure verification throughput only while the entire pool is busy.

    Inputs are select_job summaries with monotonic start/finish timestamps from
    this run. Pass decode_started when resumed/cached summaries are possible;
    old intervals outside the current decode window then contribute nothing.
    The caller supplies an actual GPU decode phase, rather than estimating GPU
    saturation from idle CPU cores. Missing timestamps are not observations.

    APPROXIMATION: completed jobs per fully-busy second estimates throughput;
    jobs may begin before a busy interval. At least one worker-width of completed
    jobs and one busy second are required by default to reduce boundary noise.
    This is a throughput measurement, not a direct GPU-utilization measurement.
    """
    workers = _positive_integer(workers, 'workers')
    minimum = (max(workers, 2) if min_completed_maps is None else
               _positive_integer(min_completed_maps, 'min_completed_maps'))
    if (isinstance(decode_finished, bool) or not math.isfinite(decode_finished)
            or isinstance(min_busy_s, bool) or not math.isfinite(min_busy_s)
            or min_busy_s <= 0):
        raise ValueError('decode_finished must be finite and min_busy_s positive and finite')
    if decode_started is not None and (isinstance(decode_started, bool)
            or not math.isfinite(decode_started) or decode_started >= decode_finished):
        raise ValueError('decode_started must be finite and precede decode_finished')
    intervals = []
    for row in selections:
        start = row.get('verification_started_monotonic')
        end = row.get('verification_finished_monotonic')
        if start is None or end is None:
            continue
        if (isinstance(start, bool) or isinstance(end, bool)
                or not math.isfinite(start) or not math.isfinite(end) or start >= end):
            raise ValueError('verification timestamps must be finite increasing intervals')
        if start >= decode_finished or (decode_started is not None and end <= decode_started):
            continue
        intervals.append((start, end))
    if not intervals:
        return None
    lower = min(start for start, _ in intervals) if decode_started is None else decode_started
    # Bucket starts and ends together. Completions at a shared timestamp belong
    # to the preceding interval; new starts cannot make that interval look busy.
    events = {lower: [0, 0], decode_finished: [0, 0]}
    active = 0
    for start, end in intervals:
        if start < lower:
            active += 1
        else:
            events.setdefault(start, [0, 0])[0] += 1
        if end <= decode_finished:
            events.setdefault(end, [0, 0])[1] += 1
    busy_s, completed = 0., 0
    previous = lower
    for timestamp in sorted(events):
        starts, finishes = events[timestamp]
        duration = timestamp - previous
        if duration > 0 and active >= workers:
            busy_s += duration
            completed += finishes
        active += starts - finishes
        previous = timestamp
    if completed < minimum or busy_s < min_busy_s:
        return None
    return observation(workers=workers, completed_maps=completed, wall_s=busy_s,
                       background_workers=background_workers, gpu_saturated=True)


def recommend_workers(observations, *, current_workers=DEFAULT_VERIFY_WORKERS,
                      max_workers=DEFAULT_VERIFY_WORKERS, background_workers=None,
                      gpu_saturated=True, improvement_fraction=.05):
    """Choose only observed settings, preferring fewer workers within 5% noise.

    APPROXIMATION: median interval throughput and a 5% improvement margin are a
    lightweight controller, not a controlled hardware benchmark. Compare like
    map mixes and load conditions; the caller supplies the load group explicitly.
    A setting above six requires both a measured six-worker baseline and a >5%
    gain under the same load. Unobserved extra capacity is never assumed.
    """
    current_workers = _positive_integer(current_workers, 'current_workers')
    max_workers = _positive_integer(max_workers, 'max_workers')
    if not math.isfinite(improvement_fraction) or not 0 <= improvement_fraction < 1:
        raise ValueError('improvement_fraction must be finite and in [0, 1)')
    grouped = {}
    for raw in observations:
        row = observation(**{key: raw[key] for key in
            ('workers', 'completed_maps', 'wall_s', 'background_workers', 'gpu_saturated')})
        if (row['background_workers'] != background_workers
                or row['gpu_saturated'] != gpu_saturated or row['workers'] > max_workers):
            continue
        grouped.setdefault(row['workers'], []).append(row['maps_per_s'])
    rates = {workers: median(values) for workers, values in grouped.items()}
    baseline = rates.get(DEFAULT_VERIFY_WORKERS)
    admissible = {workers: rate for workers, rate in rates.items()
                  if workers <= DEFAULT_VERIFY_WORKERS or
                  (baseline is not None and rate > baseline * (1 + improvement_fraction))}
    if not admissible or current_workers not in rates:
        workers = min(current_workers, DEFAULT_VERIFY_WORKERS, max_workers)
        reason = 'no comparable current-setting baseline; retain conservative starting count'
    else:
        peak = max(admissible.values())
        workers = min(count for count, rate in admissible.items()
                      if rate >= peak * (1 - improvement_fraction))
        reason = 'fewest measured workers within throughput margin of measured best'
    return dict(workers=workers, reason=reason, measured_maps_per_s=rates,
                effective_cpu_cores=GPU_EFFECTIVE_CPU_CORES if gpu_saturated else None,
                hard_fleet_cpu_cores=HARD_FLEET_CPU_CORES,
                background_workers=background_workers,
                requires_separate_fleet_admission=True)


class CapacitySizer:
    """Small restart-safe measurement journal; one controller owns each path."""

    def __init__(self, path, *, max_workers=DEFAULT_VERIFY_WORKERS):
        self.path = Path(path)
        self.max_workers = _positive_integer(max_workers, 'max_workers')
        self.observations = []
        if self.path.exists():
            state = json.loads(self.path.read_text())
            if state.get('version') != 1:
                raise ValueError('unknown capacity journal version')
            for row in state['observations']:
                self.observations.append(observation(**{key: row[key] for key in
                    ('workers', 'completed_maps', 'wall_s', 'background_workers', 'gpu_saturated')}))

    def observe(self, **kwargs):
        row = observation(**kwargs)
        self.observations.append(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'{self.path.name}.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(dict(version=1, observations=self.observations), indent=2))
        os.replace(temporary, self.path)
        return row

    def recommend(self, **kwargs):
        return recommend_workers(self.observations, max_workers=self.max_workers, **kwargs)
