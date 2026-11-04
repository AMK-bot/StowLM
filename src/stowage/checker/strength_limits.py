"""Synthetic design envelopes with exact IACS S11A wave-load formulae.

A permissible still-water value is the structural stress budget left after
reserving the S11A 2.3 wave load, separately for hogging and sagging. Sources:
``data/raw/iacs/ur-s11a.pdf`` (June 2015), 2.3 and 3.3; and
``data/raw/iacs/URS-2024.pdf`` PDF p32, UR S7.1 (Rev.4 2010). S7 gives
Z_min = c L² B (max(Cb,0.60)+0.7) k in cm³; k=1 for ordinary mild steel.
The cm³ result is multiplied by 1e-6 to obtain m³.

APPROXIMATION: assume effective net section modulus
Z = 1.10 max(Z_min, (M_wave,hog + M_design,hog)/sigma_b,
(|M_wave,sag| + M_design,sag)/sigma_b) at every assessment station.
The design still-water moments recover the reserve implicit in S7 using old
UR S11.2.2.1/3.1.1 (URS-2024.pdf); S11 excludes container ships, so those
moments are design assumptions, not applicable rule limits. Mild steel has
ReH=235 MPa and k=1: S11A 3.3 permits sigma_b=235/1.24 MPa and pure
tau=235/(1.13 sqrt(3)) MPa. The 10% design margin is assumed. Subtract the
separate S11A hogging/sagging waves from sigma_b Z.

APPROXIMATION: idealised symmetric double-skin section, with lumped deck
flanges and no coaming/longitudinal detail or local-load delta-F shear terms.
Side/inner gross thicknesses are (8+.04L)/(6.5+.03L) mm, assumed rather than
verified rule minima. Deduct 1.25 mm per S11A 1.3 (half of 2.5 mm corrosion).
A_ref=max(.95*2*(t_side,net*D+t_inner,net*(D-h_db)), wave-required area).
The .95 effectiveness, DB tank-top proxy h_db and piecewise shear profile
are design assumptions. Maintain full midbody plating, reinforce quarter
bands by 20%, and taper to .60 at the ends. The wave-required fallback
reserves S11A wave shear with 10% margin independently of cargo. Neither Z
nor A replaces actual shear-flow, buckling, ultimate-strength or combined
stress checks. These synthetic limits are not class-approved scantlings.

S1 2.3 requires accepted read-out positions. Assess at all Gauss integration
nodes, a 1%-L grid, rule corners, quarter points and hatch/coaming ends/centres.
APPROXIMATION: coamings proxy bulkheads and mid-holds (actual frames absent);
these read-out points are not Society-approved. Finite assumed shear area at
the ends avoids artificial ratios caused by vanishing designer SW limits.

Hull coefficients come from the built offset solid at scantling draught,
including retained AP/FP overhang buoyancy. S11A bow flare uses bare-hull
polygon deck and waterplane projections on the forward 0.2L; coamings are
excluded. APPROXIMATION: integrate panel-linear width between the supplied
Gauss sections, including analytically clipped boundary panels. Keep all
overhang readouts. APPROXIMATION: extend the AP/FP wave and structural
allowances constantly beyond perpendiculars, assuming equal end scantlings;
actual hull buoyancy is never cropped to the rule diagram.

Signs follow Station: hogging BM positive; SF positive upward aft. The latter
is opposite S11A 2.1, so ``wave_loads`` converts the rule's shear signs.
All outputs use SI (N and N*m); the rule's kN and kN*m are multiplied by 1000.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from stowage.model import HullModel, Station
from stowage.vessel.hydrostatics import HullGeometry

GRAVITY_M_S2 = 9.80665
SECTION_MODULUS_DESIGN_MARGIN = 1.10
BENDING_PERMISSIBLE_PA = 235e6 / 1.24
SHEAR_PERMISSIBLE_PA = 235e6 / (1.13 * math.sqrt(3))
# APPROXIMATION: assumed effective web area and longitudinal shear strakes.
SHEAR_AREA_EFFECTIVENESS = 0.95
NET_DEDUCTION_MM = 1.25
PROFILE_U = (0., .10, .15, .30, .40, .60, .70, .85, .90, 1.)
PROFILE_P = (.60, .85, 1.20, 1.20, 1., 1., 1.20, 1.20, .85, .60)
METHOD = "APPROXIMATION: Z=1.10*max(S7 minimum,(S11A hog wave+S7-implied design SW hog)/sigma,(abs(S11A sag wave)+S7-implied design SW sag)/sigma); old S11 supplies design moments only, not container-ship rule limits; mild steel k=1; A_ref=max(.95*2*(t_side_net*D+t_inner_net*(D-h_db)),1.10*wave-required area); assumed gross thickness side=8+.04L mm, inner=6.5+.03L mm; S11A net deduction 1.25mm; assumed DB-tank-top height and piecewise quarter-strake profile; SW capacities deduct signed S11A waves; idealised section, local delta-F ignored; not class-approved scantlings"


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        if 'value' in value and 'unit' in value:
            return _plain(value['value'])
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def wave_parameter(length_m: float, waterplane_coefficient: float, *, shear: bool = False) -> float:
    """S11A 2.3.1, with separate BM/SF reference lengths."""
    if length_m <= 0 or waterplane_coefficient <= 0:
        raise ValueError('positive rule length and waterplane coefficient required')
    reference = (330.0 if shear else 315.0) * waterplane_coefficient ** -1.3
    root = math.sqrt(length_m / reference)
    return 1 - 1.50 * (1-root)**2.2 if length_m <= reference else 1 - 0.45 * (root-1)**1.7


def wave_loads(x_m: float, *, length_m: float, beam_m: float, draught_m: float,
               block_coefficient: float, waterplane_coefficient: float,
               bow_flare_coefficient: float) -> dict[str, float]:
    """Exact S11A 2.3 wave envelope, including Figs 6/7, in model signs.

    Returns ``hogging_nm``, ``sagging_nm``, ``max_shear_n``,
    ``min_shear_n``. Wave SF extrema are independent of local BM sign.
    """
    L, B, T, cb, cw = length_m, beam_m, draught_m, block_coefficient, waterplane_coefficient
    if not (90 <= L <= 500 and 5 <= L/B <= 9 and 2 <= B/T <= 6 and .55 <= cb <= .9):
        raise ValueError('outside S11A 1.1.2 wave-load applicability; direct wave calculation required')
    if not (0 <= x_m <= L and 0 < cw <= 1):
        raise ValueError('invalid station or waterplane coefficient')
    u = x_m / L
    nh = min(1.1, .3 * cb / cw * math.sqrt(T))
    ns = max(1.0, 4.5 * (1 + .2*bow_flare_coefficient) / (cw*math.sqrt(cb)*L**.3))
    mb = 1.5 * .85 * L**3 * wave_parameter(L, cw) * cw * (B/L)**.8 * 1000
    fb = .85 * L**2 * wave_parameter(L, cw, shear=True) * cw * (B/L)**.8 * 1000
    hog = mb * nh * float(np.interp(u, [0,.1,.35,.55,.8,1], [0,.15,1,1,.25,0]))
    sag = -mb * ns * float(np.interp(u, [0,.35,.6,1], [0,1,1,0]))
    aft_h, fore_h = 5.2*fb*(.3+.7*nh), -5.7*fb*nh
    aft_s, fore_s, mid = -5.2*fb*(.3+.7*ns), 5.7*fb*(.25+.75*ns), 4*fb
    # Positive/negative S11A Fig.7 curves, before the SF sign conversion.
    positive = float(np.interp(u, [0,.15,.3,.4,.55,.65,.85,1], [0,aft_h,aft_h,mid,mid,fore_s,fore_s,0]))
    negative = float(np.interp(u, [0,.15,.3,.4,.5,.6,.75,1], [.25*aft_s,aft_s,aft_s,-mid,-mid,fore_h,fore_h,0]))
    return dict(hogging_nm=hog, sagging_nm=sag, max_shear_n=-negative, min_shear_n=-positive)


def _horizontal_width(polygon: np.ndarray, height_m: float) -> float:
    """Width of the closed section at z, including its horizontal top edge."""
    if height_m < polygon[:, 1].min() or height_m > polygon[:, 1].max():
        return 0.0
    # At the watertight deck the usual half-open crossing test returns no
    # segment. Use the limiting width of the actual top closure instead.
    if height_m == polygon[:, 1].max():
        top = polygon[polygon[:, 1] == height_m, 0]
        return float(top.max()-top.min())
    from stowage.vessel.hydrostatics import _water_segments
    return sum(float(np.linalg.norm(b-a)) for a,b in _water_segments(polygon,0.,1.,height_m))


def _partial_gauss_integral(geometry: HullGeometry, values: np.ndarray,
                            start_m: float, end_m: float) -> float:
    """Integrate the two-node panel interpolant on a clipped x interval.

    Full panels exactly retain the supplied Gauss weights; panels intersected
    by 0.8L or FP use the analytic integral of that panel's linear interpolant.
    APPROXIMATION: width between the actual polygon stations is linear within
    a Gauss panel; refining geometry resolves waterline/section topology knees.
    """
    x, w = geometry.x_m, geometry.integration_weights_m
    total = 0.0
    for j in range(0,len(x),2):
        centre = (x[j]+x[j+1])/2
        left, right = max(start_m,centre-w[j]), min(end_m,centre+w[j])
        if right <= left:
            continue
        slope = (values[j+1]-values[j])/(x[j+1]-x[j])
        total += (right-left)*((values[j]+values[j+1])/2+slope*((left+right)/2-centre))
    return float(total)


def _bow_flare_coefficient(geometry: HullGeometry, draught_m: float) -> float:
    """S11A 2.3.2: (A_DK-A_WL)/(0.2 L (D-T)) from bare-hull polygons.

    A_DK is the horizontal projection of the uppermost hull deck closure,
    excluding appended coaming polygons. A_WL is its scantling-waterplane area
    on the forward 0.2L. No separable-family shape coefficients are consulted.
    """
    L, D = geometry.lpp_m, geometry.depth_m
    if D <= draught_m:
        raise ValueError('positive forecastle/deck freeboard required for S11A bow flare')
    deck = np.array([_horizontal_width(polys[0],float(polys[0][:,1].max()))
                     for polys in geometry.sections])
    water = np.array([_horizontal_width(polys[0],draught_m) for polys in geometry.sections])
    return _partial_gauss_integral(geometry,deck-water,.8*L,L)/(.2*L*(D-draught_m))


def _inputs(ship_params: Mapping[str, Any], hull: HullModel | HullGeometry):
    params = _plain(ship_params)
    geometry = hull if isinstance(hull, HullGeometry) else HullGeometry.from_params(params)
    # Geometry is the only source of hull-form coefficients and dimensions.
    # HullGeometry has no separate scantling draft: callers using geometry
    # directly supply the declared draft, while production uses HullModel.
    L, B, D = geometry.lpp_m, geometry.beam_m, geometry.depth_m
    T = float(params['hull']['scantling_draught_m']) if isinstance(hull,HullGeometry) else hull.scantling_draught_m
    if isinstance(hull,HullModel):
        if not np.allclose([L,B,D],[hull.lpp_m,hull.beam_m,hull.depth_m],rtol=0,atol=1e-9):
            raise ValueError('built hull dimensions disagree with source geometry')
    # APPROXIMATION: synthetic rule length equals Lpp (S2 rule length absent).
    # Include the whole offset solid, including retained AP/FP overhangs.
    wet = geometry.submerged(T,T,0.,geometry.density_kg_m3)
    cb, cw = wet.volume_m3/(L*B*T), wet.waterplane_area_m2/(L*B)
    kw = dict(length_m=L,beam_m=B,draught_m=T,block_coefficient=cb,
              waterplane_coefficient=cw,bow_flare_coefficient=_bow_flare_coefficient(geometry,T))
    wave_loads(L/2, **kw)
    return params, geometry, wet.displacement_kg*GRAVITY_M_S2, kw


def _rule_station(x_m: float, length_m: float) -> float:
    """APPROXIMATION: extend AP/FP structural budgets constantly over overhangs.

    S11A defines its wave diagrams on 0..L. Keep every buoyant source station
    and assess its actual SF/BM against the nearest perpendicular's wave and
    shear-area allowance, with the same effective net Z and material stresses.
    This assumes overhang scantlings retain those end structural capacities;
    it is not a class-approved overhang design or a cropped buoyancy model.
    """
    return min(length_m,max(0.,x_m))


def minimum_section_modulus_m3(length_m: float, beam_m: float,
                              block_coefficient: float) -> float:
    """UR S7.1 new-build minimum, ordinary hull steel k=1; SI result.

    Source: consolidated URS-2024.pdf PDF p32. S7 is a minimum midship
    standard, not a complete scantling design and not a net/gross guarantee.
    APPROXIMATION: use this numerical minimum as the effective net-Z baseline.
    """
    L = length_m
    if not (90 <= L <= 500) or beam_m <= 0 or not 0 < block_coefficient <= 1:
        raise ValueError('outside S7 section-modulus applicability')
    if L <= 300:
        c = 10.75 - ((300-L)/100)**1.5
    elif L < 350:
        c = 10.75
    else:
        c = 10.75 - ((L-350)/150)**1.5
    return c * L**2 * beam_m * (max(block_coefficient,.60)+.7) * 1e-6


def s7_implied_design_swbm_nm(length_m: float, beam_m: float,
                              block_coefficient: float) -> tuple[float, float]:
    """APPROXIMATION: recover design hog/sag SWBM implicit in S7 (N*m).

    UR S7.1 plus old UR S11.2.2.1/3.1.1 at sigma=175/k. S11 excludes
    container ships; these are assumed design loads, not S11A rule limits.
    """
    z_min = minimum_section_modulus_m3(length_m, beam_m, block_coefficient)
    cb = max(block_coefficient, .60)
    c_l2_b = z_min * 1e6 / (cb + .7)
    return c_l2_b * (122.5 - 15*cb), 65*c_l2_b*(cb+.7)


def assumed_shear_area_m2(x_m: float, length_m: float, reference_area_m2: float) -> float:
    """APPROXIMATION: assumed piecewise shear strakes, full midbody plating.

    The reference area is the effective midbody web area, not a peak area.
    End profile values extend constantly over retained AP/FP overhangs.
    """
    return reference_area_m2 * float(np.interp(x_m/length_m, PROFILE_U, PROFILE_P))


def _double_bottom_height_m(params: Mapping[str, Any], depth_m: float) -> float:
    """APPROXIMATION: DB height from declaration or synthetic DB/WB-series tank tops."""
    declared = params['hull'].get('double_bottom_height_m')
    if declared is not None:
        height = float(declared)
    else:
        tops = [float(t['geometry']['z_max_m']) for t in params.get('tanks', [])
                if t.get('kind') == 'ballast' and t.get('id','').startswith(('DB-', 'WB-'))
                and 'geometry' in t
                and float(t['geometry']['z_min_m']) < .5]
        if not tops:
            raise ValueError('double-bottom height or low ballast-tank geometry required')
        height = max(tops)
    if not 0 < height < depth_m:
        raise ValueError('double-bottom height must lie below hull depth')
    return height


def _assumed_scantlings(kw: Mapping[str,float], depth_m: float,
                        double_bottom_height_m: float) -> tuple[float,float]:
    """APPROXIMATION: synthetic Z and midbody A sized independently of cargo.

    For piecewise-linear waves/profile, their ratio is monotone on every
    merged interval; evaluating all merged knots gives the exact maximum.
    """
    L = kw['length_m']
    wave = wave_loads(L/2, **kw)
    z_min = minimum_section_modulus_m3(L,kw['beam_m'],kw['block_coefficient'])
    hog_design, sag_design = s7_implied_design_swbm_nm(L,kw['beam_m'],kw['block_coefficient'])
    Z = SECTION_MODULUS_DESIGN_MARGIN*max(
        z_min, (wave['hogging_nm']+hog_design)/BENDING_PERMISSIBLE_PA,
        (-wave['sagging_nm']+sag_design)/BENDING_PERMISSIBLE_PA)
    knots = sorted(set(PROFILE_U) | {0.,.15,.3,.4,.5,.55,.6,.65,.75,.85,1.})
    peak_required = max(
        max(w['max_shear_n'], -w['min_shear_n']) /
        (SHEAR_PERMISSIBLE_PA*assumed_shear_area_m2(u*L,L,1.))
        for u in knots for w in [wave_loads(u*L,**kw)])
    # APPROXIMATION: assumed mild-steel gross thicknesses; .95 effective
    # area represents I*sum(t)/S of an idealised double-skin section.
    side_net = (8. + .04*L - NET_DEDUCTION_MM)*1e-3
    inner_net = (6.5 + .03*L - NET_DEDUCTION_MM)*1e-3
    web_area = 2*(side_net*depth_m + inner_net*(depth_m-double_bottom_height_m))
    return Z, max(SHEAR_AREA_EFFECTIVENESS*web_area,
                  SECTION_MODULUS_DESIGN_MARGIN*peak_required)


def permissible_stations(ship_params: Mapping[str, Any], hull: HullModel | HullGeometry,
                         *, in_port: bool = False) -> list[Station]:
    """Residual S11A stress budgets; fleet assigns result to hull.stations.

    For unsupported breadths reject rather than imply a torsion assessment.
    Does not mutate the hull and does not build expensive KN tables.

    ``in_port=True`` opts into D64's per-sign harbour SWBM envelope; the
    seagoing shear limits are retained. The cargo-operations caller owns the
    rule-freeze activation switch and must select this mode only after it is on.
    """
    params, geometry, _, kw = _inputs(ship_params, hull)
    L = kw['length_m']
    if kw['beam_m'] > 32.26:
        raise ValueError('S11A 6.1 additional large-breadth/torsional assessment required')
    h_db = _double_bottom_height_m(params, geometry.depth_m)
    Z, reference_area = _assumed_scantlings(kw, geometry.depth_m, h_db)
    xs = {float(x) for x in geometry.x_m}
    xs.update((getattr(geometry,'buoyant_x_min_m',0.),getattr(geometry,'buoyant_x_max_m',L)))
    xs.update(L*i/100 for i in range(101))
    xs.update(L*v for v in (.1,.15,.2,.25,.3,.35,.4,.5,.55,.6,.65,.7,.75,.8,.85))
    for a,b,_ in params['hull']['lightship_distribution']:
        xs.update((float(a),float(b)))
    for box in params['envelope'].get('hatch_coamings',[]):
        a,b = float(box['x_min_m']),float(box['x_max_m'])
        xs.update((a,b,(a+b)/2))
    stations = []
    for x in sorted(xs):
        rule_x = _rule_station(x,L)
        wave = wave_loads(rule_x, **kw)
        bm_hog = BENDING_PERMISSIBLE_PA*Z - wave['hogging_nm']
        bm_sag = BENDING_PERMISSIBLE_PA*Z + wave['sagging_nm']
        if in_port:
            # APPROXIMATION: IRS Main Rules Pt3 Ch6 2.1.9 harbour form is
            # applied to D22's synthetic seagoing SWBM budget. Reserve each
            # sign's own half-wave allowance, never the larger other sign.
            bm_hog += .5*abs(wave['hogging_nm'])
            bm_sag += .5*abs(wave['sagging_nm'])
        # APPROXIMATION (D64 P8): harbour keeps the seagoing SF envelope
        # conservatively; the separate IRS harbour SF formula is unverified.
        sf_capacity = SHEAR_PERMISSIBLE_PA*assumed_shear_area_m2(rule_x,L,reference_area)
        sf_pos = sf_capacity-wave['max_shear_n']
        sf_neg = sf_capacity+wave['min_shear_n']
        if min(bm_hog,bm_sag,sf_pos,sf_neg) <= 0:
            raise ValueError('assumed girder has no positive residual still-water capacity')
        stations.append(Station(x_m=x,max_shear_n=sf_pos,min_shear_n=-sf_neg,
                                max_bending_nm=bm_hog,min_bending_nm=-bm_sag))
    return stations


def design_summary(ship_params: Mapping[str, Any], hull: HullModel | HullGeometry) -> dict[str, Any]:
    """Reviewable assumed scantlings and residual capacities, all SI."""
    params, geometry, weight, kw = _inputs(ship_params, hull)
    stations = permissible_stations(ship_params,hull)
    waves = [wave_loads(_rule_station(s.x_m,kw['length_m']),**kw) for s in stations]
    z_min = minimum_section_modulus_m3(kw['length_m'],kw['beam_m'],kw['block_coefficient'])
    h_db = _double_bottom_height_m(params, geometry.depth_m)
    Z, reference_area = _assumed_scantlings(kw, geometry.depth_m, h_db)
    mid_wave = wave_loads(kw['length_m']/2, **kw)
    return dict(method=METHOD,**kw,scantling_displacement_force_n=weight,
                station_count=len(stations), torsion_required=kw['beam_m']>32.26,
                rule_length_assumption='APPROXIMATION: L_rule=Lpp; S2 rule length not supplied',
                overhang_method='APPROXIMATION: retain overhang readouts; extend AP/FP wave and structural capacities constantly',
                bow_flare_method='bare-hull polygon deck/waterline projections on forward 0.2L; coamings excluded; panel-linear partial Gauss integral',
                steel='ordinary mild steel ReH=235 MPa; k=1',
                s7_minimum_section_modulus_m3=z_min,
                section_modulus_design_margin=SECTION_MODULUS_DESIGN_MARGIN,
                section_modulus_s7_multiplier=Z/z_min,
                section_modulus_method='APPROXIMATION: 1.10*max(S7 minimum,(wave hog+design SW hog)/sigma,(abs(wave sag)+design SW sag)/sigma); old S11 design SW is not a container-ship rule limit',
                assumed_design_swbm_hogging_nm=s7_implied_design_swbm_nm(kw['length_m'],kw['beam_m'],kw['block_coefficient'])[0],
                assumed_design_swbm_sagging_magnitude_nm=s7_implied_design_swbm_nm(kw['length_m'],kw['beam_m'],kw['block_coefficient'])[1],
                permissible_bending_stress_pa=BENDING_PERMISSIBLE_PA,
                total_bending_capacity_nm=BENDING_PERMISSIBLE_PA*Z,
                midship_sw_hogging_nm=BENDING_PERMISSIBLE_PA*Z-mid_wave['hogging_nm'],
                midship_sw_sagging_magnitude_nm=BENDING_PERMISSIBLE_PA*Z+mid_wave['sagging_nm'],
                depth_m=geometry.depth_m,
                assumed_double_bottom_height_m=h_db,
                double_bottom_height_method='APPROXIMATION: declared structural height or highest low-ballast-tank top',
                assumed_side_shell_gross_thickness_m=(8+.04*kw['length_m'])*1e-3,
                assumed_inner_skin_gross_thickness_m=(6.5+.03*kw['length_m'])*1e-3,
                side_shell_net_thickness_m=(8+.04*kw['length_m']-NET_DEDUCTION_MM)*1e-3,
                inner_skin_net_thickness_m=(6.5+.03*kw['length_m']-NET_DEDUCTION_MM)*1e-3,
                net_thickness_deduction_m=NET_DEDUCTION_MM*1e-3,
                assumed_shear_area_effectiveness=SHEAR_AREA_EFFECTIVENESS,
                permissible_pure_shear_stress_pa=SHEAR_PERMISSIBLE_PA,
                design_sw_peak_bm_nm=max(s.max_bending_nm for s in stations),
                design_sw_peak_hogging_nm=max(s.max_bending_nm for s in stations),
                design_sw_peak_sagging_magnitude_nm=max(-s.min_bending_nm for s in stations),
                design_sw_peak_sf_n=max(max(s.max_shear_n,-s.min_shear_n) for s in stations),
                wave_peak_hogging_nm=max(w['hogging_nm'] for w in waves),
                wave_peak_sagging_nm=min(w['sagging_nm'] for w in waves),
                assumed_effective_net_section_modulus_m3=Z,
                assumed_effective_net_shear_area_m2=reference_area,
                assumed_midbody_effective_net_shear_area_m2=reference_area,
                shear_area_profile='APPROXIMATION: piecewise-linear A=A_ref*p(x/L); full midbody, +20% quarter-point strakes, .60 end taper; local delta-F ignored',
                shear_area_profile_u=list(PROFILE_U), shear_area_profile_p=list(PROFILE_P),
                assessment_stations_m=[s.x_m for s in stations])
