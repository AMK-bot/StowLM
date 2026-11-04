"""Necessary securing proof, direct check, inherited tanks, native V4 and full gate."""
from time import perf_counter

from stowage.checker import check
from stowage.model import PlannerResult
from stowage.moves import apply_plan
from ..frozen import planner as frozen_planner

from .early_bound import securing_bound
from .fast_finish import finish_inherited


def _checked(instance, result, *, seed):
    if result is None:
        return None, None
    if not isinstance(result, PlannerResult):
        result = PlannerResult(status='ok', plan=result, planner='frontier.engine',
                               planner_version='0.10.0', seed=seed)
    if result.status != 'ok' or result.plan is None:
        return None, None
    report = check(instance, result.plan)
    if not report.passed:
        return None, report
    result = result.model_copy(update={'planner': 'frontier.engine', 'planner_version': '0.10.0'})
    return result, report


def finish_chain(instance, plan, *, seed=0, finalize_s=600., label='candidate',
                 inherited=True, securing=True):
    """Every successful lane is gated by the ordinary full checker.

    The inherited lane is extra time. Native V4 retains its complete baseline
    budget when both faster lanes fail, preserving the 0.9.0 finishing floor.
    A securing certificate excludes this fixed stow only, never the map.
    """
    if len(instance.load_list) <= 60:
        finalize_s = max(300., finalize_s)
    started = perf_counter()
    record = dict(label=label, passed=False, stages=[])
    if securing:
        before = perf_counter()
        replay = apply_plan(instance, plan)
        if not replay.legal:
            record.update(finalize_status='illegal_replay', finalize_s=perf_counter()-started)
            return None, record
        bound = securing_bound(instance, replay.final_state.ship)
        record['stages'].append(dict(stage='securing_bound', seconds=perf_counter()-before, **bound))
        if bound.get('proof') is not None:
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
    if inherited:
        before = perf_counter()
        candidate, metadata = finish_inherited(instance, plan, seed=seed,
            finalize_s=min(120., max(1., finalize_s*.2)), label=label+'-inherited')
        # finish_inherited already performs the unchanged full check.
        record['stages'].append(dict(stage='inherited', seconds=perf_counter()-before, metadata=metadata))
        if candidate is not None and metadata.get('passed'):
            candidate = candidate.model_copy(update={'planner': 'frontier.engine', 'planner_version': '0.10.0'})
            record.update(passed=True, weighted=metadata['weighted'], finalize_status='ok',
                          lane='inherited', finalize_s=perf_counter()-started)
            return candidate, record
    before = perf_counter()
    result, metadata = frozen_planner._finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label+'-v4')
    record['stages'].append(dict(stage='v4', seconds=perf_counter()-before, metadata=metadata))
    if result is not None and metadata.get('passed'):
        result = result.model_copy(update={'planner': 'frontier.engine', 'planner_version': '0.10.0'})
        record.update(passed=True, weighted=metadata['weighted'], finalize_status='ok', lane='v4')
    else:
        record['finalize_status'] = metadata.get('finalize_status', 'failed')
    record['finalize_s'] = perf_counter()-started
    return result, record
