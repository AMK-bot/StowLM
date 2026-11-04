"""D241/D242 seam: inspect this run's plans; future frozen ladder plugs in here.

This module never changes rules or checker behavior. It is called only after
L0 search found no full pass. Existing plans are classified with exact native
reports; no relaxed result contributes to passed or proved_optimal.
"""
from pathlib import Path
import math
import tomllib

from stowage.checker import check
from stowage.moves import apply_plan

TIERS = tomllib.loads(Path(__file__).with_name('tiers.toml').read_text())
RULE_TIER = {rule: tier for tier, data in TIERS['tiers'].items() for rule in data['rules']}
COLUMNS = ('T5', 'T4', 'T3', 'T2', 'T1', 'T0 only')


def rule_tier(rule):
    return RULE_TIER.get(rule, 'T0')


def inspect(instance, plan, report=None):
    """Keep every exact breach and its metrics; unknown IDs are unpeelable T0."""
    report = report or check(instance, plan)
    replay = apply_plan(instance, plan)
    aboard = {p.container_id for p in replay.final_state.ship.placements} if replay.legal else set()
    requested = (set(instance.load_list) | {p.container_id for p in instance.onboard.placements}) - set(instance.discharge_list)
    ashore = requested-aboard
    breaches = [dict(v.model_dump(mode='json'), tier=rule_tier(v.rule_id))
                for v in report.violations if v.severity == 'hard']
    if not replay.legal:
        breaches.append(dict(rule_id='expert.moves.replay', tier='T0', severity='hard',
                             message='native replay failed'))
    if ashore and not any(v['rule_id']=='objective.all_loaded' for v in breaches):
        breaches.append(dict(rule_id='objective.all_loaded', tier='T3', severity='hard',
                             message='requested boxes left ashore', containers=sorted(ashore)))
    if any(not p.ran or p.error for p in report.parts.values()):
        breaches.append(dict(rule_id='checker.internal_error', tier='T0', severity='hard',
                             message='a native checker part did not complete'))
    tiers = {b['tier'] for b in breaches}
    level = next((n for n in range(6) if tiers <= set(TIERS['levels'][f'L{n}'])), None)
    boxes = {b.id:b for b in instance.containers}
    # Native metrics/margins are retained verbatim; no invented common breach unit.
    parts = [dict(name=p.name, metrics=p.metrics, margins=p.margins, ran=p.ran, error=p.error)
             for p in report.parts.values()]
    normalized = []
    for part in report.parts.values():
        for name, value in part.margins.items():
            if isinstance(value,(int,float)) and math.isfinite(value) and value < 0:
                normalized.append(dict(part=part.name, metric=name, margin=value))
    return dict(level=level, column=COLUMNS[level] if level is not None else None,
        full_checker_passed=bool(report.passed), completed=not ashore and replay.legal,
        boxes_left_ashore=sorted(ashore), boxes_aboard_percent=100.*len(requested-ashore)/max(1,len(requested)),
        teu_ashore=sum(boxes[b].length_ft/20. for b in ashore),
        mass_ashore_kg=sum(boxes[b].gross_mass_kg for b in ashore),
        broken_rules=sorted({b['rule_id'] for b in breaches}), breaches=breaches,
        exact_parts=parts, negative_margins=normalized,
        all_violations=[v.model_dump(mode='json') for v in report.violations],
        lifts=float(report.count.weighted_nonproductive), t0_peeled=False,
        breach_score=sum(abs(v['measured']-v['limit'])/max(abs(v['limit']),1.)
            if v.get('measured') is not None and v.get('limit') is not None else 1.
            for v in breaches), breach_score_definition='sum absolute measured-limit / max(abs(limit),1 native unit); nonnumeric breach=1')


def _classify(instance, everything_found, *, ladder=None):
    """Return completed/partial relaxed evidence; a frozen LP peel ladder may replace search.

    everything_found is caller-owned: plans, reports, stage records, checked_pass.
    A ladder receives it verbatim. Its candidates are independently checked here.
    T0 can never be peeled, and a full incumbent prevents this call entirely.
    """
    if everything_found.get('checked_pass') is not None:
        raise ValueError('never peel after a full checker pass')
    plans = list(everything_found.get('plans', []))
    if ladder is not None:
        extra = ladder(instance, everything_found)
        if extra:
            plans.extend(extra.get('plans', []) if isinstance(extra,dict) else extra)
    rows = []
    for plan in plans:
        try:
            row = inspect(instance, plan)
            rows.append((plan,row))
        except Exception as error:
            everything_found.setdefault('peel_errors', []).append(f'{type(error).__name__}: {error}')
    # No new tier search is claimed: this default seam only classifies existing
    # L0 search witnesses. Completed plans stop at the highest available level.
    def key(item):
        d=item[1]
        return (len(d['boxes_left_ashore']),d['teu_ashore'],d['mass_ashore_kg'],
                len(d['broken_rules']),d['breach_score'],d['lifts'])
    complete=[q for q in rows if q[1]['completed'] and q[1]['level'] is not None]
    completed=min(complete,key=lambda q:(q[1]['level'],key(q)),default=None)
    partial=[q for q in rows if not q[1]['completed'] and q[1]['level'] is not None and q[1]['level']<=3]
    safe_partial=min(partial,key=key,default=None)
    selected=completed or safe_partial
    return dict(status='existing_witnesses_only' if ladder is None else 'ladder_checked',
        completed=completed[1] if completed else None,
        best_safe_partial=safe_partial[1] if safe_partial else None,
        relaxed_plan=selected[0].model_dump(mode='json') if selected else None,
        inspected=len(rows), t0_unpeelable=True, passed=False,
        ladder_pending=ladder is None, errors=everything_found.get('peel_errors', []))


def no_pass(instance, everything_found, *, ladder=None, seed=0, budgets=None):
    """Keep tiny/small classification; medium no-pass invokes the frozen LP ladder.

    ``ladder=False`` disables new search. Existing explicit callable ladders keep
    their old candidate-generator contract. No checked incumbent can be peeled.
    """
    if everything_found.get('checked_pass') is not None or everything_found.get('passed') is True:
        raise ValueError('never peel after a full checker pass')
    if callable(ladder):
        return _classify(instance, everything_found, ladder=ladder)
    classified = _classify(instance, everything_found)
    from engine.peel_ladder import active, run, ordinary_pass
    if ladder is False or not active(instance):
        return classified
    # Do not start relaxed search if an existing candidate already passes L0.
    complete = classified.get('completed')
    if isinstance(complete, dict) and complete.get('level') == 0:
        return classified
    try:
        outcome = run(instance, everything_found, seed=seed, budgets=budgets)
    except Exception as error:
        held = ordinary_pass(everything_found, error=error)
        if held is not None:
            return held
        classified.setdefault('errors', []).append(f'{type(error).__name__}: {error}')
        classified['ladder_pending'] = False
        classified['ladder_status'] = 'error; existing evidence retained'
        return classified
    if not outcome.get('applicable'):
        classified['ladder_status'] = outcome.get('status')
        return classified
    outcome['classifier_evidence'] = classified
    return outcome
