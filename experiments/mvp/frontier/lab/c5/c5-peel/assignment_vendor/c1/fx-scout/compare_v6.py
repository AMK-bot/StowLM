"""Physics of a page stow beside the V6 certified stow on the same map, default tanks, to see what ballast must fix."""
from pathlib import Path
from collections import Counter, defaultdict
import json, random, sys
from time import monotonic
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(Path(__file__).parent)]
from alns import Problem, Sequencer
from lash_oracle import Fast
from method import one_stow
from pages import Model
from stowage.actions import get_action_space
from stowage.checker import check
from stowage.conventions import cells_of_slot, parse_slot
from stowage.io import load_instance
from stowage.model.plan import MovePlan
from stowage.moves import apply_plan

key = 'D192.medium-feeder_500-f60-high-s192200000'
b = json.loads((ROOT/'experiments/mvp/frontier/lab/baseline.json').read_text())['maps'][key]
i = load_instance(ROOT/b['input']); p = Problem(i)
raw = b['plan'] if b.get('plan') else json.loads((ROOT/'experiments/mvp/scale/runs/v6/maps/medium-feeder_500-f60-high-s192200000/finished.json').read_text())
raw = raw.get('plan', raw)
v6 = MovePlan.model_validate(raw)
ship_v6 = apply_plan(i, v6).final_state.ship
first, _ = one_stow(p, Model(p), seed=0, deadline=monotonic()+25)
oracle = Fast(i, apply_plan(i, first.plan).final_state.ship)
s, row = one_stow(p, Model(p, deck_tiers=4, oracle=oracle, lcg_target=float(sys.argv[1]) if len(sys.argv) > 1 else None), seed=0, deadline=monotonic()+60)
print(row)

def stats(ship, name):
    mass = lcg = tcg = vcg = 0.
    bay = Counter(); deck = 0.
    for q in ship.placements:
        box = p.boxes[q.container_id]
        cells = [p.cells[c] for c in cells_of_slot(q.slot)]
        m = box.gross_mass_kg/1000
        x = sum(c.lcg_m for c in cells)/len(cells); y = sum(c.tcg_m for c in cells)/len(cells); z = cells[0].vcg_base_m+1.3
        mass += m; lcg += m*x; tcg += m*y; vcg += m*z
        bay[parse_slot(q.slot).bay//4] += m
        deck += m*parse_slot(q.slot).on_deck
    print(name, 'n', len(ship.placements), 'mass', round(mass), 'lcg', round(lcg/mass, 2), 'tcg', round(tcg/mass, 3), 'vcg', round(vcg/mass, 2), 'deck t', round(deck), 'bay', sorted((k, round(v)) for k, v in bay.items()))

stats(ship_v6, 'v6 ')
stats(apply_plan(i, s.plan).final_state.ship, 'page')
for name, plan in (('page', s.plan),):
    r = check(i, plan)
    print(name, Counter(v.rule_id for v in r.violations).most_common(9))
    w = {}
    for v in r.violations:
        if v.measured is not None:
            k = v.rule_id+':'+v.message.split(']')[-1][:34]
            if k not in w: w[k] = (round(v.measured, 3), round(v.limit, 3) if v.limit else None)
    print('  ', w)
Path(__file__).with_name('stow_f60.json').write_text(json.dumps({a.box: a.slot for a in s.actions}))
