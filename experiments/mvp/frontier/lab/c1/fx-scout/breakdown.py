"""Every stored 0.9.0 validation plan: replay it, split its lifts, compare with simple structural lower bounds."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier')]

from stowage.io import load_instance  # noqa: E402
from stowage.model.plan import MovePlan  # noqa: E402
from stowage.moves import apply_plan  # noqa: E402

rows = []
for path in sorted((ROOT/'experiments/mvp/frontier/runs/val-0.9').glob('random*.json')) + sorted((ROOT/'experiments/mvp/frontier/runs/val-0.9').glob('sim*.json')):
    row = json.loads(path.read_text())
    i = load_instance(ROOT/row['input'])
    load = set(i.load_list)
    strangers, carried = 0, 0
    for s in i.yard.stacks:
        ks = [k for k, b in enumerate(s.boxes) if b in load]
        if ks:
            strangers += sum(1 for b in s.boxes[ks[0]+1:] if b not in load)
            carried = max(carried, len(ks))
    r = dict(id=i.id, n=len(load), strangers=strangers, max_load_in_stack=carried, passed=row.get('passed'), weighted=row.get('weighted'))
    if row.get('passed'):
        plan = MovePlan.model_validate(row['plan'])
        c = apply_plan(i, plan).count
        r |= dict(reloc=c.relocations, restow_now=c.restow_lifts, hatch_now=c.hatch_cover_lifts,
                  hatch_later=sum(f.hatch_cover_lifts for f in c.future), restow_later=sum(f.restow_lifts for f in c.future),
                  check=c.weighted_nonproductive)
    rows.append(r)
ok = [r for r in rows if r.get('passed')]
print(len(rows), 'rows', len(ok), 'passed')
for k in ('n', 'strangers', 'reloc', 'restow_now', 'hatch_now', 'hatch_later', 'restow_later', 'check'):
    print(k, round(sum(r[k] for r in ok)/len(ok), 3), Counter(r[k] for r in ok).most_common(8))
print('reloc-strangers', Counter(r['reloc']-r['strangers'] for r in ok))
print('max load in stack', Counter(r['max_load_in_stack'] for r in rows))
print('hatch total', Counter(r['hatch_now']+r['hatch_later'] for r in ok))
print('excess over strangers+8', Counter(r['check']-r['strangers']-8 for r in ok))
(Path(__file__).parent/'breakdown.json').write_text(json.dumps(rows, indent=1))
