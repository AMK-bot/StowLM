"""Securing proof, direct/LP/real-area rescue, inherited V4, native V4 and full gate."""
from time import perf_counter

from stowage.checker import check
from stowage.model import PlannerResult
from stowage.moves import apply_plan
from .. import NAME, VERSION
from ..benders.ballast import solve_ballast
from ..benders.area_securing import eligible as area_eligible, refine as refine_area_securing
from ..frozen import planner as frozen_planner

from .early_bound import securing_bound
from .fast_finish import finish_inherited


def _checked(instance, result, *, seed):
    if result is None:
        return None, None
    if not isinstance(result, PlannerResult):
        result = PlannerResult(status='ok', plan=result, planner=NAME,
                               planner_version=VERSION, seed=seed)
    if result.status != 'ok' or result.plan is None:
        return None, None
    report = check(instance, result.plan)
    if not report.passed:
        return None, report
    result = result.model_copy(update={'planner': NAME, 'planner_version': VERSION})
    return result, report


def finish_chain(instance, plan, *, seed=0, finalize_s=600., label='candidate',
                 inherited=True, securing=True, ballast_lp=True, area_securing=True,
                 area_permutation=True, area_securing_s=120.,
                 fixed_stow_screen=True, map_lower_bound=None):
    """Every successful lane is gated by the ordinary full checker.

    The inherited and local LP lanes are extra time. Native V4 retains its complete baseline
    budget when both faster lanes fail, preserving the 0.9.0 finishing floor.
    The area/securing lane precedes even the accelerated inherited-V4 lane.
    A securing certificate excludes this fixed stow only, never cargo permutation.
    """
    if len(instance.load_list) <= 60:
        finalize_s = max(300., finalize_s)
    started = perf_counter()
    record = dict(label=label, passed=False, stages=[])
    fixed_impossible = False
    if securing:
        before = perf_counter()
        replay = apply_plan(instance, plan)
        if not replay.legal:
            record.update(finalize_status='illegal_replay', finalize_s=perf_counter()-started)
            return None, record
        bound = securing_bound(instance, replay.final_state.ship)
        record['stages'].append(dict(stage='securing_bound', seconds=perf_counter()-before, **bound))
        fixed_impossible = bound.get('proof') is not None
        if fixed_impossible and not (area_securing and area_permutation):
            record.update(finalize_status='infeasible_fixed_stow', finalize_s=perf_counter()-started)
            return None, record
    before = perf_counter()
    # A raw plan with no ballast uses the ship's ordinary default tanks.
    result, report = _checked(instance, plan, seed=seed)
    record['stages'].append(dict(stage='direct', seconds=perf_counter()-before,
                                 passed=result is not None,
                                 violations=sorted({v.rule_id for v in report.violations}) if report else []))
    if result is not None:
        record.update(passed=True, weighted=float(report.count.weighted_nonproductive),
                      finalize_status='ok', lane='direct', finalize_s=perf_counter()-started)
        return result, record
    # Ballast cannot repair an exact native static hard violation. Keep the
    # candidate in the archive for future cargo/tier work, but skip every tank lane.
    if fixed_stow_screen:
        screen = fixed_stow_failure(instance, plan, report, map_lower_bound=map_lower_bound)
        record['stages'].append(dict(stage='fixed_stow_screen', **screen))
        if screen['skip_ballast']:
            record.update(finalize_status='excluded_fixed_stow', finalize_s=perf_counter()-started,
                          fixed_stow_failure=screen, lane='fixed_stow_screen')
            return None, record
    # A rejected supplied recipe is a seed only. Native finalize skips search
    # whenever ballast is present, so later lanes receive the same cargo bare.
    search_plan = plan.model_copy(update={'ballast': None}) if plan.ballast is not None else plan
    area_plan, area_report = plan, report
    if ballast_lp and not fixed_impossible:
        before = perf_counter()
        outcome = solve_ballast(instance, search_plan,
            budget_s=min(10., max(1., finalize_s*.05)), max_iterations=3,
            exact_recheck=False)
        candidate = outcome.pop('candidate_plan', None)
        # Local hydro tangents are guidance only. Even a successful nonlinear
        # loading probe needs every ordinary checker rule; unknown or outward
        # mass-proof outcomes never skip the native finishing fallback here.
        result, report = _checked(instance, candidate, seed=seed)
        record['stages'].append(dict(stage='ballast_lp', seconds=perf_counter()-before,
            passed=result is not None, candidate=candidate is not None, metadata=outcome,
            violations=sorted({v.rule_id for v in report.violations}) if report else []))
        if result is not None:
            record.update(passed=True, weighted=float(report.count.weighted_nonproductive),
                          finalize_status='ok', lane='ballast_lp', finalize_s=perf_counter()-started)
            return result, record
        if candidate is not None and area_eligible(report):
            area_plan, area_report = candidate, report
    if area_securing and area_eligible(area_report):
        before = perf_counter()
        candidate, metadata = refine_area_securing(instance, area_plan, area_report,
            seed=seed, budget_s=area_securing_s, permute_cargo=area_permutation)
        result, checked = _checked(instance, candidate, seed=seed)
        record['stages'].append(dict(stage='area_securing', seconds=perf_counter()-before,
            passed=result is not None, metadata=metadata))
        if result is not None:
            record.update(passed=True, weighted=float(checked.count.weighted_nonproductive),
                          finalize_status='ok', lane='area_securing', finalize_s=perf_counter()-started)
            return result, record
    elif area_securing:
        record['stages'].append(dict(stage='area_securing', seconds=0., passed=False,
            skipped='hard failures include rules outside area/securing'))
    if fixed_impossible:
        record.update(finalize_status='infeasible_fixed_stow', finalize_s=perf_counter()-started)
        return None, record
    if inherited:
        before = perf_counter()
        candidate, metadata = finish_inherited(instance, search_plan, seed=seed,
            finalize_s=min(120., max(1., finalize_s*.2)), label=label+'-inherited')
        # finish_inherited already performs the unchanged full check.
        record['stages'].append(dict(stage='inherited', seconds=perf_counter()-before, metadata=metadata))
        if candidate is not None and metadata.get('passed'):
            candidate = candidate.model_copy(update={'planner': NAME, 'planner_version': VERSION})
            record.update(passed=True, weighted=metadata['weighted'], finalize_status='ok',
                          lane='inherited', finalize_s=perf_counter()-started)
            return candidate, record
    before = perf_counter()
    result, metadata = frozen_planner._finish(instance, search_plan, seed=seed, finalize_s=finalize_s, label=label+'-v4')
    record['stages'].append(dict(stage='v4', seconds=perf_counter()-before, metadata=metadata))
    if result is not None and metadata.get('passed'):
        result = result.model_copy(update={'planner': NAME, 'planner_version': VERSION})
        record.update(passed=True, weighted=metadata['weighted'], finalize_status='ok', lane='v4')
    else:
        record['finalize_status'] = metadata.get('finalize_status', 'failed')
    record['finalize_s'] = perf_counter()-started
    return result, record


