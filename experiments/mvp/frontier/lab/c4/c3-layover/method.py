"""Cold arrival-aware pages with fixed prefixes and a priced prefix-restow branch.

Uses the C2 layout page constructor copied into page_base.py; no mutable engine
code is imported. The original instance and checker are never changed.
"""
from __future__ import annotations
from collections import defaultdict
from itertools import product
import importlib.util
from pathlib import Path
import random
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

pages = _load('c4_layover_pages', HERE / 'pages.py')
work_module = _load('c4_layover_work', HERE / 'work.py')
WorkBudget, WorkExhausted = work_module.WorkBudget, work_module.WorkExhausted
E9FIX = _load('c4_layover_e9fix', LAB / 'c1' / 'leader' / 'e9fix.py')
from alns import Problem, Sequencer
from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place, RestowOff
from stowage.checker import check
from stowage.conventions import cells_of_slot, parse_slot


def prepare(instance, restow_box=None, shift=None):
    """Explicitly discharge current cargo; optionally buy a prefix restow.

    The temporary Instance exposes already-forced quay boxes as pricing items.
    Its artificial load list is never passed to the final checker.
    """
    ex, actions = Expander(instance), []
    if shift is not None:
        action = Place(*shift)
        ex.step(action); actions.append(action)
    due = set(instance.discharge_list)
    while due:
        aboard = [b for b in due if ex.location(b) and ex.location(b)[0] == 'ship']
        if not aboard:
            raise ValueError('a discharge target was stranded in the quay buffer')
        box = max(aboard, key=lambda b: parse_slot(ex.location(b)[1]).tier)
        action = Discharge(box)
        ex.step(action); actions.append(action); due.remove(box)
    restow_boxes = () if restow_box is None else ((restow_box,) if isinstance(restow_box, str) else tuple(restow_box))
    for box in dict.fromkeys(restow_boxes):
        # Clearing one bottom prefix may already have removed other requested
        # roots through upward or hatch-cover closure. Never charge them twice.
        if ex.location(box) and ex.location(box)[0] == 'ship':
            action = RestowOff(box)
            ex.step(action); actions.append(action)
    state = ex.world_state()
    buffered = [b for b in state.yard.quay_buffer if b not in instance.discharge_list]
    working = instance.model_copy(update={'onboard': state.ship, 'yard': state.yard,
                                         'discharge_list': [],
                                         'load_list': list(dict.fromkeys(instance.load_list+buffered))})
    return working, tuple(actions), ex


def order_with_prefixes(problem, assignment, ex, prefix_actions):
    """Hold-first/support ordering also restores fixed boxes a hatch cleared.

    Existing boxes appear in the dependency graph, but a Place at its unchanged
    ship location is skipped. Thus fixed prefixes incur no gratuitous shifts.
    """
    final = {p.container_id:p.slot for p in problem.instance.onboard.placements}
    final.update(assignment)
    ordered = Sequencer(problem).order(final)
    if ordered is None:
        return None
    out, replay = list(prefix_actions), ex.copy()
    try:
        for action in ordered:
            if replay.location(action.box) == ('ship', action.slot):
                continue
            replay.step(action); out.append(action)
        replay.finish()
    except (ValueError, KeyError, TypeError):
        return None
    return tuple(out)


def layouts(model, limit=12):
    """Rank two-port cover designs including fixed hold edges/deck blockers."""
    ports = model.ports
    covers = sorted({p.cover for p in model.pages if p.cover is not None})
    if len(ports) != 2 or not covers:
        return [{}]
    capacity = defaultdict(lambda: defaultdict(float))
    for page in model.pages:
        if page.kind == '20':
            capacity[page.cover]['deck' if page.deck else 'hold'] += len(page.slots)
    demand = defaultdict(float)
    for b in model.boxes:
        demand[model.pod[b]] += float(model.T.units[b])
    designs = product(ports, repeat=len(covers)) if len(covers) <= 12 else [tuple(ports[k % 2] for k in range(len(covers)))]
    ranked = []
    early, late = ports
    for values in designs:
        design = dict(zip(covers, values))
        score = 0.
        for port in ports:
            room = sum(capacity[c]['hold'] for c in covers if design[c] == port)
            room += sum(capacity[c]['deck'] for c in covers if port == early or design[c] == late)
            score += 1000.*max(0., demand[port]-room)
        for cover in covers:
            fixed = model.arrival_hold_ports[cover]
            # New hold cargo on an already-used edge needs no extra later
            # cover pair; new early edges also block late arrival deck cargo.
            score += 2.*(design[cover] not in fixed)
            score += 2.*sum(model.p.pod(b) > design[cover]
                            for b in model.arrival_deck[cover])
            # Any load below an occupied deck pays now, so prefer other covers
            # when their relaxed spare capacity can meet the demand.
            score += .25*len(model.arrival_deck[cover])
        ranked.append((score, values, design))
    ranked.sort(key=lambda q:(q[0], q[1]))
    return [q[2] for q in ranked[:limit]]


