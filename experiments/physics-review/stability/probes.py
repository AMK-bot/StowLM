"""Edge-case probes of check_stability on the real feeder_500 hull (review 3.1).

Builds the same balanced design-draught condition as tests/checker/test_stability.py
(feeder fixture: lightship + one point fixed load), then varies KG/TCG/LCG, tanks
and declared openings. Nothing outside experiments/physics-review is written.
Run: UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 uv run python experiments/physics-review/stability/probes.py
"""
import copy
import math
import sys
import numpy as np

from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.tanks import build_tanks, TankGeometry
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker import stability as S
from stowage.checker.stability_loading import liquid_centroid

P = load_ship_params('feeder_500')
G = StabilityGeometry(P)


def feeder(kg=3.0, tcg=0.0, dlcg=0.0, tanks=None):
    i = build_instance(); template = i.vessel.hull
    v = load_vessel('feeder_500', with_hull=False)
    fields = {k: P['hull'][k] for k in type(template).model_fields if k in P['hull']}
    fields.update(P['operating_limits']); fields.pop('provenance', None)
    fields.update(hydrostatics=template.hydrostatics, max_draught_m=P['hull']['scantling_draught_m'])
    v.hull = template.model_copy(update=fields)
    v.tanks = build_tanks(P)
    i.vessel = v
    i.onboard = i.onboard.model_copy(update=dict(vessel_id=v.id, tank_masses_kg=dict(tanks or {})))
    s = G.full_submerged(G.design_draught_m, G.design_draught_m)
    tank_mass = sum((tanks or {}).values())
    m = s.displacement_kg - v.hull.lightship_mass_kg - tank_mass
    lcg = (s.displacement_kg*s.lcb_m - v.hull.lightship_mass_kg*v.hull.lightship_lcg_m)/m + dlcg
    v.fixed_loads = [(lcg, tcg, kg, m)]
    return i


def run(i, label):
    r = S.check_stability(i, i.onboard, call_index=0)
    if not r.ran:
        print(f'[{label}] NOT RUN: {r.error}  viol={[x.rule_id for x in r.violations]}')
        return r
    m = r.metrics
    keys = ['displacement_kg', 'kg_m', 'gm_solid_m', 'fsc_m', 'gm_m', 'trim_m', 'list_rad',
            'area_0_30_mrad', 'area_0_40_mrad', 'area_30_40_mrad', 'gz_max_m', 'angle_gz_max_rad',
            'gz_at_or_above_30_m', 'weather_ratio', 'weather_phi0_rad', 'angle_flooding_rad',
            'area_0_flood_mrad', 'containership_form_factor']
    print(f'[{label}] ' + ', '.join(f'{k}={m[k]:.5g}' for k in keys if k in m))
    print(f'   violations: {sorted({x.rule_id for x in r.violations})}')
    return r


