"""One assignment-LP stow: motion in, assignment out, replay and score through the same tools as the page methods."""
from collections import Counter, defaultdict
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[2]
sys.path[:0] = [str(HERE), str(LAB/'c4/c2-layout'), str(LAB/'c1/fx-scout'), str(LAB/'c1/leader')]

from alns import Scored, Sequencer
from stowage.checker import lashing
from stowage.checker.full import departure_condition
from repair import repair_oog
from gap import Assignment, Settings
from chainlab import chain_ratios, condition_terms, motions


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


def exact_ratio(instance, plan):
    """Worst deck securing ratio of the replayed departure, and how many chains are over 1."""
    ship = departure_condition(instance, plan)
    terms = condition_terms(instance, ship)
    graph = lashing._load_graph(instance, ship, motions(terms))
    ratios = [max(chain_ratios(c)[0].values()) for c in lashing._chains(graph) if c[0][0].on_deck]
    return terms['gm'], max(ratios, default=0.), sum(r > 1 for r in ratios)