def fixed_stow_failure(instance, plan, report, *, map_lower_bound=None):
    from stowage.checker.rules import check_static_rules
    import math
    evidence = dict(skip_ballast=False, scope='this fixed stow/action order only; not map infeasibility',
                    static_hard_violations=[], below_map_bound=False)
    if report is not None and report.passed:
        return evidence
    try:
        replay = apply_plan(instance, plan)
        if not replay.legal:
            return dict(evidence, skip_ballast=True, reason='illegal replay')
        static = check_static_rules(instance, replay.final_state.ship, call_index=plan.call_index)
        evidence['static_hard_violations'] = sorted({v.rule_id for v in static.violations if v.severity == 'hard'})
        evidence['skip_ballast'] = bool(evidence['static_hard_violations'])
        if map_lower_bound is not None and report is not None:
            lifts = float(report.count.weighted_nonproductive)
            bound = float(map_lower_bound)
            evidence['below_map_bound'] = math.isfinite(lifts) and math.isfinite(bound) and lifts < bound
            evidence['map_lower_bound'] = bound
            evidence['replay_lifts'] = lifts
            evidence['skip_ballast'] |= evidence['below_map_bound']
    except (AttributeError, ValueError, TypeError, KeyError) as error:
        evidence['unknown'] = f'{type(error).__name__}: {error}'
    return evidence
