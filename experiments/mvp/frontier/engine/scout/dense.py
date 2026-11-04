# Provenance: adapted cold construction and checked local-seed lanes from lab/c4/c2-layout/method_dense.py.
"""Additive moment-guided reconstruction, with no stored/warm reference reads.

Soft moment targets generate columns, never physics certificates. Every complete
replay survives for the ordinary engine checker/finisher, including target misses.
"""
from collections import Counter
import hashlib
from time import monotonic

from stowage.checker import check, departure_condition

from .advanced_pages import PureModel
from .layout011 import enabled
from .mixed_oracle import Fast
from .moment_pages import Model
from . import moment_lns


def construction_settings(instance):
    hull = instance.vessel.hull
    target_x = hull.lpp_m*.48
    load_ids = set(instance.load_list)
    cargo_mass = sum(box.gross_mass_kg for box in instance.containers
                     if box.id in load_ids)/1000.
    vertical_target = cargo_mass*(hull.depth_m+.5)
    return [dict(deck_tiers=tiers, vertical_weight=vertical, hold_reward=reward,
                 lcg_min=target_x-3., lcg_max=target_x+1., tcg_min=-.08, tcg_max=.08,
                 cargo_vmoment_max=vertical_target, lash_limit=lash)
            for tiers, vertical, reward, lash in zip(
                ((3, 5, 5), (3, 4, 4), (3, 4, 5), (3, 5, 5)),
                (.08, .04, .06, .10), (1., 4., 3., 1.), (1., .99, 1., .98))]


def make_model(problem, context, *, work, deadline, settings, incumbent=None):
    moments = enabled(context, 'moment_pricing')
    options = dict(settings)
    if not moments:
        options = {k: v for k, v in options.items() if k not in
                   ('vertical_weight', 'lcg_min', 'lcg_max', 'tcg_min', 'tcg_max', 'cargo_vmoment_max')}
    if incumbent is not None:
        # This reference is an engine-checked plan from this invocation, with
        # its checked tank recipe, rather than apply_plan's arrival tanks.
        options['oracle'] = Fast(problem.instance, departure_condition(problem.instance, incumbent.plan))
    factory = context.get('dense_model_factory') or (Model if moments else PureModel)
    return factory(problem, work=work, safety_deadline=deadline,
        fast_pricing=enabled(context, 'fast_pricing'), unary_cache=enabled(context, 'unary_cache'),
        asymmetric_deck=enabled(context, 'asymmetric_deck'), aggregation=enabled(context, 'aggregation'),
        restricted_master=enabled(context, 'restricted_master'), **options)


def checked_seed(model, incumbent):
    """Map only this invocation's checked incumbent into a private new pool."""
    if incumbent is None:
        return None
    oracle = model.oracle
    try:
        model.oracle = None
        return moment_lns.seed_assignment(model, {a.box: a.slot for a in incumbent.actions})
    except ValueError:
        return None
    finally:
        model.oracle = oracle


def diagnostics(model):
    return dict(moment_slacks=dict(getattr(model, 'moment_slacks', {})),
                actual_uncovered=getattr(model, 'actual_uncovered', None),
                moment_targets='soft construction guidance; never map bounds',
                numerical_threads=1)


def retain_failure_diagnostics(problem, scored, row, context):
    """Retain the actual failed plan and all native breaches for later review."""
    if scored is None or not enabled(context, 'layout_diagnostics'):
        return
    key = hashlib.sha256(scored.plan.model_dump_json().encode()).hexdigest()
    cache = context.setdefault('_engine_page_diagnostic_reports', {})
    if key not in cache:
        report_fn = context.get('page_report', check)
        cache[key] = report_fn(problem.instance, scored.plan)
    report = cache[key]
    if report.passed:
        return
    records = context.setdefault('method_metadata', {}).setdefault('failure_diagnostics', {})
    records.setdefault(key, dict(plan=scored.plan.model_dump(mode='json'),
                                 report=report.model_dump(mode='json')))
    row.setdefault('failure_diagnostics', []).append(key)


