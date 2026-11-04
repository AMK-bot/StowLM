# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-scout/method.py.
"""Lab plug-in: stacks as pages, column generation with exact knapsack pricing, then diving. See pages.py and NOTES.md.

Ladder: one plain packing gives a reference ship (GM and draught for the securing check). Each later stow has securing
in the pricing and a fore-aft row in the master, and each failed full check changes the settings for the next stow. A
stow that passes is then tried again with one port per cover, for fewer lifts. Stored plans are never used.
"""
from __future__ import annotations

import random
from time import monotonic


from collections import defaultdict  # noqa: E402

from frozen_frontier.alns import Sequencer  # noqa: E402
from .lash_oracle import Fast  # noqa: E402
from .pages import Model  # noqa: E402
from frozen_checks import check  # noqa: E402
from stowage.conventions import cells_of_slot  # noqa: E402
from stowage.moves import apply_plan  # noqa: E402

def one_stow(problem, model, *, seed, deadline):
    """LP, dive, heel balance, load order, real replay. Returns (scored or None, record)."""
    started = monotonic()
    root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1,
                          deadline=started+.5*(deadline-started))
    row = dict(lp_uncovered=round(root['uncovered'], 3), lp_proved=root['proved'], lp_s=round(monotonic()-started, 2))
    if root['uncovered'] > 3.:
        return None, row
    # A small shortfall can be the knapsack's caution, so dive anyway: the tail asks the exact securing check.
    rng = random.Random(seed)
    model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=3, deadline=deadline, max_rounds=30)
    chosen, info = model.dive(deadline=deadline, rng=rng)
    row.update(info)
    if chosen is None:
        return None, row
    chosen, heel = model.balance(chosen, rng)
    assignment = model.clear_oog(model.assignment(chosen))
    row.update(heel_tm=round(heel, 1), empty_hold=model.empty_hold_cells(chosen), mixes=problem.mixes_hold_stack(assignment),
               columns=len(model.columns), s=round(monotonic()-started, 1))
    if row['mixes']:
        return None, row
    ordered = Sequencer(problem).order(assignment)
    scored = problem.score(ordered) if ordered is not None else None
    row['lifts'] = scored.objective if scored else None
    return scored, row


def block_layout(model):
    """One discharge port per hatch cover, sized to demand: fewer port-cover edges and no deck box over an earlier hold box."""
    T = model.T
    cells = defaultdict(int)
    for page in model.pages:
        if page.kind == '20' and page.cover is not None:
            cells[page.cover] += len(page.slots)
    need = defaultdict(float)
    for b in model.boxes:
        need[model.pod[b]] += float(T.units[b])
    layout = {}
    for cover in sorted(cells, key=lambda c: (-cells[c], c)):
        port = max(need, key=need.get)
        layout[cover] = port
        need[port] -= cells[cover]
    return layout


def verdict(instance, plan):
    """The checker's own rule ids and the worst trim sign, so the next rung knows what to change."""
    report = check(instance, plan)
    rules = sorted({v.rule_id for v in report.violations})
    bow_down = any('trim' in v.rule_id and v.measured is not None and v.measured < 0 for v in report.violations)
    forward_draught = any('draft' in v.rule_id and 'fwd' in v.message for v in report.violations)
    return report.passed, rules, bow_down or forward_draught


