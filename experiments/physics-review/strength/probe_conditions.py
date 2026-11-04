"""Physics review 3.2: run check_strength on probe conditions and compare with an
independent fine-grid re-integration of the same loads.

Run: UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 uv run python \
     experiments/physics-review/strength/probe_conditions.py [ship]

Independent part: buoyancy is re-sampled from HullGeometry.submerged() at the
checker's own solved draughts on a uniform 4000-panel midpoint grid (no Gauss
reconstruction), containers are spread uniformly over their 20' cell length
(6.058 m) instead of point loads, and SF/BM are integrated with cumulative sums.
The raw (uncorrected) end values and the theoretical trim-lever residual
(VCG-VCB)*tan(theta) are printed next to the checker's closure metrics.
"""
from __future__ import annotations

import math
import sys

import numpy as np

from stowage.checker import strength as S
from stowage.checker.strength_limits import permissible_stations
from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id, cells_of_slot
from stowage.model import Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.tanks import build_tanks

G = 9.80665
CELL_LEN = 6.058
NAME = ['']


def make_instance(name):
    params = load_ship_params(name)
    vessel = load_vessel(name, with_hull=False)
    instance = build_instance()
    vessel.hull = instance.vessel.hull.model_copy(update={**params['hull'], **params['operating_limits']})
    vessel.hull.stations = permissible_stations(params, vessel.hull)
    vessel.tanks = build_tanks(params)
    instance.vessel = vessel
    return instance, params


def load_boxes(instance, slots, mass):
    template = instance.containers[0]
    boxes, placements = [], []
    for i, slot in enumerate(slots):
        cid = make_container_id(SYNTHETIC_OWNER_CODES[0], 100000 + i)
        n = len(cells_of_slot(slot))
        boxes.append(template.model_copy(update=dict(
            id=cid, gross_mass_kg=mass * n, length_ft=20 if n == 1 else 40, oog=None)))
        placements.append(Placement(slot=slot, container_id=cid))
    instance.containers = boxes
    return placements


