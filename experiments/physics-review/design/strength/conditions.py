"""Before/after strength utilisation for feeder_500/1000/1700 (in-memory overrides only).

Run (single core):
  STOWAGE_VESSEL_CACHE_DIR=/private/tmp/stow-strength-vessels OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/physics-review/design/strength/conditions.py
Writes conditions.json next to this file. Never edits params/ or src/.
"""
from __future__ import annotations
import json, math, sys, importlib.util, pathlib
import numpy as np
from stowage.checker.strength import check_strength
from stowage.checker import strength_limits as SL
from stowage.model import Station, ShipState

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod); return mod
fl = _load('fl', ROOT/'experiments/strength/full_load.py')
sec = _load('sec', HERE/'section.py')

# ---- recommended design values ------------------------------------------------------------
# Shear-area longitudinal profile, piecewise linear in u=x/L (knots follow S11A Fig. 7 corners).
# Midbody 0.4L-0.6L keeps the full midship side plating (UR S7.2: midship scantlings maintained over 0.4L);
# quarter-point shear strakes +20 % at 0.15-0.30L / 0.70-0.85L; ends taper to 0.60 (thinner end plating,
# reduced section depth); all APPROXIMATIONS of a typical design, not class-approved scantlings.
PROFILE_U = [0, .10, .15, .30, .40, .60, .70, .85, .90, 1]
PROFILE_P = [.60, .85, 1.20, 1.20, 1.0, 1.0, 1.20, 1.20, .85, .60]
KAPPA = 0.95
def profile_new(u): return float(np.interp(u, PROFILE_U, PROFILE_P))
PROFILE_P_FLAT = [.60, .85, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, .85, .60]
def profile_flat(u): return float(np.interp(u, PROFILE_U, PROFILE_P_FLAT))
def profile_old(u): return .35 + .65*math.sin(2*math.pi*u)**2

def section_inputs(inst, params):
    hull = inst.vessel.hull; B, D = hull.beam_m, hull.depth_m
    tanks = params.get('tanks', [])
    h_db = max(t['geometry']['z_max_m'] for t in tanks if t['geometry']['z_min_m'] < .5 and t['geometry']['z_max_m'] < 2.5)
    hold = [c for c in inst.vessel.cells if c.vcg_base_m < D]
    inner_half = max(abs(c.tcg_m) for c in hold) + 2.438/2 + .10
    w = max(B/2 - inner_half, 0.8)
    b_hatch = max(float(h['y_max_m']) for h in params['envelope']['hatch_coamings'])
    return dict(B=B, D=D, h_db=float(h_db), w=float(w), b_hatch=b_hatch)

def stations(inst, params, *, Z, A_peak_mid, profile):
    """Same read-out x as production; Station limits from tau*A(x) - wave, sigma*Z - wave."""
    hull = inst.vessel.hull; _, _, _, kw = SL._inputs(params, hull); L = kw['length_m']
    out = []
    for s in hull.stations:
        rx = SL._rule_station(s.x_m, L); w = SL.wave_loads(rx, **kw)
        cap = SL.SHEAR_PERMISSIBLE_PA*A_peak_mid*profile(rx/L)
        hog = SL.BENDING_PERMISSIBLE_PA*Z - w['hogging_nm']; sag = SL.BENDING_PERMISSIBLE_PA*Z + w['sagging_nm']
        sp, sn = cap - w['max_shear_n'], cap + w['min_shear_n']
        assert min(hog, sag, sp, sn) > 0, (s.x_m, hog, sag, sp, sn)
        out.append(Station(x_m=s.x_m, max_shear_n=sp, min_shear_n=-sn, max_bending_nm=hog, min_bending_nm=-sag))
    return out

def wave_required_peak(kw, profile):
    L = kw['length_m']; u = np.linspace(0, 1, 4001)
    need = [max(SL.wave_loads(x*L, **kw)['max_shear_n'], -SL.wave_loads(x*L, **kw)['min_shear_n'])/(SL.SHEAR_PERMISSIBLE_PA*profile(x)) for x in u]
    return max(need)

