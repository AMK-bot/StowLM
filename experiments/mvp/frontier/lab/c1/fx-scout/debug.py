from pathlib import Path
import json, sys
from time import monotonic
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(Path(__file__).parent)]
from alns import Problem
from pages import Model
from stowage.io import load_instance
i = load_instance(ROOT/'experiments/mvp/corpus/val/small/feeder_800/random_yard/seed-040100023.json'); p = Problem(i)
m = Model(p)
T = m.T
print('pages', [(x.kind, len(x.slots), x.deck, x.limit_kg) for x in m.pages][:12], len(m.pages))
print('boxes', sorted((int(T.length[b]), int(T.rank[b]), round(float(T.mass[b])/1000, 1), m.rarity[b]) for b in m.boxes))
r = m.generate(active_boxes=set(m.boxes), active_stacks=set(m.stacks), phase=1, deadline=monotonic()+10)
print('lp', r['uncovered'], r['proved'], len(m.columns))
c, info = m.dive(deadline=monotonic()+20, log=print)
print(info, m.last_refused)

print('--- medium')
from alns import Sequencer
from lash_oracle import Fast
from stowage.moves import apply_plan
i = load_instance(ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_500/random_yard/seed-185100000.json'); p = Problem(i)
reference = json.loads((Path(__file__).parent/'stow_4_1.0.json').read_text())
ship = apply_plan(i, p.score(Sequencer(p).order(reference)).plan).final_state.ship
m = Model(p, deck_tiers=4, oracle=Fast(i, ship), lash_limit=1.)
T = m.T
r = m.generate(active_boxes=set(m.boxes), active_stacks=set(m.stacks), phase=1, deadline=monotonic()+40)
print('lp', r['uncovered'], r['proved'])
for b, v in m.short.items():
    slots = [T.slot_ids[s] for s in range(len(T.slot_ids)) if m.static[b, s]]
    print(T.box_ids[b], round(v, 2), int(T.length[b]), round(float(T.mass[b])/1000, 1), p.boxes[T.box_ids[b]].reefer, p.boxes[T.box_ids[b]].pod, len(slots), slots[:24])