def main():
    base_kg = 6.0
    r0 = run(feeder(kg=base_kg), 'base KG_fixed=6')
    D = r0.metrics['displacement_kg']
    # --- P2 slack fixed ballast pair at 50 %
    t = {x['id']: x for x in P['tanks']}
    half = 130.39488/2*1025
    r = run(feeder(kg=base_kg, tanks={'WB-2-P': half, 'WB-2-S': half}), 'slack WB-2 P/S 50%')
    hand = 2*1025*21.56*5.76**3/12/D
    print(f'   hand FSC (B3.1.8, 2 rho l b^3/12 / Delta) = {hand:.6f} m')
    # --- P3 consumable: FO-P 30% only -> both FO tanks max FSM (B3.1.3.2/3.1.4)
    fo = 15.988896*.3*970
    r = run(feeder(kg=base_kg, tanks={'FO-P': fo}), 'FO-P 30% (variable)')
    hand = 2*970*2.695*1.536**3/12/D
    print(f'   hand FSC both FO tanks max = {hand:.6f} m (code assumes all FO tanks slack)')
    # --- P4 TCG offset: list + worst-side GZ
    r = run(feeder(kg=base_kg, tcg=.05), 'TCG fixed +0.05 m')
    tg = r.metrics.get('list_rad', 0)
    # hand list ~ atan(tcg_total / GM)
    tt = .05*(D - P['hull']['lightship_mass_kg'])/D + P['hull']['lightship_mass_kg']*P['hull'].get('lightship_tcg_m', 0)/D
    print(f'   hand list ~ atan(TCG/GM) = {math.degrees(math.atan(tt/r0.metrics["gm_m"])):.4f} deg; code {math.degrees(tg):.4f} deg')
    print(f'   area_0_30 change: {r.metrics["area_0_30_mrad"]-r0.metrics["area_0_30_mrad"]:.5f}; hand -TCG*sin30 = {-tt*.5:.5f}')
    run(feeder(kg=base_kg, tcg=-.05), 'TCG fixed -0.05 m (mirror; must equal +0.05)')
    # --- P5 trim by head
    r = run(feeder(kg=base_kg, dlcg=+3.0), 'LCG +3 m fwd (trim by head)')
    # --- P7 negative GM
    run(feeder(kg=11.0), 'KG_fixed=11 (negative GM?)')
    # --- P6 declared opening (deckhouse door aft) via monkeypatched params
    orig = S.load_ship_params
    for z in (10.0, 12.0):
        p2 = copy.deepcopy(P)
        p2['envelope']['openings'] = [dict(id='door', x_m=8.0, y_m=7.0, z_m=z)]
        S.load_ship_params = lambda name, p2=p2: p2
        S._geometry.cache_clear()
        r = run(feeder(kg=base_kg), f'opening x=8 y=7 z={z}')
        T = r.metrics['draught_aft_m']
        print(f'   hand static (no sinkage) phi_f ~ atan((z-Ta)/y) = {math.degrees(math.atan((z-T)/7)):.3f} deg; '
              f'code {math.degrees(r.metrics["angle_flooding_rad"]):.3f} deg')
    S.load_ship_params = orig; S._geometry.cache_clear()
    # --- P8 effect of sealed 90 deg endpoint on B2.3.2.5 / gz_max: recompute curve to compare
    rr = feeder(kg=base_kg)
    cond = S.loading_condition(rr, rr.onboard, geometry=G, ship_params=P)
    angles = np.radians(np.sort(np.r_[np.arange(0., 90., 2.), 25., 89.99]))
    gz, dr = G.free_trim_curve(angles, cond.mass_kg, cond.lcg_m, cond.kg_m, 1025.)
    from stowage.checker.stability_criteria import _integral
    idx = np.where((gz[1:] < 0) & (gz[:-1] >= 0))[0]
    phiv = angles[idx[0]] + (angles[idx[0]+1]-angles[idx[0]])*gz[idx[0]]/(gz[idx[0]]-gz[idx[0]+1]) if len(idx) else math.pi/2
    print('\n[P8] GZ every 10 deg:', [round(float(g), 3) for a, g in zip(np.degrees(angles), gz) if a % 10 == 0])
    print(f'   vanishing angle {math.degrees(phiv):.2f} deg')
    for e in (math.radians(40), math.radians(50), math.radians(60), phiv, math.radians(89.99)):
        print(f'   area 0-{math.degrees(e):.1f} = {_integral(angles, gz, 0, e):.4f} m rad')


def tank_moment():
    """Check the 'finite-angle transfer is greatest at half fill' claim (stability_loading)."""
    print('\n[P9] transverse liquid shifting moment M(v)=v*(c(v)-c_upright).t / M(half)')
    for tid in ('WB-2-P', 'FO-P'):
        raw = next(x for x in P['tanks'] if x['id'] == tid)
        solid = TankGeometry.from_params(raw)
        cap = solid.capacity_m3
        for deg in (15., 30., 60., 85.):
            phi = math.radians(deg)
            tr = np.array([0., math.cos(phi), math.sin(phi)])
            def M(f):
                v = f*cap; up = solid.sounding(v)
                c = liquid_centroid(solid, v, phi, 0.)
                return v*float((c-np.array([up.lcg_m, up.tcg_m, up.vcg_m])) @ tr)
            fr = np.linspace(.02, .98, 49)
            vals = np.array([M(f) for f in fr]); mh = M(.5)
            print(f'   {tid} {deg:4.0f} deg: max M/M(0.5)={vals.max()/mh:.4f} at fill {fr[vals.argmax()]:.2f}')


if __name__ == '__main__':
    main(); tank_moment()