def independent(instance, ship, report):
    """Fine-grid integration at the checker's solved draughts, cargo spread over cell length."""
    hull = instance.vessel.hull
    _, geom = S._geometry(instance.vessel.hull.hull_offsets_ref.split('#')[0].split('/')[-1][:-5])
    ta, tf, heel = report.metrics['draught_aft_m'], report.metrics['draught_fwd_m'], report.metrics['list_rad']
    rho = instance.port.water_density_kg_m3
    sub = geom.submerged(ta, tf, heel, rho)
    L = hull.lpp_m
    n = 4000
    edges = np.linspace(0, L, n + 1)
    mid = (edges[:-1] + edges[1:]) / 2
    dx = L / n
    # buoyancy: linear interpolation of the checker's sectional areas (same hydrostatics source)
    area = np.interp(mid, sub.x_m, sub.sectional_areas_m2)
    q = area * rho * G
    w = np.zeros(n)
    for a, b, m in hull.lightship_distribution:
        w += m * G / (b - a) * np.clip((np.minimum(edges[1:], b) - np.maximum(edges[:-1], a)) / dx, 0, 1)
    boxes = {c.id: c for c in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    for p in ship.placements:
        cs = cells_of_slot(p.slot)
        for cid in cs:
            x = cells[cid].lcg_m
            m = boxes[p.container_id].gross_mass_kg / len(cs)
            a, b = x - CELL_LEN / 2, x + CELL_LEN / 2
            w += m * G / (b - a) * np.clip((np.minimum(edges[1:], b) - np.maximum(edges[:-1], a)) / dx, 0, 1)
    for x, y, z, m in instance.vessel.fixed_loads:
        k = min(int(x / dx), n - 1)
        w[k] += m * G / dx
    # tanks: mass spread uniformly over the tank's x-extent (upright approximation)
    tparams = {t['id']: t for t in load_ship_params(instance.vessel.id if False else NAME[0]).get('tanks', [])}
    zw = np.zeros(n)  # vertical first moment of weight per panel (N*m per panel)
    for tid, mass in ship.tank_masses_kg.items():
        g_ = tparams[tid]['geometry']
        a, b = g_['x_min_m'], g_['x_max_m']
        frac = np.clip((np.minimum(edges[1:], b) - np.maximum(edges[:-1], a)) / (b - a), 0, 1)
        w += mass * G * frac / dx
        fill = mass / tparams[tid]['content_density_kg_m3'] / ((b-a)*(g_['y_max_m']-g_['y_min_m']))
        zw += mass * G * frac * (g_['z_min_m'] + fill / 2)
    ls = hull.lightship_vcg_m
    for a, b, m in hull.lightship_distribution:
        zw += m * G * ls * np.clip((np.minimum(edges[1:], b) - np.maximum(edges[:-1], a)) / (b - a), 0, 1)
    for p in ship.placements:
        cs = cells_of_slot(p.slot)
        box = boxes[p.container_id]
        for cid in cs:
            c = cells[cid]
            a, b = c.lcg_m - CELL_LEN / 2, c.lcg_m + CELL_LEN / 2
            zw += box.gross_mass_kg / len(cs) * G * (c.vcg_base_m + box.height_m / 2) * np.clip(
                (np.minimum(edges[1:], b) - np.maximum(edges[:-1], a)) / (b - a), 0, 1)
    # buoyancy vertical first moment: section centroid ~ 0.5*local draught (box-section estimate)
    T_loc = ta + (tf - ta) * mid / L
    zb = q * dx * 0.5 * np.maximum(T_loc, 0)
    theta = math.atan((ta - tf) / L)
    # exact rigid-body trimmed-beam couple: (weight z-moment - buoyancy z-moment) aft of x times sin(theta)
    couple = np.concatenate([[0], np.cumsum(zw - zb)]) * math.sin(theta)
    net = (q - w) * dx
    sf_e = np.concatenate([[0], np.cumsum(net)])
    bm_e = -np.concatenate([[0], np.cumsum((sf_e[:-1] + sf_e[1:]) / 2 * dx)])
    return edges, sf_e, bm_e, sub, couple


def run(name, label, instance, ship):
    r = S.check_strength(instance, ship, call_index=0)
    if not r.ran:
        print(f'{name:12s} {label:28s} NOT RUN: {r.error}')
        return r
    m = r.metrics
    print(f'{name:12s} {label:28s} disp={m["displacement_kg"]/1e3:8.0f}t Ta={m["draught_aft_m"]:.3f} '
          f'Tf={m["draught_fwd_m"]:.3f} trim={m["trim_m"]:+.3f} closF={m["closure_force_fraction"]:.2e} '
          f'closM={m["closure_moment_fraction"]:.2e} maxSF={m["max_sf_ratio"]:.3f} maxBM={m["max_bm_ratio"]:.3f} '
          f'viol={[v.message[:40] for v in r.violations][:2]}')
    try:
        edges, sf_e, bm_e, sub, couple = independent(instance, ship, r)
    except Exception as exc:  # pragma: no cover
        print('   independent failed', exc)
        return r
    stations = [s.x_m for s in instance.vessel.hull.stations]
    sf_c = np.array([m[f'sf_n@{format(x, ".12g")}'] for x in stations])
    bm_c = np.array([m[f'bm_nm@{format(x, ".12g")}'] for x in stations])
    sf_i = np.interp(stations, edges, sf_e)
    bm_i = np.interp(stations, edges, bm_e)
    k = int(np.argmax(np.abs(bm_c)))
    print(f'   checker peak BM {bm_c[k]/1e6:+9.2f} MN.m @x={stations[k]:.2f}; independent (raw, cargo spread) '
          f'{bm_i[k]/1e6:+9.2f}; max|dBM|={np.max(np.abs(bm_c-bm_i))/1e6:.2f} MN.m; '
          f'max|dSF|={np.max(np.abs(sf_c-sf_i))/1e6:.3f} MN; indep raw end SF={sf_e[-1]/1e6:+.3f} MN '
          f'BM={bm_e[-1]/1e6:+.3f} MN.m')
    # physical trimmed-beam couple (sign chosen so it cancels the raw end residual)
    sgn = -np.sign(couple[-1]*bm_e[-1]) if couple[-1] else 1.
    bm_t = bm_e + sgn*couple
    bm_ti = np.interp(stations, edges, bm_t)
    print(f'   couple at FP={sgn*couple[-1]/1e6:+.2f} MN.m (cancels raw {bm_e[-1]/1e6:+.2f}; est. section zb=T/2); '
          f'indep+couple end BM={bm_t[-1]/1e6:+.2f}; max|checker-(indep+couple)|={np.max(np.abs(bm_c-bm_ti))/1e6:.2f} MN.m '
          f'at x={stations[int(np.argmax(np.abs(bm_c-bm_ti)))]:.1f}; envelope peak={max(s.max_bending_nm for s in instance.vessel.hull.stations)/1e6:.0f} MN.m')
    # theoretical trim-lever residual
    L = instance.vessel.hull.lpp_m
    theta = math.atan(m['trim_m'] / L)
    print(f'   checker raw end BM={m["raw_end_bending_nm"]/1e6:+.3f} MN.m; corr a={m["correction_a_n_m"]:.1f} N/m '
          f'b={m["correction_b_n_m2"]:.3f} N/m2; implied (KG-KB)=raw_end_BM/(W tan(theta)) = '
          f'{(m["raw_end_bending_nm"]/(m["displacement_kg"]*G*math.tan(theta)) if abs(theta)>1e-6 else float("nan")):.3f} m')
    return r


def main():
    names = sys.argv[1:] or ['feeder_1000']
    for name in names:
        NAME[:] = [name]
        instance, params = make_instance(name)
        vid = instance.vessel.id
        cells = instance.vessel.cells
        L = instance.vessel.hull.lpp_m
        tanks = instance.vessel.tanks
        # 1 empty, no tanks
        run(name, 'empty, tanks empty', instance, ShipState(vessel_id=vid))
        # 2 empty + all tanks 50%
        run(name, 'empty, tanks 50%', instance, ShipState(vessel_id=vid, tank_masses_kg={
            t.id: t.capacity_m3 * t.content_density_kg_m3 * .5 for t in tanks}))
        # 3 homogeneous: every 20' cell a 10 t box
        slots = [c.id for c in cells if c.accepts_20]
        pl = load_boxes(instance, slots, 10_000)
        run(name, f'homog 10t x{len(slots)}', instance, ShipState(vessel_id=vid, placements=pl))
        # 4 fore cells 6 t (trim by head) + aft cells 3 t
        slots = [c.id for c in cells if c.accepts_20 and c.lcg_m > .55 * L]
        pl = load_boxes(instance, slots, 6_000)
        run(name, 'fore cells 6t (by head)', instance, ShipState(vessel_id=vid, placements=pl))
        # 5 aft cells 6 t (by stern)
        slots = [c.id for c in cells if c.accepts_20 and c.lcg_m < .45 * L]
        pl = load_boxes(instance, slots, 6_000)
        run(name, 'aft cells 6t (by stern)', instance, ShipState(vessel_id=vid, placements=pl))
        # 6 midship heavy (sagging)
        slots = [c.id for c in cells if c.accepts_20 and abs(c.lcg_m - .5 * L) < .2 * L]
        pl = load_boxes(instance, slots, 30_000)
        run(name, 'midship cells 30t (sag)', instance, ShipState(vessel_id=vid, placements=pl))
        # 7 ends heavy (hogging)
        slots = [c.id for c in cells if c.accepts_20 and abs(c.lcg_m - .5 * L) > .25 * L]
        pl = load_boxes(instance, slots, 30_000)
        run(name, 'end cells 30t (hog)', instance, ShipState(vessel_id=vid, placements=pl))
        instance.containers = instance.containers[:0] or build_instance().containers


if __name__ == '__main__':
    main()
