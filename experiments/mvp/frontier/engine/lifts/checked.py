"""Bind a native checked plan to the frozen current-run lift polisher."""
import math
from time import monotonic

from alns import Problem, Scored
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker import check, departure_condition, report_matches_inputs
from stowage.moves import apply_plan

from .polish import polish


def _full_pass(report, instance, plan):
    return (report is not None and report.passed and report.count is not None
            and not report.shut_out and bool(report.parts)
            and all(part.ran for part in report.parts.values())
            and report_matches_inputs(report, instance, plan))


def _setup(instance, plan, problem):
    """Rebuild only local geometry and securing rows at the checked tank recipe."""
    from ..assignment.pages_sight import Model
    from ..assignment.gap import Assignment, Settings
    from ..assignment.chainlab import condition_terms, motions
    model = Model(problem, deck_tiers=None, lash_limit=1., vertical_weight=.02)
    ship = departure_condition(instance, plan)
    solver = Assignment(model, motions(condition_terms(instance, ship)), Settings(limit=1.))
    return model, solver


def polish_checked(instance, checked_plan, checked_report=None, *, seed=0, budget_s=30., problem=None):
    """Return (strictly cheaper native passing plan or exact held plan, metadata).

    Only a same-invocation ordinary full pass is eligible. No earlier plan,
    assignment, result or ballast file is read. Atomic setup and checker calls
    can overrun the wall-clock search allowance. Exceptions retain the input.
    """
    started = monotonic()
    meta = dict(kind='frozen_c6_fixed_topology_lift_polish', seed=seed,
                budget_s=budget_s, improved=False, held_plan_retained=True,
                groups_at_most=8, changed_boxes_at_most=32, cp_seconds_per_group=8.,
                numerical_threads=1, scope='native full pass; D15 fixed stow and no later loads')
    try:
        if not math.isfinite(budget_s) or budget_s < 0:
            raise ValueError('budget_s must be finite and nonnegative')
        deadline = started+budget_s
        report = checked_report
        if not _full_pass(report, instance, checked_plan):
            report = check(instance, checked_plan)
            meta['input_rechecked'] = True
        if not _full_pass(report, instance, checked_plan):
            meta['status'] = 'input_not_a_bound_full_pass'
            return checked_plan, meta
        original_lifts = float(report.count.weighted_nonproductive)
        meta.update(input_full_pass=True, incumbent_lifts=original_lifts, lifts=original_lifts)
        # The kept method fixes every occupied page. Arrival/discharge routes
        # need a different neighbourhood and remain with their held full pass.
        if instance.onboard.placements or instance.discharge_list:
            meta['status'] = 'outside_empty_arrival_family'
            return checked_plan, meta
        actions = tuple(get_action_space('boxslot').from_plan(instance, checked_plan))
        if (not all(isinstance(action, Place) for action in actions)
                or {action.box for action in actions} != set(instance.load_list)):
            meta['status'] = 'outside_complete_place_family'
            return checked_plan, meta
        replay = apply_plan(instance, checked_plan)
        if (not replay.legal or {p.container_id for p in replay.final_state.ship.placements}
                != set(instance.load_list)):
            meta['status'] = 'input_not_complete_replay'
            return checked_plan, meta
        if deadline-monotonic() < 15.:
            meta['status'] = 'insufficient_search_allowance'
            return checked_plan, meta
        problem = problem or Problem(instance)
        setup_started = monotonic()
        model, solver = _setup(instance, checked_plan, problem)
        meta['setup_s'] = monotonic()-setup_started
        incumbent = Scored(actions, original_lifts, checked_plan)
        result = polish(instance, incumbent, problem=problem, model=model, solver=solver,
                        deadline=deadline, seed=seed, metadata=meta)
        if result is incumbent or result.plan is checked_plan:
            meta['status'] = 'incumbent_retained'
            return checked_plan, meta
        # Never trust the compact port objective or a replay objective as the
        # improvement gate. Recheck the concrete returned recipe independently.
        final = check(instance, result.plan)
        if (_full_pass(final, instance, result.plan)
                and float(final.count.weighted_nonproductive) < original_lifts):
            meta.update(status='improved', improved=True, held_plan_retained=False,
                        lifts=float(final.count.weighted_nonproductive),
                        saved_lifts=original_lifts-float(final.count.weighted_nonproductive))
            return result.plan, meta
        meta['status'] = 'final_native_gate_rejected'
        return checked_plan, meta
    except Exception as error:
        meta.update(status='incumbent_retained_exception', error=f'{type(error).__name__}: {error}')
        return checked_plan, meta
    finally:
        meta['wall_s'] = monotonic()-started
