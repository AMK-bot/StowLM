from pathlib import Path
import json, sys
from time import monotonic
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(Path(__file__).parent)]
from alns import Problem
from pages import Model
from stowage.io import load_instance
for path in ['experiments/mvp/corpus/val/small/feeder_1700/random_yard/seed-041100018.json', 'experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100034.json']:
    i = load_instance(ROOT/path); p = Problem(i); m = Model(p); T = m.T
    r = m.generate(active_boxes=set(m.boxes), active_stacks=set(m.stacks), phase=1, deadline=monotonic()+10)
    print(i.id, 'lp', r['uncovered'], r['proved'])
    for b, v in m.short.items():
        box = p.boxes[T.box_ids[b]]
        print(' ', box.id, box.length_ft, box.gross_mass_kg, box.height_m, box.reefer, box.dg, box.oog, box.special_stowage, 'static', int(m.static[b].sum()), 'T.static', int(T.static[b].sum()), 'closes', bool(T.closes_stack[b]))
        slots = [T.slot_ids[s] for s in range(len(T.slot_ids)) if m.static[b, s]]
        print('  slots', slots[:12])
        print('  page kinds', [(x.kind, len(x.slots), x.deck, round(x.hmax, 2)) for x in m.pages if set(x.slots) & {T.slot_index[s] for s in slots}][:8])
