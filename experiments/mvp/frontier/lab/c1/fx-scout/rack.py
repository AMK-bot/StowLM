"""Which deck stacks fail racking in a stored stow, with their pattern."""
from pathlib import Path
from collections import Counter, defaultdict
import json, sys
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(Path(__file__).parent)]
from alns import Problem, Sequencer
from stowage.checker.lashing import check_lashing
from stowage.io import load_instance
from stowage.conventions import cells_of_slot
from stowage.moves import apply_plan
i = load_instance(ROOT/sys.argv[1]); p = Problem(i)
a = json.loads((Path(__file__).parent/sys.argv[2]).read_text())
s = p.score(Sequencer(p).order(a))
ship = apply_plan(i, s.plan).final_state.ship
r = check_lashing(i, ship, call_index=i.call_index)
stacks = defaultdict(list)
for b, sl in a.items():
    for c in cells_of_slot(sl):
        stacks[p.cells[c].stack_id].append((sl, p.boxes[b].length_ft, round(p.boxes[b].gross_mass_kg/1000, 1)))
print(len(r.violations))
bad = Counter(); worst = {}
for v in r.violations:
    for sl in v.slots:
        k = sl[:5]; bad[k] += 1
        if v.measured and v.measured > worst.get(k, 0): worst[k] = v.measured
    if not v.slots: bad['none'] += 1
print(bad.most_common(30)); print(v.message, v.slots)
for k, val in sorted(worst.items(), key=lambda kv: -kv[1])[:14]:
    print(k, round(val, 3), [(x[0], x[1], x[2]) for j in stacks for x in stacks[j] if x[0][:5] == k][:8])
