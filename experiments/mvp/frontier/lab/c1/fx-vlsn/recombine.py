"""Budgeted exact enumeration of a parent-component recombination family.

Only occupancy is decomposed. Every child is sequenced and scored globally;
ship support, hatches, yard relocation and physics are NOT additive factors.
Full finish/check admission remains the caller's responsibility.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from itertools import combinations
import random
from time import monotonic

from alns import Sequencer, _mask_ok
from stowage.conventions import cells_of_slot


def _assignment(parent):
    """Accept an assignment, action sequence, or alns.Scored parent."""
    if isinstance(parent, Mapping):
        return dict(parent), None
    actions = tuple(getattr(parent, 'actions', parent))
    assignment = {a.box: a.slot for a in actions}
    if len(assignment) != len(actions):
        raise ValueError('recombination requires one Place per box')
    return assignment, actions


def _partition(boxes, resources):
    """Connected components of boxes sharing any resource, using union-find."""
    parent = {box: box for box in boxes}

    def find(box):
        while parent[box] != box:
            parent[box] = parent[parent[box]]
            box = parent[box]
        return box

    owner = {}
    for box in boxes:
        for resource in resources(box):
            if resource in owner:
                parent[find(box)] = find(owner[resource])
            else:
                owner[resource] = box
    components = defaultdict(list)
    for box in boxes:
        components[find(box)].append(box)
    return sorted((tuple(sorted(component)) for component in components.values()),
                  key=lambda component: (-len(component), component))


def components(problem, left, right, *, conservative=False):
    """Return box-choice components, with both cells of every 40ft slot.

    The conservative diagnostic also links stacks, hatch covers and original
    yard stacks, including unchanged boxes as possible dependency bridges.
    Neither partition proves global objective or physical independence.
    """
    differing = {box for box in left if left[box] != right[box]}
    boxes = sorted(left if conservative else differing)

    def resources(box):
        found = set()
        for slot in (left[box], right[box]):
            for cell in cells_of_slot(slot):
                found.add(('cell', cell))
                if conservative:
                    stack = problem.cells[cell].stack_id
                    found.add(('stack', stack))
                    cover = problem.stacks[stack].hatch_cover_id
                    if cover is not None:
                        found.add(('cover', cover))
        if conservative and box in problem.yard_stack:
            found.add(('yard', problem.yard_stack[box]))
        return found

    return [tuple(box for box in component if box in differing)
            for component in _partition(boxes, resources)
            if any(box in differing for box in component)]


def _valid_assignment(problem, assignment):
    if set(assignment) != set(problem.instance.load_list):
        return False
    occupied = set()
    for box, slot in assignment.items():
        footprint = cells_of_slot(slot)
        if (box not in problem.boxes or any(cell not in problem.cells for cell in footprint)
                or occupied.intersection(footprint)):
            return False
        occupied.update(footprint)
    return True


def _masks(k, rng):
    """Parents, single components and complements first, then remaining masks."""
    all_bits = (1 << k)-1
    yielded = set()
    preferred = [0, all_bits]
    order = list(range(k))
    rng.shuffle(order)
    preferred.extend(1 << i for i in order)
    preferred.extend(all_bits ^ (1 << i) for i in order)
    for mask in preferred:
        if mask not in yielded:
            yielded.add(mask)
            yield mask
    for mask in range(1 << k):
        if mask not in yielded:
            yield mask


def recombine(problem, parents, *, deadline, seed=0):
    """Return up to 16 cheapest replay/mask-legal complete action tuples.

    ``deadline`` is absolute time.monotonic(). Parents may be mappings,
    ordered Place tuples or alns.Scored instances. Two or more parents are
    supported; each pair is considered once. At each pair, choose each
    occupancy component wholly from either parent, then enumerate all 2^k
    masks if time permits. There is no independent-cost approximation.

    ``recombine.last_stats`` records coverage; ``complete`` certifies only
    exhaustive enumeration of that component-switch family under the fixed
    alns.Sequencer order and replay/mask screen, never full checker validity
    or an optimum over all inherited per-box choices or all load orders.
    Deadline checks occur between expensive calls; one call can overrun.
    """
    started = monotonic()
    stats = dict(pairs=0, pair_stats=[], masks=0, scored=0, mask_legal=0,
                 duplicate_assignments=0, invalid_parents=0, complete=True,
                 objective='problem.score: weighted_nonproductive',
                 certificate_scope='component switches; canonical load order; replay and mask only')
    recombine.last_stats = stats
    rng = random.Random(seed)
    sequencer = Sequencer(problem)
    seen = set()
    best = {}
    normalized = []

    def retain(assignment, actions):
        if actions is None or monotonic() >= deadline:
            return
        scored = problem.score(actions)
        stats['scored'] += 1
        if scored is None or monotonic() >= deadline:
            return
        if not _mask_ok(problem, scored.actions):
            return
        stats['mask_legal'] += 1
        key = tuple(sorted(assignment.items()))
        old = best.get(key)
        if old is None or scored.objective < old.objective:
            best[key] = scored
        if len(best) > 16:
            worst = max(best, key=lambda item: best[item].objective)
            del best[worst]

    # Stage A is the supported lab scope. Onboard/discharge problems need
    # action models that include their existing cargo and discharge actions.
    if problem.instance.onboard.placements or problem.instance.discharge_list:
        stats.update(complete=False, unsupported=True, elapsed_s=monotonic()-started)
        return []
    for parent in parents:
        if monotonic() >= deadline:
            stats['complete'] = False
            break
        try:
            assignment, actions = _assignment(parent)
            if not _valid_assignment(problem, assignment):
                stats['invalid_parents'] += 1
                continue
        except (AttributeError, ValueError, TypeError, KeyError):
            stats['invalid_parents'] += 1
            continue
        normalized.append(assignment)
        # Preserve a good inherited load order as a bonus. Canonical orders
        # are still evaluated in the masks, so exact-family coverage is clear.
        if actions is not None:
            retain(assignment, actions)

    pairs = list(combinations(normalized, 2))
    for left, right in pairs:
        if monotonic() >= deadline:
            stats['complete'] = False
            break
        groups = components(problem, left, right)
        conservative = components(problem, left, right, conservative=True)
        k = len(groups)
        pair_stats = dict(components=k, conservative_components=len(conservative),
                          component_sizes=[len(group) for group in groups],
                          family_size=1 << k, masks=0, complete=False)
        stats['pairs'] += 1
        stats['pair_stats'].append(pair_stats)
        for mask in _masks(k, rng):
            if monotonic() >= deadline:
                break
            child = dict(left)
            for i, group in enumerate(groups):
                if mask & (1 << i):
                    child.update((box, right[box]) for box in group)
            stats['masks'] += 1
            pair_stats['masks'] += 1
            key = tuple(sorted(child.items()))
            if key in seen:
                stats['duplicate_assignments'] += 1
                continue
            if problem.mixes_hold_stack(child):
                seen.add(key)
                continue
            ordered = sequencer.order(child)
            retain(child, ordered)
            # No half-evaluated child may support an exhaustiveness claim.
            if monotonic() >= deadline:
                break
            seen.add(key)
        pair_stats['complete'] = (pair_stats['masks'] == pair_stats['family_size']
                                  and monotonic() < deadline)
        stats['complete'] &= pair_stats['complete']

    stats['complete'] &= len(pairs) == stats['pairs'] and len(normalized) >= 2
    stats['elapsed_s'] = monotonic()-started
    stats['best_objectives'] = sorted(scored.objective for scored in best.values())
    return [scored.actions for scored in sorted(best.values(), key=lambda item:item.objective)]


recombine.last_stats = {}