def reconstruct(problem, *, context, work, deadline, seed, incumbent, evaluate):
    from .layout_work import construct
    rows = []
    for attempt, settings in enumerate(construction_settings(problem.instance)):
        if monotonic() >= deadline:
            break
        row = dict(kind='dense_moment', iteration=attempt, settings=dict(settings),
                   seed_source='engine-checked this invocation' if incumbent is not None else 'cold')
        rows.append(row)
        try:
            model = make_model(problem, context, work=work, deadline=deadline,
                               settings=settings, incumbent=incumbent)
            chosen = checked_seed(model, incumbent)
            if chosen is not None:
                model.seed_chosen = chosen
            local = dict(context, _warm_ballast=incumbent.plan.ballast) if incumbent is not None else context
            found, info = construct(problem, model, seed=seed+attempt,
                                    deadline=deadline, context=local)
            row.update(info)
            if enabled(context, 'layout_diagnostics'):
                row.update(diagnostics(model))
            for scored in found:
                ok, _ = evaluate(scored, row)
                retain_failure_diagnostics(problem, scored, row, context)
                if ok and (incumbent is None or scored.objective < incumbent.objective):
                    incumbent = scored
        except Exception as error:
            if context.get('_engine_page_callback_error') is error:
                raise
            row['error'] = f'{type(error).__name__}: {error}'
    return rows, incumbent


def improve_checked(problem, incumbent, *, context, work, deadline, seed, evaluate):
    """Two C4 fixed-charge neighbourhoods around a same-invocation checked pass."""
    from .layout_work import score_assignment
    rows = []
    local = dict(context, _warm_ballast=incumbent.plan.ballast)
    settings = dict(deck_tiers=None, vertical_weight=.02, lash_limit=1.)
    model = make_model(problem, context, work=work, deadline=deadline,
                       settings=settings, incumbent=incumbent)
    chosen = checked_seed(model, incumbent)
    if chosen is None:
        return [dict(kind='dense_moment_lns', status='checked seed outside pure family')]
    assignment = {a.box: a.slot for a in incumbent.actions}
    if hasattr(model, 'selection_moments'):
        moment = model.selection_moments(chosen)
        model.cargo_vmoment_max = moment['vertical_tm']+moment['mass_t']*.05
        model.lcg_min, model.lcg_max = moment['lcg_m']-.35, moment['lcg_m']+.35
        model.lcg_target = model.lcg_max
        model.tcg_min, model.tcg_max = moment['tcg_m']-.03, moment['tcg_m']+.03
    counts = Counter(model.pages[model.columns[k].page].cover for k in chosen
                     if not model.pages[model.columns[k].page].deck)
    covers = sorted({p.cover for p in model.pages if p.cover is not None})
    for attempt, target in enumerate(sorted(counts, key=lambda c: (counts[c], c))[:2]):
        if monotonic() >= deadline:
            break
        nearby = sorted(covers, key=lambda c: (c != target, abs(covers.index(c)-covers.index(target)), c))[:4]
        fixed = {b: slot for b, slot in assignment.items() if all(
            model.T.stack_cover[int(j)] not in nearby
            for j in model.T.slot_stacks[model.T.slot_index[slot]] if j >= 0)}
        out = moment_lns.improve(model, chosen, nearby, closed_hold=[target] if attempt == 0 else (),
            seconds=max(0., deadline-monotonic()), pricing_s=max(0., deadline-monotonic()),
            seed=seed+attempt, max_rounds=work.polish_rounds, heel_slack_tm=30., lcg_slack_tm=100.,
            work=work, safety_deadline=deadline)
        row = {k: v for k, v in out.items() if k not in ('chosen', 'assignment')}
        row.update(kind='dense_moment_lns', iteration=attempt, seed_source='engine-checked this invocation')
        rows.append(row)
        scored = score_assignment(problem, model, out['assignment'], context=local, fixed=fixed) if out.get('chosen') else None
        ok, _ = evaluate(scored, row)
        retain_failure_diagnostics(problem, scored, row, context)
        if scored is not None and ok and scored.objective < incumbent.objective:
            incumbent, assignment = scored, {a.box: a.slot for a in scored.actions}
            local['_warm_ballast'] = incumbent.plan.ballast
            chosen = checked_seed(model, incumbent)
            if chosen is None:
                break
    return rows