def summarise(inst, report, L):
    m = report.metrics; best = dict(sf=(0, None), hog=(0, None), sag=(0, None))
    for s in inst.vessel.hull.stations:
        lab = format(s.x_m, '.12g'); sf, bm = m[f'sf_n@{lab}'], m[f'bm_nm@{lab}']
        r = sf/s.max_shear_n if sf >= 0 else sf/s.min_shear_n
        if r > best['sf'][0]: best['sf'] = (r, s.x_m)
        if bm > 0 and bm/s.max_bending_nm > best['hog'][0]: best['hog'] = (bm/s.max_bending_nm, s.x_m)
        if bm < 0 and bm/s.min_bending_nm > best['sag'][0]: best['sag'] = (bm/s.min_bending_nm, s.x_m)
    return {k: dict(ratio=v[0], x_m=v[1], u=None if v[1] is None else v[1]/L) for k, v in best.items()} | dict(
        violations=len(report.violations), t_aft=m.get('draught_aft_m'), t_fwd=m.get('draught_fwd_m'),
        displacement_t=m.get('displacement_kg', 0)/1e3, max_abs_sf_mn=max(abs(m[f'sf_n@{format(s.x_m, ".12g")}']) for s in inst.vessel.hull.stations)/1e6,
        max_abs_bm_mnm=max(abs(m[f'bm_nm@{format(s.x_m, ".12g")}']) for s in inst.vessel.hull.stations)/1e6)

def recommended_Z(kw, zmin):
    """Z = 1.10*max(Zmin, (MwHog+MsHog_d)/sigma, (|MwSag|+MsSag_d)/sigma).

    MsSag_d, MsHog_d: design SWBM implied by S7 Zmin under old UR S11 (sigma=175/k, Mw_sag=110 C L2 B (Cb+.7),
    Mw_hog=190 C L2 B Cb, kNm): MsSag_d = 65 c L2 B (Cb+.7) e-3 kNm; MsHog_d = c L2 B (122.5-15 Cb) e-3 kNm.
    """
    L, B = kw['length_m'], kw['beam_m']; cb = max(kw['block_coefficient'], .60)
    c = zmin/(L**2*B*(cb+.7)*1e-6)
    ms_sag = 65*c*L**2*B*(cb+.7)*1e-3*1e3; ms_hog = c*L**2*B*(122.5-15*cb)*1e-3*1e3   # N m
    u = np.linspace(0, 1, 401); w = [SL.wave_loads(x*L, **kw) for x in u]
    mh = max(v['hogging_nm'] for v in w); msg = -min(v['sagging_nm'] for v in w)
    req = dict(s7=zmin, hog=(mh+ms_hog)/SL.BENDING_PERMISSIBLE_PA, sag=(msg+ms_sag)/SL.BENDING_PERMISSIBLE_PA,
               ms_sag_d_mnm=ms_sag/1e6, ms_hog_d_mnm=ms_hog/1e6, c=c)
    return SL.SECTION_MODULUS_DESIGN_MARGIN*max(req['s7'], req['hog'], req['sag']), req

def set_stations(hull, st):
    try: hull.stations = st
    except Exception: object.__setattr__(hull, 'stations', st)

