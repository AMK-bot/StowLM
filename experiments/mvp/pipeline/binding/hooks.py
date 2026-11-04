"""Scoped instruction hooks around the pinned Frontier engine.

No engine/checker source is modified.  The static compatibility matrix is
intersected before the native engine builds any completion caches, so page
pricing, assignment LP edges, and peeled masks consume the same restrictions.
Spawned processes enter the same scope before calling their original target.
One solve may own these process-wide hooks at a time.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import copy
import importlib
import json
import multiprocessing.process
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType

import numpy as np

# pytest may import this package as ``binding`` while production imports its
# repository-qualified namespace. A process must still have exactly one owner,
# and worker targets must observe activation through either import spelling.
_STATE_NAME = '_stowlm_pipeline_binding_process_state'
_STATE = sys.modules.setdefault(_STATE_NAME, ModuleType(_STATE_NAME))
_LOCK = _STATE.__dict__.setdefault('lock', threading.Lock())
_STATE.__dict__.setdefault('active', None)


def binding_active() -> bool:
    """True inside an installed scope, including its spawned worker targets."""
    return _STATE.active is not None


def bound_forbidden_pairs(engine) -> int:
    """Instruction-only forbidden edge count, for independent worker probes."""
    return int(getattr(engine.T, '_pipeline_forbidden_pairs', 0))


def _event(path, event, **details):
    if path is None:
        return
    # Each event is a short, single O_APPEND write; independent child processes
    # never rewrite each other's evidence. Only the owning scope removes it.
    data = (json.dumps(dict(event=event, pid=os.getpid(), **details))+'\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _worker_entry(target, args, kwargs, instance, constrained, audit_path):
    """Pickleable multiprocessing target; invoked after spawn imports finish."""
    if binding_active():
        # A fork already inherited the exact live wrappers and lock. Re-entering
        # would deadlock, whereas using the inherited restrictions is correct.
        _event(audit_path, 'worker_hook_active', inherited=True)
        return target(*args, **kwargs)
    with binding_scope(instance, constrained, _audit_path=audit_path, _worker=True):
        return target(*args, **kwargs)


@contextmanager
def _reusable_ballast_scope(owned_processes, audit):
    """Keep adapter-bound reusable workers within this invocation's lifetime.

    An existing native worker is borrowed neither for this instruction nor for
    cleanup: preserve its exact process/connection pair, use an empty native
    slot for this scope, then retire only a handle started by our start hook.
    The native helper releases its lock before returning each completed call.
    """
    from stowage.solver import ballast_deadline as native

    if not native._lock.acquire(blocking=False):
        raise RuntimeError('native ballast worker is busy; use a separate process')
    try:
        previous_process, previous_connection = native._process, native._connection
        native._process = native._connection = None
    finally:
        native._lock.release()
    try:
        yield native
    finally:
        # Native requests are synchronous and have returned/released this lock
        # before engine cleanup. Only this invocation owns the detached slot.
        with native._lock:
            process, connection = native._process, native._connection
            current_owned = any(process is owned for owned, _ in owned_processes)
            try:
                if process is not None and current_owned:
                    native._process = native._connection = None
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception as error:
                            audit['cleanup_errors'].append(f'reusable worker connection: {type(error).__name__}: {error}')
                elif process is not None:
                    # Never discard a handle this invocation did not start.
                    audit['cleanup_errors'].append('reusable worker slot changed to an unowned handle; preserved it')
                # Include a worker whose start succeeded but whose caller
                # failed before assigning the native module's reusable slot.
                for owned, pid in owned_processes:
                    try:
                        if getattr(owned, '_closed', False):
                            continue  # Native timeout/discard already retired it.
                        # Closing its exact pipe normally lets the idle receive
                        # loop exit cleanly. Escalation touches this handle only.
                        owned.join(timeout=.2)
                        if owned.is_alive():
                            owned.terminate()
                            owned.join(timeout=1.)
                        if owned.is_alive():
                            owned.kill()
                            owned.join(timeout=1.)
                        alive, exitcode = owned.is_alive(), owned.exitcode
                        audit['reusable_workers_retired'].append(
                            dict(pid=pid, exited=not alive, exitcode=exitcode))
                        if not alive:
                            owned.close()
                        else:
                            audit['cleanup_errors'].append(f'reusable worker {pid} did not exit after owned-handle cleanup')
                    except Exception as error:
                        audit['cleanup_errors'].append(f'reusable worker {pid}: {type(error).__name__}: {error}')
            finally:
                # A pre-existing unbound/native worker retains its own warm
                # caches and receives no instruction or stale audit pathname.
                if process is None or current_owned:
                    native._process, native._connection = previous_process, previous_connection


@contextmanager
def binding_scope(instance, constrained=None, on_checked=None, *,
                  _audit_path=None, _worker=False):
    """Install and restore all instruction hooks, yielding JSON-ready audit.

    ``on_checked(plan, report)`` observes strictly improving full checker passes
    on the exact original ``instance`` object that also obey the instruction.
    Reports on projected/peeled copies are excluded even when their IDs match.
    Its errors are recorded, never propagated into the engine. Native reports
    are returned unchanged. The adapter must independently gate the final
    result against the original instance.
    """
    if constrained is not None and constrained.instance.id != instance.id:
        raise ValueError('constraint belongs to another instance')
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError('pipeline binding already active; use a separate process')
    audit = dict(tables_bound=0, forbidden_pairs=0, scored_rejected=0,
                 checked_instruction_rejected=0, checked_incumbents=0,
                 workers_started=0, worker_events=[], callback_errors=[], cleanup_errors=[],
                 reusable_workers_retired=[])
    owner = threading.get_ident()
    owned_path = None
    restores = []
    score_classes = set()
    mask_cache = {}
    best_lifts = float('inf')
    worker_table_logged = False
    checked = None
    original_check = None
    builder_wrappers = []
    owned_processes = []
    _STATE.active = audit

    def patch(obj, name, value):
        old = getattr(obj, name)
        restores.append((obj, name, old))
        setattr(obj, name, value)

    def same_instance(inst):
        return getattr(inst, 'id', None) == instance.id

    def patch_score(cls):
        if cls in score_classes or constrained is None:
            return
        score_classes.add(cls)
        original = cls.score

        def score(self, actions, *args, **kwargs):
            actions = tuple(actions)
            if same_instance(self.instance) and any(
                    hasattr(a, 'slot') and hasattr(a, 'box') and
                    constrained.forbids(a.box, a.slot) for a in actions):
                audit['scored_rejected'] += 1
                return None
            return original(self, actions, *args, **kwargs)

        patch(cls, 'score', score)

    try:
        if _audit_path is None:
            fd, owned_path = tempfile.mkstemp(prefix='.binding-worker-', suffix='.jsonl',
                                             dir=Path(__file__).resolve().parent)
            os.close(fd)
            _audit_path = owned_path
        if _worker:
            _event(_audit_path, 'worker_hook_active', inherited=False)

        with ExitStack() as stack:
            from experiments.mvp.pipeline.binding.runtime import runtime_cache_scope
            stack.enter_context(runtime_cache_scope())
            reusable_native = stack.enter_context(_reusable_ballast_scope(owned_processes, audit))
            if constrained is not None:
                import stowage.feasibility._engine as native
                import stowage.feasibility.tables as public_tables
                original_build = native.build_tables
                original_public_build = public_tables.build_tables
                binding_token = object()

                def instruction_allowed(table):
                    key = (tuple(table.box_ids), tuple(table.slot_ids))
                    allowed = mask_cache.get(key)
                    if allowed is None:
                        allowed = np.array([
                            [not constrained.forbids(box, slot) for slot in table.slot_ids]
                            for box in table.box_ids], dtype=bool).reshape(table.static.shape)
                        allowed.setflags(write=False)
                        mask_cache[key] = allowed
                    return allowed

                def bind_table(inst, table):
                    nonlocal worker_table_logged
                    if not same_instance(inst):
                        return table
                    # An ambient native mask wrapper can itself call the
                    # public builder. Apply this scope's intersection once,
                    # without changing the original builder's cached table.
                    if getattr(table, '_pipeline_binding_token', None) is binding_token:
                        return table
                    allowed = instruction_allowed(table)
                    table = copy(table)
                    table.static = np.logical_and(table.static, allowed)
                    table.static.setflags(write=False)
                    count = int(np.count_nonzero(~allowed))
                    table._pipeline_forbidden_pairs = count
                    table._pipeline_binding_token = binding_token
                    audit['tables_bound'] += 1
                    audit['forbidden_pairs'] = max(audit['forbidden_pairs'], count)
                    if not worker_table_logged:
                        _event(_audit_path, 'worker_table_bound', forbidden_pairs=count)
                        worker_table_logged = True
                    return table

                def build(inst):
                    return bind_table(inst, original_build(inst))

                def public_build(inst):
                    return bind_table(inst, original_public_build(inst))

                if original_public_build is original_build:
                    public_build = build
                builder_wrappers.append((build, original_build))
                if public_build is not build:
                    builder_wrappers.append((public_build, original_public_build))
                patch(native, 'build_tables', build)
                # Targeted CP construction and some constructor packing helpers
                # import the public builder directly instead of constructing a
                # feasibility engine. Their static variable domains must obey
                # exactly the same mask as page/LP/ladder feasibility tables.
                for module in tuple(sys.modules.values()):
                    namespace = getattr(module, '__dict__', None)
                    if namespace is not None:
                        for name, value in tuple(namespace.items()):
                            if value is original_public_build:
                                patch(module, name, public_build)
                # The exact constructor deliberately rebuilds optimistic DG
                # rows after reading T.static. Preserve that native relaxation
                # while reapplying the non-peelable instruction to its result.
                from engine.bounds.exact import strengthening
                original_unary = strengthening.safe_unary_mask

                def unary(inst, table):
                    static, info = original_unary(inst, table)
                    if same_instance(inst):
                        static = np.logical_and(static, instruction_allowed(table))
                    return static, info

                builder_wrappers.append((unary, original_unary))
                for module in tuple(sys.modules.values()):
                    namespace = getattr(module, '__dict__', None)
                    if namespace is not None:
                        for name, value in tuple(namespace.items()):
                            if value is original_unary:
                                patch(module, name, unary)
                # Native constructor lanes that use geometry predicates rather
                # than the incremental engine receive exactly the same filter.
                from llm.instructions.replan import constraint_rule
                stack.enter_context(constraint_rule(constrained))
                alns = importlib.import_module('alns')
                patch_score(alns.Problem)
                # Ladder uses a private absolute-import namespace. Patch its
                # newly loaded Problem after entering that namespace, before
                # the controller constructs or scores a candidate.
                from engine.peel_ladder import loader
                original_isolated = loader.isolated

                @contextmanager
                def isolated():
                    with original_isolated() as method:
                        patch_score(importlib.import_module('alns').Problem)
                        yield method

                patch(loader, 'isolated', isolated)

            # Any Process, including ProcessPoolExecutor workers, carries the
            # constraints explicitly rather than relying on inherited globals.
            process_cls = multiprocessing.process.BaseProcess
            original_start = process_cls.start

            def start(process, *args, **kwargs):
                if getattr(process, '_target', None) is not None:
                    target, target_args, target_kwargs = process._target, process._args, process._kwargs
                    process._target = _worker_entry
                    process._args = (target, target_args, target_kwargs, instance,
                                     constrained, _audit_path)
                    process._kwargs = {}
                    audit['workers_started'] += 1
                    # pytest may expose the namespace through an import hook
                    # while spawn only inherits concrete sys.path entries.
                    # Make the trampoline's repository package importable in
                    # the new interpreter before it unpickles the target.
                    repository = str(Path(__file__).resolve().parents[4])
                    old_path = list(sys.path)
                    target_module = sys.modules.get(getattr(target, '__module__', ''))
                    target_file = getattr(target_module, '__file__', None)
                    roots = [repository, str(Path(__file__).resolve().parents[1])]
                    if target_file:
                        # pytest can name this package ``binding`` rather than
                        # its repository-qualified namespace. Preserve the
                        # concrete import root of the original worker target.
                        depth = len(target.__module__.split('.'))-1
                        roots.append(str(Path(target_file).resolve().parents[depth]))
                    for root in roots:
                        if root not in sys.path:
                            sys.path.insert(0, root)
                    try:
                        value = original_start(process, *args, **kwargs)
                        if target is reusable_native._worker:
                            owned_processes.append((process, process.pid))
                        return value
                    except BaseException:
                        process._target, process._args, process._kwargs = target, target_args, target_kwargs
                        raise
                    finally:
                        sys.path[:] = old_path
                return original_start(process, *args, **kwargs)

            patch(process_cls, 'start', start)

            if on_checked is not None:
                import stowage.checker as checker
                original_check = checker.check

                def checked(inst, plan, *args, **kwargs):
                    nonlocal best_lifts
                    report = original_check(inst, plan, *args, **kwargs)
                    if (threading.get_ident() == owner and inst is instance
                            and report.passed and report.count is not None):
                        failures = constrained.violations(plan) if constrained is not None else []
                        if failures:
                            audit['checked_instruction_rejected'] += 1
                        else:
                            lifts = float(report.count.weighted_nonproductive)
                            if lifts < best_lifts:
                                best_lifts = lifts
                                audit['checked_incumbents'] += 1
                                try:
                                    on_checked(plan, report)
                                except Exception as error:
                                    audit['callback_errors'].append(f'{type(error).__name__}: {error}')
                    return report

                # Existing aliases and future ``from ... import check`` calls
                # both observe the wrapper. No checker verdict is modified.
                for module in tuple(sys.modules.values()):
                    namespace = getattr(module, '__dict__', None)
                    if namespace is None:
                        continue
                    for name, value in tuple(namespace.items()):
                        if value is original_check:
                            patch(module, name, checked)
            yield audit
    finally:
        def cleanup_error(stage, error):
            audit['cleanup_errors'].append(f'{stage}: {type(error).__name__}: {error}')

        try:
            # Imports after installation can capture checker observers or table
            # builders. Restore those aliases too; a completed invocation must
            # not leave its callbacks or instruction predicate behind.
            late_aliases = list(builder_wrappers)
            if checked is not None:
                late_aliases.append((checked, original_check))
            if late_aliases:
                for module in tuple(sys.modules.values()):
                    try:
                        namespace = getattr(module, '__dict__', None)
                        if namespace is not None:
                            for name, value in tuple(namespace.items()):
                                for wrapped, original in late_aliases:
                                    if value is wrapped:
                                        try:
                                            setattr(module, name, original)
                                        except Exception as error:
                                            cleanup_error(f'late hook alias {name}', error)
                                        break
                    except Exception as error:
                        cleanup_error('checker alias enumeration', error)
            for obj, name, original in reversed(restores):
                try:
                    setattr(obj, name, original)
                except Exception as error:
                    cleanup_error(f'hook restoration {name}', error)
            if owned_path is not None:
                try:
                    lines = Path(owned_path).read_text().splitlines()
                except Exception as error:
                    cleanup_error('worker audit read', error)
                    lines = []
                for number, line in enumerate(lines, 1):
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError('worker event must be a JSON object')
                        audit['worker_events'].append(event)
                    except Exception as error:
                        cleanup_error(f'worker audit line {number}', error)
                try:
                    Path(owned_path).unlink(missing_ok=True)
                except Exception as error:
                    cleanup_error('worker audit unlink', error)
        finally:
            # Evidence collection is optional. It cannot erase a held plan or
            # leave a process permanently busy after truncated worker output.
            try:
                _STATE.active = None
            finally:
                _LOCK.release()
