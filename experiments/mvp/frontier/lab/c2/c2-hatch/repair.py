"""Replay-gated freeing-slot search and mask repair of optimistic witnesses.

Imports the unchanged Frontier sequencer, masks and move replay. This is a
candidate generator; only lab/score.py full checks establish feasibility.
"""
from __future__ import annotations

from collections import defaultdict
import importlib.util
import itertools
import json
from pathlib import Path
import random
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parent.parent
_FIX_INSTALLED = False


def _install():
    global _FIX_INSTALLED
    if _FIX_INSTALLED:
        return
    path = LAB / 'c1/leader/e9fix.py'
    spec = importlib.util.spec_from_file_location('hatch_e9fix', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.install()
    _FIX_INSTALLED = True


def _assignment(candidate):
    return dict(candidate) if isinstance(candidate, dict) else {a.box: a.slot for a in candidate}


def _ordered(problem, assignment):
    from alns import Sequencer
    if problem.mixes_hold_stack(assignment):
        return None
    return Sequencer(problem).order(assignment)


def _geometric(problem, assignment):
    from stowage.conventions import cells_of_slot
    occupied = set()
    T = problem.engine.T
    boxes, slots = {b: i for i, b in enumerate(T.box_ids)}, {s: i for i, s in enumerate(T.slot_ids)}
    for box, slot in assignment.items():
        if slot not in slots or not T.static[boxes[box], slots[slot]]:
            return False
        cells = set(cells_of_slot(slot))
        if occupied & cells:
            return False
        occupied |= cells
    return True


def neighbors(problem, assignment, rng):
    """Single-slot, same-length exchange and whole-column exchange moves."""
    from stowage.conventions import cells_of_slot, parse_slot, format_slot
    boxes = list(assignment)
    rng.shuffle(boxes)
    # Exchanges preserve support and can cross hold/deck or port boundaries.
    pairs = list(itertools.combinations(boxes, 2))
    rng.shuffle(pairs)
    for one, two in pairs:
        if problem.boxes[one].length_ft == problem.boxes[two].length_ft:
            out = dict(assignment)
            out[one], out[two] = out[two], out[one]
            yield out
    # An entire stack can change hatch without an unsupported intermediate.
    columns = defaultdict(list)
    for box, slot in assignment.items():
        pos = parse_slot(slot)
        columns[(pos.bay, pos.row, pos.on_deck)].append((box, pos.tier))
    keys = list(columns)
    rng.shuffle(keys)
    for key in keys:
        bay, row, deck = key
        for other in keys:
            if key >= other or other[2] != deck:
                continue
            out = dict(assignment)
            for box, tier in columns[key]:
                out[box] = format_slot(other[0], other[1], tier)
            for box, tier in columns[other]:
                out[box] = format_slot(bay, row, tier)
            yield out
    # Freeing slots is deliberately included: class-only exchange cannot
    # remove an occupied hatch cover or move cargo off an obstructed cover.
    occupied = {c for slot in assignment.values() for c in cells_of_slot(slot)}
    T = problem.engine.T
    for box in boxes:
        bi = T.box_ids.index(box)
        options = [s for si, s in enumerate(T.slot_ids) if T.static[bi, si]
                   and not occupied.intersection(cells_of_slot(s))]
        rng.shuffle(options)
        for slot in options:
            out = dict(assignment)
            out[box] = slot
            yield out


def freeing_search(problem, starts, *, deadline, seed=0, keep=12):
    """Bounded descent, evaluating replay before the expensive legal mask."""
    from alns import _mask_ok
    rng = random.Random(seed)
    stats = dict(evaluated=0, geometry_rejected=0, replay_legal=0,
                 mask_rejected=0, mask_complete=0, trace=[])
    retained, seen = {}, set()
    queue = []
    for start in starts:
        actions = _ordered(problem, _assignment(start)) if isinstance(start, dict) else tuple(start)
        scored = problem.score(actions) if actions else None
        if scored:
            queue.append(scored)
            retained[tuple(sorted(_assignment(actions).items()))] = scored
    queue.sort(key=lambda s: s.objective)
    # Limited neutral moves permit crossing a mask-legal plateau.
    neutral_left = 3
    while queue and monotonic() < deadline:
        current = queue.pop(0)
        assignment = _assignment(current.actions)
        for proposal in neighbors(problem, assignment, rng):
            if monotonic() >= deadline:
                break
            identity = tuple(sorted(proposal.items()))
            if identity in seen:
                continue
            seen.add(identity)
            if not _geometric(problem, proposal):
                stats['geometry_rejected'] += 1
                continue
            stats['evaluated'] += 1
            actions = _ordered(problem, proposal)
            scored = problem.score(actions) if actions else None
            if scored is None:
                continue
            stats['replay_legal'] += 1
            best = min(s.objective for s in retained.values())
            improving = scored.objective < best
            neutral = scored.objective <= current.objective and neutral_left > 0
            if not improving and not neutral:
                continue
            if not _mask_ok(problem, actions):
                stats['mask_rejected'] += 1
                continue
            stats['mask_complete'] += 1
            retained[identity] = scored
            if improving:
                stats['trace'].append([scored.objective, stats['evaluated']])
                queue.insert(0, scored)
                break
            neutral_left -= 1
            queue.append(scored)
    out = sorted(retained.values(), key=lambda s: s.objective)[:keep]
    stats['objectives'] = [s.objective for s in out]
    freeing_search.last_stats = stats
    return [s.actions for s in out]


def repair_witness(problem, assignment, *, deadline, seed=0):
    """Try witness verbatim, then mask-legal greedy repair with witness bias.

    Greedy completions are heuristic. No failure establishes infeasibility.
    Engine push/pop is restored on every exit.
    """
    from alns import Rebuilder, _mask_ok
    from stowage.actions.space import Place
    from stowage.actions.expander import Expander
    from stowage.conventions import cells_of_slot, parse_slot
    rng = random.Random(seed)
    out = []
    direct = _ordered(problem, assignment)
    if direct and problem.score(direct) and _mask_ok(problem, direct):
        out.append(direct)
    engine = problem.engine
    rounds = 0
    while monotonic() < deadline and rounds < 12:
        pushed, actions, occupied = 0, [], set()
        ex = Expander(problem.instance)
        try:
            while len(actions) < len(problem.instance.load_list) and monotonic() < deadline:
                choices = []
                for box in engine.legal_box_ids():
                    for slot in engine.legal_slots_for_box(box):
                        footprint = set(cells_of_slot(slot))
                        if occupied & footprint:
                            continue
                        cost = Rebuilder(problem, rng)._estimate(ex, [(a.box, a.slot) for a in actions], box, slot, 0.)
                        # Prefer exact witness slots, then its hold/deck and cover.
                        target = parse_slot(assignment[box])
                        candidate = parse_slot(slot)
                        cost += 0. if slot == assignment[box] else (1.5 if candidate.on_deck == target.on_deck else 3.)
                        cost += rng.uniform(0., .3 * rounds)
                        choices.append((cost, box, slot))
                if not choices:
                    break
                _, box, slot = min(choices)
                action = Place(box, slot)
                engine.push(action)
                pushed += 1
                ex.step(action)
                actions.append(action)
                occupied.update(cells_of_slot(slot))
            if len(actions) == len(problem.instance.load_list) and engine.is_complete():
                scored = problem.score(actions)
                if scored:
                    out.append(tuple(actions))
        finally:
            for _ in range(pushed):
                engine.pop()
        rounds += 1
    return out


def propose(instance, *, seed, budget_s, context):
    _install()
    problem = context['problem']
    deadline = monotonic() + max(0., budget_s)
    starts = list(context.get('starts', []))
    baseline = context.get('baseline', {})
    if baseline.get('ordered_actions'):
        from stowage.actions.space import Place
        starts.append(tuple(Place(a['box'], a['slot']) for a in baseline['ordered_actions']))
    elif context.get('stored'):
        starts.append(context['stored'])
    witnesses = context.get('witnesses', [])
    for index, witness in enumerate(witnesses):
        allocation = min(deadline, monotonic() + max(.01, budget_s * .35 / max(1, len(witnesses))))
        starts.extend(repair_witness(problem, witness, deadline=allocation, seed=seed + index))
    out = freeing_search(problem, starts, deadline=deadline, seed=seed)
    context.setdefault('method_metadata', {})['freeing_search'] = freeing_search.last_stats
    return out


def main():
    import argparse
    from catalog import load_baseline, load_set
    from stowage.io import load_instance
    from stowage.actions.space import Place
    from alns import Problem
    parser = argparse.ArgumentParser()
    parser.add_argument('--set', default='quick_small')
    parser.add_argument('--id')
    parser.add_argument('--budget-s', type=float, default=90)
    parser.add_argument('--chain', action='store_true',
                        help='Use conservative CP hatch/restow construction instead of local repair')
    parser.add_argument('--limit', type=int, default=15,
                        help='Maximum CP optima to enumerate per map with --chain')
    parser.add_argument('--out', default=str(HERE / 'results/repair-candidates.json'))
    args = parser.parse_args()
    destination = Path(args.out).resolve()
    if not destination.is_relative_to(HERE / 'results') or not destination.name.startswith('repair'):
        raise ValueError('repair results only')
    baseline = load_baseline()
    _install()
    rows = []
    for path in load_set(args.set):
        instance = load_instance(path)
        if args.id and instance.id != args.id:
            continue
        row = baseline.get(instance.id, {})
        starts = []
        saved = LAB / 'c1/fx-vlsn/results/candidate_check' / f'{instance.id}.json'
        if saved.exists():
            data = json.loads(saved.read_text())
            if data.get('passed'):
                starts.append(tuple(Place(a['box'], a['slot']) for a in data['actions']))
        witnesses = []
        saved = LAB / 'c1/fx-exact/results/bounds-small-final' / f'{instance.id}.json'
        if saved.exists():
            data = json.loads(saved.read_text())
            if data['result'].get('relaxation_assignment'):
                witnesses.append(data['result']['relaxation_assignment'])
        context = dict(problem=Problem(instance), baseline=row, starts=starts, witnesses=witnesses)
        began = monotonic()
        if args.chain:
            candidates = chain_candidates(context['problem'], deadline=began+args.budget_s,
                                          seed=0, limit=args.limit)
            context['method_metadata'] = chain_candidates.last_stats
        else:
            candidates = propose(instance, seed=0, budget_s=args.budget_s, context=context)
        output = dict(instance_id=instance.id, input=path, seconds=monotonic()-began,
                      metadata=context.get('method_metadata', {}),
                      candidates=[[dict(box=a.box, slot=a.slot) for a in actions] for actions in candidates])
        rows.append(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(rows, indent=2)+'\n')
        # Root's portfolio reads a per-map frozen candidate file. Preserve its
        # previous candidates when running additional construction experiments.
        if args.chain:
            folder = HERE / 'results/repair_candidates'
            folder.mkdir(parents=True, exist_ok=True)
            frozen = folder / f'{instance.id}.json'
            old = json.loads(frozen.read_text()) if frozen.exists() else {}
            ranked = list(old.get('candidates', []))
            for actions in candidates:
                scored = context['problem'].score(actions)
                if scored:
                    ranked.append(dict(actions=[dict(box=a.box, slot=a.slot) for a in actions],
                                       replay_lifts=scored.objective))
            unique = {}
            for candidate in ranked:
                key = tuple((a['box'], a['slot']) for a in candidate['actions'])
                unique.setdefault(key, candidate)
            ranked = sorted(unique.values(), key=lambda c: c['replay_lifts'])
            frozen.write_text(json.dumps(dict(instance_id=instance.id, input=path,
                                             metadata=context['method_metadata'],
                                             candidates=ranked), indent=2)+'\n')
        print(instance.id, output['seconds'], context['method_metadata'], flush=True)


def chain_candidates(problem, *, deadline, seed=0, limit=8):
    """Conservative CP packing with hatch/restow objective and real replay gate.

    The surrogate is designed for the measured two-discharge-port unit-weight
    maps. Its value is only a ranking heuristic; actual replay supplies costs.
    Reuses c1 ChainPacking unchanged. Its sufficient E6 blocking is stronger
    than the operational rule, so neither optimum nor infeasibility is a bound.
    """
    import sys
    from ortools.sat.python import cp_model
    from sequence import order_legal
    folder = str(LAB / 'c1/fx-chains')
    if folder not in sys.path:
        sys.path.insert(0, folder)
    from chain_model import ChainPacking, validate
    packing = ChainPacking(problem, bucket_kg=0, port_chains=True, full_hold=False)
    model, T = packing.model, packing.T
    hold_cover, hold_pod = defaultdict(list), defaultdict(list)
    deck = []
    for (g, s), var in packing.x.items():
        pod = problem.pod(packing.groups[g][0])
        covers = {T.stack_cover[int(j)] for j in T.slot_stacks[s] if j >= 0}
        if T.slot_deck[s]:
            deck.append((var, covers, pod))
        else:
            for cover in covers:
                hold_cover[cover].append(var)
                hold_pod[cover, pod].append(var)
    terms, used_pod = [], {}
    for cover, variables in hold_cover.items():
        used = model.new_bool_var(f'objective_cover_{cover}')
        model.add_max_equality(used, variables)
        terms.append(2 * used)
    for (cover, pod), variables in hold_pod.items():
        used = model.new_bool_var(f'objective_cover_{cover}_{pod}')
        model.add_max_equality(used, variables)
        used_pod[cover, pod] = used
        terms.append(2 * used)
    for index, (var, covers, pod) in enumerate(deck):
        earlier = [used for (cover, hpod), used in used_pod.items() if cover in covers and hpod < pod]
        if not earlier:
            continue
        blocked = model.new_bool_var(f'objective_blocked_{index}')
        model.add_max_equality(blocked, earlier)
        restow = model.new_bool_var(f'objective_restow_{index}')
        model.add(restow <= var)
        model.add(restow <= blocked)
        model.add(restow >= var + blocked - 1)
        terms.append(2 * restow)
    model.minimize(sum(terms))
    out, rounds = [], []
    while monotonic() < deadline and len(rounds) < limit:
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = seed + len(rounds)
        solver.parameters.max_time_in_seconds = max(.01, min(30., deadline-monotonic()))
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            rounds.append({'status': solver.status_name(status)})
            break
        assignment = packing.assignment(solver)
        valid, metadata = validate(problem, assignment)
        metadata.update(status=solver.status_name(status), surrogate=solver.objective_value)
        rounds.append(metadata)
        if valid:
            actions, stuck = order_legal(problem, assignment)
            if actions:
                out.append(tuple(actions))
        packing.forbid(assignment)
    chain_candidates.last_stats = {'scope': 'conservative candidate model, no original-map certificate', 'rounds': rounds}
    return out


if __name__ == '__main__':
    main()
