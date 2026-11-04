"""Cargo descent/exchange using only a stow and physics from this cold run.

Copied C3 page/permutation algorithms live beside this module. No historical
plan, recipe, map ID or saved reference is read. These are constructive search
preferences; every returned plan still requires the ordinary full checker.
"""
from __future__ import annotations

from pathlib import Path
from time import monotonic
import math
import sys

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


def refine_current(instance, plan, physics=None, *, seed=0, budget_s=45., context=None):
    """Return (list[MovePlan], JSON metadata) in cheap-first order.

    Input physics, if supplied, is an exact current-run oracle result. Output
    preserves those tank masses while legally changing cargo; callers must
    reevaluate physics, since the old margins are no longer applicable.
    """
    import page_tools
    permutation = page_tools._load('_c4_repair_permutation', HERE/'permutation.py')
    PhysicsEvaluator = page_tools._load('_c4_repair_physics', HERE/'physics.py').PhysicsEvaluator
    from stowage.model import BallastCondition

    started = monotonic()
    deadline = started + max(0., budget_s)
    context = dict(context or {})
    _, alns, _ = page_tools._runtime()
    problem = context.get('problem') or alns.Problem(instance)
    evaluator = PhysicsEvaluator(instance, plan)
    masses = (physics or {}).get('tank_masses_kg')
    if masses is None:
        masses = dict(zip(evaluator.tank_ids, map(float, evaluator.initial_masses_kg)))
    ship = evaluator.ship.model_copy(update={'tank_masses_kg': {
        **evaluator.fixed_masses_kg, **masses}})
    lash = page_tools._load('_c4_repair_fast', page_tools.LAB/'c1/fx-scout/lash_oracle.py')
    oracle = lash.Fast(instance, ship)
    ballast = BallastCondition(tank_masses_kg=masses, source='finisher')
    output = []
    metadata = dict(cold_current_run_only=True, stow_changes=True,
                    physical_certificate=False, attempts=[],
                    transverse_balance_policy='native ballast-caller balance after cargo change')

    if monotonic() < deadline:
        scored, row = page_tools.refine(instance, plan, seed=seed,
            budget_s=min(12., max(0., .35*(deadline-monotonic()))),
            context=dict(problem=problem, oracle=oracle, protect_special=True,
                         lcg_tolerance_m=.5, heel_limit_tm=100.,
                         require_postprocess_guards=True),
            settings=dict(deck_tiers=None, vertical_weight=.02, hold_reward=3.))
        metadata['attempts'].append(dict(kind='vertical_moment_descent', **row))
        if scored is not None:
            candidate = scored.plan.model_copy(update={'ballast': ballast})
            output.append(candidate)
            plan = candidate

    if monotonic() < deadline:
        # Reserve a little securing room for the true motion change when KG
        # improves. At an already badly overloaded seed, relax only enough to
        # retain constructive opportunities; exact checks decide acceptance.
        ratios = []
        try:
            pages, _, _ = page_tools._runtime()
            model = page_tools._model_class(pages)(problem, deck_tiers=None, low_first_fit=False)
            model.deadline = deadline
            chosen = page_tools._seed(model, plan)
            if chosen:
                for k in chosen:
                    col = model.columns[k]
                    page = model.pages[col.page]
                    if page.deck:
                        ratios.append(oracle.ratio([model.T.box_ids[b] for b in col.boxes],
                            [model.T.slot_ids[s] for s in page.slots[:len(col.boxes)]]))
        except (ValueError, KeyError, ArithmeticError):
            ratios = []
        worst = max(ratios, default=1.)
        cap = .995 if worst <= 1.01 else min(worst, 1.02)
        scored, row = permutation.optimize_assignment(instance, plan,
            dict(tank_masses_kg=masses), seed=seed+1,
            budget_s=max(0., deadline-monotonic()),
            relax_rank=True, relax_heavy=True, relax_category=True,
            # Descent may change cargo TCG. Requiring zero total TCG under
            # the old tanks can exclude the incumbent hint. The ballast
            # caller balances every returned cargo before exact evaluation.
            context=dict(problem=problem, reference_limit=cap,
                         balance_reference_tcg=False))
        metadata['attempts'].append(dict(kind='fixed_topology_exchange', **row))
        if scored is not None:
            output.append(scored.plan.model_copy(update={'ballast': ballast}))
    seen = set()
    unique = []
    for candidate in output:
        key = candidate.model_dump_json()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    metadata.update(wall_s=monotonic()-started, candidates=len(unique))
    return unique, metadata


def polish_restows(instance, plan, report, *, seed=0, budget_s=60.):
    """Exchange current-run boxes to cut native projected lifts after a pass.

    The incumbent must already have its ordinary successful report. Candidate
    scoring and acceptance both include D15's actual later-port projection.
    The fitted CP objective only guides search and never establishes a gain.
    """
    import page_tools
    from stowage.checker import check
    PhysicsEvaluator = page_tools._load('_c4_repair_physics', HERE/'physics.py').PhysicsEvaluator
    permutation = page_tools._load('_c4_repair_permutation', HERE/'permutation.py')
    if not report.passed:
        raise ValueError('restow polishing needs a current-run full-checker pass')
    started = monotonic()
    old_lifts = float(report.count.weighted_nonproductive)
    meta = dict(kind='projected_restow_exchange', incumbent_lifts=old_lifts,
                accepted=False, cold_current_run_only=True)
    if budget_s < 25.:
        return plan, dict(meta, status='insufficient_check_reserve', wall_s=monotonic()-started)
    evaluator = PhysicsEvaluator(instance, plan)
    masses = dict(zip(evaluator.tank_ids, map(float, evaluator.initial_masses_kg)))
    scored, row = permutation.optimize_assignment(instance, plan,
        dict(tank_masses_kg=masses), seed=seed,
        budget_s=max(1., budget_s-25.), lcg_tolerance_m=.1,
        relax_rank=True, relax_heavy=True, relax_category=True,
        context=dict(restow_first=True, cargo_kg_tolerance_m=0.,
                     reference_limit=1., balance_reference_tcg=True, tcg_band_tm=1.))
    meta['search'] = row
    if scored is None or scored.objective >= old_lifts:
        return plan, dict(meta, status='no_cheaper_replay', wall_s=monotonic()-started)
    checked = check(instance, scored.plan)
    meta.update(candidate_lifts=float(checked.count.weighted_nonproductive),
                full_checker_passed=checked.passed,
                violations=[v.model_dump(mode='json') for v in checked.violations])
    if checked.passed and checked.count.weighted_nonproductive < old_lifts:
        plan = scored.plan
        meta['accepted'] = True
        meta['accepted_report'] = checked.model_dump(mode='json')
    meta.update(status='improved' if meta['accepted'] else 'incumbent_retained',
                wall_s=monotonic()-started)
    return plan, meta
