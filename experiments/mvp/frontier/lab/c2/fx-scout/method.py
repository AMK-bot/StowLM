"""Lab plug-in: stacks as pages, column generation with exact knapsack pricing, then diving. See pages.py and NOTES.md.

Copied from c1/fx-scout. Each stow is built on GM, draught and sight roofs read from the stow before it, with mixed deck pages,
reserved slots for out-of-gauge boxes and a fore-aft row in the master. Each failed full check changes the settings for the next
stow. A stow that passes is then tried again with one port per cover, for fewer lifts. Stored plans are never used.
"""
from __future__ import annotations

from pathlib import Path
import random
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent/'c1/leader'))
sys.path.insert(0, str(HERE))

from collections import defaultdict  # noqa: E402

from alns import Sequencer  # noqa: E402
import e9fix  # noqa: E402
from lash_oracle import Fast  # noqa: E402
from pages import Model  # noqa: E402
from physics import Reference  # noqa: E402
from stowage.checker import check  # noqa: E402
from stowage.conventions import cells_of_slot  # noqa: E402
from stowage.moves import apply_plan  # noqa: E402

e9fix.install()


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
    assignment = model.assignment(chosen)
    row.update(heel_tm=round(heel, 1), empty_hold=model.empty_hold_cells(chosen), mixes=problem.mixes_hold_stack(assignment),
               columns=len(model.columns), s=round(monotonic()-started, 1))
    if row['mixes']:
        return None, row
    ordered = Sequencer(problem).order(assignment)
    scored = problem.score(ordered) if ordered is not None else None
    if scored is not None and model.oracle is not None:
        ship = apply_plan(problem.instance, scored.plan).final_state.ship
        polished, note = model.polish(chosen, Fast(problem.instance, ship))
        row['polish'] = note
        if note['swaps']:
            ordered = Sequencer(problem).order(model.assignment(polished))
            again = problem.score(ordered) if ordered is not None else None
            scored = again or scored
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
    worst = {}
    for v in report.violations:
        if v.measured is None or v.limit is None:
            continue
        place = v.message.split(']')[0].split(':')
        key = f"{v.rule_id.split('.')[-1]}|{':'.join(place[-2:]) if len(place) > 1 else ''}|{v.message.split(']')[-1].split()[0]}"
        ratio = v.measured/v.limit if v.limit else 0.
        if key not in worst or abs(ratio-1) > abs(worst[key][2]-1):
            worst[key] = (round(v.measured, 3), round(v.limit, 3), round(ratio, 3))
    verdict.worst = worst
    bow_down = any('trim' in v.rule_id and v.measured is not None and v.measured < 0 for v in report.violations)
    forward_draught = any('draft' in v.rule_id and 'fwd' in v.message for v in report.violations)
    return report.passed, rules, bow_down or forward_draught


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    """Checker-guided ladder: each failed full check names what the next stow must change."""
    problem = context['problem']
    meta = context.setdefault('method_metadata', {})
    if instance.onboard.placements:
        meta['skipped'] = 'page model covers empty arrival ships'
        return []
    started = monotonic()
    deadline = started+budget_s
    meta['ladder'] = []
    first, row = one_stow(problem, Model(problem, reserve=False), seed=seed, deadline=started+.4*budget_s)
    meta['ladder'].append(dict(rung='plain', **row))
    if first is None:
        return []
    ship = apply_plan(instance, first.plan).final_state.ship
    mass = sum(problem.boxes[q.container_id].gross_mass_kg for q in ship.placements)
    lcg = sum(problem.boxes[q.container_id].gross_mass_kg*problem.cells[cells_of_slot(q.slot)[0]].lcg_m
              for q in ship.placements)/mass
    settings = dict(margin=0., lash_limit=.97, lcg_target=None, mixed=False)
    passing, others = [], []
    tables = problem.engine.T

    def learn(rules, bow_down, settings):
        """What the failed full check says the next stow must change."""
        settings = dict(settings)
        if any('visibility' in r for r in rules):
            settings['margin'] += .7
        if any('securing' in r for r in rules) and settings['lash_limit'] > .985:
            settings['lash_limit'] = .985
        if bow_down:
            settings['lcg_target'] = (settings['lcg_target'] or lcg-1.5)-1.5
        elif any('trim' in r or 'draft' in r for r in rules) and settings['lcg_target'] is not None:
            settings['lcg_target'] += 1.
        if any('e6' in r for r in rules):
            settings['hold_reward'] = settings.get('hold_reward', 1.)*3.
        if any('second_check' in r for r in rules):
            settings['lift_weight'] = settings.get('lift_weight', 2e-5)*10.
        return settings

    ok, rules, bow_down = verdict(instance, first.plan)
    meta['ladder'][-1].update(full_check=ok, rules=rules)
    if ok:
        passing.append(first)
    else:
        others.append((first, rules))
        if not any('securing' in r for r in rules):
            settings = learn(rules, bow_down, settings)
        else:
            settings = learn([r for r in rules if 'securing' not in r], bow_down, settings)
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
        reference = Reference(instance, ship, tables, margin=settings['margin'])
        arguments = {k: v for k, v in settings.items() if k != 'margin'}
        arguments.update(oracle=reference.oracle, roofs=reference.roofs)
        if layout.get('layout'):
            arguments.update(layout, layout=block_layout(Model(problem, **arguments)))
        share = max(20., min(200., deadline-monotonic()))
        scored, row = one_stow(problem, Model(problem, **arguments), seed=seed+attempt, deadline=monotonic()+share)
        row.update(rung=attempt, settings=str(settings), layout=str({k: v for k, v in layout.items()}))
        if scored is not None and any(scored.objective >= s.objective for s in passing):
            row['note'] = 'no cheaper than a passing stow'
            meta['ladder'].append(row)
            layout = next(layouts, None)
            if layout is None:
                break
            continue
        if scored is not None:
            ship = apply_plan(instance, scored.plan).final_state.ship
            ok, rules, bow_down = verdict(instance, scored.plan)
            row.update(full_check=ok, rules=rules, worst=verdict.worst)
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
        if not settings['mixed']:
            settings['mixed'] = True
        elif settings['lcg_target'] is not None and row.get('lp_uncovered', 0.) > 3. and settings['lcg_target'] < lcg-.75:
            settings['lcg_target'] += 1.5
        elif settings['lash_limit'] < .999:
            settings['lash_limit'] = min(1., settings['lash_limit']+.015)
        elif settings['margin'] > 0.:
            settings['margin'] = max(0., settings['margin']-.35)
        else:
            break
    meta['propose_s'] = round(monotonic()-started, 2)
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
    report = check(instance, plan)
    if report.passed:
        return plan, dict(finisher='direct', passed=True)
    result, row = _finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label)
    return result, row
