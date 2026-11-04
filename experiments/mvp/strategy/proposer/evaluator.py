"""Repair-free text adapter to the exact D175 headroom evaluation functions.

Every submitted string spends one proposal try, including invalid layouts.
Only completed terminal full-checker reports spend the checked-evaluation budget.
Every solver-complete layout gets V4 and the terminal checker; there is no ranking
screen, shared certificate, baseline solve, or repair in this adapter.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from experiments.mvp.strategy.headroom import run as headroom
from experiments.mvp.strategy.headroom.layout import BlockModel

DEFAULT_CONFIG = {
    'small_budget': 15.0, 'medium_budget': 60.0,
    'planner_seed': 0, 'finisher_budget': 600.0,
}


def evaluation_config(config=None):
    result = {**DEFAULT_CONFIG, **(config or {})}
    for key in ('small_budget', 'medium_budget', 'finisher_budget'):
        value = result[key]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{key} must be finite and positive')
    if result['finisher_budget'] > 600:
        raise ValueError('finisher_budget must be <= 600 seconds')
    if result['planner_seed'] != 0:
        raise ValueError('planner_seed must remain 0 to match headroom')
    return {key: result[key] for key in DEFAULT_CONFIG}


ACCOUNTING_VERSION = 2
V4_INTERNAL_CHECKER_WORK = 'unmeasured; includes child invocations and possibly interrupted checks'


def _work_fields(counters):
    """Expose terminal certificate work without claiming to count V4 children."""
    completed = counters['terminal_certificate_check_completed']
    return dict(counters, checked_evaluations=completed, evaluations_used=completed,
                proposal_tries=1, checker_accounting_version=ACCOUNTING_VERSION,
                all_full_checker_invocations=None,
                v4_internal_checker_work=V4_INTERNAL_CHECKER_WORK)


def _new_counters():
    return dict(terminal_certificate_check_calls=0, terminal_certificate_check_completed=0,
                terminal_certificate_check_status='not_started', terminal_check_accounting='observed')


def _legacy_counters(row, folder):
    """Infer only a completed cached certificate, never all full-checker work."""
    completed = int('checker_passed' in (row.get('check') or {}))
    old_path = folder / 'checker_calls' / f"{row['layout_id']}.json"
    calls = headroom.read_json(old_path)['calls'] if old_path.exists() else completed
    return dict(terminal_certificate_check_calls=max(calls, completed),
                terminal_certificate_check_completed=completed,
                terminal_certificate_check_status='completed' if completed else 'unknown_legacy',
                terminal_check_accounting='legacy_inferred')


def _finish_terminal_checks_counted(instance, model, row, folder, config):
    """Observe parent terminal certificate calls and completed reports only.

    V4 invokes the checker in a spawned child process, outside this wrapper.
    Those invocations (including interrupted work) remain explicitly unmeasured.
    Write before each call and after each return so interruption cannot become a
    fictitious completed report. Reusing a cached certificate spends no new work.
    Prior terminal work without a durable certificate fails closed on resume.
    """
    import stowage.checker as checker
    counter_path = folder / 'terminal_certificate_checks' / f"{row['layout_id']}.json"
    had_counter = counter_path.exists()
    legacy_path = folder / 'checker_calls' / f"{row['layout_id']}.json"
    counters = (headroom.read_json(counter_path) if had_counter else
                _legacy_counters(row, folder) if legacy_path.exists() else _new_counters())
    if counters['terminal_certificate_check_status'] == 'in_progress':
        counters['terminal_certificate_check_status'] = 'interrupted_or_unknown'
        headroom.atomic_json(counter_path, counters)
    certificate_path = folder / 'checks' / f"{row['layout_id']}.json"
    if certificate_path.exists():
        # Use the same durable headroom certificate directly, with no finisher
        # replay or terminal checker call across the resume boundary.
        result = dict(row, check=headroom.read_json(certificate_path))
        if not had_counter:
            counters = _legacy_counters(result, folder)
        elif 'checker_passed' in result['check'] and not counters['terminal_certificate_check_completed']:
            counters.update(terminal_certificate_check_calls=max(1, counters['terminal_certificate_check_calls']),
                            terminal_certificate_check_completed=1, terminal_certificate_check_status='completed',
                            terminal_check_accounting='certificate_inferred')
        headroom.atomic_json(counter_path, counters)
        return result, counters
    if counters['terminal_certificate_check_calls']:
        raise RuntimeError(
            f"attempt {row['layout_id']} has prior terminal checker work but no durable "
            "headroom certificate; completion is unknown/incomplete and rerunning is "
            "refused. Requires new output and explicit accounting of prior work.")
    original = checker.check

    def counted(*args, **kwargs):
        counters['terminal_certificate_check_calls'] += 1
        counters['terminal_certificate_check_status'] = 'in_progress'
        headroom.atomic_json(counter_path, counters)
        try:
            report = original(*args, **kwargs)
        except BaseException as error:
            counters['terminal_certificate_check_status'] = (
                'exception' if isinstance(error, Exception) else 'interrupted_or_unknown')
            headroom.atomic_json(counter_path, counters)
            raise
        counters['terminal_certificate_check_completed'] += 1
        counters['terminal_certificate_check_status'] = 'completed'
        headroom.atomic_json(counter_path, counters)
        return report

    checker.check = counted
    try:
        result = headroom.finish_check(instance, model, row, folder, config)
    finally:
        checker.check = original
    # Headroom cache records from before terminal instrumentation can attest to
    # one completed terminal report, but cannot reconstruct all checker work.
    if not had_counter and counters['terminal_certificate_check_calls'] == 0 and (
            'checker_passed' in (result.get('check') or {})):
        counters = _legacy_counters(result, folder)
    headroom.atomic_json(counter_path, counters)
    return result, counters


def evaluate_text(instance, text, folder, *, attempt_id='proposal-001', config=None):
    """Return persisted attempt, checked moves only, and explicit work counters.

    ``proposal_tries`` is one, including invalid text. ``evaluations_used`` and
    ``checked_evaluations`` count completed terminal full-checker reports, pass
    or fail. Invalid text, planner failure, V4 timeout and raised terminal checks
    do not consume a checked evaluation. V4 internal checker work is unmeasured.
    Resuming an identical attempt reuses its durable records. Changing its
    text/config/instance under the same ID is refused.
    """
    from experiments.mvp.strategy.proposer.format import LayoutError, parse_layout
    from stowage.rules.catalogue import rule_freeze_v1

    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', attempt_id):
        raise ValueError('attempt_id must be a safe, short filename component')
    config = evaluation_config(config)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'attempts' / f'{attempt_id}.json'
    identity = {
        'instance_sha256': hashlib.sha256(json.dumps(
            instance.model_dump(mode='json'), sort_keys=True).encode()).hexdigest(),
        'text': text, 'config': config,
        'rules': {'rule_freeze_v1': False, 'global_screen': 0, 'v4': True},
    }
    guard = folder / 'requests' / f'{attempt_id}.json'
    if guard.exists() and headroom.read_json(guard) != identity:
        raise ValueError(f'attempt {attempt_id} already belongs to a different request')
    headroom.atomic_json(guard, identity)
    if target.exists():
        cached = headroom.read_json(target)
        if cached.get('checker_accounting_version') != ACCOUNTING_VERSION:
            cached.update(_work_fields(_legacy_counters(cached, folder)))
            headroom.atomic_json(target, cached)
        return cached
    model = BlockModel.from_instance(instance)
    try:
        with rule_freeze_v1(enabled=False):
            layout = parse_layout(text, instance, model=model)
    except LayoutError as error:
        row = dict(layout_id=attempt_id, status='invalid_layout', valid_layout=False,
                   message=str(error), passed=False, moves=None,
                   solver_evaluations=0, finisher_evaluations=0,
                   wasted_try=True, repair_attempted=False, **_work_fields(_new_counters()))
    else:
        # These are the headroom functions themselves, with identical config,
        # global-screen setting, rule epoch, ballast reset and conformance gates.
        with rule_freeze_v1(enabled=False):
            row = headroom.evaluate(instance, model, layout, attempt_id,
                                    'proposer', folder, config)
            row, counters = _finish_terminal_checks_counted(instance, model, row, folder, config)
        check = row.get('check') or {}
        passed = check.get('passed') is True
        row = dict(row, valid_layout=True, passed=passed,
                   moves=check.get('moves') if passed else None,
                   solver_evaluations=1, finisher_evaluations=int(bool(check)),
                   wasted_try=False, repair_attempted=False, **_work_fields(counters))
    headroom.atomic_json(target, row)
    return row
