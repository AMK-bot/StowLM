"""Original-instance checker, exact instructions and every crane frame."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import importlib
import math
import os
from pathlib import Path
import tempfile
import threading
from time import perf_counter

from experiments.mvp.pipeline.contract.schema import sha256_json

HERE = Path(__file__).resolve().parent
_CACHE_LOCK = threading.RLock()


@contextmanager
def runtime_cache_scope():
    """Keep numerical derived-data writes inside this feature's fence."""
    with _CACHE_LOCK:
        with _runtime_cache_paths():
            yield


@contextmanager
def _runtime_cache_paths():
    changes = []
    try:
        fleet_cache = importlib.import_module('stowage.vessel.fleet_cache')
        changes.append((fleet_cache, '_cache_dir', fleet_cache._cache_dir))
        fleet_cache._cache_dir = lambda: HERE / 'runtime-cache/vessels'
        for name, attr, suffix in (
            ('stowage.checker.stability_tables', 'CACHE_DIR', 'stability'),
            ('stowage.checker.stability_sounding', 'CACHE_DIRECTORY', 'stability'),
            ('stowage.checker.stability_liquid', 'CACHE_DIRECTORY', 'stability'),
            ('stowage.checker.stability_liquid_tables', 'CACHE_DIRECTORY', 'stability'),
            ('stowage.linear.cache', '_DEFAULT_ROOT', 'linear'),
        ):
            module = importlib.import_module(name)
            changes.append((module, attr, getattr(module, attr)))
            setattr(module, attr, HERE / 'runtime-cache' / suffix)
        tables = importlib.import_module('stowage.checker.stability_tables')
        for name in ('load_table_geometry', 'cached_geometry', 'migrate_legacy'):
            function = getattr(tables, name)
            changes.append((function, '__kwdefaults__', function.__kwdefaults__))
            function.__kwdefaults__ = dict(function.__kwdefaults__, cache_dir=HERE / 'runtime-cache/stability')
        yield
    finally:
        for obj, attr, value in reversed(changes):
            setattr(obj, attr, value)


def engine_identity():
    with _CACHE_LOCK:
        return _engine_identity()


def _engine_identity():
    # The binding's import configures solver workers. Pure app gates preserve
    # their caller's numerical environment, which is part of the checker epoch.
    thread_keys = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS')
    def relevant(key):
        return key.startswith('STOWAGE_') or key in thread_keys
    previous = {key: value for key, value in os.environ.items() if relevant(key)}
    try:
        from experiments.mvp.pipeline.binding.adapter import verify_manifest
        from engine import VERSION
        manifest = verify_manifest()
    finally:
        for key in list(os.environ):
            if relevant(key) and key not in previous:
                del os.environ[key]
        os.environ.update(previous)
    return dict(engine_version=VERSION, engine_manifest_sha256=manifest['manifest_sha256'],
                engine_manifest=manifest, passed=manifest['passed'])


def _check(instance, plan):
    from stowage.checker import check
    return check(instance, plan)


class _GaugeAbort(RuntimeError):
    pass


def gauge_frame_order(n_lifts):
    """Reject a doomed departure early; passing plans still need every prefix."""
    return list(dict.fromkeys(k for k in (0, n_lifts - 1, n_lifts, *range(n_lifts + 1))
                              if 0 <= k <= n_lifts))


def _write_frame_checkpoint(path, identity, completed, n_lifts, *, aborted):
    from experiments.mvp.pipeline.contract.schema import canonical_json
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(completed, key=lambda frame: frame['step'])
    complete = [frame['step'] for frame in ordered] == list(range(n_lifts + 1))
    row = dict(identity, schema_version='stowlm.lattice-frames/1', n_lifts=n_lifts,
               frames=ordered, complete=complete, passed=complete and not aborted,
               aborted=aborted, last_step=completed[-1]['step'],
               scope='native stability and strength frames only; not a stored-plan release')
    descriptor, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(canonical_json(row)+'\n')
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _frames(instance, plan, *, frame_checkpoint_path=None):
    from app.stage_physics import compute_physics, physics_drift, physics_header, input_digests, physics_epoch
    from .runtime import discarded_plot_scope
    if reason := physics_drift():
        raise ValueError(reason)
    completed = []
    checkpoint_identity = dict(input_digests(instance, plan), physics_epoch=physics_epoch()) \
        if frame_checkpoint_path is not None else None

    def on_frame(k, frame):
        completed.append(frame)
        aborted = bool(frame.get('error') or frame.get('stability_passed') is not True or frame.get('strength_passed') is not True)
        if frame_checkpoint_path is not None:
            _write_frame_checkpoint(frame_checkpoint_path, checkpoint_identity, completed, len(plan.moves), aborted=aborted)
        if aborted:
            raise _GaugeAbort()

    with discarded_plot_scope():
        try:
            physics = compute_physics(instance, plan, frames=gauge_frame_order(len(plan.moves)), workers=1, on_frame=on_frame)
        except _GaugeAbort:
            physics = dict(physics_header(instance, 'all'), frames=completed, n_lifts=len(plan.moves),
                           aborted=True, abort_step=completed[-1]['step'],
                           abort_reason='first outside-limits or unavailable crane frame')
    if reason := physics_drift():
        raise ValueError(reason)
    physics['physics_epoch'] = physics_epoch()
    physics['plot_optimization'] = 'withheld hatch-off plot skipped; native parts unchanged'
    return physics


