from pathlib import Path; import sys, json
from collections import Counter, defaultdict
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier')]
from stowage.io import load_instance
from stowage.conventions import parse_slot
for p in sys.argv[1:]:
    i = load_instance(ROOT/p); v = i.vessel
    print(i.id, 'covers', [ (c.id, len(getattr(c,'stack_ids',[]))) for c in v.hatch_covers])
    by = defaultdict(list)
    for s in v.stacks:
        by[(s.hatch_cover_id, s.on_deck)].append((s.id, len(s.cell_ids)))
    for k, val in sorted(by.items(), key=str):
        print(' ', k, 'stacks', len(val), 'cells', sum(x[1] for x in val), val[:8])
    s0 = v.stacks[0]; print(' stack fields', [f for f in type(s0).model_fields])
    c0 = v.cells[0]; print(' cell fields', [f for f in type(c0).model_fields]); print(' first', s0.id, s0.cell_ids, getattr(s0,'max_weight_kg',None), getattr(s0,'max_stack_mass_kg',None))
    print(' rotation', [(c.locode, c.index) for c in i.rotation.calls], 'objective', i.objective)
    print(' port max deck tiers', i.port.quay_crane.max_on_deck_tiers, 'yard tiers', i.port.yard_max_tiers, 'blocks', [(b.id, b.max_tiers) for b in i.port.yard.blocks][:4])
