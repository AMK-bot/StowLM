"""Affine loading-row assembly copied from engine/benders/ballast.py.

That engine file is copied from lab/c2/c2-benders/ballast.py. The local
_assemble function now supports a native GM-bias calibration as well as
draught/trim corrections; tank reference math is shared with affine_gm(). Half-fill tank arms/FSM and
local hull tangents are heuristic search restrictions, never an outward
physical certificate. Work-count LP execution lives in ballast_local.py.
"""
from __future__ import annotations
import math
import numpy as np


def _tank_reference(ctx, mode='half'):
    """The same half-fill tank arms/FSM used by every affine row and GM probe."""
    from stowage.checker.stability_sounding import sounding
    if mode not in ('half', 'pressed'):
        raise ValueError('tank reference mode must be half or pressed')
    arms, fsms = [], []
    for tank in ctx.tanks:
        point = sounding(ctx.solids[tank.id], (.5 if mode == 'half' else 1.)*tank.capacity_m3)
        arms.append([point.lcg_m, point.tcg_m, point.vcg_m])
        fsm = point.fsm_m4*tank.content_density_kg_m3 if mode == 'half' else 0.
        fsms.append(fsm if fsm/ctx.instance.vessel.hull.lightship_mass_kg >= .01 else 0.)
    return np.asarray(arms).reshape(len(ctx.tanks), 3), float(ctx.base.fsm_kgm)+sum(fsms)


def affine_gm(ctx, reference_drafts, fractions, *, tank_reference_mode='half'):
    """GM predicted by exactly the assembler's reference KM/half-fill math."""
    ta, tf = reference_drafts
    point = ctx.direct_geometry.full_submerged(ta, tf, 0., ctx.rho)
    km = point.vcb_m+point.bm_t_m
    arms, fsm = (_tank_reference(ctx) if tank_reference_mode == 'half' else
                 _tank_reference(ctx, mode=tank_reference_mode))
    masses = ctx.capacity*np.asarray(fractions, float)
    mass = float(ctx.base.mass_kg+sum(masses))
    moment_z = float(ctx.base_moments[2]+masses@arms[:, 2])
    return float(km-(moment_z+fsm)/mass)


def _gm_bound(minimum, bias):
    """Actual-GM ~= affine-GM+bias; all stiffness rows are in kg*m."""
    return float(minimum)-float(bias)


