"""Cold real-area/securing rescue of in-memory engine cargo candidates.

No retained plans, recipes, records or experimental results are loaded. Every
accepted recipe/stow passes the ordinary complete checker. Search exhaustion,
local LP infeasibility and fitted securing rows are never physical proofs.
"""
from time import monotonic

from frozen_checks import check
from stowage.model import MovePlan

from .optimize import optimize
from .permute import optimize_assignment


SECURING = 'iacs.member_irs.container_securing'
SECOND_CHECK = 'iscode.b2_3.containership_second_check'


def eligible(report):
    """Only actual hard area/securing failures authorize this expensive lane."""
    if report is None or report.passed:
        return False
    hard = [v for v in report.violations if v.severity == 'hard']
    def allowed(v):
        if v.rule_id == SECURING:
            return True
        # The second-check rule also emits lever/angle failures. Unit must
        # identify an actual area failure, rather than assuming every B2.3
        # violation is the binding area_0_flood condition from the warm witness.
        return (v.unit in ('m*rad', 'm·rad', 'm rad') and
                (v.rule_id == SECOND_CHECK or v.rule_id.startswith('iscode.a2_2.gz_area_')))
    return bool(hard) and all(allowed(v) for v in hard)


def refine(instance, plan, report, *, seed=0, budget_s=120.,
           optimize_ballast=True, permute_cargo=True, problem=None):
    """Return (fully checked MovePlan-or-None, JSON-safe stage evidence).

    The initial tanks come from this engine stow's native default or its local
    LP recipe. All later reference recipes/physics are computed in this call.
    Only an in-memory MovePlan is accepted; there is no artifact-loading API.
    """
    if not isinstance(plan, MovePlan):
        raise TypeError('area/securing search requires an in-memory engine MovePlan')
    started = monotonic()
    deadline = started+max(0., budget_s)
    meta = dict(status='skipped', passed=False, cold=True, stages=[],
                physical_infeasibility_proven=False)
    if not eligible(report) or not (optimize_ballast or permute_cargo) or budget_s <= 0.:
        meta.update(reason='requires area/securing-only hard failures and enabled search budget', wall_s=monotonic()-started)
        return None, meta
    meta['status'] = 'unknown'
    candidate, reference = plan, None

    def ballast_search(label, allowance):
        nonlocal candidate, reference
        proposed, row = optimize(instance, candidate, budget_s=max(0., allowance),
                                 max_evaluations=48, max_iterations=8)
        reference = row.pop('best_physics', None)
        meta['stages'].append(dict(stage=label, **row))
        candidate = proposed
        if row.get('full_checker_passed'):
            # optimize performs this complete checker itself. Rechecking here
            # makes the public rescue gate independent of search metadata.
            checked = check(instance, proposed)
            if checked.passed:
                meta.update(status='full_checker_pass', passed=True,
                            weighted=float(checked.count.weighted_nonproductive),
                            wall_s=monotonic()-started)
                return proposed
        return None

    try:
        if optimize_ballast:
            remaining = max(0., deadline-monotonic())
            found = ballast_search('real_area_ballast', remaining*(.5 if permute_cargo else 1.))
            if found is not None:
                return found, meta
        if permute_cargo and monotonic() < deadline:
            remaining = max(0., deadline-monotonic())
            context = dict(balance_reference_tcg=True)
            if problem is not None:
                context['problem'] = problem
            scored, row = optimize_assignment(instance, candidate, reference,
                budget_s=min(30., remaining*(.5 if optimize_ballast else 1.)), seed=seed,
                context=context, relax_heavy=True, relax_category=True)
            meta['stages'].append(dict(stage='area_cargo_permutation', **row))
            if scored is not None:
                candidate = scored.plan
                checked = check(instance, candidate)
                meta['stages'][-1].update(full_checker_passed=bool(checked.passed),
                    rule_ids=sorted({v.rule_id for v in checked.violations if v.severity == 'hard'}))
                if checked.passed:
                    meta.update(status='full_checker_pass', passed=True,
                                weighted=float(checked.count.weighted_nonproductive), wall_s=monotonic()-started)
                    return candidate, meta
                if optimize_ballast and eligible(checked) and monotonic() < deadline:
                    found = ballast_search('permuted_real_area_ballast', deadline-monotonic())
                    if found is not None:
                        return found, meta
    except (ValueError, TypeError, KeyError, ArithmeticError, RuntimeError) as error:
        meta['error'] = f'{type(error).__name__}: {error}'
    meta['wall_s'] = monotonic()-started
    return None, meta