def run(name):
    params = fl.load_ship_params(name)
    inst = fl.make_condition(name, mass_per_cell_kg=10_000.)
    hull = inst.vessel.hull; L = hull.lpp_m
    _, _, weight_n, kw = SL._inputs(params, hull)
    Z_cur, A_cur = SL._assumed_scantlings(kw)
    zmin = SL.minimum_section_modulus_m3(L, kw['beam_m'], kw['block_coefficient'])
    geo = section_inputs(inst, params)
    Z_new, zparts = recommended_Z(kw, zmin)
    des_cur = sec.design(Z_cur, L, **geo)
    des = sec.design(Z_new, L, **geo)
    A_wave_new = SL.SECTION_MODULUS_DESIGN_MARGIN*wave_required_peak(kw, profile_new)
    A_wave_flat = SL.SECTION_MODULUS_DESIGN_MARGIN*wave_required_peak(kw, profile_flat)
    # Production-form recommendation: A_ref = KAPPA * sum(t_net * web height); KAPPA=0.95 <= section I*sum(t)/S / web area
    A_web = KAPPA*des['web_area_m2']
    A_shear_only = max(A_web, A_wave_new)
    A_new = max(A_web, A_wave_new)          # reference (p=1) area = midbody area
    sets = dict(current=list(hull.stations),
                shear_fix_only=stations(inst, params, Z=Z_cur, A_peak_mid=A_shear_only, profile=profile_new),
                recommended=stations(inst, params, Z=Z_new, A_peak_mid=A_new, profile=profile_new),
                recommended_flat_profile=stations(inst, params, Z=Z_new, A_peak_mid=max(A_web, A_wave_flat), profile=profile_flat))
    n = len(inst.onboard.placements)
    # fixed (non-cargo) mass from a 10 t run with production stations
    r10 = check_strength(inst, inst.onboard, call_index=0)
    fixed = r10.metrics['displacement_kg'] - n*10_000.
    m_scant = (weight_n/SL.GRAVITY_M_S2 - fixed)/n
    conds = [('empty (lightship)', None), ('homog 10 t/cell', 10_000.), ('homog 14 t/TEU', 14_000.),
             (f'homog at scantling T ({m_scant/1e3:.2f} t/TEU)', m_scant),
             ('midship-heavy 16 t/TEU in 0.3-0.7L only', ('band', 16_000.))]
    res = dict(L=L, B=kw['beam_m'], D=hull.depth_m, T_scant=kw['draught_m'], cells=n, Cb=kw['block_coefficient'],
               Z_s7min=zmin, Z_cur=Z_cur, A_cur_peak=A_cur, A_cur_mid=.35*A_cur,
               wave_sag_mid_mnm=-SL.wave_loads(L/2, **kw)['sagging_nm']/1e6, wave_hog_mid_mnm=SL.wave_loads(L/2, **kw)['hogging_nm']/1e6,
               wave_sf_mid_mn=SL.wave_loads(L/2, **kw)['max_shear_n']/1e6,
               section_cur=geo | des_cur, section=geo | des, Z_new=Z_new, Z_parts=zparts, A_wave_req_newprofile=A_wave_new, A_shear_only_mid=A_shear_only, A_new_mid=A_new,
               limits_mid=dict(cur_hog=(SL.BENDING_PERMISSIBLE_PA*Z_cur-SL.wave_loads(L/2,**kw)['hogging_nm'])/1e6, cur_sag=(SL.BENDING_PERMISSIBLE_PA*Z_cur+SL.wave_loads(L/2,**kw)['sagging_nm'])/1e6, new_hog=(SL.BENDING_PERMISSIBLE_PA*Z_new-SL.wave_loads(L/2,**kw)['hogging_nm'])/1e6, new_sag=(SL.BENDING_PERMISSIBLE_PA*Z_new+SL.wave_loads(L/2,**kw)['sagging_nm'])/1e6, cur_sf_mid=sets['current'][min(range(len(sets['current'])),key=lambda i:abs(sets['current'][i].x_m-L/2))].max_shear_n/1e6, new_sf_mid=sets['recommended'][min(range(len(sets['current'])),key=lambda i:abs(sets['current'][i].x_m-L/2))].max_shear_n/1e6), conditions={})
    for label, mass in conds:
        if mass is None:
            ci = fl.make_condition(name); ci.onboard = ShipState(vessel_id=ci.vessel.id)
        elif isinstance(mass, tuple):
            ci = fl.make_condition(name, mass_per_cell_kg=mass[1])
            cells = {c.id: c for c in ci.vessel.cells}
            keep = [p for p in ci.onboard.placements if .3 <= cells[p.slot].lcg_m/L <= .7]
            ci.onboard = ShipState(vessel_id=ci.vessel.id, placements=keep)
        else:
            ci = fl.make_condition(name, mass_per_cell_kg=mass)
        res['conditions'][label] = {}
        for key, st in sets.items():
            set_stations(ci.vessel.hull, st)
            rep = check_strength(ci, ci.onboard, call_index=0)
            res['conditions'][label][key] = summarise(ci, rep, L) | dict(ran=rep.ran, error=rep.error)
        print(name, label, json.dumps({k: (round(v['sf']['ratio'], 3), round(v['sf']['u'] or 0, 3), round(v['hog']['ratio'], 3), round(v['sag']['ratio'], 3)) for k, v in res['conditions'][label].items()}), flush=True)
    return res

if __name__ == '__main__':
    names = sys.argv[1:] or ['feeder_500', 'feeder_1000', 'feeder_1700']
    out = {}
    for nm in names:
        out[nm] = run(nm)
        print(nm, {k: out[nm][k] for k in ('Z_s7min', 'Z_cur', 'Z_new', 'Z_parts', 'A_cur_peak', 'A_shear_only_mid', 'A_new_mid', 'A_wave_req_newprofile', 'limits_mid')},
              {k: round(v, 4) if isinstance(v, float) else v for k, v in out[nm]['section'].items()}, flush=True)
    (HERE/('conditions.json' if len(names) == 3 else f'conditions_{"_".join(names)}.json')).write_text(json.dumps(out, indent=1, default=float))
