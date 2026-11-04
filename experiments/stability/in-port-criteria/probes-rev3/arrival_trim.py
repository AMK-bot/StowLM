"""Rev-3 probe (read-only on src): empty-ship arrival in-port state per ship.
Cases: empty tanks; arrival consumables (10% of 97%); + forward trim ballast scan; + HEEL pair pre-fill.
Uses the T4 fixture construction from tests/checker/test_stability_in_port.py."""
import json, math, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tests.checker.test_stability_policy_fixtures import build_instance
from stowage.checker import stability
from stowage.vessel.fleet import load_vessel

def empty(name):
    i = build_instance(); i.vessel = load_vessel(name); i.vessel.fixed_loads = []; i.containers = []
    i.onboard = i.onboard.model_copy(update={'vessel_id': i.vessel.id, 'placements': [], 'tank_masses_kg': {}, 'covers_off': {}})
    i.port.max_draught_m = None
    return i

def run(i, tanks):
    j = i.model_copy(deep=True); j.onboard = j.onboard.model_copy(update={'tank_masses_kg': dict(tanks)})
    r = stability.check_stability(j, j.onboard, call_index=0, in_port=True)
    m = r.metrics
    return dict(ran=r.ran, rules=sorted({v.rule_id for v in r.violations}), msgs=[v.message[:90] for v in r.violations][:3],
                trim=m.get('trim_m'), gm=m.get('gm_m'), list_deg=math.degrees(m.get('list_rad', 0) or 0),
                ta=m.get('draught_aft_m'), tf=m.get('draught_fwd_m'), disp_t=(m.get('displacement_kg') or 0)/1e3)

def cap(t): return t.capacity_m3 * t.content_density_kg_m3

out = {}
for name in sys.argv[1:] or ['feeder_500', 'feeder_800', 'feeder_1700']:
    i = empty(name); h = i.vessel.hull; lim = h.lpp_m * math.tan(math.radians(1))
    tanks = {t.id: t for t in i.vessel.tanks}
    cons = {k: .1 * .97 * cap(t) for k, t in tanks.items() if not t.adjustable}
    res = dict(lpp=h.lpp_m, trim_limit=lim, cases={})
    t0 = time.time()
    res['cases']['empty'] = run(i, {})
    res['cases']['arrival_consumables'] = run(i, cons)
    # forward ballast candidates: adjustable ballast tanks forward of midships, sorted by lcg desc
    fwd = sorted([t for t in tanks.values() if t.adjustable and t.kind == 'ballast' and t.table[-1].lcg_m > .5 * h.lpp_m and not t.id.startswith('HEEL')],
                 key=lambda t: -t.table[-1].lcg_m)
    groups = {}
    for t in fwd: groups.setdefault(t.id.rsplit('-', 1)[0], []).append(t)
    res['fwd_groups'] = {g: dict(t=round(sum(cap(t) for t in ts) / 1e3, 1), lcg=round(ts[0].table[-1].lcg_m, 1), vcg=round(ts[0].table[-1].vcg_m, 2)) for g, ts in groups.items()}
    scan = []
    order = list(groups)  # forward-most first
    for frac in (.25, .5, .75, 1.):
        fill = dict(cons)
        for g in order[:1]:
            for t in groups[g]: fill[t.id] = frac * cap(t)
        scan.append(dict(fill=f"{order[0]}@{frac}", **run(i, fill)))
    for frac in (.5, 1.):
        fill = dict(cons)
        for g in order[:2]:
            for t in groups[g]: fill[t.id] = (1. if g == order[0] else frac) * cap(t)
        scan.append(dict(fill=f"{order[0]}@1+{order[1]}@{frac}" if len(order) > 1 else 'n/a', **run(i, fill)))
    # all forward groups full
    fill = dict(cons)
    for g in order:
        for t in groups[g]: fill[t.id] = cap(t)
    scan.append(dict(fill='all_fwd_full', **run(i, fill)))
    res['fwd_scan'] = scan
    # HEEL pre-fill 50%: GM cost (FSM) and transfer authority
    heel = [t for t in tanks.values() if t.id.startswith('HEEL')]
    best = min((s for s in scan if not any('trim' in m for m in s['msgs'])), key=lambda s: abs(s['trim'] or 9), default=scan[-1])
    base_fill = dict(cons)
    for g in order:
        pass
    res['heel'] = dict(tanks={t.id: dict(cap_t=round(cap(t) / 1e3, 1), tcg=round(t.table[-1].tcg_m, 2), vcg=round(t.table[-1].vcg_m, 2)) for t in heel})
    for frac in (0., .5):
        fill = dict(cons); fill.update({t.id: frac * cap(t) for t in heel})
        res['heel'][f'pair@{frac}'] = run(i, fill)
    # authority: moment from moving half-pair total across: m = cap/2 per side transferable, lever 2*|tcg|
    if heel:
        c = cap(heel[0]); tcg = abs(heel[0].table[-1].tcg_m)
        r50 = res['heel']['pair@0.5']
        mom = .5 * c * 2 * tcg  # kg*m, moving half of one tank's capacity... pair at 50%: each has c/2; move c/2 from one to other
        res['heel']['max_correction_moment_tm'] = round(mom / 1e3, 1)
        if r50['gm'] and r50['gm'] > 0:
            res['heel']['max_correction_list_deg'] = round(math.degrees(math.atan(mom / (r50['disp_t'] * 1e3 * r50['gm']))), 2)
    res['wall_s'] = round(time.time() - t0, 1)
    out[name] = res
    print(name, json.dumps({k: res['cases'][k] for k in res['cases']}, default=str)[:600], flush=True)
    for s in scan: print('  ', s['fill'], round(s['trim'] or 0, 3), '/', round(lim, 3), 'gm', round(s['gm'] or 0, 2), 'tf', round(s['tf'] or 0, 2), s['rules'], flush=True)
    print('   heel', json.dumps(res['heel'], default=str)[:500], flush=True)
Path(__file__).with_name('arrival_trim.json').write_text(json.dumps(out, indent=1, default=str))
