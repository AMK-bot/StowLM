"""Lab plug-in: stacks as pages, column generation with exact knapsack pricing, then diving. See pages.py and NOTES.md.

Ladder: one plain packing gives a reference ship (GM and draught for the securing check). Each later stow has securing
in the pricing and a fore-aft row in the master, and each failed full check changes the settings for the next stow. A
stow that passes is then tried again with one port per cover, for fewer lifts. Stored plans are never used.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import random
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
sys.path.insert(0, str(LAB / 'c1' / 'fx-scout'))
sys.path.insert(0, str(LAB / 'c1' / 'leader'))
sys.path.insert(0, str(HERE))

from e9fix import install as install_e9
install_e9()

from collections import defaultdict  # noqa: E402

from alns import Problem, Sequencer  # noqa: E402
from lash_oracle import Fast  # noqa: E402
# File-based loading avoids a cached c1 ``pages`` module when comparing both methods.
_page_spec = importlib.util.spec_from_file_location('_cgscale_pages', HERE / 'pages.py')
_pages = importlib.util.module_from_spec(_page_spec)
sys.modules[_page_spec.name] = _pages
_page_spec.loader.exec_module(_pages)
Model = _pages.Model
from stowage.checker import check  # noqa: E402
from stowage.conventions import cells_of_slot  # noqa: E402
from stowage.moves import apply_plan  # noqa: E402

def warm_columns(model, previous, *, deadline, limit=4000):
    """Reuse concrete patterns only after the new rung's unchanged column gates.

    New oracle, tier caps, port layout and mass limits can all invalidate a pattern.
    The old pool is therefore a source of candidates, never a trusted solution.
    """
    if previous is None:
        return 0
    if hasattr(model, 'warm_from'):
        return model.warm_from(previous, deadline=deadline, limit=limit)
    where = {(page.kind, page.stacks): k for k, page in enumerate(model.pages)}
    useful = list(getattr(previous, '_warm_indices', ()))
    useful += list(range(max(0, len(previous.columns)-limit), len(previous.columns)))
    added, seen = 0, set()
    for index in useful:
        if monotonic() >= deadline:
            break
        old = previous.columns[index]
        page_index = where.get((previous.pages[old.page].kind, previous.pages[old.page].stacks))
        key = (page_index, old.boxes)
        if page_index is None or key in seen:
            continue
        seen.add(key)
        page = model.pages[page_index]
        if len(old.boxes) <= len(page.slots) and model.legal(page, old.boxes) and model.lash_ok(page, old.boxes):
            added += int(model.add(page_index, old.boxes))
    return added


def one_stow(problem, model, *, seed, deadline):
    """LP, dive, heel balance, load order, real replay. Returns (scored or None, record)."""
    started = monotonic()
    root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1,
                          deadline=started+.5*(deadline-started))
    row = dict(lp_uncovered=round(root['uncovered'], 6), lp_objective=float(root['objective']),
               lp_proved=root['proved'], lp_s=round(monotonic()-started, 4),
               lp_rounds=root['rounds'], lp_columns=root['columns'], dive_s=0.,
               replay_complete=False)
    model._warm_indices = list(root['x'])
    if root['uncovered'] > 3.:
        return None, row
    # A small shortfall can be the knapsack's caution, so dive anyway: the tail asks the exact securing check.
    rng = random.Random(seed)
    dive_started = monotonic()
    # A few distinct best-fit ties are cheap compared with polishing thousands
    # of fractional columns. Every attempt uses the original column gates.
    if getattr(model, 'seed_chosen', None) is None and len(model.boxes) >= 400:
        retry_deadline = min(deadline, monotonic()+2.)
        for retry in range(8):
            if monotonic() >= retry_deadline:
                break
            incumbent = model.first_fit(set(model.boxes), set(model.stacks), random.Random(seed+retry+1))
            if incumbent is not None:
                model.seed_chosen = incumbent
                row['incumbent_retry'] = retry+1
                break
    # A checked integer seed is already an incumbent. Preserve it immediately;
    # the later ladder rungs address ship physics and hatch layout.
    if getattr(model, 'seed_chosen', None) is None:
        cost_deadline = min(deadline, monotonic()+.2*max(0., deadline-monotonic()))
        model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=3, deadline=cost_deadline, max_rounds=30)
    chosen, info = model.dive(deadline=deadline, rng=rng)
    row.update(info, dive_s=round(monotonic()-dive_started, 4))
    if chosen is None:
        return None, row
    chosen, heel = model.balance(chosen, rng)
    model._warm_indices = chosen+model._warm_indices
    assignment = model.clear_oog(model.assignment(chosen))
    row.update(heel_tm=round(heel, 1), empty_hold=model.empty_hold_cells(chosen), mixes=problem.mixes_hold_stack(assignment),
               columns=len(model.columns), s=round(monotonic()-started, 1))
    if row['mixes']:
        return None, row
    ordered = Sequencer(problem).order(assignment)
    scored = problem.score(ordered) if ordered is not None else None
    row['lifts'] = scored.objective if scored else None
    row['replay_complete'] = bool(scored)
    row['s'] = round(monotonic()-started, 4)
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


def plan_key(plan):
    return hashlib.sha256(plan.model_dump_json().encode()).hexdigest()


def verdict(instance, plan, *, context=None):
    """The checker's own rule ids and the worst trim sign, so the next rung knows what to change."""
    cache = context.setdefault('_cgscale_full_checks', {}) if context is not None else {}
    key = plan_key(plan)
    if key in cache:
        return cache[key]
    report = check(instance, plan)
    rules = sorted({v.rule_id for v in report.violations})
    bow_down = any('trim' in v.rule_id and v.measured is not None and v.measured < 0 for v in report.violations)
    forward_draught = any('draft' in v.rule_id and 'fwd' in v.message for v in report.violations)
    answer = report.passed, rules, bow_down or forward_draught
    cache[key] = answer
    return answer


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    """Checker-guided ladder: each failed full check names what the next stow must change."""
    install_e9()
    # score.py creates context's Problem before importing a plug-in. Construct its
    # tables again after E9 installation, so the default clearance reaches masks.
    if not context.get('_cgscale_e9_problem'):
        context['problem'] = Problem(instance)
        context['_cgscale_e9_problem'] = True
    problem = context['problem']
    meta = context.setdefault('method_metadata', {})
    meta['pages_sha256'] = hashlib.sha256((HERE / 'pages.py').read_bytes()).hexdigest()
    meta['e9_installed_before_problem'] = True
    if instance.onboard.placements:
        meta['skipped'] = 'page model covers empty arrival ships'
        return []
    started = monotonic()
    deadline = started+budget_s
    meta['ladder'] = []
    model_started = monotonic()
    previous = Model(problem)
    meta['initial_model_s'] = round(monotonic()-model_started, 4)
    # Large maps need a real dive before spending the ladder on ship physics.
    # The old 40% share plus a long cost polish could consume the entire first
    # attempt without fixing a single column.
    first_share = .85 if len(instance.load_list) >= 400 else .4
    meta['first_stow_budget_fraction'] = first_share
    first_deadline = min(deadline, monotonic()+first_share*max(0., deadline-monotonic()))
    first, row = one_stow(problem, previous, seed=seed, deadline=first_deadline)
    meta['ladder'].append(dict(rung='plain', **row))
    meta['replay_complete'] = bool(row.get('replay_complete'))
    if first is None:
        meta['propose_s'] = round(monotonic()-started, 4)
        return []
    # Keep a concrete replay certificate even when ship physics later refuses
    # every candidate and the scorecard therefore has no passing plan to save.
    meta['replay_actions'] = [dict(box=action.box, slot=action.slot) for action in first.actions]
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

    checked_at = monotonic()
    ok, rules, bow_down = verdict(instance, first.plan, context=context)
    meta['ladder'][-1].update(full_check=ok, rules=rules, full_check_s=round(monotonic()-checked_at, 4))
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
        share = min(120., max(0., deadline-monotonic()))
        if share <= 0.:
            break
        rung_deadline = monotonic()+share
        model = Model(problem, oracle=oracle, **arguments)
        warm_at = monotonic()
        warmed = warm_columns(model, previous, deadline=min(rung_deadline, warm_at+min(4., .05*share)))
        warm_s = monotonic()-warm_at
        scored, row = one_stow(problem, model, seed=seed+attempt, deadline=rung_deadline)
        row.update(warm_columns=warmed, warm_s=round(warm_s, 4))
        previous = model
        meta['replay_complete'] |= bool(row.get('replay_complete'))
        row.update(rung=attempt, settings=str(settings), layout=str({k: v for k, v in layout.items()}))
        if scored is not None and any(scored.objective >= s.objective for s in passing):
            row['note'] = 'no cheaper than a passing stow'
            meta['ladder'].append(row)
            layout = next(layouts, None)
            if layout is None:
                break
            continue
        if scored is not None:
            checked_at = monotonic()
            ok, rules, bow_down = verdict(instance, scored.plan, context=context)
            row.update(full_check=ok, rules=rules, full_check_s=round(monotonic()-checked_at, 4))
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
    meta['full_check_passes'] = len(passing)
    passing.sort(key=lambda s: s.objective)
    # A stow failing only rules that tanks can change is worth a finisher run. The rest can't be saved by ballast.
    fixable = ('iscode',)
    hopeful = sorted((s for s, rules in others if all(r.startswith(fixable) for r in rules)), key=lambda s: s.objective)
    best = passing[0].objective if passing else float('inf')
    cheaper = [s for s in hopeful if s.objective < best][:1]
    return [s.actions for s in cheaper+passing]+[s.actions for s in hopeful if s not in cheaper][:1]


def finish(instance, plan, *, seed: int, finalize_s: float, label: str, context: dict):
    """A stow that passes at the ship's default tanks needs no ballast search. Otherwise run the V4 finisher."""
    from planner import _finish
    cached = context.get('_cgscale_full_checks', {}).get(plan_key(plan))
    ok = cached[0] if cached is not None else verdict(instance, plan, context=context)[0]
    if ok:
        # The runner always performs its own independent full check afterwards.
        return plan, dict(finisher='direct', passed=True, reused_full_check=cached is not None)
    result, row = _finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label)
    return result, row