def _clean(instance, plan, report):
    from stowage.frozen import active_stubs, require_clean
    if active_stubs():
        raise ValueError('active checker stubs prevent stored plan release')
    require_clean(instance, plan, report, what='lattice original-instance gate')


def _violations(instance, answer, plan):
    from experiments.mvp.reader.pipeline import intrinsic_violations
    return list(intrinsic_violations(instance, answer, plan))


def _placements(instance, plan):
    from stowage.moves import apply_plan
    replay = apply_plan(instance, plan)
    return {p.container_id: p.slot for p in replay.final_state.ship.placements}


def _lifts(report):
    return report.count.weighted_nonproductive if report.count is not None else None


def _validate_count(report):
    if report.count is None:
        return
    def finite(value):
        if isinstance(value, dict):
            return all(finite(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return all(finite(item) for item in value)
        return math.isfinite(value) if isinstance(value, (int, float)) else True
    count = report.count
    values = count.model_dump(mode='json') if hasattr(count, 'model_dump') else vars(count)
    if not finite(values) or not math.isfinite(count.weighted_nonproductive) or \
            not math.isfinite(count.total_lifts_now):
        raise ValueError('checker move count contains a nonfinite value')


def gauge_verdict(physics, n_lifts):
    """Match the stage badge: false is OUTSIDE LIMITS; missing is not a pass."""
    frames = physics.get('frames', [])
    outside = [f.get('step') for f in frames
               if f.get('stability_passed') is False or f.get('strength_passed') is False]
    unavailable = [f.get('step') for f in frames if f.get('error') or
                   f.get('stability_passed') is not True or f.get('strength_passed') is not True]
    complete = [f.get('step') for f in frames] == list(range(n_lifts + 1))
    return dict(passed=complete and not outside and not unavailable and not physics.get('aborted'), all_frames=complete,
                n_frames=len(frames), outside_limits_steps=outside, unavailable_steps=unavailable,
                physics=physics)


def gate_plan(instance, plan, rules, *, base_plan=None, admit=True, frame_checkpoint_path=None):
    """Fresh full gate; inputs may be model instances or JSON dictionaries."""
    from stowage.model import Instance, MovePlan
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from experiments.mvp.pipeline.lattice.store import canonical_rule_set
    import json
    instance = Instance.model_validate(instance) if isinstance(instance, dict) else instance
    plan = MovePlan.model_validate(plan) if isinstance(plan, dict) else plan
    if isinstance(base_plan, dict):
        base_plan = MovePlan.model_validate(base_plan)
    context = context_from_instance(instance)
    canonical = canonical_rule_set(rules)
    answers = [validate_answer(json.dumps(rule), context) for rule in canonical]
    identity = engine_identity()
    if not identity['passed']:
        raise ValueError('pinned engine sources do not match')
    admission = None
    if admit:
        from experiments.mvp.pipeline.binding.admission import wait_for_capacity
        admission = wait_for_capacity()
    started = perf_counter()
    with runtime_cache_scope():
        report = _check(instance, plan)
        _clean(instance, plan, report)
        _validate_count(report)
        rows = [dict(envelope=rule, violations=_violations(instance, answer, plan))
                for rule, answer in zip(canonical, answers)]
        for row in rows:
            row['passed'] = not row['violations']
        physics = _frames(instance, plan) if frame_checkpoint_path is None else \
            _frames(instance, plan, frame_checkpoint_path=frame_checkpoint_path)
        gauges = gauge_verdict(physics, len(plan.moves))
        base_lifts, moved, binding, obeys, base_passed = None, [], None, None, None
        if base_plan is not None:
            before = _check(instance, base_plan)
            _clean(instance, base_plan, before)
            _validate_count(before)
            base_lifts, base_passed = _lifts(before), bool(before.passed and before.count is not None)
            old, new = _placements(instance, base_plan), _placements(instance, plan)
            moved = [dict(container_id=box, before=old.get(box), after=new.get(box))
                     for box in sorted(set(old) | set(new)) if old.get(box) != new.get(box)]
            obeys = [not _violations(instance, answer, base_plan) for answer in answers]
            binding = bool(moved) and bool(obeys) and not all(obeys)
    final_identity = engine_identity()
    lifts = _lifts(report)
    passed = bool(report.passed and lifts is not None and all(r['passed'] for r in rows)
                  and gauges['passed'] and base_passed is not False and final_identity == identity)
    return dict(passed=passed, instance_sha256=sha256_json(instance.model_dump(mode='json')),
                plan_sha256=sha256_json(plan.model_dump(mode='json')), checker=report.model_dump(mode='json'),
                rules=rows, gauges=gauges, lifts=lifts, base_lifts=base_lifts,
                total_lifts_now=report.count.total_lifts_now if report.count is not None else None,
                lift_delta=lifts-base_lifts if lifts is not None and base_lifts is not None else None,
                what_moved=moved, boxes_moved=len(moved), binding=binding, base_already_obeys=obeys,
                base_passed=base_passed, engine_identity=identity, admission=admission,
                checked_utc=datetime.now(timezone.utc).isoformat(), wall_s=perf_counter()-started)
