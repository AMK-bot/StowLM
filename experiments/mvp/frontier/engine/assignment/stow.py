# Copied from experiments/mvp/frontier/lab/c6/c5-securing/stow.py; engine-local imports and guarded integration.
"""One assignment-LP stow: motion in, assignment out, replay and score through the same tools as the page methods."""
# Copied from lab/c5/c5-securing/stow.py, then changed for cycle 6.
from collections import Counter, defaultdict
from dataclasses import replace
from time import monotonic

from alns import Scored, Sequencer
from stowage.checker import lashing
from stowage.checker.full import departure_condition
from .repair import repair_oog
from .gap import Assignment, Settings
from .chainlab import chain_ratios, condition_terms, motions


def block_layout(model):
    """One port per hatch cover, sized to demand. Copied from c1/fx-scout/method.py."""
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


def reference(hull, gm, draught=None, cb=.65):
    return lashing._motions(hull, draught if draught is not None else hull.scantling_draught_m*.85, gm, cb)


def columns(model, slots_by_box, solver):
    """Boxes per page, bottom-up."""
    out = defaultdict(dict)
    for b, q in slots_by_box.items():
        j, k = solver.slot_page[q][0]
        out[j][k] = b
    return {j: [tiers[k] for k in sorted(tiers)] for j, tiers in out.items()}


def resort(model, boxes):
    """Heavy low: securing rows are worst for mass high, and E1/E2 agree. Keeps a stack closer on top."""
    T = model.T
    return sorted(boxes, key=lambda b: (bool(T.closes_stack[b]), not model.heavy[b], -int(T.rank[b]), -model.pod[b], -float(T.mass[b]), b))


def to_assignment(model, placed, solver):
    """{box id: slot id} with stacks resorted, and any column that breaks `legal` listed for the record."""
    T = model.T
    out, illegal = {}, []
    for j, boxes in columns(model, placed, solver).items():
        page = model.pages[j]
        ordered = resort(model, boxes)
        if solver.stack_ratio(page, boxes) < solver.stack_ratio(page, ordered):
            ordered = boxes
        if not model.legal(page, tuple(ordered), any_port=True):
            if model.legal(page, tuple(boxes), any_port=True):
                ordered = boxes
            else:
                illegal.append((j, [T.box_ids[b] for b in ordered]))
        for k, b in enumerate(ordered):
            out[T.box_ids[b]] = T.slot_ids[page.slots[k]]
    return out, illegal


def rolling_dive(model, motions, settings, layout, *, deadline, seed=0, log=None):
    """For the largest maps: dive one step on a sampled LP, resample the free slots, repeat, then finish on all pairs.

    A fixed sample starves late boxes of slots, because the slots it kept fill up first.
    """
    fixed = {}
    snapshots = [{}]
    round_seed = seed
    while monotonic() < deadline-40. and len(model.boxes)-len(fixed) > 70:
        settings = replace(settings, fixed=dict(fixed), seed=round_seed)
        solver = Assignment(model, motions, settings, layout=layout)
        solver.dive(deadline=monotonic()+60., steps=1, batch=.3, log=log)
        round_seed += 1
        grown = solver.partial
        if len(grown) <= len(fixed):
            break
        fixed = grown
        snapshots.append(dict(fixed))
    placed = None
    for start in snapshots[:-3:-1]:
        settings = replace(settings, fixed=start, pairs_per_box=0, high_tier_rank=(), seed=round_seed)
        solver = Assignment(model, motions, settings, layout=layout)
        placed = solver.dive(deadline=max(deadline, monotonic()+40.), tail_left=30, tail_s=15., seed=seed, log=log)
        if placed is not None:
            break
    return solver, placed


def exact_ratio(instance, plan):
    """Worst deck securing ratio of the replayed departure, and how many chains are over 1."""
    ship = departure_condition(instance, plan)
    terms = condition_terms(instance, ship)
    graph = lashing._load_graph(instance, ship, motions(terms))
    ratios = [max(chain_ratios(c)[0].values()) for c in lashing._chains(graph) if c[0][0].on_deck]
    return terms['gm'], max(ratios, default=0.), sum(r > 1 for r in ratios)
