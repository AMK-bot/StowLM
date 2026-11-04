"""Audit probe (stow-physics): hydrostatics, GM, ballast conditions, Z/A. Light, single-thread."""
import json, math, sys, time
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.tanks import TankGeometry
from stowage.checker.strength_limits import design_summary

def roll_period(B, d, L, gm):
    c = .373 + .023*B/d - .043*L/100
    return 2*c*B/math.sqrt(gm)

out = {}
for n in (500, 1000, 1700):
    t0 = time.time()
    p = load_ship_params(f'feeder_{n}')
    h = p['hull']; L, B, D, T = h['lpp_m'], h['beam_m'], h['depth_m'], h['design_draught_m']
    g = HullGeometry.from_params(p, nx=96, nz=128)
    row = g.hydrostatic_row(T)
    r = dict(L=L, B=B, D=D, T=T, displacement_t=row.displacement_kg/1e3, KB=row.kb_m, KMt=row.kmt_m,
             TPC_t=row.tpc_kg_per_cm/1e3, LCB=row.lcb_m, LCF=row.lcf_m)
    ls = h['lightship_mass_kg']; dwt = row.displacement_kg - ls
    r.update(lightship_t=ls/1e3, dwt_t=dwt/1e3, lightship_over_disp=ls/row.displacement_kg)
    ncells = len(p['cells']); r['cells'] = ncells; r['teu_per_kdwt'] = ncells/(dwt/1e6)
    # tanks
    tanks = [(t, TankGeometry.from_params(t)) for t in p['tanks']]
    wb = [(t, s) for t, s in tanks if t['kind'] == 'ballast']
    wbv = sum(s.capacity_m3 for _, s in wb)
    r['ballast_m3'] = wbv; r['ballast_t_over_dwt'] = wbv*1.025e3/dwt
    for kind in ('fuel_oil', 'diesel_oil', 'fresh_water'):
        r[kind+'_m3'] = sum(s.capacity_m3 for t, s in tanks if t['kind'] == kind)
    def cond(fills):
        m = ls; mx = ls*h['lightship_lcg_m']; mz = ls*h['lightship_vcg_m']; fsm = 0.
        for t, s in tanks:
            f = fills.get(t['kind'], 0.)
            if f <= 0: continue
            v = s.capacity_m3*f; sp = s.sounding(v); w = v*t['content_density_kg_m3']
            m += w; mx += w*sp.lcg_m; mz += w*sp.vcg_m; fsm += sp.fsm_m4*t['content_density_kg_m3']
        lcg, kg = mx/m, mz/m
        fc = g.equilibrium(m, lcg, 0., kg)
        sub = fc.submerged
        km = sub.vcb_m + sub.bm_t_m
        gm = km - kg - fsm/m
        dm = (fc.t_aft_m+fc.t_fwd_m)/2
        return dict(mass_t=m/1e3, KG=kg, KM=km, GM_fluid=gm, FSC=fsm/m, t_aft=fc.t_aft_m, t_fwd=fc.t_fwd_m,
                    trim_by_stern=fc.t_aft_m-fc.t_fwd_m, mean_draught=dm,
                    roll_T_s=roll_period(B, dm, L, gm) if gm > 0 else None,
                    freeboard_mid=D-dm)
    r['lightship'] = cond({})
    r['full_ballast_dep_consumables'] = cond({'ballast': 1., 'fuel_oil': .98, 'diesel_oil': .98, 'fresh_water': .98})
    r['full_ballast_arr_consumables'] = cond({'ballast': 1., 'fuel_oil': .10, 'diesel_oil': .10, 'fresh_water': .10})
    r['roll_T_at_GM'] = {gm: roll_period(B, T, L, gm) for gm in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)}
    # strength
    try:
        ds = design_summary(p, g)
        r['Z_m3'] = ds['assumed_effective_net_section_modulus_m3']; r['Z_over_S7'] = ds['section_modulus_s7_multiplier']
        r['A_ref_m2'] = ds['assumed_effective_net_shear_area_m2']; r['h_db'] = ds['assumed_double_bottom_height_m']
        r['Cb_scantling'] = ds['block_coefficient']
        r['SW_hog_mid_MNm'] = ds['midship_sw_hogging_nm']/1e6; r['SW_sag_mid_MNm'] = ds['midship_sw_sagging_magnitude_nm']/1e6
        r['SF_peak_MN'] = ds['design_sw_peak_sf_n']/1e6
    except Exception as e:
        r['strength_error'] = repr(e)
    r['elapsed_s'] = time.time()-t0
    out[n] = r
    print(n, json.dumps(r, indent=1, default=float), flush=True)
json.dump(out, open('experiments/physics-review/convergence/audit/probe_hydro.json', 'w'), indent=1, default=float)
