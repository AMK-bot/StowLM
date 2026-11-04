# Frozen pre-performance curve-orchestration reference; test-only.
# Copied byte-for-byte below these comments from the executive baseline
# /private/tmp/stability-perf-before/stability_curve.py, 2026-10-27.
# Original source SHA-256: c8b5e1957baf4923d5d5125e0766bb618b7fd81482d45259aa73ba66062ac734
# Both reference and production deliberately use the same current numerical
# kernels, so the portable sweep isolates orchestration/state-reuse changes.
# Do not update this oracle when changing production orchestration.
"""Bound intact-stability work by real flooding and positive stability.

IS Code A2.3 limits the leeward weather area by flooding, 50 degrees and the
second gust intercept. D21 caps B2.3 total work at flooding or first vanishing
stability. Neither needs a spurious near-horizontal floating condition. The
windward rolling branch is independent of leeward flooding: within A2.3.5's
applicability range phi1 <=109*sqrt(1.03*0.1)=34.99 degrees, so 35 degrees
of windward coverage is retained even when the leeward endpoint is earlier.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np
from scipy.optimize import brentq

from stowage.checker.stability_loading import curve_correction, liquid_cg
from stowage.checker.stability_budget import check_budget
from stowage.checker.stability_tables import OutsideTable


@dataclass
class StabilityCurve:
    angles: np.ndarray
    gz: np.ndarray
    negative_gz: np.ndarray
    drafts: np.ndarray
    flooding_angle: float
    at_angle: object


def build_stability_curve(geometry, condition, density, *, initial_drafts=None, correction_solver=None,
                          free_surface_method=1):
    """Return a corrected free-trim curve without solving irrelevant 90° heel.

    Sparse free-trim solves locate the first progressive-flooding opening.
    Successive curve solves then stop once leeward criteria and windward roll
    have coverage. A sealed envelope retains its labelled 90-degree flooding
    assumption, but needs a resolved vanishing angle within the solver domain.
    """
    L = geometry.lpp_m
    correction_solver = correction_solver or (lambda c, a, t: curve_correction(c, a, t, method=free_surface_method))
    if free_surface_method == 2:
        # Use the same virtual-rise centre in free-trim and floating equilibrium.
        # Raw GZ retains physical KG; subtract FSC*sin(phi) exactly once below.
        callback = lambda phi,trim:(condition.lcg_m,0.,condition.kg_m+condition.fsc_m)
    else:
        callback = ((lambda phi,trim:liquid_cg(condition,phi,trim)) if condition.actual_tanks else None)
    cache = {}
    raw_cache = {}
    def evaluate(phi, raw, ds):
        check_budget("liquid-transfer curve")
        raw_cache[float(phi)] = float(raw)
        correction = float(correction_solver(condition,[phi],math.atan((ds[0]-ds[1])/L))[0])
        tcg = abs(condition.tcg_m)*geometry._axes(*ds,phi)[2][1]
        arm = float(raw-correction-tcg)
        return arm, float(-raw+correction-tcg), tuple(ds)
    def at_angle(phi):
        check_budget("free-trim curve")
        phi = float(phi)
        if phi not in cache:
            seed = (cache[min(cache,key=lambda old:abs(old-phi))][2] if cache else initial_drafts)
            arms,ds = geometry.free_trim_curve([phi],condition.mass_kg,condition.lcg_m,
                condition.kg_m,density,cg_callback=callback,initial_drafts=seed)
            cache[phi] = evaluate(phi,float(arms[0]),ds[0])
        return cache[phi][0],cache[phi][2]
    at_angle(0.)
    protected = {'weathertight','watertight','weathertight_closable','watertight_closable'}
    openings = [o for o in geometry.openings if o.get('progressive_flooding',True)
                and o.get('closure') not in protected]
    def clearance(phi):
        ta,tf = at_angle(phi)[1]
        return min(float(o['z_m'])-(ta+(tf-ta)*float(o['x_m'])/L
                                   +abs(float(o['y_m']))*math.tan(phi)) for o in openings)
    # APPROXIMATION: no declared unprotected opening means a sealed 90-degree
    # flooding endpoint. No constant-GZ tail to that endpoint is fabricated.
    phi_f = math.pi/2
    if openings:
        if clearance(0.) <= 0:
            phi_f = 0.
        else:
            previous = 0.
            for phi in np.radians(np.r_[np.arange(5.,90.,5.),88.,89.9]):
                try:
                    opening_clearance=clearance(float(phi))
                except OutsideTable:
                    # A coarse probe may overshoot flooding into an irrelevant
                    # state outside the KN domain. Search only between the last
                    # genuinely dry solve and the failed probe for a genuinely
                    # wet solve; no invented endpoint/clearance enters brentq.
                    dry,unavailable=previous,float(phi)
                    for _ in range(24):
                        check_budget("flooding boundary refinement")
                        trial=(dry+unavailable)/2
                        try:
                            opening_clearance=clearance(trial)
                        except OutsideTable:
                            unavailable=trial
                            continue
                        if opening_clearance<=0:
                            phi_f=float(brentq(clearance,dry,trial,xtol=1e-9))
                            break
                        dry=trial
                    else:
                        # There is no measured wet bracket before the actual
                        # coverage boundary. Retain the original OutsideTable
                        # so the checker performs its bounded direct fallback.
                        raise
                    break
                if opening_clearance <= 0:
                    phi_f = float(brentq(clearance,previous,float(phi),xtol=1e-9))
                    break
                previous = float(phi)
            else:
                raise ValueError('declared downflooding openings not immersed within upward-normal solver domain (89.9 degrees)')
    windward = math.radians(35.)
    # The total B-area ends at vanishing stability, but the separate 0–40
    # and 30–40 clauses still require their full specified range, including
    # any negative work before flooding. Never silently shorten those areas.
    area_end = min(phi_f,math.radians(40.))
    required_coverage = max(windward,area_end)
    end = max(windward,min(phi_f,math.radians(89.9)))
    angles = np.unique(np.r_[np.arange(0.,end,math.radians(2.)),
                            [a for a in np.radians([25.,30.,35.,40.,50.]) if a <= end],end])
    if phi_f <= end:
        angles = np.unique(np.r_[angles,phi_f])
    # Criteria are deliberately integrated from geometric upright on the
    # conservative worst-TCG side (labelled in stability.py).
    vanished = False
    had_positive = False
    def stop(phi,raw,ds):
        nonlocal vanished,had_positive
        cache[float(phi)] = evaluate(float(phi),raw,ds)
        arm = cache[float(phi)][0]
        vanished |= had_positive and arm <= 0
        had_positive |= arm > 0
        return phi >= required_coverage-1e-12 and (vanished or phi >= phi_f-1e-12)
    raw,ds = geometry.free_trim_curve(angles,condition.mass_kg,condition.lcg_m,
        condition.kg_m,density,cg_callback=callback,initial_drafts=cache[0.][2],stop_when=stop)
    angles = angles[:len(raw)]
    gz = np.array([cache[float(phi)][0] for phi in angles])
    negative = np.array([cache[float(phi)][1] for phi in angles])
    for i in range(len(angles)-1):
        if gz[i] > 0 and gz[i+1] <= 0:
            phi_v = float(brentq(lambda phi:at_angle(phi)[0],float(angles[i]),float(angles[i+1]),xtol=1e-9))
            at_angle(phi_v)
            j = int(np.searchsorted(angles,phi_v))
            if not np.any(np.isclose(angles,phi_v,rtol=0.,atol=1e-10)):
                angles = np.insert(angles,j,phi_v)
                gz = np.insert(gz,j,0.)
                negative = np.insert(negative,j,cache[phi_v][1])
                ds = np.insert(ds,j,cache[phi_v][2],axis=0)
            # Retain only the root and any independent windward coverage.
            # The coarse step that first crossed zero is not an assessment
            # endpoint and must not keep a post-capsize leeward tail alive.
            keep = angles <= max(required_coverage,min(phi_f,phi_v))+1e-10
            angles,gz,negative,ds = angles[keep],gz[keep],negative[keep],ds[keep]
            break
    if free_surface_method == 1 and condition.actual_tanks:
        # Asymmetric fills need their actual windward liquid transfer. Mirroring
        # the leeward correction is valid only for a symmetric liquid condition.
        # The hull itself is bilateral. Its positive free-trim solution can be
        # reflected only when the longitudinal mass moments (LCG/VCG) agree.
        for i,phi in enumerate(angles):
            trim=math.atan((ds[i][0]-ds[i][1])/L)
            plus=liquid_cg(condition,float(phi),trim)
            minus=liquid_cg(condition,-float(phi),trim)
            if getattr(geometry,'_stability_table_bilateral',False) and np.allclose(
                    np.asarray(plus)[[0,2]],np.asarray(minus)[[0,2]],rtol=0.,atol=1e-7):
                raw_negative=-raw_cache[float(phi)]
                negative_drafts=ds[i]
            else:
                arms,negative_states=geometry.free_trim_curve([-float(phi)],condition.mass_kg,
                    condition.lcg_m,condition.kg_m,density,cg_callback=callback,initial_drafts=ds[i])
                raw_negative=float(arms[0]);negative_drafts=negative_states[0]
            negative_trim=math.atan((negative_drafts[0]-negative_drafts[1])/L)
            correction=float(correction_solver(condition,[-float(phi)],negative_trim)[0])
            tcg=abs(condition.tcg_m)*geometry._axes(*negative_drafts,-float(phi))[2][1]
            negative[i]=raw_negative-correction-tcg
    return StabilityCurve(angles,gz,negative,ds,phi_f,at_angle)
