"""Plain graph primitives and conditional D15 bounds; no solver/checker patches."""
from collections import defaultdict


def circle_mwis(chords, weights=None):
    """Maximum weight noncrossing chord set, by endpoint-interval DP.

    chords maps an arbitrary vertex to two distinct endpoints. All endpoints
    must be distinct. This is the elementary weighted circle-graph independent
    set recurrence: omit the last endpoint's chord, or split at that chord.
    Capacity/physics are deliberately absent; this is a pricing relaxation.
    """
    if not chords:
        return 0., ()
    ordered = sorted((p, v) for v, ends in chords.items() for p in ends)
    if len({p for p, _ in ordered}) != len(ordered):
        raise ValueError('circle endpoints must be distinct')
    location = defaultdict(list)
    for i, (_, v) in enumerate(ordered):
        location[v].append(i)
    mate = {}
    for v, (i, j) in location.items():
        mate[i], mate[j] = j, i
    n = len(ordered)
    value = [[0.] * n for _ in range(n)]
    take = {}
    def get(i, j):
        return value[i][j] if i <= j else 0.
    for width in range(1, n + 1):
        for i in range(n - width + 1):
            j = i + width - 1
            value[i][j] = get(i, j - 1)
            k = mate[j]
            if i <= k < j:
                v = ordered[j][1]
                candidate = get(i, k - 1) + get(k + 1, j - 1) + (weights.get(v, 0.) if weights is not None else 1.)
                if candidate > value[i][j] + 1e-12:
                    value[i][j] = candidate
                    take[i, j] = k
    selected, pending = [], [(0, n - 1)]
    while pending:
        i, j = pending.pop()
        if i > j:
            continue
        if (i, j) in take:
            k = take[i, j]
            selected.append(ordered[j][1])
            pending.extend(((i, k - 1), (k + 1, j - 1)))
        else:
            pending.append((i, j - 1))
    return value[0][-1], tuple(selected)


def load_chords(order, pod):
    """Fixed load order, then ordered discharge events; reverse same-port ties."""
    n = len(order)
    exits = sorted(range(n), key=lambda i: (pod[order[i]], -i))
    right = {order[i]: n + j for j, i in enumerate(exits)}
    return {b: (i, right[b]) for i, b in enumerate(order)}


def pattern_events(boxes, pod):
    """(call, blocker) pairs forced by a fixed bottom-up one-page sequence.

    A later box is charged once at every earlier distinct discharge call below
    it, regardless of how many targets share that call. This is exact in D15's
    fixed-coordinate counting convention for an isolated pure-length page.
    """
    below, events = set(), set()
    for b in boxes:
        events.update((p, b) for p in below if p < pod[b])
        below.add(pod[b])
    return events


def closure_counts(pod, stack_boxes, hold_cover, deck_cover):
    """Exact top/hatch closure for a fixed departure assignment, by future call.

    stack_boxes is {physical_stack: bottom-up box ids}; spanning boxes occur in
    both stacks. hold_cover and deck_cover map physical stacks to covers.
    Rebuilding after each discharge removes spurious bridges through departed
    long boxes. Crane crossing dependencies can add events, so this is a lower
    bound on the full D15 projection, not on arbitrary executable future moves.
    """
    active = set(pod)
    out = []
    for call in sorted(set(pod.values())):
        targets = {b for b in active if pod[b] == call}
        above = defaultdict(set)
        current = {}
        for s, boxes in stack_boxes.items():
            current[s] = [b for b in boxes if b in active]
            for lower, upper in zip(current[s], current[s][1:]):
                above[lower].add(upper)
        opened = {c for s, c in hold_cover.items() if any(b in targets for b in current.get(s, ()))}
        required = set(targets)
        for s, cover in deck_cover.items():
            if cover in opened:
                required.update(current.get(s, ()))
        pending = list(required)
        while pending:
            for b in above[pending.pop()]:
                if b not in required:
                    required.add(b)
                    pending.append(b)
        out.append(dict(call=call, targets=sorted(targets), blockers=sorted(required-targets),
                        covers=sorted(opened), restowed_boxes=len(required-targets)))
        active -= targets
    return out


def assignment_bound(instance, assignment):
    """Conditional, exact top/hatch count for this assignment; see closure_counts."""
    from stowage.conventions import cells_of_slot, parse_slot
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    boxes = {b.id: b for b in instance.containers}
    first = {}
    for i in range(instance.call_index + 1, len(instance.rotation.calls)):
        first.setdefault(instance.rotation.calls[i].locode, i)
    # Cargo with no future destination stays aboard forever in this relaxation.
    infinity = len(instance.rotation.calls)
    pod = {b: first.get(boxes[b].pod, infinity) for b in assignment}
    on_stack = defaultdict(list)
    for b, slot in assignment.items():
        for c in cells_of_slot(slot):
            on_stack[cells[c].stack_id].append((parse_slot(slot).tier, b))
    ordered = {s: [b for _, b in sorted(items)] for s, items in on_stack.items()}
    hold = {s: stacks[s].hatch_cover_id for s in ordered if not stacks[s].on_deck}
    deck = {s: stacks[s].hatch_cover_id for s in ordered if stacks[s].on_deck}
    rows = [r for r in closure_counts(pod, ordered, hold, deck) if r['call'] < infinity]
    n = sum(r['restowed_boxes'] for r in rows)
    return dict(scope='conditional fixed-coordinate top/hatch D15 lower bound; crane crossings relaxed',
                restowed_boxes=n, restow_lifts=n*instance.objective.future_restow_lifts_per_box,
                hatch_later=2*sum(len(r['covers']) for r in rows), calls=rows)