def restow_options(instance):
    """Prioritised multi-prefix choices; the original expander prices every lift.

    Clearing all retained prefixes is an essential fallback when lighter
    arrival cargo occupies the bases needed by heavy new cargo. Deck-only and
    per-cover groups then buy less freedom at a lower real off/on price.
    """
    cells = {c.id:c for c in instance.vessel.cells}
    stacks = {s.id:s for s in instance.vessel.stacks}
    ports = {c.locode:c.index for c in instance.rotation.calls}
    boxes = {b.id:b for b in instance.containers}
    by_stack = defaultdict(list)
    for p in instance.onboard.placements:
        if p.container_id not in instance.discharge_list:
            by_stack[cells[cells_of_slot(p.slot)[0]].stack_id].append(p)
    choices, all_roots, deck_roots, by_cover = [], [], [], defaultdict(list)
    for stack, cargo in sorted(by_stack.items()):
        cargo.sort(key=lambda p:parse_slot(p.slot).tier)
        root = cargo[0].container_id
        all_roots.append(root)
        by_cover[stacks[stack].hatch_cover_id].append(root)
        if stacks[stack].on_deck:
            deck_roots.append(root)
        score = (len(cargo) if stacks[stack].on_deck else 0)
        score += sum(ports[boxes[b.container_id].pod] < ports[boxes[a.container_id].pod]
                     for i,a in enumerate(cargo) for b in cargo[:i])
        choices.append((-score, len(cargo), root))
    ordered = [('all_prefixes', tuple(all_roots)), ('deck_prefixes', tuple(deck_roots))]
    ordered += [('cover:'+str(cover), tuple(roots)) for cover,roots in sorted(by_cover.items(),key=lambda q:str(q[0]))]
    ordered += [('single:'+q[2], (q[2],)) for q in sorted(choices)[:2]]
    out, seen = [], set()
    for label, roots in ordered:
        key = frozenset(roots)
        if roots and key not in seen:
            seen.add(key); out.append((label, roots))
    return out


def direct_shift_options(instance, problem):
    """Tiny exact one-shift neighbourhood before a forced current discharge."""
    if len(instance.vessel.cells) > 12 or not instance.discharge_list:
        return []
    occupied = {c for p in instance.onboard.placements for c in cells_of_slot(p.slot)}
    choices = []
    for p in instance.onboard.placements:
        if p.container_id in instance.discharge_list:
            continue
        for slot in problem.slots_by_length.get(problem.boxes[p.container_id].length_ft, ()):
            if occupied.intersection(cells_of_slot(slot)):
                continue
            ex = Expander(instance)
            try:
                added = ex.step(Place(p.container_id, slot))
            except (ValueError, KeyError, TypeError):
                continue
            if sum(m.kind=='restow_shift' for m in added)==1 and not any(m.kind=='restow_off' for m in added):
                choices.append((p.container_id,slot))
    return choices[:12]


