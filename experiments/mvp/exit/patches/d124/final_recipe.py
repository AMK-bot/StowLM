"""D110 final adapter audit with the pre-registered 9.1 row-d decode recipe.

Uses own model samples only: best-of-four with bounded backtracking and current
layered masks. No external repair, baseline, gold, or LNS plan enters this audit.
Importing this module does not import MLX or initialize Metal.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import time


class FinalAuditCheckUnavailable(BaseException):
    """Bypass planner Exception handlers; unknown checks are not bad plans."""

    def __init__(self, details):
        self.details = details
        super().__init__(details.get('unavailable_reason', 'checker unavailable'))


@contextmanager
def scoped_checker_watchdog(attempts, *, timeout_s=300.):
    """Intercept planner-local checker imports while the resident lock is held."""
    import stowage.checker as module
    from llm.exit.checker_watchdog import run_full_check
    original = module.check

    def check(instance, plan):
        started = time.monotonic()
        try:
            checked = run_full_check(instance, plan, timeout_s=timeout_s)
            details = dict(checker_available=checked.available,
                checker_watchdog=dict(checked.details(), deadline_s=timeout_s))
            if checked.available:
                details.update(report=checked.report.model_dump(mode='json'),
                               passed=checked.report.passed)
            else:
                details.update(unavailable=True, unavailable_reason=checked.reason, passed=False)
        except Exception as error:
            details = dict(checker_available=False, unavailable=True, passed=False,
                unavailable_reason='checker_process_or_transport_error',
                checker_watchdog=dict(expired=False, deadline_s=timeout_s,
                    error=f'{type(error).__name__}: {error}'))
        details.update(stage='planner_internal', wall_s=time.monotonic()-started)
        attempts.append(details)
        if not details['checker_available']:
            raise FinalAuditCheckUnavailable(details)
        return checked.report

    module.check = check
    try:
        yield
    finally:
        module.check = original


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False))
    temporary.replace(path)


def _pins(parent):
    from llm.exit.runner import adapter_identity, source_pins
    return dict(adapter=adapter_identity(parent), sources=source_pins())


@contextmanager
def scoped_v4_finalize():
    """Enable V4 only inside individual finalize calls; always restore the hook."""
    import stowage.planner as module
    from llm.exit.selection import _gold_driver
    original = module.finalize
    scope = _gold_driver().scoped_ballast_v4
    def finalize(*args, **kwargs):
        with scope(True):
            return original(*args, **kwargs)
    module.finalize = finalize
    try:
        yield
    finally:
        module.finalize = original


def _admit_cached(row, instance, identity):
    if row.get('cache_identity') != identity or row.get('instance_id') != instance.id:
        raise RuntimeError('final-recipe cached row identity changed')
    if row.get('passed') is True:
        from stowage.model import CheckReport, MovePlan
        from stowage.checker import CHECKER_VERSION, report_matches_inputs
        from stowage.frozen import require_clean
        plan = MovePlan.model_validate(row['plan'])
        report = CheckReport.model_validate(row['verification']['report'])
        require_clean(instance, plan, report, what='cached final row-d audit')
        if (not report.passed or report.provenance.versions.get('checker') != CHECKER_VERSION
                or not report_matches_inputs(report, instance, plan) or report.count is None
                or report.count.weighted_nonproductive != row['weighted_moves']
                or row['verification'].get('mask', {}).get('passed') is not True
                or row.get('source') != 'own_model' or row.get('recipe') != 'row_d'):
            raise RuntimeError('final-recipe cached pass lacks bound current own-plan evidence')
    return row


def evaluate_recipe(parent, paths, directory, args):
    """Return own-plan checked rows, retaining the existing resident base model.

    args.final_recipe_time_limit defaults to 600 seconds per planner invocation.
    The production planner's decode/finisher deadline is cooperative. Each full
    checker invocation has a separate 300-second owned-process watchdog; expiry
    makes the audit row unavailable. Final verification remains mandatory.
    """
    from llm.exit import runtime
    from llm.exit.identity import identity_guard, DEFAULT_CORPUS
    from llm.exit.selection import Candidate, decoder_factory, verify_candidate
    from llm.exit.outcomes import checker_unavailable, outcome_counts
    from llm.stowlm.planner import StowLMPlanner, DEFAULT_MODEL
    from stowage.feasibility import FeasibilityConfig
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        raise RuntimeError('final recipe requires demo rule-freeze switch OFF')
    if os.environ.get('STOWAGE_BALLAST_V4') not in (None, '', '0'):
        raise RuntimeError('V4 must remain off outside scoped finalization')
    limit = float(getattr(args, 'final_recipe_time_limit', 600.))
    max_tokens = int(getattr(args, 'max_tokens', 20_000))
    if not math.isfinite(limit) or limit <= 0 or max_tokens < 1:
        raise ValueError('final recipe deadline and max_tokens must be positive')
    paths = [Path(path).resolve() for path in paths]
    instances = [load_instance(path) for path in paths]
    guard = identity_guard(getattr(args, 'corpus', None) or DEFAULT_CORPUS)
    for instance, path in zip(instances, paths):
        guard.require_allowed(instance, role='audit', input_path=path)
    parent = Path(parent).resolve()
    model_path = str(getattr(args, 'model_path', None) or DEFAULT_MODEL)
    recipe = dict(name='row_d', n_samples=4, greedy_first=True, temperature=.7,
                  backtrack=32, candidate_finalize_s=90., global_screen=0,
                  max_tokens=max_tokens, time_limit_s=limit, model_path=model_path,
                  layers=['production', 'lookahead', 'securing'], lns=False,
                  check_watchdog=dict(version='d124-owned-subprocess-v1', timeout_s=300.),
                  seed=getattr(args, 'seed', 0))
    pins = _pins(parent)
    identity = dict(**pins, maps=[(str(path), hashlib.sha256(path.read_bytes()).hexdigest())
                                  for path in paths], recipe=recipe)
    identity = json.loads(json.dumps(identity))
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = Path(directory)
    identity_path = directory / 'identity.json'
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise RuntimeError('final-recipe cache adapter, maps, source or recipe identity changed')
    _write(identity_path, identity)
    rows = []
    for index, (path, instance) in enumerate(zip(paths, instances)):
        target = directory / f'{index:03d}.json'
        if target.exists():
            rows.append(_admit_cached(json.loads(target.read_text()), instance, digest))
            continue
        started = time.monotonic()
        attempts = []
        unavailable = None
        result = None
        with runtime._LOCK:
            planner = StowLMPlanner(model_path=model_path, adapter_path=str(parent),
                temperature=.7, n_samples=4, greedy_first=True, backtrack=32,
                candidate_finalize_s=90., max_tokens=max_tokens,
                engine_config=FeasibilityConfig(extra={'global_screen': 0}),
                decoder_factory=decoder_factory())
            runtime._resident_for(planner)
            try:
                with scoped_v4_finalize(), scoped_checker_watchdog(attempts):
                    result = planner.plan(instance, seed=getattr(args, 'seed', 0) + index,
                                          time_limit_s=limit)
            except FinalAuditCheckUnavailable as error:
                unavailable = error.details
            planned = time.monotonic()
            checked = None
            remaining = limit - (planned - started)
            if unavailable is None and result.status == 'ok' and result.plan is not None and remaining > 0:
                checked = verify_candidate(instance, Candidate(result.plan, 'model'),
                                           ballast_s=min(90., remaining))
        verification = (unavailable if unavailable is not None else checked.details if checked
                        else dict(error='no complete own plan within deadline'))
        if checked is not None and 'checker_available' in checked.details:
            attempts.append(dict(checked.details, stage='final_verify', passed=checked.passed))
        passed = bool(checked and checked.passed and not checker_unavailable(verification))
        row = dict(instance_id=instance.id, instance_path=str(path), passed=passed,
            weighted_moves=checked.weighted_moves if passed else None,
            recipe='row_d', source='own_model', lns=False, cache_identity=digest,
            planner_status=result.status if result is not None else 'checker_unavailable',
            planner_metrics=result.metrics if result is not None else {},
            verification=verification, unavailable=checker_unavailable(verification),
            checker_attempts=attempts, verification_counts=outcome_counts(attempts),
            plan=checked.plan.model_dump(mode='json') if passed else None,
            timings=dict(plan_s=planned-started, verification_s=time.monotonic()-planned,
                         wall_s=time.monotonic()-started),
            deadline_mode='cooperative planner; each full check has a 300s owned-process watchdog')
        _write(target, row)
        rows.append(row)
    if _pins(parent) != pins:
        raise RuntimeError('final-recipe source or adapter changed during audit')
    _write(directory / 'summary.json', rows)
    return rows
