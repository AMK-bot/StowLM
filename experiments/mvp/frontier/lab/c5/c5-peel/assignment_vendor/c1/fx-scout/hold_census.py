"""Stored plans: who is in the hold, and could fewer covers hold the boxes that must be there? Read-only."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier')]

from stowage.conventions import cells_of_slot, parse_slot  # noqa: E402
from stowage.io import load_instance  # noqa: E402
from stowage.model.plan import MovePlan  # noqa: E402
from stowage.moves import apply_plan  # noqa: E402
from stowage.rules.catalogue import rule_freeze_v1_enabled  # noqa: E402

print('rule_freeze_v1', rule_freeze_v1_enabled())
tot = Counter()
for path in sorted((ROOT/'experiments/mvp/frontier/runs/val-0.9').glob('random*.json')):
    row = json.loads(path.read_text())
    if not row.get('passed'):
        continue
    i = load_instance(ROOT/row['input'])
    plan = MovePlan.model_validate(row['plan'])
    res = apply_plan(i, plan)
    boxes = {b.id: b for b in i.containers}
    cells = {c.id: c for c in i.vessel.cells}
    stacks = {s.id: s for s in i.vessel.stacks}
    hold = [p for p in res.final_state.ship.placements if not parse_slot(p.slot).on_deck]
    H = sum(boxes[b].gross_mass_kg >= 20000 for b in i.load_list)
    Hhold = sum(boxes[p.container_id].gross_mass_kg >= 20000 for p in hold)
    covers = Counter(stacks[cells[cells_of_slot(p.slot)[0]].stack_id].hatch_cover_id for p in hold)
    pc = {(boxes[p.container_id].pod, stacks[cells[cells_of_slot(p.slot)[0]].stack_id].hatch_cover_id) for p in hold}
    hold_cap = Counter(s.hatch_cover_id for s in i.vessel.stacks for _ in s.cell_ids if not s.on_deck)
    print(i.id, 'n', len(i.load_list), 'w', row['weighted'], 'H', H, 'Hhold', Hhold, 'hold', len(hold), dict(covers), 'cap', dict(hold_cap), 'pairs', len(pc), 'reloc', res.count.relocations)
