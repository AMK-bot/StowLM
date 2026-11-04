"""Audit probe: hold clearance under hatch, tank containment in hull, tank intrusion into hold columns."""
import json, sys, collections
sys.path.insert(0, 'experiments/fleet-params')
import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from stowage.vessel.fleet import load_ship_params
from kcs_fit import OffsetFit
out = {}
for n in (500, 1000, 1700):
    p = load_ship_params(f'feeder_{n}'); h = p['hull']; D = h['depth_m']
    st = {s['id']: s for s in p['stacks']}; cells = {c['id']: c for c in p['cells']}
    r = {}
    # 1. hold height vs deck
    over = []
    for s in p['stacks']:
        if s['on_deck']: continue
        k = len(s['cell_ids'])
        top_allowed = s['base_z_m'] + s['max_height_m']
        top_std = s['base_z_m'] + k*2.591
        top_1hc = s['base_z_m'] + (k-1)*2.591 + 2.896
        top_allhc = s['base_z_m'] + k*2.896
        over.append((s['id'], k, round(s['base_z_m'], 3), round(s['max_height_m'], 3), round(top_allowed-D, 3), round(top_std-D, 3), round(top_1hc-D, 3), round(top_allhc-D, 3)))
    r['hold_stacks'] = len(over)
    r['hold_allowed_top_minus_D_max'] = max(o[4] for o in over)
    r['n_std_top_above_D'] = sum(o[5] > 0 for o in over)
    r['n_1hc_top_above_D'] = sum(o[6] > 0 for o in over)
    r['n_allhc_top_above_D'] = sum(o[7] > 0 for o in over)
    r['n_allowed_above_deckstack_base'] = sum(o[4] > 0.6 for o in over)
    r['examples'] = sorted(over, key=lambda o: -o[4])[:4]
    # 2. tank containment and hold-column intrusion
    fit = OffsetFit(p)
    holdboxes = []  # hold column space: cell footprint (bay half pitch 3.067, row half pitch 1.25), floor -> D
    for s in p['stacks']:
        if s['on_deck']: continue
        c = cells[s['cell_ids'][0]]
        holdboxes.append((c['lcg_m']-3.029, c['lcg_m']+3.029, c['tcg_m']-1.219, c['tcg_m']+1.219, s['base_z_m'], D))
    tk = {}
    tot_tank = 0.
    for t in p['tanks']:
        g = t['geometry']
        if g.get('shape') != 'hull_sections': continue
        vol = outside = inhold = 0.
        for x, w, pp in zip(g['x_m'], g['integration_weights_m'], g['sections_yz_m']):
            half = fit.half(float(x))
            hull = Polygon(np.vstack([half, half[::-1]*[-1, 1]])).buffer(0)
            hb = [box(b[2], b[4], b[3], b[5]) for b in holdboxes if b[0] < x < b[1]]
            hu = unary_union(hb) if hb else None
            for poly in pp:
                P = Polygon(poly).buffer(0)
                vol += w*P.area
                outside += w*(P.area - P.intersection(hull).area)
                if hu is not None: inhold += w*P.intersection(hu).area
        tot_tank += vol
        tk[t['id']] = dict(vol=round(vol, 2), outside_hull_m3=round(outside, 3), inside_hold_columns_m3=round(inhold, 3))
    r['tanks'] = tk
    r['tank_total_m3'] = tot_tank
    out[n] = r
    print(n, json.dumps({k: v for k, v in r.items() if k != 'tanks'}, default=float))
    for k, v in tk.items():
        if v['outside_hull_m3'] > 0.05 or v['inside_hold_columns_m3'] > 0.05: print('   ', k, v)
json.dump(out, open('experiments/physics-review/convergence/audit/probe_geom.json', 'w'), indent=1, default=float)
