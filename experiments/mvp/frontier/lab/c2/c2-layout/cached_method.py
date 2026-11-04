"""Search the small port/cover design graph; pack each design with scout pages.

The page LP is a heuristic screen, never a certified whole-map lower bound.
Only the full checker establishes success.  Copied pages.py is documented in
search_notes.md; all shared production and C1 sources remain unchanged.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import product
from pathlib import Path
import importlib.util
import random
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
SCOUT = LAB / 'c1' / 'fx-scout'
sys.path.insert(0, str(SCOUT))
sys.path.insert(0, str(HERE))

from alns import Sequencer
from lash_oracle import Fast
from pages_cached import Model
from stowage.checker import check
from stowage.conventions import cells_of_slot
from stowage.moves import apply_plan
from stowage.bounds import lower_bound as counting_lower_bound


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


E9FIX = _load('c2_layout_e9fix', LAB / 'c1' / 'leader' / 'e9fix.py')
E9FIX.install()


def one_stow(problem, model, *, seed, deadline, root=None):
    """LP coverage, integer dive, balance, E9 repair and complete legal replay."""
    started = monotonic()
    if root is None:
        root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1,
                              deadline=started + .35 * max(0., deadline-started))
    row = dict(lp_uncovered=round(root['uncovered'], 3), lp_proved=root['proved'],
               lp_s=round(monotonic()-started, 2))
    if root['uncovered'] > 3. or monotonic() >= deadline:
        return None, row
    rng = random.Random(seed)
    model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=3,
                   deadline=deadline, max_rounds=30)
    chosen, info = model.dive(deadline=deadline, rng=rng)
    row.update(info)
    if chosen is None:
        return None, row
    chosen, heel = model.balance(chosen, rng)
    assignment = model.clear_oog(model.assignment(chosen))
    try:
        from repair import repair_oog
    except ImportError:
        pass
    else:
        assignment = repair_oog(problem, model, assignment)
    row.update(heel_tm=round(heel, 1), empty_hold=model.empty_hold_cells(chosen),
               mixes=problem.mixes_hold_stack(assignment), columns=len(model.columns),
               s=round(monotonic()-started, 1))
    if row['mixes']:
        return None, row
    ordered = Sequencer(problem).order(assignment)
    scored = problem.score(ordered) if ordered is not None else None
    row['lifts'] = scored.objective if scored else None
    row['replay_legal'] = scored is not None
    return scored, row


def block_layout(model):
    """Original scout layout retained first as a reproducible search anchor."""
    cells, need = defaultdict(int), defaultdict(float)
    for page in model.pages:
        if page.kind == '20' and page.cover is not None:
            cells[page.cover] += len(page.slots)
    for b in model.boxes:
        need[model.pod[b]] += float(model.T.units[b])
    layout = {}
    for cover in sorted(cells, key=lambda c: (-cells[c], c)):
        port = max(need, key=need.get)
        layout[cover] = port
        need[port] -= cells[cover]
    return layout


def enumerate_layouts(model, *, limit=64):
    """Enumerate pure cover assignments and rank by optimistic resource slack.

    This score only orders search.  Capacity estimates deliberately ignore most
    interactions; they are not reported as a lift lower bound.  The subsequent
    page LP tests the actual individual boxes and fitted stack capacities.
    """
    ports = model.ports
    if len(ports) != 2:
        return [block_layout(model)]
    early, late = ports
    capacity = defaultdict(lambda: defaultdict(float))
    for page in model.pages:
        if page.cover is None:
            continue
        area = 'deck' if page.deck else 'hold'
        if page.kind == '20':
            capacity[page.cover][area+'_units'] += len(page.slots)
            capacity[page.cover][area+'_mass'] += page.limit_kg
        else:
            capacity[page.cover][area+'_long'] += len(page.slots)
    covers = sorted(capacity)
    demand = defaultdict(lambda: defaultdict(float))
    for b in model.boxes:
        port = model.pod[b]
        demand[port]['units'] += float(model.T.units[b])
        demand[port]['long'] += float(model.T.units[b] > 1)
        demand[port]['mass'] += float(model.T.mass[b])
        if model.heavy[b]:
            demand[port]['heavy'] += float(model.T.units[b])
    # If the graph is unexpectedly large, make a deterministic neighbourhood
    # of the scout layout instead of allocating an exponential list.
    anchor = block_layout(model)
    if len(covers) <= 16:
        candidates = ({c: p for c, p in zip(covers, values)}
                      for values in product(ports, repeat=len(covers)))
    else:
        candidates = [anchor]
        for c in covers:
            trial = dict(anchor)
            trial[c] = late if trial[c] == early else early
            candidates.append(trial)
    ranked = []
    for layout in candidates:
        score = 0.
        for port in ports:
            hold_units = sum(capacity[c]['hold_units'] for c in covers if layout[c] == port)
            # Late boxes may only use late covers. Early boxes may use either
            # deck, because leaving before the hold opens costs no restow.
            for resource, scale in (('units', 1.), ('long', 2.), ('mass', .00005)):
                available = sum(capacity[c]['hold_'+resource] for c in covers if layout[c] == port)
                available += sum(capacity[c]['deck_'+resource] for c in covers
                                 if port == early or layout[c] == late)
                shortage = max(0., demand[port][resource]-available)
                score += 1000.*scale*shortage
            score += 30.*max(0., demand[port]['heavy']-hold_units)
            target = demand[port]['units']/max(1., sum(d['units'] for d in demand.values()))
            total_hold = sum(capacity[c]['hold_units'] for c in covers)
            score += abs(hold_units-target*total_hold)
        # Balanced mass capacity is a weaker tie breaker than genuine shortage.
        score += .01*sum(abs(sum(capacity[c]['hold_mass'] for c in covers if layout[c] == p)
                              -demand[p]['mass']*.55)/1000. for p in ports)
        ranked.append((score, tuple(layout[c] for c in covers), layout))
    ranked.sort(key=lambda x: (x[0], x[1]))
    out = [anchor]
    seen = {tuple(anchor[c] for c in covers)}
    for _, key, layout in ranked:
        if key not in seen:
            out.append(layout)
            seen.add(key)
        if len(out) >= limit:
            break
    return out


def verdict(instance, plan):
    report = check(instance, plan)
    rules = sorted({v.rule_id for v in report.violations})
    bow = any('trim' in v.rule_id and v.measured is not None and v.measured < 0
              for v in report.violations)
    bow |= any('draft' in v.rule_id and 'fwd' in v.message for v in report.violations)
    return report.passed, rules, bow


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    E9FIX.install()
    problem = context['problem']
    meta = context.setdefault('method_metadata', {})
    meta.update(method='enumerated port/cover layout + page packing', ladder=[], layouts=[])
    if instance.onboard.placements:
        meta['skipped'] = 'empty-arrival model'
        return []
    started = monotonic()
    deadline = started+budget_s
    first_model = Model(problem)
    meta['unary_setup_s'] = round(monotonic()-started, 2)
    first, row = one_stow(problem, first_model, seed=seed,
                         deadline=min(deadline, monotonic()+max(20., .18*budget_s)))
    meta['ladder'].append(dict(rung='plain_reference', **row))
    if first is None:
        return []
    ship = apply_plan(instance, first.plan).final_state.ship
    oracle = Fast(instance, ship)
    mass = sum(problem.boxes[q.container_id].gross_mass_kg for q in ship.placements)
    lcg = sum(problem.boxes[q.container_id].gross_mass_kg
              *problem.cells[cells_of_slot(q.slot)[0]].lcg_m for q in ship.placements)/mass
    # These are family-level caps learned in C1, applied to all requested TRAIN
    # maps by size, rather than map-id-specific settings or stored assignments.
    settings = dict(deck_tiers=3 if len(instance.load_list)>120 else 4,
                    lash_limit=.94, lcg_target=None, hold_reward=3.)
    passing, hopeful = [], []
    forced_relocations = counting_lower_bound(instance).relocations
    ok, rules, bow_down = verdict(instance, first.plan)
    meta['ladder'][-1].update(full_check=ok, rules=rules)
    if ok:
        passing.append(first)
    if bow_down:
        settings['lcg_target'] = lcg-1.5

    def learn(rules, bow_down):
        if any('visibility' in r for r in rules):
            settings['deck_tiers'] = 3
        if bow_down:
            settings['lcg_target'] = (settings['lcg_target'] or lcg)-1.5
        elif any('trim' in r or 'draft' in r for r in rules) and settings['lcg_target'] is not None:
            settings['lcg_target'] += 1.
        if any('securing' in r for r in rules):
            settings['lash_limit'] = max(.85, settings['lash_limit']-.03)
        if any('e6' in r for r in rules):
            settings['hold_reward'] *= 3.

    # Try the incumbent cover design immediately. On feasible medium families
    # this avoids constructing/checking several expensive high-lift free designs.
    base_model = Model(problem, oracle=oracle, **settings)
    layouts = enumerate_layouts(base_model)
    meta['layouts_enumerated'] = 2**len(layouts[0]) if len(base_model.ports)==2 else 1
    queue = [(layouts[0], True), (layouts[0], 'no_restow')]
    queue += [(layout, 'no_restow') for layout in layouts[1:4]]
    queue += [(layouts[0], False), (None, False)]
    queue += [(layout, 'no_restow') for layout in layouts[4:]]
    seen = set()
    attempt = 0
    while queue and monotonic() < deadline-2.:
        layout, deck_mode = queue.pop(0)
        key = (tuple(sorted((layout or {}).items())), str(deck_mode), str(settings))
        if key in seen:
            continue
        seen.add(key)
        attempt += 1
        remaining = deadline-monotonic()
        share = min(65., remaining)
        model = Model(problem, oracle=oracle, layout=layout, layout_deck=deck_mode, **settings)
        lp_started = monotonic()
        root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1,
                              deadline=min(deadline, lp_started+min(8., .25*share)), max_rounds=100)
        if root['uncovered'] > 3.:
            meta['layouts'].append(dict(layout=layout, deck_mode=deck_mode, settings=dict(settings),
                                        lp_uncovered=round(root['uncovered'], 3),
                                        lp_s=round(monotonic()-lp_started, 2), screened=True))
            continue
        scored, row = one_stow(problem, model, seed=seed+attempt,
                              deadline=min(deadline, lp_started+share), root=root)
        row.update(layout=layout, deck_mode=deck_mode, settings=dict(settings))
        meta['layouts'].append(row)
        if scored is None:
            continue
        if passing and scored.objective >= min(s.objective for s in passing):
            row['note'] = 'no improvement over checked incumbent'
            continue
        ok, rules, bow_down = verdict(instance, scored.plan)
        row.update(full_check=ok, rules=rules)
        if ok:
            passing.append(scored)
            hold_covers = {p.cover for p in model.pages if not p.deck and p.cover is not None}
            if scored.objective <= forced_relocations+4*len(hold_covers):
                row['note'] = 'reached four lifts per available hold cover; stop design search'
                break
            continue
        if all(r.startswith('iscode') for r in rules):
            hopeful.append(scored)
        # Learn physics on the first design before spending time on every
        # alternative layout with the same known-invalid loading condition.
        old = dict(settings)
        learn(rules, bow_down)
        if old != settings and attempt < 9:
            queue.insert(0, (layout, deck_mode))
        if len(meta['layouts']) > 20 and not passing:
            break
    meta['propose_s'] = round(monotonic()-started, 2)
    meta['complete_replay_legal_stows'] = sum(bool(r.get('replay_legal')) for r in meta['ladder']+meta['layouts'])
    meta['fully_checked_stows'] = len(passing)
    passing.sort(key=lambda s: s.objective)
    hopeful.sort(key=lambda s: s.objective)
    best = passing[0].objective if passing else float('inf')
    cheaper = [s for s in hopeful if s.objective < best][:1]
    return [s.actions for s in cheaper+passing]+[s.actions for s in hopeful if s not in cheaper][:1]


def finish(instance, plan, *, seed: int, finalize_s: float, label: str, context: dict):
    from planner import _finish
    report = check(instance, plan)
    if report.passed:
        return plan, dict(finisher='direct', passed=True)
    return _finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label)
