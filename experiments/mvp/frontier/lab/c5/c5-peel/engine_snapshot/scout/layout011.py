# Provenance: copied from engine/scout/layout.py (0.11); upstream copied from lab/c2/c2-layout/cached_method.py; integrated lab/c2/c2-cgscale method.py budget and RMP handling.
"""Search the small port/cover design graph; pack each design with scout pages.

The page LP is a heuristic screen, never a certified whole-map lower bound.
Only the full checker establishes success.  Copied pages.py is documented in
search_notes.md; all shared production and C1 sources remain unchanged.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import product
import hashlib
import random
from time import monotonic

from alns import Sequencer
from .lash_oracle import Fast
from .layout_pages import Model, SlowModel
from .repair import repair_oog
from stowage.checker import check
from stowage.conventions import cells_of_slot
from stowage.moves import apply_plan
from stowage.bounds import lower_bound as counting_lower_bound


def enabled(context, name):
    return bool(getattr(context.get('engine_config'), name, True))


def model_factory(problem, context, **kwargs):
    # A future mixed-pattern/per-bay-sight constructor can be supplied here.
    custom = context.get('page_model_factory')
    cls = Model if enabled(context, 'fast_pricing') else SlowModel
    options = dict(unary_cache=enabled(context, 'unary_cache'),
                   asymmetric_deck=enabled(context, 'asymmetric_deck'),
                   restricted_master=enabled(context, 'restricted_master'),
                   aggregation=enabled(context, 'aggregation'))
    return (custom or cls)(problem, **options, **kwargs)



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



def securing_feedback(problem, model, chosen, scored, *, deadline):
    from ..benders.cuts import page_cut_callback, page_cut_repair
    cut = page_cut_callback(problem, model, chosen, scored)
    row = dict(certified_cuts=int(cut is not None), scope='fixed page choices only')
    if cut is None:
        return None, row
    model.page_cuts.append(cut)
    row['kind'] = cut['kind']
    replacement, repair = page_cut_repair(model, chosen, model.page_cuts, deadline)
    row['repair'] = repair
    return replacement, row



def balance_with_cuts(model, chosen, rng):
    """Heel guidance must preserve every certified no-good on page choices."""
    raw = list(chosen)
    if not model.respects_cuts(raw):
        raise ValueError('replacement violates a certified page cut')
    balanced, heel = model.balance(list(raw), rng)
    if model.respects_cuts(balanced):
        return balanced, heel, False
    return raw, model.moments(raw), True


def one_stow(problem, model, *, seed, deadline, root=None, context=None):
    """LP coverage, integer dive, balance, E9 repair and complete legal replay."""
    started = monotonic()
    if root is None:
        root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1,
                              deadline=started + .35 * max(0., deadline-started))
    row = dict(lp_uncovered=round(root['uncovered'], 3), lp_proved=root['proved'],
               lp_objective=float(root.get('objective', 0.)), dive_s=0., replay_complete=False,
               lp_s=round(monotonic()-started, 2),
               unary_cache_hit=bool(getattr(model, 'unary_cache_hit', False)))
    if root['uncovered'] > 3. or monotonic() >= deadline:
        return None, row
    rng = random.Random(seed)
    # Integer coverage already supplies an incumbent. Preserve dive time rather
    # than polishing a fractional solution whose lift surrogate is only guidance.
    if getattr(model, 'seed_chosen', None) is None:
        cost_deadline = min(deadline, monotonic()+.2*max(0., deadline-monotonic()))
        model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=3,
                       deadline=cost_deadline, max_rounds=30)
    dive_started = monotonic()
    chosen, info = model.dive(deadline=deadline, rng=rng)
    row.update(info, dive_s=round(monotonic()-dive_started, 4))
    if chosen is None:
        return None, row
    chosen, heel = model.balance(chosen, rng)
    assignment = model.clear_oog(model.assignment(chosen))
    before_repair = dict(assignment)
    if context is None or enabled(context, 'oog_repair'):
        assignment = repair_oog(problem, model, assignment)
    row['oog_repair_moved_boxes'] = sum(assignment[b] != slot for b, slot in before_repair.items())
    row.update(heel_tm=round(heel, 1), empty_hold=model.empty_hold_cells(chosen),
               mixes=problem.mixes_hold_stack(assignment), columns=len(model.columns),
               s=round(monotonic()-started, 1))
    if row['mixes']:
        return None, row
    ordered = Sequencer(problem).order(assignment)
    scored = problem.score(ordered) if ordered is not None else None
    # A callback may add a certified no-good and retry the current integer pool.
    # All repaired choices go through balance, OOG repair and real replay again.
    feedback = (context or {}).get('page_feedback') if context is not None and enabled(context, 'securing_cuts') else None
    if context is not None and enabled(context, 'securing_cuts') and feedback is None:
        feedback = securing_feedback
    if scored is not None and feedback is not None:
        replacement, feedback_row = feedback(problem, model, chosen, scored, deadline=deadline)
        row['securing_cuts'] = feedback_row
        if replacement is not None:
            chosen, heel, reverted = balance_with_cuts(model, replacement, rng)
            feedback_row['balance_reverted_for_cut'] = reverted
            assignment = model.clear_oog(model.assignment(chosen))
            if enabled(context, 'oog_repair'):
                assignment = repair_oog(problem, model, assignment)
            ordered = Sequencer(problem).order(assignment)
            repaired = problem.score(ordered) if ordered is not None else None
            if repaired is not None:
                scored = repaired
    history = list(getattr(model, 'rmp_history', ()))
    row.update(rmp_calls=len(history), rmp_s=round(sum(r['seconds'] for r in history), 6), rmp=history)
    row['lifts'] = scored.objective if scored else None
    row['replay_legal'] = scored is not None
    row['replay_complete'] = scored is not None
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


def verdict(instance, plan, *, context=None):
    callback = (context or {}).get('page_verdict')
    if callback is not None:
        return callback(instance, plan)
    cache = context.setdefault('_engine_page_checks', {}) if context is not None else {}
    key = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
    if key in cache:
        return cache[key]
    report = check(instance, plan)
    rules = sorted({v.rule_id for v in report.violations})
    bow = any('trim' in v.rule_id and v.measured is not None and v.measured < 0
              for v in report.violations)
    bow |= any('draft' in v.rule_id and 'fwd' in v.message for v in report.violations)
    answer = report.passed, rules, bow
    cache[key] = answer
    return answer



def candidate_stows(passing, hopeful, failed, *, retain_ballast_candidates=False):
    """Retain every replay-complete action sequence; no family or top-k filter.

    The compatibility flag no longer drops candidates: review P1 showed that
    even a securing failure or a third hopeful may be the sole finishable stow.
    """
    selected = {}
    for scored in sorted(list(passing)+list(hopeful), key=lambda item: item.objective):
        key = tuple((action.box, action.slot) for action in scored.actions)
        selected.setdefault(key, scored)
    retained = 0
    for scored in sorted(failed, key=lambda item: item.objective):
        key = tuple((action.box, action.slot) for action in scored.actions)
        if key not in selected:
            selected[key] = scored
            retained += 1
    return sorted(selected.values(), key=lambda item: item.objective), retained


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    problem = context['problem']
    meta = context.setdefault('method_metadata', {})
    meta['options'] = {name: enabled(context, name) for name in ('asymmetric_deck', 'oog_repair', 'unary_cache', 'fast_pricing', 'aggregation', 'restricted_master')}
    meta.update(method='enumerated port/cover layout + page packing', ladder=[], layouts=[])
    if instance.onboard.placements:
        meta['skipped'] = 'empty-arrival model'
        return []
    started = monotonic()
    deadline = started+budget_s
    first_model = model_factory(problem, context)
    meta['unary_setup_s'] = round(monotonic()-started, 2)
    first, row = one_stow(problem, first_model, seed=seed,
                         deadline=min(deadline, monotonic()+max(20., (.65 if len(instance.load_list)>=400 else .18)*budget_s)), context=context)
    meta['ladder'].append(dict(rung='plain_reference', **row))
    if first is None:
        meta['propose_s'] = round(monotonic()-started, 2)
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
    passing, hopeful, failed = [], [], []
    forced_relocations = counting_lower_bound(instance).relocations
    ok, rules, bow_down = verdict(instance, first.plan, context=context)
    meta['ladder'][-1].update(full_check=ok, rules=rules)
    if ok:
        passing.append(first)
    else:
        failed.append(first)
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
    base_model = model_factory(problem, context, oracle=oracle, **settings)
    previous = first_model
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
        model = model_factory(problem, context, oracle=oracle, layout=layout, layout_deck=deck_mode, **settings)
        warmed = warm_columns(model, previous, deadline=min(deadline, monotonic()+min(2., .05*share)))
        previous = model
        lp_started = monotonic()
        root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1,
                              deadline=min(deadline, lp_started+min(8., .25*share)), max_rounds=100)
        if root['uncovered'] > 3.:
            meta['layouts'].append(dict(layout=layout, deck_mode=deck_mode, settings=dict(settings),
                                        lp_uncovered=round(root['uncovered'], 3),
                                        lp_s=round(monotonic()-lp_started, 2), screened=True))
            continue
        scored, row = one_stow(problem, model, seed=seed+attempt,
                              deadline=min(deadline, lp_started+share), root=root, context=context)
        row.update(layout=layout, deck_mode=deck_mode, settings=dict(settings), warm_columns=warmed)
        meta['layouts'].append(row)
        if scored is None:
            continue
        if passing and scored.objective >= min(s.objective for s in passing):
            row['note'] = 'no improvement over checked incumbent'
            continue
        ok, rules, bow_down = verdict(instance, scored.plan, context=context)
        row.update(full_check=ok, rules=rules)
        if ok:
            passing.append(scored)
            hold_covers = {p.cover for p in model.pages if not p.deck and p.cover is not None}
            if scored.objective <= forced_relocations+4*len(hold_covers):
                row['note'] = 'reached four lifts per available hold cover; stop design search'
                break
            continue
        failed.append(scored)
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
    rows = meta['ladder']+meta['layouts']
    meta['contributions'] = dict(
        complete_replay_legal_stows=meta['complete_replay_legal_stows'],
        fully_checked_stows=len(passing),
        asymmetric_deck_trials=sum(r.get('deck_mode') == 'no_restow' for r in rows),
        unary_cache_hits=sum(bool(r.get('unary_cache_hit')) for r in rows),
        oog_repair_moved_boxes=sum(r.get('oog_repair_moved_boxes', 0) for r in rows),
        restricted_master_calls=sum(r.get('rmp_calls', 0) for r in rows),
        securing_cuts=sum(r.get('securing_cuts', {}).get('certified_cuts', 0) for r in rows),
        best_checked_lifts=min((s.objective for s in passing), default=None))
    selected, guidance_count = candidate_stows(
        passing, hopeful, failed,
        retain_ballast_candidates=bool(context.get('retain_ballast_candidates')))
    meta['retained_guidance_candidates'] = guidance_count
    meta['contributions']['retained_guidance_candidates'] = guidance_count
    seen = {tuple(scored.actions) for scored in selected}
    for scored in sorted(failed, key=lambda item: item.objective):
        if tuple(scored.actions) not in seen:
            selected.append(scored)
            seen.add(tuple(scored.actions))
    selected.sort(key=lambda item: item.objective)
    meta['all_complete_candidates_retained'] = True
    return [scored.actions for scored in selected]


def finish(instance, plan, *, seed: int, finalize_s: float, label: str, context: dict):
    from ..portfolio import baseline_finish as _finish
    report = check(instance, plan)
    if report.passed:
        return plan, dict(finisher='direct', passed=True)
    return _finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label)
