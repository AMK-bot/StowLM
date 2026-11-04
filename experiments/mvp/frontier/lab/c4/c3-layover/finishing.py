"""Inherited recipe, real-area local LP, optional legacy V4 finishing lanes.

The complete native checker is the only acceptance gate. The local lane is
bounded by oracle/LP iteration counts. Optional V4 retains its native wall
watchdog and is explicitly reported separately, disabled by default.
"""
from __future__ import annotations

from time import monotonic


def inherited_plan(instance, plan):
    from stowage.model import BallastCondition
    if plan.ballast is not None:
        return plan
    masses = {tank.id:float(instance.onboard.tank_masses_kg.get(tank.id, 0.))
              for tank in instance.vessel.tanks if tank.adjustable}
    return plan.model_copy(update={'ballast': BallastCondition(tank_masses_kg=masses, source='finisher')})


def _check(instance, plan):
    from stowage.checker import check
    before = monotonic()
    report = check(instance, plan)
    return report, dict(passed=bool(report.passed), checker_s=monotonic()-before,
        lifts=float(report.count.weighted_nonproductive) if report.passed else None,
        violations=sorted({v.rule_id for v in report.violations if v.severity=='hard'}),
        area_margins={name+'/'+key:float(value) for name,part in report.parts.items()
                      for key,value in part.margins.items() if '0_flood' in key})


def finish(instance, plan, *, seed=0, finalize_s=30., label='layover-c4', context=None):
    context = context if context is not None else {}
    config = context.get('finishing_config', {})
    started = monotonic()
    metadata = dict(finisher='inherited + work-bounded real-area local LP', label=label,
                    lanes=[], work_bounded=True, v4_enabled=bool(config.get('v4_fallback', False)))
    candidate = inherited_plan(instance, plan)
    report, row = _check(instance, candidate)
    metadata['lanes'].append(dict(lane='inherited_tanks', **row))
    if report.passed:
        return candidate, dict(metadata, passed=True, wall_s=monotonic()-started)
    # An illegal/static cargo plan cannot be repaired by changing water. These
    # are ordinary part names from the checker; stability/lashing/strength and
    # visibility can all change with tank contents.
    failed_parts = {name for name,part in report.parts.items()
                    if (any(v.severity=='hard' for v in part.violations) or
                        not getattr(part, 'ran', True) or getattr(part, 'error', None))}
    water_parts = {'stability', 'lashing', 'strength', 'visibility'}
    # A D57 part key is condition:<arbitrary condition id>:<part name>.
    # Match the actual part suffix, so an id containing "stability" cannot
    # accidentally classify a cargo/constraints error as water-repairable.
    repairable = bool(failed_parts) and all(name.rsplit(':', 1)[-1] in water_parts
                                            for name in failed_parts)
    max_evaluations = int(config.get('max_evaluations', 28))
    if repairable and max_evaluations > 0:
        try:
            from ballast_local import optimize
            local, telemetry = optimize(instance, candidate,
                max_evaluations=max_evaluations,
                max_iterations=int(config.get('max_iterations', 2)),
                max_lp_iterations=int(config.get('max_lp_iterations', 2000)),
                trust_radius=float(config.get('trust_radius', .15)),
                use_loading_seed=bool(config.get('loading_seed', True)))
            checked, checked_row = _check(instance, local)
            metadata['lanes'].append(dict(lane='real_area_local_lp', search=telemetry, **checked_row))
            if checked.passed:
                return local, dict(metadata, passed=True, wall_s=monotonic()-started)
        except (ValueError, ArithmeticError, KeyError, OSError) as error:
            metadata['lanes'].append(dict(lane='real_area_local_lp', error=f'{type(error).__name__}: {error}'))
    elif not repairable:
        metadata['local_skip'] = 'non-water hard violation: '+', '.join(sorted(failed_parts))
    if repairable and config.get('v4_fallback', False):
        from planner import _finish
        # Remove the inherited declaration so native finalize actually searches.
        raw = plan.model_copy(update={'ballast': None})
        result, telemetry = _finish(instance, raw, seed=seed, finalize_s=finalize_s, label=label+'-v4')
        metadata['lanes'].append(dict(lane='native_v4_wall_watchdog', search=telemetry))
        metadata['work_bounded'] = False
        if result is not None:
            finished = result.plan if hasattr(result, 'plan') else result
            checked, checked_row = _check(instance, finished)
            metadata['lanes'][-1].update(checked_row)
            if checked.passed:
                return finished, dict(metadata, passed=True, wall_s=monotonic()-started)
    return None, dict(metadata, passed=False, wall_s=monotonic()-started,
                      physical_infeasibility_proven=False)
