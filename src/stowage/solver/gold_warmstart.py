"""Opt-in gold planner: checked two-step fallback plus strict yard-order LNS.

The baseline stays outside search, so timeouts, failed screens and checker
vetoes cannot replace it with a worse or unverified proposal. Ordering repairs
retain every final box-to-slot assignment and reuse ballast only as a proposal.
Every admitted improvement passes actual weighted move replay, the public
C2-off mask, D92 design-envelope securing and the unchanged full checker.
No default solver, rule, two-step policy or environment variable is changed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from time import monotonic

from stowage.model import MovePlan, PlannerResult


VERSION = '1.0.0'
NAME = 'solver.gold_warmstart'


@dataclass(frozen=True)
class GoldWarmstartConfig:
    baseline_time_limit_s: float = 120.
    ballast_time_limit_s: float = 90.
    lns_time_limit_s: float = 15.
    validation_reserve_s: float = 12.
    max_neighborhoods: int = 64
    max_candidates_to_check: int = 1

    def __post_init__(self):
        if any(not math.isfinite(value) or value < 0. for value in (
                self.baseline_time_limit_s, self.ballast_time_limit_s,
                self.lns_time_limit_s, self.validation_reserve_s)):
            raise ValueError('gold budgets must be finite and nonnegative')
        if self.max_neighborhoods < 0 or self.max_candidates_to_check < 1:
            raise ValueError('invalid gold neighborhood/candidate limit')


def _as_result(plan_or_result, seed):
    if isinstance(plan_or_result, PlannerResult):
        return plan_or_result
    if not isinstance(plan_or_result, MovePlan):
        raise TypeError('incumbent must be a MovePlan or PlannerResult')
    return PlannerResult(status='ok', plan=plan_or_result, planner='baseline.twostep',
                         planner_version='supplied', seed=seed,
                         provenance=plan_or_result.provenance)


def _bound_report(instance, result):
    """Reuse only the pipeline's private evidence bound to this exact input.

    A metrics flag, passed boolean or deserialized result is never evidence.
    """
    if instance.plan_calls or result.later_plans:
        return None  # current-call evidence cannot certify a later-call scope
    evidence = result._shipping_evidence
    if evidence is None or result.plan is None:
        return None
    _, instance_json, plan_json, report, report_json = evidence
    if (instance.model_dump_json() != instance_json
            or result.plan.model_dump_json() != plan_json
            or report.model_dump_json() != report_json or not report.passed):
        return None
    return report


def _mask_replay(instance, plan, *, deadline=math.inf, screen=None):
    from stowage.actions import get_action_space
    from stowage.actions.expander import Expander
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.solver.securing_geometry import securing_geometry_violation
    space = get_action_space('boxslot')
    engine = make_feasibility_engine(instance, space,
                                    config=FeasibilityConfig(extra={'global_screen':0}))
    ex, count = Expander(instance), 0
    for action in space.from_plan(instance, plan):
        if monotonic() >= deadline:
            return dict(passed=False, cause='timeout', actions=count)
        if not engine.is_legal(action):
            return dict(passed=False, cause='public_mask', actions=count)
        if securing_geometry_violation(instance, ex, action):
            return dict(passed=False, cause='securing_geometry', actions=count)
        if screen is not None and not screen.check_placement(ex, action).passed:
            return dict(passed=False, cause='design_securing_prefix', actions=count)
        if monotonic() >= deadline:
            return dict(passed=False, cause='timeout', actions=count)
        engine.push(action)
        ex.step(action)
        count += 1
    return dict(passed=engine.is_complete(), cause=None if engine.is_complete() else 'incomplete',
                actions=count)


def qualify_baseline(instance, incumbent, *, seed=0):
    """Check one supplied finalized two-step plan; no ballast search occurs.

    Conservative screen failures remain explicit fallback qualifications. A
    full-checker-passing baseline is retained even outside the design envelope;
    no new fixed-stow candidate is admitted outside that envelope.
    """
    from stowage.checker import check
    from stowage.moves import apply_plan
    from stowage.solver.securing_screen import DeckSecuringScreen
    started = monotonic()
    baseline = _as_result(incumbent, seed)
    metrics = dict(baseline.metrics)
    qualified = baseline.model_copy(update={'metrics':metrics})
    if instance.plan_calls or baseline.later_plans:
        metrics['baseline_qualification_error'] = 'later-call scope unsupported'
        return qualified.model_copy(update={'status':'error'})
    if baseline.status != 'ok' or baseline.plan is None or baseline.plan.ballast is None:
        metrics['baseline_qualification_error'] = 'complete finalized baseline required'
        return qualified.model_copy(update={'status':'error'})
    try:
        before = monotonic()
        report = _bound_report(instance, baseline) or check(instance, baseline.plan)
        metrics['baseline_check_s'] = monotonic()-before
        metrics['baseline_checker_passed'] = report.passed
        if not report.passed:
            return qualified.model_copy(update={'status':'error', 'message':'Baseline full checker rejected'})
        replay = apply_plan(instance, baseline.plan)
        if not replay.legal:
            return qualified.model_copy(update={'status':'error', 'message':'Baseline move replay rejected'})
        metrics['baseline_objective'] = replay.count.weighted_nonproductive
        before = monotonic()
        metrics['baseline_mask'] = _mask_replay(instance, baseline.plan)
        metrics['baseline_mask_s'] = monotonic()-before
        if not metrics['baseline_mask']['passed']:
            return qualified.model_copy(update={'status':'error',
                'message':'Baseline public mask/completeness qualification rejected'})
        qualified = qualified.model_copy(update={'provenance':qualified.provenance.merge(report.provenance)})
        qualified._record_full_check(instance, report)
        before = monotonic()
        securing = DeckSecuringScreen(instance).evaluate(replay.final_state.ship.placements)
        metrics['baseline_design_securing_passed'] = securing.passed
        metrics['baseline_securing_screen_passed'] = securing.passed
        metrics['baseline_design_securing_reason'] = securing.reason
        metrics['baseline_design_securing_s'] = monotonic()-before
        return qualified
    except Exception as error:
        metrics['baseline_qualification_error'] = f'{type(error).__name__}: {error}'
        if (_bound_report(instance, qualified) is not None
                and metrics.get('baseline_mask', {}).get('passed', False)):
            # A conservative search-screen/setup failure cannot erase a
            # genuinely checked baseline outside the search domain.
            metrics.setdefault('baseline_design_securing_passed', False)
            metrics.setdefault('baseline_securing_screen_passed', False)
            return qualified
        return qualified.model_copy(update={'status':'error'})
    finally:
        metrics['baseline_qualification_s'] = monotonic()-started


def improve_checked(instance, incumbent, *, time_limit_s=15., seed=0, config=None):
    """Try bounded fixed-stow LNS; retain the independently checked baseline.

    This budget includes search and candidate admission. Baseline qualification
    occurs first if private bound evidence is missing, and is reported separately.
    Opaque mask/checker calls are indivisible; real overrun is reported. For
    paired 15/30/60s trials, qualify once and reuse that same checked result.
    """
    from stowage.checker import check
    from stowage.frozen import current_provenance
    from stowage.moves import apply_plan
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.solver.gold_warmstart_search import search
    from stowage.solver.securing_screen import DeckSecuringScreen
    cfg = config or GoldWarmstartConfig()
    started = monotonic()
    baseline = _as_result(incumbent, seed)
    if (_bound_report(instance, baseline) is None
            or 'baseline_design_securing_passed' not in baseline.metrics
            or 'baseline_mask' not in baseline.metrics
            or 'baseline_objective' not in baseline.metrics):
        baseline = qualify_baseline(instance, baseline, seed=seed)
    metrics = dict(baseline.metrics)
    metrics.update(source='two_step_fallback', improved=False, num_workers=1,
                   global_screen=0, securing_screen=True, lns_budget_s=time_limit_s,
                   lns_search_s=0., acceptance_check_s=0., acceptance_mask_s=0.,
                   candidate_gates=[], baseline_setup_in_call_s=monotonic()-started)
    report = (_bound_report(instance, baseline) if baseline.status == 'ok'
              and metrics.get('baseline_mask', {}).get('passed', False) else None)
    best = baseline.plan
    selected_report = report
    budget_started = monotonic()
    objective = metrics.get('baseline_objective')

    def finish(message=None):
        elapsed = monotonic()-budget_started
        metrics.update(objective=objective, objective_before=metrics.get('baseline_objective'),
            objective_after=objective, lns_elapsed_s=elapsed,
            lns_budget_overrun_s=max(0., elapsed-max(0., time_limit_s)) if math.isfinite(time_limit_s) else None,
            total_warmstart_s=monotonic()-started,
            checker_passed=selected_report.passed if selected_report is not None else False,
            requires_finalize_and_check=selected_report is None)
        provenance = current_provenance(created_by=NAME, seed=seed).merge(
            instance.provenance, baseline.provenance, *([best.provenance] if best is not None else []))
        result = PlannerResult(status='ok' if selected_report is not None else 'error',
            plan=best, planner=NAME, planner_version=VERSION, seed=seed,
            wall_time_s=monotonic()-started, metrics=metrics, message=message, provenance=provenance)
        if selected_report is not None:
            result._record_full_check(instance, selected_report)
        return result

    if report is None or best is None:
        return finish('No checker-passing finalized baseline available')
    if rule_freeze_v1_enabled():
        return finish('Warm-start search is scoped to the demo rule set (freeze off)')
    if not math.isfinite(time_limit_s) or time_limit_s < 0.:
        return finish('Invalid LNS budget; retained checked baseline')
    if time_limit_s == 0.:
        return finish('No LNS budget; retained checked baseline')
    if (not metrics.get('baseline_design_securing_passed', False)
            or not metrics.get('baseline_mask', {}).get('passed', False)):
        metrics['skipped_lns_reason'] = ('baseline_outside_design_envelope'
            if not metrics.get('baseline_design_securing_passed', False) else 'baseline_mask_not_qualified')
        return finish('Baseline outside search qualification; retained full-checked fallback')
    deadline = budget_started+time_limit_s
    # APPROXIMATION: reserve a bounded share for one candidate's mask/full gate.
    # The full checker is never shortened or replaced when this estimate misses.
    estimate = max(cfg.validation_reserve_s,
        metrics.get('baseline_check_s', 0.)*1.2+metrics.get('baseline_mask_s', 0.)*2+.5)
    reserve = min(estimate, time_limit_s*.8)
    metrics.update(validation_reserve_estimate_s=estimate, validation_reserve_s=reserve)
    try:
        reference = apply_plan(instance, best)
        objective = reference.count.weighted_nonproductive
        metrics['baseline_objective'] = objective
        before = monotonic()
        proposals, diagnostic = search(instance, best, deadline=deadline-reserve,
            seed=seed, max_neighborhoods=cfg.max_neighborhoods,
            keep=cfg.max_candidates_to_check, baseline_replay=reference)
        metrics['lns_search_s'] = monotonic()-before
        metrics['search'] = diagnostic
        screen = DeckSecuringScreen(instance)
        original_mapping = {p.container_id:p.slot for p in reference.final_state.ship.placements}
        for proposal in proposals[:cfg.max_candidates_to_check]:
            if monotonic() >= deadline:
                break
            candidate = proposal.plan
            gate = dict(neighborhood=proposal.neighborhood, accepted=False)
            metrics['candidate_gates'].append(gate)
            replay = apply_plan(instance, candidate)
            value = replay.count.weighted_nonproductive
            gate['objective'] = value
            if (not replay.legal or value >= objective-1e-9
                    or {p.container_id:p.slot for p in replay.final_state.ship.placements} != original_mapping
                    or candidate.ballast != baseline.plan.ballast):
                gate['rejection'] = 'not strictly cheaper with identical fixed stow/ballast proposal'
                continue
            securing = screen.evaluate(replay.final_state.ship.placements)
            gate['design_securing_passed'] = securing.passed
            if not securing.passed:
                gate['rejection'] = 'design securing'
                continue
            before = monotonic()
            mask = _mask_replay(instance, candidate, deadline=deadline, screen=screen)
            metrics['acceptance_mask_s'] += monotonic()-before
            gate['mask'] = mask
            if not mask['passed'] or monotonic() >= deadline:
                gate['rejection'] = 'mask or deadline'
                continue
            before = monotonic()
            candidate_report = check(instance, candidate)
            metrics['acceptance_check_s'] += monotonic()-before
            gate['full_checker_passed'] = candidate_report.passed
            if not candidate_report.passed:
                gate['rejection'] = 'full checker'
                continue
            if monotonic() >= deadline:
                gate['rejection'] = 'deadline after indivisible full check'
                continue
            best, objective, selected_report = candidate, value, candidate_report
            gate['accepted'] = True
            metrics.update(source='fixed_stow_yard_lns', improved=True)
            break
        return finish('Strict checked move improvement' if metrics['improved'] else 'Retained checked two-step fallback')
    except Exception as error:
        metrics['lns_error'] = f'{type(error).__name__}: {error}'
        return finish('Search error; retained checked two-step fallback')


def solve_gold(instance, *, seed=0, baseline_time_limit_s=120., ballast_time_limit_s=90.,
               lns_time_limit_s=15., config=None):
    """Fresh read-only two-step -> scoped V4 ballast -> checker -> yard LNS.

    Stage budgets are separate and actual total time is recorded. The direct
    opt-in V4 dispatch avoids process-wide environment changes during planning.
    """
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.ballast_v4_dispatch import finish as finish_ballast
    from stowage.solver.twostep import TwoStepPlanner
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    started = monotonic()
    if instance.plan_calls or rule_freeze_v1_enabled():
        return PlannerResult(status='error', planner=NAME, planner_version=VERSION,
            seed=seed, provenance=instance.provenance, wall_time_s=monotonic()-started,
            metrics={'end_to_end_s':monotonic()-started},
            message='Gold warm start supports current-call demo rules with freeze off only')
    baseline = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen':0})).plan(
        instance, seed=seed, time_limit_s=baseline_time_limit_s)
    metrics = dict(baseline.metrics)
    metrics['baseline_planning_s'] = monotonic()-started
    metrics['stage_budgets_s'] = dict(baseline=baseline_time_limit_s,
                                    ballast=ballast_time_limit_s, lns=lns_time_limit_s)
    if baseline.status != 'ok' or baseline.plan is None:
        metrics['end_to_end_s'] = monotonic()-started
        return baseline.model_copy(update={'metrics':metrics, 'wall_time_s':metrics['end_to_end_s']})
    before = monotonic()
    outcome = finish_ballast(instance, baseline.plan, time_limit_s=ballast_time_limit_s, seed=seed)
    metrics.update(baseline_ballast_s=monotonic()-before, ballast_status=outcome.status,
                   ballast_finisher='explicit opt-in V4 dispatch; no environment changes')
    metrics['baseline_ballast_budget_overrun_s'] = max(0., metrics['baseline_ballast_s']-ballast_time_limit_s)
    if outcome.status not in ('ok','kept') or outcome.plan is None:
        metrics['not_found_cause'] = getattr(outcome, 'not_found_cause', None)
        metrics['end_to_end_s'] = monotonic()-started
        return baseline.model_copy(update={'status':'error', 'metrics':metrics,
                                           'wall_time_s':metrics['end_to_end_s'],
                                           'message':'Two-step ballast not found; no gold baseline'})
    baseline = baseline.model_copy(update={'plan':outcome.plan, 'metrics':metrics,
                                           'provenance':baseline.provenance.merge(outcome.provenance)})
    baseline = qualify_baseline(instance, baseline, seed=seed)
    if baseline.status != 'ok' or _bound_report(instance, baseline) is None:
        baseline.metrics['end_to_end_s'] = monotonic()-started
        return baseline.model_copy(update={'wall_time_s':baseline.metrics['end_to_end_s']})
    result = improve_checked(instance, baseline, time_limit_s=lns_time_limit_s, seed=seed, config=config)
    result.metrics['end_to_end_s'] = monotonic()-started
    return result.model_copy(update={'wall_time_s':result.metrics['end_to_end_s']})


class GoldWarmstartPlanner:
    name, version, sets_ballast, deterministic = NAME, VERSION, True, False

    def __init__(self, config=None):
        self.config = config or GoldWarmstartConfig()

    def plan(self, instance, *, seed=0, time_limit_s=None):
        """Separate stage caps; a generic caller can restrict, never inflate LNS."""
        cfg = self.config
        return solve_gold(instance, seed=seed, baseline_time_limit_s=cfg.baseline_time_limit_s,
            ballast_time_limit_s=cfg.ballast_time_limit_s,
            lns_time_limit_s=cfg.lns_time_limit_s if time_limit_s is None else min(cfg.lns_time_limit_s, time_limit_s),
            config=cfg)
