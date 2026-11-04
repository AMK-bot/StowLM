"""Assignment/ballast rows for a checker-gated constructive MIP.

A binary ``x[box, slot]`` is one exactly when that real box occupies that slot.
Splitting a long box's *coefficient* over its two physical cells does not split
cargo: one binary still chooses the complete long slot. ``linear_constraints``
owns the D49 direction, all limits, measured reserves and domain restrictions.
This adapter substitutes those cell masses into its existing affine rows.

APPROXIMATION: 3.7 is a locally calibrated physics linearisation, not the exact
hull. D57 samples departure/midpoint/arrival rather than the continuous voyage.
The securing force envelope below is deliberately conservative within D56's
synthetic design. Every returned candidate still needs the full exact checker.
Necessary D49 rows are empirical screens, never a rigorous instance certificate
(see solver.ballast_proof). Domain restrictions and securing envelopes are not
valid infeasibility evidence. Sources: Pacino et al. (2011), Fast generation of
near-optimal plans for eco-efficient stowage of large container vessels;
https://developers.google.com/optimization/mip/mip_example .
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from time import monotonic
from typing import Any, Literal

import numpy as np

from stowage.conventions import cells_of_slot
from stowage.linear import build_linear_model, linear_constraints
from stowage.model.plan import BallastCondition
from stowage.model.voyage import arrival_remaining_fraction
from stowage.solver.ballast_arrival import arrival_condition, mid_voyage_condition


def _deadline(deadline):
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError("construct physics assembly exhausted its wall budget")


@dataclass
class ConstructPhysics:
    """Solver handles and explicit limits on what an infeasible solve means."""

    ballast_vars: dict[str, Any]
    direction: str
    state_names: tuple[str, ...]
    row_names: list[str] = field(default_factory=list)
    certificate_eligible: bool = False
    certificate_blockers: tuple[str, ...] = (
        "D49 necessary/conservative residual envelopes are empirical, not uniform proofs",
        "3.7 validity-band restrictions need not cover every checker-valid condition",
    )
    approximations: tuple[str, ...] = (
        "3.7 affine hull/tank stability and strength with D49 directional reserves",
        "D57 departure, midpoint and arrival samples of proportional draw-down",
        "D56 upper-GM proxy uses zero FSM and an absolute measured prediction guard",
        "D56 securing force/moment envelopes discard relief and bound rated geometry",
    )

    def ballast_condition(self) -> BallastCondition:
        """Extract only adjustable departure fills after a feasible MIP solve."""
        return BallastCondition(tank_masses_kg={
            tid: max(0., float(variable.solution_value()))
            for tid, variable in self.ballast_vars.items()
        }, source="planner")

    # Named alias is convenient for planner adapters.
    ballast_plan = ballast_condition


def _scaled_row(solver, terms, constant, lower, upper, name):
    """Use SI coefficients, normalising a complete row to avoid huge SF/BM RHS."""
    terms = [(variable, float(value)) for variable, value in terms if value]
    values = [float(constant), *(float(v) for v in (lower, upper) if np.isfinite(v))]
    scale = max(1., *(abs(value) for value in values),
                *(abs(value) for _, value in terms))
    lo = (float(lower)-constant)/scale if np.isfinite(lower) else -solver.infinity()
    hi = (float(upper)-constant)/scale if np.isfinite(upper) else solver.infinity()
    row = solver.RowConstraint(lo, hi, name)
    for variable, value in terms:
        row.SetCoefficient(variable, value/scale)
    return row


def _contexts(instance):
    """Use the same D57 identities, fractions and absent next-berth policy."""
    if instance.voyage is None:
        raise ValueError("construct physics requires the checker's explicit D57 voyage")
    departure = instance.onboard
    arrival, _ = arrival_condition(instance, departure)
    middle, _ = mid_voyage_condition(instance, departure)
    r = arrival_remaining_fraction(departure, instance.voyage, vessel=instance.vessel)
    return (("departure", instance, 1.), ("mid_voyage", middle, (1.+r)/2.),
            ("arrival", arrival, r))


def add_construct_physics(solver, instance, x, *, mode: Literal["conservative", "necessary"] = "conservative",
                          securing_proxy: bool = True, deadline: float | None = None) -> ConstructPhysics:
    """Add shared continuous ballast and the 3.7 rows at all three D57 states.

    ``x`` maps ``(container_id, D18 slot_id)`` to MIP binary variables. Other
    retained onboard placements are constants. Fixed-load aggregates are already
    in 3.7 constants; they must never be added a second time as cargo masses.
    Nonadjustable consumables are fixed D57 fractions of their departure fills.
    An unusual adjustable consumable under explicit kg consumption is fixed at
    its input departure fill, because that policy's common fraction otherwise
    depends nonlinearly on the optimisation variables. This local restriction
    cannot contribute to a certificate.
    """
    if mode not in ("conservative", "necessary"):
        raise ValueError("construct physics mode must be conservative or necessary")
    states = _contexts(instance)
    tanks = {tank.id: tank for tank in instance.vessel.tanks}
    policy = instance.vessel.voyage_policy
    consumables = {tid for group in policy.groups for tid in group.tank_ids}
    ballast = {}
    for tid, tank in sorted(tanks.items()):
        if not tank.adjustable:
            continue
        capacity = float(tank.capacity_m3*tank.content_density_kg_m3)
        lower, upper = 0., capacity
        if tid in consumables and instance.voyage.consumption is not None:
            lower = upper = float(instance.onboard.tank_masses_kg.get(tid, 0.))
        ballast[tid] = solver.NumVar(lower, upper, "construct_ballast@"+tid)
    result = ConstructPhysics(ballast, mode, tuple(name for name, _, _ in states))
    boxes = {box.id: box for box in instance.containers}
    moving = {cid for cid, _ in x}
    fixed = [p for p in instance.onboard.placements
             if p.container_id not in instance.discharge_list and p.container_id not in moving]
    footprints = {slot: cells_of_slot(slot) for _, slot in x}
    for phase, context, fraction in states:
        _deadline(deadline)
        model = build_linear_model(context)
        _deadline(deadline)
        rows = linear_constraints(model, direction=mode)
        ci = {cid: i for i, cid in enumerate(model.cell_ids)}
        fixed_cargo = np.zeros(len(ci), dtype=float)
        for p in fixed:
            footprint = cells_of_slot(p.slot)
            for cell in footprint:
                fixed_cargo[ci[cell]] += boxes[p.container_id].gross_mass_kg/len(footprint)
        used_names = set()
        for row_index, name in enumerate(rows.names):
            _deadline(deadline)
            cell = rows.a_cell[row_index]
            constant = float(rows.const[row_index] + cell@fixed_cargo)
            terms = []
            # The geometric coefficient belongs to a slot, not an individual
            # box; share its lookup across the manifest's binary choices.
            slot_coefficient = {slot: sum(cell[ci[q]] for q in footprint)/len(footprint)
                                for slot, footprint in footprints.items()}
            for (cid, slot), variable in x.items():
                value = boxes[cid].gross_mass_kg*slot_coefficient[slot]
                if value:
                    terms.append((variable, value))
            for tank_index, tid in enumerate(model.tank_ids):
                if tid in ballast:
                    factor = fraction if tid in consumables else 1.
                    terms.append((ballast[tid], float(rows.a_tank[row_index, tank_index])*factor))
            full_name = f"construct/{phase}/{name}"
            # 3.7 formats stations with :g, so physically distinct nearby
            # stations can share a display name. Keep both rows and give the
            # solver unique identifiers (LookupConstraint otherwise aborts).
            if full_name in used_names:
                full_name += f"#row{row_index}"
            used_names.add(full_name)
            _scaled_row(solver, terms, constant, rows.lb[row_index], rows.ub[row_index], full_name)
            result.row_names.append(full_name)
        if mode == "conservative" and securing_proxy:
            _upper_design_gm(solver, context, model, x, fixed_cargo, ci, footprints,
                             ballast, consumables, fraction, phase, result)
    if mode == "conservative" and securing_proxy:
        _deadline(deadline)
        _securing_envelopes(solver, instance, x, fixed, result)
    return result


def _upper_design_gm(solver, instance, model, x, fixed_cargo, ci, footprints,
                     ballast, consumables, fraction, phase, result):
    """Zero-FSM affine stiffness plus absolute point error bounds fluid GM above.

    APPROXIMATION: this is an empirical conservative proxy, since 3.7's absolute
    error envelope is measured. Zero FSM is deliberately used for this *upper*
    bound, while 3.7's minimum-GM rows use maximum FSM for their lower bound.
    Reusing maximum FSM here would reverse conservatism and permit excessive GM.
    The absolute point error includes the source model's mixed-height guard.
    """
    from stowage.checker.lashing import _lashing_design_gm
    design = _lashing_design_gm(instance.vessel.hull)
    a, b = model.km_line
    reserve = float(model.errors.gm_m)
    coefficient = b-design+reserve
    cell = coefficient-model.cell_vcg_m
    constant = a+coefficient*model.const_mass_kg-model.const_moment_z_kgm+cell@fixed_cargo
    boxes = {box.id: box for box in instance.containers}
    terms = []
    for (cid, slot), variable in x.items():
        footprint = footprints[slot]
        value = boxes[cid].gross_mass_kg*sum(cell[ci[q]] for q in footprint)/len(footprint)
        terms.append((variable, value))
    for i, tid in enumerate(model.tank_ids):
        if tid in ballast:
            factor = fraction if tid in consumables else 1.
            terms.append((ballast[tid], (coefficient-model.tank_vcg_m[i])*factor))
    name = f"construct/{phase}/APPROXIMATION_lashing_design_gm_upper"
    _scaled_row(solver, terms, float(constant), -np.inf, 0., name)
    result.row_names.append(name)


def _securing_envelopes(solver, instance, x, fixed, result):
    """Linear force/moment upper envelopes on planned physical columns.

    APPROXIMATION: conservative proxy for the checker's synthetic D56 securing,
    not a replacement for its elastic spring/DAG calculations. D54's own motion
    bound supplies maximum accelerations for any draught and GM <= design GM.
    Every physical column bounds a complete possible end-frame chain. Long-box
    mass is NOT halved again: its one end receives half the whole-box load.

    For deck chains, positive equivalent joint loads sum to
    Q=.45*F_bottom+.5*W_bottom+sum_upper(F+W), since the checker's
    default CG is .45H and every upper box enters adjacent joint equations.
    A passive rod's horizontal reaction and absolute frame racking are <= Q;
    its vertical reaction is <= Q*tan(angle). Triangle inequalities bound corner
    shear. For any tier |M-R*z| <= max(M,R*z) for nonnegative M and R*z, so two
    linear rows bound compression/tension without relying on relieving rods.
    All moment arms use inventory/stack upper heights, all tare floors and wind
    floors are the exact checker's. Hold guides use D54's rated mass/height
    envelope and a separate vertical-corner/fitting bound.

    Background-only external columns use their separately audited D42 geometry;
    they are not decision columns and remain entirely under full-checker control.
    The numeric 30500 kg box rating, 150/75 kN transverse/longitudinal racking,
    225 kN rod-vertical and 848 kN post limits, and 85% fitting derating below
    are immutable synthetic IRS rule constants from checker.lashing._check_lashing
    (mass loop, deck _end_response/record calls and hold vertical calls). The
    model exposes no per-instance fields for these constants. Vessel-specific
    design GM, stack mass/height, dimensions and motion inputs are always read
    from the instance; fitting/rod SWLs use the checker's exported constants.
    """
    from stowage.checker import lashing
    from stowage.linear.physics import geometry_for
    from stowage.linear.bands import box_height, departure_manifest

    hull = instance.vessel.hull
    design = lashing._lashing_design_gm(hull)
    geometry = geometry_for(instance)
    volume = geometry.submerged(hull.scantling_draught_m, hull.scantling_draught_m).volume_m3
    cb = volume/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)
    motions = lashing._motions(hull, hull.scantling_draught_m/4, design, cb)
    cells = {cell.id: cell for cell in instance.vessel.cells}
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    positions = {cid: index for stack in stacks.values() for index, cid in enumerate(stack.cell_ids)}
    boxes = {box.id: box for box in instance.containers}
    manifest = departure_manifest(instance)
    maximum_height = max((box_height(box) for box in manifest), default=2.591)
    entries = {sid: [] for sid in stacks}
    for (cid, slot), variable in x.items():
        for cell in cells_of_slot(slot):
            entries[cells[cell].stack_id].append((boxes[cid], cell, variable))
    for p in fixed:
        for cell in cells_of_slot(p.slot):
            entries[cells[cell].stack_id].append((boxes[p.container_id], cell, None))

    for sid, members in entries.items():
        if not members:
            continue
        stack = stacks[sid]
        lever = max(abs(z-r) for z in (stack.base_z_m, stack.base_z_m+stack.max_height_m)
                    for r in (hull.depth_m/4, hull.depth_m/2))
        at = math.hypot(motions.sway, lashing.G*math.sin(motions.phi)+motions.roll*lever)
        al = .7*math.hypot(motions.surge, math.sqrt(max(hull.lpp_m, 110)/325)*
                          (lashing.G*math.sin(motions.theta)+motions.pitch*lever))
        anchor_height = min(stack.max_height_m, 2*maximum_height)
        tangent = anchor_height/lashing.WIDTH
        cosine = 1/math.sqrt(1+tangent*tangent)
        metrics = {key: [] for key in ("q", "ql", "ft", "fl", "m", "ml", "v", "ecc", "guide")}
        for box, cell_id, variable in members:
            cell = cells[cell_id]
            oog = box.oog
            dx = (oog.over_length_fore_m-oog.over_length_aft_m)/2 if oog else 0.
            dy = (oog.over_width_port_m-oog.over_width_starboard_m)/2 if oog else 0.
            length = 12.192 if box.length_ft == 45 else lashing.LENGTH[box.length_ft]
            mass = max(box.gross_mass_kg, 2500. if box.length_ft == 20 else 3500.)
            share = .5+abs(dx)/length
            av = math.sqrt(motions.heave**2+(motions.roll*(abs(cell.tcg_m)+abs(dy)))**2+
                           (motions.pitch*(abs(cell.lcg_m-.45*hull.lpp_m)+abs(dx)))**2)
            height = box_height(box)
            expanded_l = lashing.LENGTH[box.length_ft]+(
                oog.over_length_fore_m+oog.over_length_aft_m if oog else 0.)
            expanded_w = lashing.WIDTH+(oog.over_width_port_m+oog.over_width_starboard_m if oog else 0.)
            wt = max(15700. if box.length_ft == 20 else 31600., expanded_l*height*1000.)*share if stack.on_deck else 0.
            wl = max(6400., expanded_w*height*1000.)*share if stack.on_deck else 0.
            ft, fl = mass*at*share, mass*al*share
            floor = min(positions[cell_id]*maximum_height, stack.max_height_m-height)
            # Use the full physical height rather than the IRS default .45H.
            arm = max(0., floor)+height
            bottom = positions[cell_id] == 0
            values = dict(q=.45*ft+.5*wt if bottom else ft+wt,
                          ql=.45*fl+.5*wl if bottom else fl+wl,
                          ft=ft+wt, fl=fl+wl, m=(ft+wt)*arm, ml=(fl+wl)*arm,
                          v=mass*(lashing.G+av)*share/2,
                          ecc=mass*(lashing.G+av)*share*abs(dy)/lashing.WIDTH,
                          guide=(ft+fl)/2)
            for key, value in values.items():
                metrics[key].append((variable, value))
            if box.gross_mass_kg > 30500.:
                name = f"construct/APPROXIMATION_securing/{sid}/box_mass/{box.id}"
                _scaled_row(solver, [(variable, 1.)] if variable is not None else [],
                            1. if variable is None else 0., -np.inf, 0., name)
                result.row_names.append(name)

        def add(metric_weights, limit, label):
            terms, constant = {}, 0.
            for key, factor in metric_weights.items():
                for variable, value in metrics[key]:
                    if variable is None:
                        constant += value*factor
                    else:
                        terms[variable] = terms.get(variable, 0.)+value*factor
            name = f"construct/APPROXIMATION_securing/{sid}/{label}"
            _scaled_row(solver, list(terms.items()), constant, -np.inf, limit, name)
            result.row_names.append(name)

        if stack.on_deck:
            add({"q": 1.}, 150000., "transverse_racking")
            add({"ql": 1.}, 75000., "longitudinal_racking")
            add({"q": 1./cosine}, .85*lashing.ROD_SWL, "rod_resultant")
            add({"q": tangent}, 225000., "rod_vertical")
            # Max(M,R*z) is bounded by requiring both alternatives.
            for weights, suffix in (({"m": 1./lashing.WIDTH}, "cargo"),
                                    ({"q": anchor_height/lashing.WIDTH}, "rod")):
                add({**weights, "ecc": 1.}, .85*lashing.LOCK_TENSION_SWL, "tension_"+suffix)
                comp = dict(weights)
                comp["q"] = comp.get("q", 0.)+tangent
                comp.update(v=1., ecc=1.)
                add(comp, min(848000., .85*lashing.LOCK_COMPRESSION_SWL), "compression_"+suffix)
            add({"ft": .5, "fl": .5}, .85*lashing.LOCK_SHEAR_SWL, "corner_shear")
            # Longitudinal DAG moment bound uses the shortest 20ft support.
            add({"v": 1., "ecc": 1., "ml": 1./lashing.LENGTH[20]},
                min(848000., .85*lashing.LOCK_COMPRESSION_SWL), "longitudinal_compression")
            add({"ecc": 1., "ml": 1./lashing.LENGTH[20]},
                .85*lashing.LOCK_TENSION_SWL, "longitudinal_tension")
        else:
            add({"v": 1., "ecc": 1.}, min(848000., .85*lashing.LOCK_COMPRESSION_SWL), "hold_vertical")
            # Summing all contacts is stronger than the single-contact fitting
            # bound. D54's guide stress is already bounded by declared ratings.
            add({"guide": 1.}, .85*lashing.LOCK_SHEAR_SWL, "hold_fitting")