def _propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    """Checker-guided ladder: each failed full check names what the next stow must change."""
    problem = context['problem']
    meta = context.setdefault('method_metadata', {})
    if instance.onboard.placements:
        meta['skipped'] = 'page model covers empty arrival ships'
        return []
    started = monotonic()
    deadline = started+budget_s
    meta['ladder'] = []
    first, row = one_stow(problem, Model(problem), seed=seed, deadline=started+.4*budget_s)
    meta['ladder'].append(dict(rung='plain', **row))
    if first is None:
        return []
    context['_scout_candidates'].append(first)
    ship = apply_plan(instance, first.plan).final_state.ship
    oracle = Fast(instance, ship)
    mass = sum(problem.boxes[q.container_id].gross_mass_kg for q in ship.placements)
    lcg = sum(problem.boxes[q.container_id].gross_mass_kg*problem.cells[cells_of_slot(q.slot)[0]].lcg_m
              for q in ship.placements)/mass
    settings = dict(deck_tiers=None, lash_limit=.97, lcg_target=None)
    passing, others = [], []

    fresh = dict(settings)

    def learn(rules, bow_down, settings):
        """What the failed full check says the next stow must change."""
        settings = dict(settings)
        if any('visibility' in r for r in rules):
            settings['deck_tiers'] = 4 if settings['deck_tiers'] is None else 3
        if any('securing' in r for r in rules) and settings['deck_tiers'] is not None and settings != fresh:
            settings['lash_limit'] = max(.85, settings['lash_limit']-.03)
        if bow_down:
            settings['lcg_target'] = (settings['lcg_target'] or lcg-1.5)-1.5
        elif any('trim' in r or 'draft' in r for r in rules) and settings['lcg_target'] is not None:
            settings['lcg_target'] += 1.
        if any('e6' in r for r in rules):
            settings['hold_reward'] = settings.get('hold_reward', 1.)*3.
        return settings

    ok, rules, bow_down = verdict(instance, first.plan)
    meta['ladder'][-1].update(full_check=ok, rules=rules)
    if ok:
        passing.append(first)
    else:
        others.append((first, rules))
        settings = learn(rules, bow_down, settings)
    ballast_only = len(problem.boxes) <= 60 and all(r.startswith('iscode') for r in rules)
    tried = set()
    attempt = 0
    layouts = iter((dict(layout=None), dict(layout=True), dict(layout=True, layout_deck=False)))
    layout = next(layouts)
    if passing or ballast_only:
        layout = next(layouts)
    while monotonic() < deadline and attempt < 12:
        attempt += 1
        key = (str(settings), str(layout))
        if key in tried:
            break
        tried.add(key)
        arguments = dict(settings)
        if layout.get('layout'):
            arguments.update(layout, layout=block_layout(Model(problem, oracle=oracle, **settings)))
        share = rung_allowance(deadline)
        if share <= 0:
            break
        rung_deadline = min(deadline, monotonic()+share)
        scored, row = one_stow(problem, Model(problem, oracle=oracle, **arguments), seed=seed+attempt, deadline=rung_deadline)
        row.update(rung=attempt, settings=str(settings), layout=str({k: v for k, v in layout.items()}))
        if scored is not None:
            context['_scout_candidates'].append(scored)
        if scored is not None and any(scored.objective >= s.objective for s in passing):
            others.append((scored, []))
            row['note'] = 'no cheaper than a passing stow'
            meta['ladder'].append(row)
            layout = next(layouts, None)
            if layout is None:
                break
            continue
        if scored is not None:
            ok, rules, bow_down = verdict(instance, scored.plan)
            row.update(full_check=ok, rules=rules)
            meta['ladder'].append(row)
            if ok:
                passing.append(scored)
                layout = next(layouts, None)
                if layout is None:
                    break
                continue
            others.append((scored, rules))
            if len(problem.boxes) <= 60 and all(r.startswith('iscode') for r in rules):
                # On small ships ballast fixes these, and the finisher is quick: go on to the lifts.
                layout = next(layouts, None)
                if layout is None:
                    break
                continue
            settings = learn(rules, bow_down, settings)
            continue
        meta['ladder'].append(row)
        if passing or layout.get('layout'):
            # A layout that doesn't pack: try the softer one, or stop with what passed.
            layout = next(layouts, None)
            if layout is None:
                break
            continue
        # Too tight to pack: give back securing margin first, then deck tiers aft.
        settings = dict(settings)
        if settings['lcg_target'] is not None and row.get('lp_uncovered', 0.) > 3. and settings['lcg_target'] < lcg-.75:
            settings['lcg_target'] += 1.5
        elif settings['lash_limit'] < .999:
            settings['lash_limit'] = 1.
        elif settings['deck_tiers'] == 4:
            settings['deck_tiers'] = (4, 5, 5)
        else:
            break
    meta['propose_s'] = round(monotonic()-started, 2)
    return retain_candidates(passing, [scored for scored, rules in others])


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    """A late optional rung cannot erase an earlier replay-complete order."""
    retained = []
    context['_scout_candidates'] = retained
    try:
        return _propose(instance, seed=seed, budget_s=budget_s, context=context)
    except Exception as error:
        context.setdefault('method_metadata', {}).setdefault('errors', []).append(
            f'{type(error).__name__}: {error}')
        return retain_candidates([], retained)
    finally:
        context.pop('_scout_candidates', None)


def rung_allowance(deadline, *, now=None):
    """A short remainder never authorizes a fresh minimum-sized rung."""
    return max(0., min(120., deadline-(monotonic() if now is None else now)))


def retain_candidates(passing, failed):
    """All replay-complete orders remain eligible for ordinary finishing."""
    unique = {}
    for scored in sorted(passing+failed, key=lambda s: s.objective):
        key = tuple((type(a).__name__, a.box, getattr(a, 'slot', None),
                     getattr(a, 'to', None)) for a in scored.actions)
        unique.setdefault(key, scored)
    return [scored.actions for scored in unique.values()]



def finish(instance, plan, *, seed: int, finalize_s: float, label: str, context: dict):
    """A stow that passes at the ship's default tanks needs no ballast search. Otherwise run the V4 finisher."""
    from ..portfolio import baseline_finish as _finish
    report = check(instance, plan)
    if report.passed:
        return plan, dict(finisher='direct', passed=True)
    result, row = _finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label)
    return result, row
