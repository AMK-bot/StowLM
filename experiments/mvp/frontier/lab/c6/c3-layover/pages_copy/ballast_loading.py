"""Affine loading-row assembly copied from engine/benders/ballast.py.

That engine file is copied from lab/c2/c2-benders/ballast.py. Only the
unchanged _assemble function is retained here. Half-fill tank arms/FSM and
local hull tangents are heuristic search restrictions, never an outward
physical certificate. Work-count LP execution lives in ballast_local.py.
"""
from __future__ import annotations
import math
import numpy as np


def _assemble(contexts, references=None, corrections=None, search_gm_floor=None):
    """Return affine LP rows; references are aft/forward draft pairs."""
    from stowage.checker import stability
    from stowage.checker.stability_sounding import sounding
    n = len(contexts[0][1].tanks)
    variables = n+2*len(contexts)
    rows, rhs, names, calibration = [], [], [], []

    def upper(row, bound, name):
        if math.isfinite(bound):
            scale = max(float(np.max(np.abs(row))), abs(float(bound)), 1.)
            rows.append(row/scale); rhs.append(float(bound)/scale); names.append(name)

    def equality(row, value, name):
        upper(row, value, name+'/positive')
        upper(-row, -value, name+'/negative')

    for j in range(n):
        row = np.zeros(variables); row[j] = 1.
        upper(row, 1., f'tank_{j}/upper'); upper(-row, 0., f'tank_{j}/lower')
    for k, (phase, ctx) in enumerate(contexts):
        hull, inst, geometry = ctx.instance.vessel.hull, ctx.instance, ctx.direct_geometry
        ta, tf = references[k] if references else (hull.design_draught_m, hull.design_draught_m)
        reference = geometry.full_submerged(ta, tf, 0., ctx.rho)
        perturbation = .002
        shifted = [geometry.full_submerged(ta+perturbation, tf, 0., ctx.rho),
                   geometry.full_submerged(ta, tf+perturbation, 0., ctx.rho)]
        buoyancy = reference.displacement_kg
        moment = buoyancy*reference.lcb_m
        db = np.array([(s.displacement_kg-buoyancy)/perturbation for s in shifted])
        dm = np.array([(s.displacement_kg*s.lcb_m-moment)/perturbation for s in shifted])
        arms, fsms = [], []
        for tank in ctx.tanks:
            point = sounding(ctx.solids[tank.id], .5*tank.capacity_m3)
            arms.append([point.lcg_m, point.tcg_m, point.vcg_m])
            fsm = point.fsm_m4*tank.content_density_kg_m3
            fsms.append(fsm if fsm/hull.lightship_mass_kg >= .01 else 0.)
        arms = np.asarray(arms)
        # Keep the half-fill FSM as a constant reserve. It is neither a
        # global maximum nor an outward necessary row. Full/empty exact
        # candidates recover the true discontinuous correction on recheck.
        fsm = float(ctx.base.fsm_kgm)+sum(fsms)
        km = reference.vcb_m+reference.bm_t_m
        c = ctx.capacity
        base = ctx.base.mass_kg
        mx, my, mz = ctx.base_moments
        mass_row = np.zeros(variables); mass_row[:n] = c
        xrow = np.zeros(variables); xrow[:n] = c*arms[:, 0]
        yrow = np.zeros(variables); yrow[:n] = c*arms[:, 1]
        zrow = np.zeros(variables); zrow[:n] = c*arms[:, 2]
        draft = slice(n+2*k, n+2*k+2)
        r = mass_row.copy(); r[draft] -= db
        equality(r, buoyancy-db@[ta, tf]-base, phase+'/buoyancy')
        r = xrow.copy(); r[draft] -= dm
        equality(r, moment-dm@[ta, tf]-mx, phase+'/longitudinal_equilibrium')
        lo_trim, hi_trim, lo_mass, hi_mass = stability._loading_policy(inst)
        upper(mass_row, hi_mass-base, phase+'/mass_upper')
        upper(-mass_row, base-lo_mass, phase+'/mass_lower')
        trim = np.zeros(variables); trim[n+2*k] = 1.; trim[n+2*k+1] = -1.
        offset = (corrections or {}).get(phase+'/trim', 0.)
        upper(trim, hi_trim-offset-.01, phase+'/trim_upper')
        upper(-trim, -lo_trim+offset-.01, phase+'/trim_lower')
        maximum = min(hull.max_draught_m, inst.port.max_draught_m or hull.max_draught_m)
        for end in (0, 1):
            r = np.zeros(variables); r[n+2*k+end] = 1.
            offset = (corrections or {}).get(phase+f'/draft_{end}', 0.)
            upper(r, maximum-offset-.01, phase+f'/draft_{end}_upper')
            upper(-r, -(hull.min_draught_fwd_m if end else 0.)+offset-.01,
                  phase+f'/draft_{end}_lower')
        minimum_gm = max(stability._minimum_loading_gm(inst),
                         float(inst.rule_overrides.get('iscode.a2_2.initial_gm', {}).get('minimum', .15)))
        if search_gm_floor is not None:
            # Optional construction restriction. It is deliberately inward
            # and cannot enter mass_relaxation or justify any physical proof.
            minimum_gm = max(minimum_gm, search_gm_floor)
        stiffness = km*mass_row-zrow
        fixed_stiffness = km*base-mz-fsm
        upper(minimum_gm*mass_row-stiffness,
              fixed_stiffness-minimum_gm*base, phase+'/gm')
        tangent = math.tan(hull.max_list_rad)
        upper(yrow-tangent*stiffness, tangent*fixed_stiffness-my, phase+'/heel_positive')
        upper(-yrow-tangent*stiffness, tangent*fixed_stiffness+my, phase+'/heel_negative')
        calibration.append(dict(phase=phase, reference_drafts_m=[ta, tf],
            buoyancy_kg=buoyancy, buoyancy_derivative_kg_m=db.tolist(),
            longitudinal_derivative_kg=dm.tolist(), km_m=km, fsm_reserve_kgm=fsm))
    return np.asarray(rows), np.asarray(rhs), names, calibration