def _construct(problem, model, *, seed, work):
    rng = random.Random(seed)
    chosen = None
    info = {}
    try:
        chosen = model.first_fit(set(model.boxes), set(model.stacks), rng)
        if chosen is None:
            chosen = model.first_fit(set(model.boxes), set(model.stacks), random.Random(seed), guided=False)
            info['room_first_fallback_complete'] = chosen is not None
        if chosen is None:
            root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                                  phase=1, max_rounds=100)
            info.update(lp_uncovered=root['uncovered'], columns=len(model.columns))
            if root['uncovered'] <= 3.:
                integer = model.integer(seconds=None, hold_full=False, seed=seed)
                info['integer'] = model.last_integer
                if integer is not None:
                    chosen = integer['chosen']
                    info['integer_status'] = integer['status']
                else:
                    chosen, dive_info = model.dive(deadline=None, rng=rng, rounds=2, retreats=2)
                    info.update(dive_info)
    except WorkExhausted as exc:
        info['work_exhausted'] = str(exc)
    if chosen is None:
        return None, dict(info, reason='bounded_construction_incomplete', columns=len(model.columns))
    # Preserve a valid complete incumbent even if balancing admission exhausts.
    # Balance itself mutates columns only after each complete admissible swap.
    balance_rounds = min(150, work.remaining('balance_trials'))
    if balance_rounds:
        try:
            chosen, heel = model.balance(chosen, rng, rounds=balance_rounds)
        except WorkExhausted as exc:
            info['balance_work_exhausted'] = str(exc)
            heel = model.moments(chosen)
    else:
        heel = model.moments(chosen)
    return model.assignment(chosen), dict(info, columns=len(model.columns), heel_tm=heel,
        fixed_prefix_boxes=len(model.arrival),
        priced_restow_cost=sum(model.columns[k].cost for k in chosen))


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    """Cold bounded-work construction; budget_s is telemetry, never a stop rule."""
    E9FIX.install()
    started = monotonic()
    work = WorkBudget.from_context(context)
    original = context.get('problem') or Problem(instance)
    meta = context.setdefault('method_metadata', {})
    from_file = _load('c4_layover_ballast_targets', HERE / 'ballast_local.py')
    targets = from_file.ballast_targets(instance)
    meta.update(method='work-bounded arrival pages with inherited-ballast moment pricing',
                requested_budget_s=budget_s, ballast_pricing=targets, attempts=[], branch_work=[])
    candidates, seen = [], set()
    branches = [('preserve_prefixes', None, None)]+[(label,roots,None) for label,roots in restow_options(instance)]
    shifts = [('direct_shift', None, shift) for shift in direct_shift_options(instance, original)]
    # Tiny shifts must survive the branch cap: they may save one physical lift.
    if shifts:
        branches = branches[:1]+shifts+branches[1:]
    else:
        # Spend two branches on preserve/all before more selective restows.
        branches = branches[:work.limits['branches']]
    for branch_number, (branch_label, restow_box, shift) in enumerate(branches):
        try:
            work.take('branches')
            branches_left = min(len(branches)-branch_number, work.remaining('branches')+1)
            branch_work = work.for_branch(branch_label, branches_left)
            working, prefix, ex = prepare(instance, restow_box, shift)
            problem = Problem(working)
            model = pages.Model(problem, work=branch_work, ballast_targets=targets)
        except WorkExhausted:
            break
        except (ValueError, KeyError, TypeError) as exc:
            meta['attempts'].append(dict(branch=branch_label, restow=restow_box, error=str(exc)))
            continue
        # Each branch gets unconstrained pages and one blocked-cover design.
        queue = [(None, model)]+[(layout, None) for layout in layouts(model)[:1]]
        for layout_number, (layout, cached_model) in enumerate(queue):
            try:
                work.take('layouts')
                if cached_model is None:
                    cached_model = pages.Model(problem, layout=layout, layout_deck='no_restow',
                                              work=branch_work, ballast_targets=targets)
                assignment, row = _construct(problem, cached_model,
                    seed=seed+17*branch_number+layout_number, work=branch_work)
            except WorkExhausted as exc:
                meta['attempts'].append(dict(branch=branch_label, work_exhausted=str(exc)))
                break
            except RuntimeError as exc:
                meta['attempts'].append(dict(branch=branch_label, bounded_solver_stop=str(exc)))
                continue
            row.update(branch=branch_label, restow_prefix=restow_box, direct_shift=shift, layout=layout,
                prefix_buffer_boxes=len(working.load_list)-len(instance.load_list),
                prefix_restow_off_lifts=sum(m.kind=='restow_off' for m in ex.moves))
            if assignment is not None:
                actions = order_with_prefixes(problem, assignment, ex, prefix)
                scored = original.score(actions) if actions is not None else None
                row['replay_legal'] = scored is not None
                if scored is not None:
                    row['lifts'] = scored.objective
                    row['restow_now_lifts'] = sum(m.kind.startswith('restow') for m in scored.plan.moves)
                    key = tuple(actions)
                    if key not in seen:
                        seen.add(key)
                        candidates.append(scored)
            meta['attempts'].append(row)
        meta['branch_work'].append(dict(branch=branch_label, **branch_work.snapshot()))
    meta.update(propose_s=monotonic()-started, work=work.snapshot(),
        complete_replay_legal_stows=sum(bool(r.get('replay_legal')) for r in meta['attempts']))
    candidates.sort(key=lambda q:q.objective)
    return [q.actions for q in candidates[:5]]


def finish(instance, plan, *, seed: int, finalize_s: float, label: str, context: dict):
    finisher = _load('c4_layover_finishing', HERE / 'finishing.py')
    return finisher.finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label, context=context)
