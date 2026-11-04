"""Frozen C5 assignment ladder, called only after medium L0 search has no pass."""
from pathlib import Path
from time import monotonic
import math

DEFAULT_BUDGETS = dict(proposal_s=240., finish_s=120., max_candidates=2)


def active(instance):
    """Match the ordinary assignment routing before importing numerical code."""
    size = getattr(instance, 'size_class', None)
    size = getattr(size, 'value', size)
    size = size.lower() if isinstance(size, str) else size
    if size in ('tiny', 'small'):
        return False
    if size in ('medium', 'large', 'full'):
        return True
    return len(getattr(instance, 'load_list', ())) > 60


def _guard(everything_found):
    if everything_found.get('checked_pass') is not None:
        raise ValueError('never peel after a full checker pass')
    if everything_found.get('passed') is True:
        raise ValueError('never peel after a full checker pass')


def _work(budgets):
    work = dict(DEFAULT_BUDGETS, **(budgets or {}))
    if set(work) != set(DEFAULT_BUDGETS):
        raise ValueError('unknown peel budget')
    for key in ('proposal_s', 'finish_s'):
        if isinstance(work[key], bool) or not math.isfinite(work[key]) or work[key] <= 0:
            raise ValueError('peel time budgets must be positive and finite')
    if (isinstance(work['max_candidates'], bool) or not isinstance(work['max_candidates'], int)
            or work['max_candidates'] < 1):
        raise ValueError('max_candidates must be a positive integer')
    return work



class _NativePassFound(BaseException):
    """Unwind optional private-controller work immediately after its full gate."""


def ordinary_pass(everything_found, *, error=None):
    """Serialize a caller-held native pass without risking its raw ownership."""
    plan = everything_found.get('checked_pass')
    report = everything_found.get('checked_report')
    if plan is None or report is None or not report.passed:
        return None
    complete = dict(level=0, column='T5', passed=True, full_checker_passed=True,
                    boxes_left_ashore=[], rules_broken=[], t0_peeled=False)
    row = dict(status='full_checker_pass', passed=True, relaxed=False, applicable=True,
               level=0, completed=complete, best_safe_partial=None, rules_broken=[],
               full_checker_passed=True, t0_unpeelable=True, ladder_pending=False,
               source=everything_found.get('checked_source', 'peel-native-full-pass'))
    if error is not None:
        row['post_pass_error'] = f'{type(error).__name__}: {error}'
    # Publication precedes every optional ranking/record conversion. A broken
    # serializer cannot erase the exact plan/report held by the caller.
    try:
        row['plan'] = plan.model_dump(mode='json')
        row['relaxed_plan'] = row['plan']  # existing public L0 promotion contract
    except Exception as failure:
        row['serialization_error'] = f'{type(failure).__name__}: {failure}'
    return row

def run(instance, everything_found, *, seed=0, budgets=None):
    """Return ordinary native passes immediately; otherwise relaxed D242 evidence.

    The copied controller receives this call's existing plans as its L0 bank,
    rather than invoking the ordinary planner again. All new LP and ballast
    candidates are checked by the original checker on the original instance.
    """
    _guard(everything_found)
    if not active(instance):
        return dict(status='inactive_size_route', passed=False, relaxed=False,
                    applicable=False, completed=None, best_safe_partial=None,
                    rules_broken=[], level=None, t0_unpeelable=True)
    work = _work(budgets)
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.frozen import active_stubs
    if rule_freeze_v1_enabled() or active_stubs():
        return dict(status='unsupported_native_domain', passed=False, relaxed=False,
                    applicable=False, completed=None, best_safe_partial=None,
                    rules_broken=[], level=None, t0_unpeelable=True)
    started = monotonic()
    from .loader import isolated, verify_copies
    with isolated() as method:
        original_observe = method.Candidates.observe
        def observe(bank, plan, report=None, source='candidate'):
            if plan is None:
                return original_observe(bank, plan, report=report, source=source)
            checked = report if report is not None else method.check(bank.instance, plan)
            if checked.passed:
                # Caller ownership is established before callbacks, analysis,
                # ranking or serialization can raise or replace this recipe.
                everything_found['checked_pass'] = plan
                everything_found['checked_report'] = checked
                everything_found['checked_source'] = source
                publish = everything_found.get('publish_checked_pass')
                if publish is not None:
                    try:
                        publish(plan, checked, source=source)
                    except Exception as failure:
                        everything_found.setdefault('peel_errors', []).append(
                            f'full-pass publication: {type(failure).__name__}: {failure}')
                raise _NativePassFound
            return original_observe(bank, plan, report=checked, source=source)
        method.Candidates.observe = observe
        try:
            def current_run(instance, *, bank, **kwargs):
                for plan in everything_found.get('plans', ()):
                    bank.observe(plan, source='same-invocation-L0-rejected')
                return None, dict(source='engine L0 already searched; same-invocation candidates only')
            try:
                _, row = method.solve(instance, seed=seed, budgets=work, normal_call=current_run)
            except _NativePassFound:
                return ordinary_pass(everything_found)
            except Exception as failure:
                held = ordinary_pass(everything_found, error=failure)
                if held is not None:
                    return held
                raise
        finally:
            method.Candidates.observe = original_observe
        # All nested reports retain native verdicts, but relaxed outcomes must
        # never count toward the engine's ordinary pass/optimality columns.
        for value in (row, row.get('completed'), row.get('best_safe_partial')):
            if isinstance(value, dict):
                value['full_checker_passed'] = bool(value.get('passed'))
                value['passed'] = False
                value['t0_peeled'] = False
        row.update(relaxed=True, passed=False, applicable=True,
                   status='relaxed_assignment_ladder', t0_unpeelable=True,
                   ladder_pending=False, budgets=work, wall_s=monotonic()-started,
                   source='frozen C5 measured assignment LP, separate from engine.assignment',
                   frozen_original_sha256=verify_copies())
        row.setdefault('rules_broken', [])
        row.setdefault('level', None)
        row['relaxed_plan'] = row.get('plan')
        return row