def _assemble(contexts, references=None, corrections=None, search_gm_floor=None,
              cargo_mass=None, cargo_moments=None, removed_cargo=None, auxiliary_quantum=None, tank_reference_mode='half', tcg_halfwidth_m=None,kg_caps_m=None):
    """Return affine LP rows; references are aft/forward draft pairs."""
    from stowage.checker import stability
    from stowage.checker.stability_sounding import sounding
    if tcg_halfwidth_m is not None and not (math.isfinite(tcg_halfwidth_m) and tcg_halfwidth_m >= 0.):
        raise ValueError('TCG search halfwidth must be finite and nonnegative')
    kg_caps={} if kg_caps_m is None else dict(kg_caps_m)
    if set(kg_caps)-{phase for phase,_ in contexts} or any(not math.isfinite(float(v)) or float(v)<=0. for v in kg_caps.values()):
        raise ValueError('KG search caps require known phases and positive finite metres')
    n = len(contexts[0][1].tanks)
    nc = 0 if cargo_mass is None else len(cargo_mass)
    if nc:
        cargo_mass = np.asarray(cargo_mass, float)
        cargo_moments = np.asarray(cargo_moments, float)
        removed_cargo = np.asarray(removed_cargo, float)
        if cargo_mass.shape != (nc,) or cargo_moments.shape != (nc, 3) or removed_cargo.shape != (len(contexts), 4):
            raise ValueError('joint cargo coefficients/removed cargo have inconsistent dimensions')
        if not all(np.isfinite(v).all() for v in (cargo_mass, cargo_moments, removed_cargo)):
            raise ValueError('joint cargo coefficients must be finite')
    variables = nc+n+2*len(contexts)
    if auxiliary_quantum is not None:
        auxiliary_quantum = np.asarray(auxiliary_quantum, float)
        if auxiliary_quantum.shape != (n+2*len(contexts),) or not np.isfinite(auxiliary_quantum).all() or np.any(auxiliary_quantum <= 0.):
            raise ValueError('auxiliary grid must have one positive finite quantum per tank/draft')
    rows, rhs, names, calibration = [], [], [], []
    equality_bands = {}

    def upper(row, bound, name):
        if math.isfinite(bound):
            scale = max(float(np.max(np.abs(row))), abs(float(bound)), 1.)
            rows.append(row/scale); rhs.append(float(bound)/scale); names.append(name)

    def equality(row, value, name):
        # Declared construction grid band; policy limits are not relaxed.
        # One full auxiliary grid step allows a nearby continuous loading
        # equilibrium to survive quantization of tanks and draught variables.
        tolerance = float(np.abs(row[nc:])@auxiliary_quantum) if auxiliary_quantum is not None else 0.
        equality_bands[name] = tolerance
        upper(row, value+tolerance, name+'/positive')
        upper(-row, -value+tolerance, name+'/negative')

    for j in range(n):
        row = np.zeros(variables); row[nc+j] = 1.
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
        arms, fsm = (_tank_reference(ctx) if tank_reference_mode == 'half' else
                     _tank_reference(ctx, mode=tank_reference_mode))
        # A local native GM offset may correct the half-fill FSM reserve.
        # This is search calibration, never a necessary feasibility row.
        km = reference.vcb_m+reference.bm_t_m
        c = ctx.capacity
        base = float(ctx.base.mass_kg)-(float(removed_cargo[k, 0]) if nc else 0.)
        mx, my, mz = np.asarray(ctx.base_moments, float)-(removed_cargo[k, 1:] if nc else 0.)
        tank = slice(nc, nc+n)
        mass_row = np.zeros(variables); mass_row[tank] = c
        xrow = np.zeros(variables); xrow[tank] = c*arms[:, 0]
        yrow = np.zeros(variables); yrow[tank] = c*arms[:, 1]
        zrow = np.zeros(variables); zrow[tank] = c*arms[:, 2]
        if nc:
            mass_row[:nc] = cargo_mass
            xrow[:nc], yrow[:nc], zrow[:nc] = cargo_moments.T
        draft = slice(nc+n+2*k, nc+n+2*k+2)
        r = mass_row.copy(); r[draft] -= db
        delta_mass = float((corrections or {}).get(phase+'/buoyancy_kg', 0.))
        equality(r, buoyancy-db@[ta, tf]-base+delta_mass, phase+'/buoyancy')
        r = xrow.copy(); r[draft] -= dm
        delta_moment = float((corrections or {}).get(phase+'/longitudinal_kgm', 0.))
        equality(r, moment-dm@[ta, tf]-mx+delta_moment, phase+'/longitudinal_equilibrium')
        lo_trim, hi_trim, lo_mass, hi_mass = stability._loading_policy(inst)
        upper(mass_row, hi_mass-base, phase+'/mass_upper')
        upper(-mass_row, base-lo_mass, phase+'/mass_lower')
        trim = np.zeros(variables); trim[nc+n+2*k] = 1.; trim[nc+n+2*k+1] = -1.
        offset = (corrections or {}).get(phase+'/trim', 0.)
        upper(trim, hi_trim-offset-.01, phase+'/trim_upper')
        upper(-trim, -lo_trim+offset-.01, phase+'/trim_lower')
        maximum = min(hull.max_draught_m, inst.port.max_draught_m or hull.max_draught_m)
        for end in (0, 1):
            r = np.zeros(variables); r[nc+n+2*k+end] = 1.
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
        gm_bias = float((corrections or {}).get(phase+'/gm', 0.))
        required_affine_gm = _gm_bound(minimum_gm, gm_bias)
        stiffness = km*mass_row-zrow
        fixed_stiffness = km*base-mz-fsm
        upper(required_affine_gm*mass_row-stiffness,
              fixed_stiffness-required_affine_gm*base, phase+'/gm')
        tangent = math.tan(hull.max_list_rad)
        upper(yrow-tangent*stiffness, tangent*fixed_stiffness-my, phase+'/heel_positive')
        upper(-yrow-tangent*stiffness, tangent*fixed_stiffness+my, phase+'/heel_negative')
        if tcg_halfwidth_m is not None:
            tau = float(tcg_halfwidth_m)
            # Optional inward SEARCH window on total TCG, including modeled
            # cargo, fixed background and variable tank masses exactly once.
            upper(yrow-tau*mass_row, tau*base-my, phase+'/tcg_upper')
            upper(-yrow-tau*mass_row, tau*base+my, phase+'/tcg_lower')
        kg_cap=kg_caps.get(phase)
        if kg_cap is not None:
            kg_cap=float(kg_cap)
            # Total KG includes fixed background, modeled cargo and variable
            # tanks exactly once. This is an optional inward search bound.
            upper(zrow-kg_cap*mass_row,kg_cap*base-mz,phase+'/kg_upper')
        calibration.append(dict(phase=phase, reference_drafts_m=[ta, tf],
            buoyancy_kg=buoyancy, buoyancy_derivative_kg_m=db.tolist(),
            longitudinal_derivative_kg=dm.tolist(), km_m=km, fsm_reserve_kgm=fsm,
            native_gm_bias_m=gm_bias, requested_gm_floor_m=minimum_gm,
            required_affine_gm_m=required_affine_gm,
            tank_reference_mode=tank_reference_mode,
            tcg_search_halfwidth_m=tcg_halfwidth_m,
            kg_search_cap_m=kg_cap,
            native_buoyancy_anchor_kg=delta_mass,
            native_longitudinal_anchor_kgm=delta_moment,
            pair_columns=nc, removed_cargo_kg_kgm=removed_cargo[k].tolist() if nc else None,
            equality_bands_unscaled={name: value for name, value in equality_bands.items() if name.startswith(phase+'/')}))
    return np.asarray(rows), np.asarray(rhs), names, calibration
