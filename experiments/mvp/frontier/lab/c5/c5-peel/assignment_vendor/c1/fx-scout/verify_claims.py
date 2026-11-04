"""Check each sentence in GRAPH-MODEL.md against stowage.moves on stored plans, instead of trusting memory."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier')]

from stowage.conventions import cells_of_slot, parse_slot  # noqa: E402
from stowage.io import load_instance  # noqa: E402
from stowage.model.plan import MovePlan  # noqa: E402
from stowage.moves import apply_plan  # noqa: E402

bad = Counter()
n = 0
kinds = Counter()
reloc_of_load = 0
reloc_total = 0
example = None
for path in sorted((ROOT/'experiments/mvp/frontier/runs/val-0.9').glob('*.json')):
    if path.name == 'AUDIT.json':
        continue
    row = json.loads(path.read_text())
    if not row.get('passed'):
        continue
    i = load_instance(ROOT/row['input'])
    plan = MovePlan.model_validate(row['plan'])
    res = apply_plan(i, plan)
    boxes = {b.id: b for b in i.containers}
    cells = {c.id: c for c in i.vessel.cells}
    stacks = {s.id: s for s in i.vessel.stacks}
    load = set(i.load_list)
    edges, used = set(), set()
    for p in res.final_state.ship.placements:
        if parse_slot(p.slot).on_deck:
            continue
        cover = stacks[cells[cells_of_slot(p.slot)[0]].stack_id].hatch_cover_id
        used.add(cover)
        edges.add((boxes[p.container_id].pod, cover))
    c = res.count
    n += 1
    bad['hatch_now != 2*used'] += c.hatch_cover_lifts != 2*len(used)
    bad['hatch_later != 2*edges'] += sum(f.hatch_cover_lifts for f in c.future) != 2*len(edges)
    for m in plan.moves:
        kinds[m.kind] += 1
        if m.kind == 'relocate':
            reloc_total += 1
            reloc_of_load += m.container_id in load
    if row['id' if 'id' in row else 'instance_id'] == 'random.small.40100003.0.2':
        example = [(m.kind, m.container_id if hasattr(m, 'container_id') else '', getattr(m, 'src', ''), getattr(m, 'dst', '')) for m in plan.moves]
print('plans', n, dict(bad))
print('kinds', kinds)
print('relocated boxes that are themselves on the load list', reloc_of_load, 'of', reloc_total)
print(example[:14])
