"""A lower bound from the port-cover graph.

The hatch part of the objective is a graph count. Draw one vertex per discharge port, one per hatch cover, and an edge
(p, c) when some hold box under cover c goes to port p. On an empty ship the plan pays 2 lifts per used cover now and 2
per edge later: hatch = 2*|used covers| + 2*|edges| (engine.py, project_future_moves step 4). Choosing edges under
capacities is a fixed-charge transportation problem, the small relative of network design.

Bound: yard strangers above a load box must each be lifted (one relocation at least), plus the cheapest port-cover
edge set that E6 leaves possible. E6 relaxation used: if cover u is unused, every non-DG heavy box that fits u's empty
hold must sit in the hold of a used cover, since u's cells stay empty and compatible. Only cell area is counted for the
used covers, so this is a relaxation, never an overestimate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations, product
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier')]

from stowage.checker.rules_weight import compatible_empty_hold_slots  # noqa: E402
from stowage.conventions import cells_of_slot, parse_slot  # noqa: E402
from stowage.io import load_instance  # noqa: E402
from stowage.model.ship import ShipState  # noqa: E402


def strangers_above(instance):
    load = set(instance.load_list)
    total = 0
    for stack in instance.yard.stacks:
        where = [k for k, b in enumerate(stack.boxes) if b in load]
        if where:
            total += sum(1 for b in stack.boxes[where[0]+1:] if b not in load)
    return total


def hold_cells(instance):
    out = defaultdict(int)
    for stack in instance.vessel.stacks:
        if not stack.on_deck and stack.hatch_cover_id is not None:
            out[stack.hatch_cover_id] += len(stack.cell_ids)
    return dict(out)


def forced_heavy(instance, threshold=20000.):
    """Non-DG heavy boxes with, per cover, whether an empty-ship hold slot of that cover accepts them."""
    stacks = {s.id: s for s in instance.vessel.stacks}
    cells = {c.id: c for c in instance.vessel.cells}
    out = {}
    for box in instance.containers:
        if box.id not in set(instance.load_list) or box.gross_mass_kg < threshold or box.dg is not None:
            continue
        slots = compatible_empty_hold_slots(instance, instance.onboard, box)
        out[box.id] = {stacks[cells[cells_of_slot(s)[0]].stack_id].hatch_cover_id for s in slots}
    return out


def cheapest_hatch(instance):
    """Enumerate used-cover sets; the bound is the cheapest set E6's necessary condition allows."""
    boxes = {b.id: b for b in instance.containers}
    cap = hold_cells(instance)
    covers = sorted(cap)
    heavy = forced_heavy(instance)
    best = (10**9, None)
    for k in range(len(covers)+1):
        for used in combinations(covers, k):
            unused = set(covers)-set(used)
            must = [b for b, fits in heavy.items() if fits & unused]
            need = sum(1 if boxes[b].length_ft == 20 else 2 for b in must)
            if need > sum(cap[c] for c in used):
                continue
            # Fit by port: edges needed so that each port's forced boxes reach used covers.
            ports = sorted({boxes[b].pod for b in must})
            area = {p: sum(1 if boxes[b].length_ft == 20 else 2 for b in must if boxes[b].pod == p) for p in ports}
            cheapest = None
            for mask in product(*[[s for r in range(1, k+1) for s in combinations(used, r)] for _ in ports]) if ports else [()]:
                if any(sum(cap[c] for c in chosen) < area[p] for p, chosen in zip(ports, mask)):
                    continue
                edges = sum(len(chosen) for chosen in mask)
                # Every used cover holds a box of some port by definition, so it has at least one edge.
                touched = set(c for chosen in mask for c in chosen)
                edges += len(set(used)-touched)
                if cheapest is None or edges < cheapest:
                    cheapest = edges
            if cheapest is None:
                continue
            value = 2*k+2*cheapest
            if value < best[0]:
                best = (value, dict(used=list(used), edges=cheapest, forced=len(must)))
    return best


if __name__ == '__main__':
    base = ROOT/'experiments/mvp/frontier/runs/val-0.9'
    rows = json.loads((Path(__file__).parent/'breakdown.json').read_text())
    gaps = Counter()
    out = []
    for r in rows:
        stored = json.loads((base/f"{r['id']}.json").read_text())
        i = load_instance(ROOT/stored['input'])
        s = strangers_above(i)
        h, info = cheapest_hatch(i)
        row = dict(id=r['id'], n=r['n'], strangers=s, hatch_bound=h, bound=s+h, stored=r.get('check'), info=info,
                   hatch_stored=(r.get('hatch_now', 0)+r.get('hatch_later', 0)) if r.get('passed') else None, reloc=r.get('reloc'), restow_later=r.get('restow_later'))
        row['gap'] = row['stored']-row['bound'] if row['stored'] is not None else None
        out.append(row)
        gaps[row['gap']] += 1
    ok = [x for x in out if x['gap'] is not None]
    print('maps with stored pass', len(ok), 'sum stored', sum(x['stored'] for x in ok), 'sum bound', sum(x['bound'] for x in ok))
    print('mean stored', sum(x['stored'] for x in ok)/len(ok), 'mean bound', sum(x['bound'] for x in ok)/len(ok))
    print('gap histogram', sorted(gaps.items(), key=str))
    print('hatch bound', Counter(x['hatch_bound'] for x in ok), 'hatch stored', Counter(x['hatch_stored'] for x in ok))
    print('hatch gap', Counter(x['hatch_stored']-x['hatch_bound'] for x in ok))
    print('reloc gap', Counter(x['reloc']-x['strangers'] for x in ok))
    (Path(__file__).parent/'hatch_bound.json').write_text(json.dumps(out, indent=1))
