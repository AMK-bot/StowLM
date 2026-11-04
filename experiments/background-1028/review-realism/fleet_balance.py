"""Balance a synthetic loading-manual background before adjusting ballast.

Mass moments are mass times distance. Matching the departure's longitudinal and
transverse centres to the upright buoyancy centre produces an even-keel, upright
starting condition; KG is chosen for a stated initial solid GM (KM minus KG).
Source: IMO 2008 IS Code Part A 2.2 and the production hull's offset integration.
Deck roofs additionally stay below the SOLAS V/22 sea-surface sightline derived
in checker.visibility: h_eye*dx + R*h_roof <= R*h_eye. The screen uses complete
ISO20 boxes, a 0.3 m roof allowance and sea level 0.1 m below expected departure
waterline. It excludes unsafe tiers rather than hiding or shortening their
emitted silhouettes. Exact visibility still uses the actual loading equilibrium.
A linear programme changes background mass only at declared outside cargo cells,
respecting cell, stack and tier capacities, while minimizing changes from C3.

APPROXIMATION: continuous synthetic equivalent cell masses are not individual
containers or an approved loading manual. Sounding-table liquid centres are an
initial estimate, without free-surface corrections. Ballast moment reserve is a
conservative upright tank-table envelope with displacement headroom, not proof
that every extreme fill satisfies every stability or strength criterion. The
unchanged full checker and ballast finisher remain the final authorities.
"""
from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
import json
from typing import Any
import warnings

import numpy as np
from scipy.optimize import brentq, linprog, OptimizeWarning
from scipy.sparse import lil_matrix, csr_matrix, hstack, eye

from stowage.conventions import CONTAINER_HEIGHT_STD_M, CONTAINER_LENGTH_M
from stowage.model import Vessel
from stowage.model.vessel import FixedLoad


@lru_cache(maxsize=4)
def _geometry(encoded_params: str):
    from stowage.vessel.hydrostatics import HullGeometry
    return HullGeometry.from_params(json.loads(encoded_params))


def _hull(params: dict):
    return _geometry(json.dumps(params, sort_keys=True, separators=(",", ":")))


def ballast_moment_reserve(params: dict, design_displacement_kg: float,
                           fraction: float = .30) -> dict[str, Any]:
    """Reserve mass headroom for 30% of each signed ballast moment envelope.

    For each direction, longest available levers are used first. A tank's least
    favourable lever over all nonempty sounding rows makes this mass budget
    conservative despite its changing centroid. The full capacity moment uses
    its actual full-tank centre, not the conservative minimum lever.
    Four directions are alternative
    controls, not four simultaneous extreme ballast conditions.
    """
    if not 0 <= fraction <= 1:
        raise ValueError("ballast moment reserve fraction must be between zero and one")
    geometry = _hull(params)
    draught = params["hull"]["design_draught_m"]
    submerged = geometry.submerged(draught, draught)
    centres = {"longitudinal": submerged.lcb_m, "transverse": submerged.tcb_m}
    directions = {}
    directional_tanks = {}
    for axis, key in (("longitudinal", "lcg_m"), ("transverse", "tcg_m")):
        for sign, label in ((1, "positive"), (-1, "negative")):
            tanks = []
            full_moment = 0.
            for tank in params.get("tanks", []):
                if not tank.get("adjustable", False):
                    continue
                levers = [sign*(row[key]-centres[axis]) for row in tank["table"]
                          if row["volume_m3"] > 0]
                lever = max(0., min(levers, default=0.))
                capacity = tank["capacity_m3"]*tank["content_density_kg_m3"]
                full_lever = max(0., sign*(tank["table"][-1][key]-centres[axis]))
                full_moment += full_lever*capacity
                if lever > 0:
                    tanks.append((lever, capacity))
            required = fraction*full_moment
            remaining, mass = required, 0.
            for lever, capacity in sorted(tanks, reverse=True):
                amount = min(capacity, remaining/lever)
                mass += amount
                remaining -= amount*lever
            directional_tanks[f"{axis}_{label}"] = tanks
            directions[f"{axis}_{label}"] = dict(
                full_capacity_moment_kgm=full_moment,
                reserved_moment_kgm=required,
                minimum_fill_mass_kg=mass,
                reserve_fraction=fraction if full_moment > 0 else 1.,
                envelope_sufficient=remaining < 1e-5)
    headroom = 1.05*max((row["minimum_fill_mass_kg"] for row in directions.values()), default=0.)
    target = min(.97*design_displacement_kg, design_displacement_kg-headroom)
    for direction, tanks in directional_tanks.items():
        left, attainable = design_displacement_kg-target, 0.
        for lever, capacity in sorted(tanks, reverse=True):
            amount = min(capacity, left)
            attainable += amount*lever
            left -= amount
        row = directions[direction]
        row["attainable_moment_within_headroom_kgm"] = attainable
        row["attainable_reserve_fraction"] = (attainable/row["full_capacity_moment_kgm"]
            if row["full_capacity_moment_kgm"] > 0 else 1.)
    return dict(policy="signed_upright_ballast_moment_envelope", fraction=fraction,
                required_mass_headroom_kg=headroom,
                target_displacement_kg=target, recommended_departure_mass_kg=target,
                headroom_safety_factor=1.05, displacement_headroom_kg=design_displacement_kg-target,
                directions=directions,
                reserve_feasible=all(row["envelope_sufficient"] and
                    row["attainable_reserve_fraction"] >= fraction-1e-10 for row in directions.values()),
                approximation="APPROXIMATION: conservative upright sounding-table moment "
                "envelope; directions are alternative controls; exact departure "
                "acceptance and finisher success require the full checker")


def stability_ballast_masses(params: dict) -> dict[str, float]:
    """Real low double-bottom ballast, retained throughout the D57 voyage.

    Full symmetric double bottoms keep low water mass without slack-tank free
    surfaces. This spends cargo deadweight on KG control, as the C3 manual does;
    the finisher may subsequently change these adjustable quantities.
    """
    return {tank["id"]: (tank["capacity_m3"]*tank["content_density_kg_m3"]
            if tank["id"].startswith("DB-") else 0.)
            for tank in params.get("tanks", []) if tank.get("adjustable", False)}


def stability_deck_profile(params: dict) -> dict[str, float | int]:
    """Per-ship light-deck envelopes qualified at the D56 design GM.

    Lower individual deck masses permit taller stacks without exceeding rod,
    fitting or corner loads. These synthetic envelopes retain whole-ship TEU
    loading: 800 uses three 10 t tiers; 1700 uses four 6 t tiers instead of two
    heavy tiers. Full D57 physics and design-GM securing are still verified.
    They constrain only the background, never the actual decision cargo.
    """
    from stowage.conventions import canonical_vessel_id
    tiers, mass = {"feeder_500": (2, 24999.), "feeder_800": (3, 10000.),
                   "feeder_1700": (4, 6000.)}.get(
                       canonical_vessel_id(params.get("id", "")), (2, 24999.))
    return dict(maximum_deck_tiers=tiers, maximum_deck_box_mass_kg=mass)


def stability_target_gm(params: dict) -> float:
    """Per-ship departure design screen; acceptance measures arrival B 2.3.

    APPROXIMATION: these solid-GM screens reserve the observed D57 consumable
    draw-down/free-surface loss, rather than replacing finite-angle GZ criteria.
    The full checker must certify arrival B 2.3 >= 1.05 for every accepted draw.
    """
    from stowage.conventions import canonical_vessel_id
    return {"feeder_500": 2.10, "feeder_800": 2.10,
            "feeder_1700": 3.40}.get(canonical_vessel_id(params.get("id", "")), 2.35)


def _fit_symmetric_ballast(params: dict, points, target_mass_kg: float, *,
                           keep_double_bottoms_full: bool,
                           balance_transverse: bool = False) -> tuple[dict[str, float] | None, dict]:
    """Fit real symmetric tank fractions to mass and upright longitudinal moment.

    APPROXIMATION: sounding-table centres and free-surface moments initialize
    the exact finite-angle checker. This is a capacity/moment sizing witness,
    never an actual-plan or full-checker feasibility certificate.
    """
    from scipy.optimize import minimize

    geometry = _hull(params)
    h = params["hull"]
    fixed_mass = sum(p[3] for p in points)
    fixed_x = sum(p[0]*p[3] for p in points)
    fixed_y = sum(p[1]*p[3] for p in points)
    draught = brentq(lambda d: geometry.submerged(d, d).displacement_kg-target_mass_kg,
                     .01, h["design_draught_m"]*1.1)
    lcb = geometry.submerged(draught, draught).lcb_m
    wanted_mass = target_mass_kg-fixed_mass
    wanted_x = target_mass_kg*lcb-fixed_x
    wanted_y = target_mass_kg*geometry.submerged(draught, draught).tcb_m-fixed_y
    groups = {}
    for tank in params.get("tanks", []):
        if tank.get("adjustable", False):
            stem, _, suffix = tank["id"].rpartition("-")
            group = (stem if suffix in {"P", "S"} else tank["id"]) if not balance_transverse else tank["id"]
            groups.setdefault(group, []).append(tank)
    names = sorted(groups)
    if not names or wanted_mass < 0:
        return None, dict(status="infeasible_tank_mass", target_mass_kg=target_mass_kg)
    capacities = np.array([sum(t["capacity_m3"]*t["content_density_kg_m3"]
                              for t in groups[name]) for name in names])
    lower = np.array([1. if keep_double_bottoms_full and name.startswith("DB-") else 0.
                      for name in names])
    upper = np.ones(len(names))

    def moments(fractions):
        mass = x = y = z = fsm = 0.
        for name, fraction in zip(names, fractions):
            for tank in groups[name]:
                volume = tank["capacity_m3"]*fraction
                amount = volume*tank["content_density_kg_m3"]
                rows = tank["table"]
                volumes = [row["volume_m3"] for row in rows]
                mass += amount
                x += amount*np.interp(volume, volumes, [row["lcg_m"] for row in rows])
                y += amount*np.interp(volume, volumes, [row["tcg_m"] for row in rows])
                z += amount*np.interp(volume, volumes, [row["vcg_m"] for row in rows])
                fsm += tank["content_density_kg_m3"]*np.interp(
                    volume, volumes, [row["fsm_m4"] for row in rows])
        return np.array([mass, x, y, z, fsm])

    full_x = np.array([sum(t["capacity_m3"]*t["content_density_kg_m3"]*
                          t["table"][-1]["lcg_m"] for t in groups[name]) for name in names])
    full_z = np.array([sum(t["capacity_m3"]*t["content_density_kg_m3"]*
                          t["table"][-1]["vcg_m"] for t in groups[name]) for name in names])
    full_y = np.array([sum(t["capacity_m3"]*t["content_density_kg_m3"]*
                          t["table"][-1]["tcg_m"] for t in groups[name]) for name in names])
    count = 3 if balance_transverse else 2
    desired = np.array([wanted_mass, wanted_x, wanted_y])[:count]
    scales = np.array([1e6, 1e8, 1e7])[:count]
    equality = np.vstack([capacities/1e6, full_x/1e8, full_y/1e7])[:count]
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=OptimizeWarning,
                                message="Unrecognized options detected.*")
        initial = linprog(full_z/1e6, A_eq=equality,
            b_eq=desired/scales, bounds=list(zip(lower, upper)),
            method="highs", options={"threads": 1})
    audit = dict(target_departure_mass_kg=float(target_mass_kg),
        target_departure_draught_m=float(draught), target_departure_lcg_m=float(lcb),
        required_ballast_mass_kg=float(wanted_mass), keep_double_bottoms_full=keep_double_bottoms_full,
        transverse_moment_balanced=balance_transverse, target_departure_tcg_m=0.,
        approximation="APPROXIMATION: upright sounding-table symmetric tank mass/moment fit; "
        "finite-angle liquid centres, strength and every full checker part remain authoritative")
    if not initial.success:
        return None, dict(audit, status="infeasible_linear_initializer", error=initial.message)
    fit = minimize(lambda f: float(sum(moments(f)[-2:]))/1e6, initial.x,
        method="SLSQP", bounds=list(zip(lower, upper)),
        constraints=[dict(type="eq", fun=lambda f:
            (moments(f)[:count]-desired)/scales)],
        options={"ftol": 1e-11, "maxiter": 250})
    # Project optimizer-bound residue onto exactly empty/full tanks. Tiny
    # positive volumes can be below the geometric liquid integration's scale.
    # Validate the emitted recipe AFTER projection; no checker limit changes.
    fractions = np.clip(fit.x, 0., 1.)
    endpoint_tolerance = 1e-12
    fractions = np.where(fractions <= endpoint_tolerance, 0.,
                         np.where(fractions >= 1.-endpoint_tolerance, 1., fractions))
    physical = moments(fractions)
    residual = physical[:count]-desired
    if not fit.success or np.max(np.abs(residual)/np.array([1., 100., 100.])[:count]) > .05:
        return None, dict(audit, status="inconclusive_nonlinear_fit", error=fit.message,
                          mass_x_moment_residual=residual[:2].tolist(),
                          mass_x_y_moment_residual=residual.tolist())
    recipe = {tank["id"]: float(fraction*tank["capacity_m3"]*tank["content_density_kg_m3"])
              for name, fraction in zip(names, fractions) for tank in groups[name]}
    return recipe, dict(audit, status="sized", ballast_mass_kg=sum(recipe.values()),
        mass_x_moment_residual=residual[:2].tolist(),
        mass_x_y_moment_residual=residual.tolist(), fractions=dict(zip(names, fractions.tolist())),
        endpoint_fraction_tolerance=endpoint_tolerance,
        estimated_ballast_vertical_plus_free_surface_moment_kgm=float(sum(physical[-2:])))


def empty_background_ballast_witness(full: Vessel, params: dict, loads: Sequence[FixedLoad],
                                     booking_ballast: dict[str, float]) -> tuple[dict[str, float], dict]:
    """Size EMPTY-region ballast independently of the booked departure recipe.

    IS Code A 2.3.5 weather-table applicability requires B/d < 3.5. Prospective
    booking cargo is absent here, so ballast may replace its weight. The witness
    is never initialized on the actual booked ship or subtracted from its cargo
    deadweight. All D57 states must subsequently pass the full checker.
    """
    if full.hull is None:
        return dict(booking_ballast), dict(status="deferred_missing_hull")
    geometry = _hull(params)
    retained = (full.voyage_policy.default_arrival_remaining_fraction
                if full.voyage_policy is not None else .1)
    consumables = sum(.97*t["capacity_m3"]*t["content_density_kg_m3"]
                      for t in params.get("tanks", []) if not t.get("adjustable", False))
    points = _known_points(params, loads, 0., (0., 0., 0.), {})
    current_mass = sum(p[3] for p in points)+sum(booking_ballast.values())
    assumption = dict(arrival_consumable_remaining_fraction=float(retained),
        sizing_water_density_kg_m3=float(geometry.density_kg_m3),
        density_assumption="Maximum declared feeder scenario density is 1025 kg/m3; "
            "lower density gives deeper draught at equal mass. Actual voyage states are fully checked.",
        witness_is_booking_initializer=False, booking_ballast_mass_kg=sum(booking_ballast.values()))
    attempts = []
    for target_bd in (3.4, 3.425, 3.45, 3.475):
        draught = params["hull"]["beam_m"]/target_bd
        required = geometry.submerged(draught, draught).displacement_kg+(1-retained)*consumables
        if current_mass >= required-1e-5:
            return dict(booking_ballast), dict(assumption, status="booking_recipe_sufficient",
                target_arrival_beam_over_draught=target_bd,
                target_departure_mass_kg=current_mass, ballast_mass_kg=sum(booking_ballast.values()))
        if required > full.hull.max_displacement_kg:
            attempts.append(dict(target_arrival_beam_over_draught=target_bd,
                                 status="above_maximum_displacement"))
            continue
        recipe, audit = _fit_symmetric_ballast(params, points, required,
                                               keep_double_bottoms_full=True)
        attempts.append(dict(target_arrival_beam_over_draught=target_bd, **audit))
        if recipe is not None:
            return recipe, dict(assumption, **audit,
                target_arrival_beam_over_draught=target_bd, sizing_attempts=attempts)
    return dict(booking_ballast), dict(assumption, status="no_sizing_witness_found",
                                      sizing_attempts=attempts)


def booking_reference_ballast_sizing(full: Vessel, params: dict, loads: Sequence[FixedLoad], *,
                                     booking_mass_kg: float, booking_cg_m: tuple[float, float, float],
                                     booking_ballast: dict[str, float]) -> dict:
    """D36/D48 mass and both horizontal-moment check of the booking reference.

    The equivalent booking reference is not an actual stow. A fitted recipe
    establishes tank capacity/moment room, never actual completion or full-plan
    stability/strength/securing feasibility. The booking initializer is unchanged.
    """
    if full.hull is None:
        return dict(status="deferred_missing_hull", actual_plan_certificate=False)
    points = _known_points(params, loads, booking_mass_kg, booking_cg_m, {})
    initial_mass = sum(p[3] for p in points)+sum(booking_ballast.values())
    maximum = full.hull.max_displacement_kg
    if initial_mass > maximum+1e-5:
        return dict(status="above_maximum_displacement", within_displacement_limit=False,
            booking_initializer_mass_kg=initial_mass, maximum_displacement_kg=maximum,
            retained_displacement_headroom_kg=maximum-initial_mass,
            actual_plan_certificate=False, empty_witness_mass_included=False)
    attempts = []
    for fraction in (0., .25, .5, .75, 1.):
        target = initial_mass+fraction*max(0., maximum-initial_mass)
        recipe, audit = _fit_symmetric_ballast(params, points, target,
                                               keep_double_bottoms_full=False, balance_transverse=True)
        attempts.append(audit)
        if recipe is not None:
            return dict(audit, tank_masses_kg=recipe, sizing_attempts=attempts,
                booking_initializer_mass_kg=initial_mass, maximum_displacement_kg=maximum,
                retained_displacement_headroom_kg=maximum-target,
                within_displacement_limit=True,
                actual_plan_certificate=False, empty_witness_mass_included=False)
    return dict(status="no_reference_sizing_witness_found", sizing_attempts=attempts,
                actual_plan_certificate=False, empty_witness_mass_included=False)


def _known_points(params: dict, base_loads: Sequence[FixedLoad],
                  own_mass: float, own_cg: tuple[float, float, float],
                  ballast_masses_kg: dict[str, float] | None = None):
    h = params["hull"]
    points = [(h["lightship_lcg_m"], h.get("lightship_tcg_m", 0.),
               h["lightship_vcg_m"], h["lightship_mass_kg"]), (*own_cg, own_mass)]
    points.extend(tuple(load) for load in base_loads)
    for tank in params.get("tanks", []):
        mass = (ballast_masses_kg or {}).get(tank["id"], 0.) if tank.get("adjustable", False) else .97*tank["capacity_m3"]*tank["content_density_kg_m3"]
        if mass <= 0:
            continue
        volume = mass/tank["content_density_kg_m3"]
        rows = tank["table"]
        centre = tuple(float(np.interp(volume, [r["volume_m3"] for r in rows],
                                       [r[key] for r in rows]))
                       for key in ("lcg_m", "tcg_m", "vcg_m"))
        points.append((*centre, volume*tank["content_density_kg_m3"]))
    return points


def balance_background(full: Vessel, params: dict, placements: list[tuple[str, float]], *,
                       stack_ids: set[str], expected_load_mass_kg: float,
                       expected_load_cg_m: tuple[float, float, float],
                       base_loads: Sequence[FixedLoad] = (), target_gm_m: float | None = None,
                       ballast_masses_kg: dict[str, float] | None = None,
                       ) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    """Balance the EMPTY-region background at a per-ship stability screen.

    Four equalities enforce background cargo mass and x/y/z moments, including
    real initial ballast and consumables. Booking mass is audit context only:
    it is absent from this physical loading condition. A deterministic L1
    objective stays close to C3, with 30.48 t hold / 24.999 t deck cell bounds,
    98% stack ratings, heavy-low supported prefixes and per-ship deck envelopes:
    500 two 24.999 t tiers, 800 three 10 t tiers, 1700 four 6 t tiers.
    Complete deck roofs clear an upright SOLAS V/22 screen, with a 0.3 m roof
    allowance and a 0.1 m waterline allowance. Exact stability and visibility
    still require the full checker at all D57 states after the ballast finisher.
    If the exact screen is unattainable, the lower screen may fall by 0.12 m
    and the upper screen may rise to 90% of the declared D56 design GM. This
    accommodates sparse regions without pretending they carry booking cargo.
    Full arrival GZ-area acceptance remains mandatory; failure returns
    target_reached=False without relaxing physical constraints.
    """
    target_gm_m = stability_target_gm(params) if target_gm_m is None else target_gm_m
    deck_profile = stability_deck_profile(params)
    deck_tiers = deck_profile["maximum_deck_tiers"]
    deck_mass_limit = deck_profile["maximum_deck_box_mass_kg"]
    cells = {cell.id: cell for cell in full.cells}
    stacks = {stack.id: stack for stack in full.stacks}
    active_covers = {stacks[sid].hatch_cover_id for sid in stack_ids}
    background_mass = sum(mass for _, mass in placements)
    # D58: certify the physical background with an EMPTY decision region.
    # Booking mass reserves deadweight upstream; it is not present cargo and
    # cannot lend its stabilizing moments to an empty-background certificate.
    points = _known_points(params, base_loads, 0., expected_load_cg_m, ballast_masses_kg)
    total_mass = background_mass+sum(p[3] for p in points)
    geometry = _hull(params)
    max_draught = params["hull"]["design_draught_m"]
    draught = brentq(lambda t: geometry.submerged(t, t).displacement_kg-total_mass,
                     .01, max_draught*1.1, xtol=1e-10)
    submerged = geometry.submerged(draught, draught)
    km = submerged.vcb_m+submerged.bm_t_m
    target_cg = np.array([submerged.lcb_m, submerged.tcb_m, km-target_gm_m])
    known_moments = np.array([sum(p[axis]*p[3] for p in points) for axis in range(3)])
    cargo_target = total_mass*target_cg-known_moments
    available = []
    columns = []
    excluded_deck_cells = []
    bridge = full.bridge
    # APPROXIMATION: upright screen with conservative waterline and roof
    # allowances; the full checker transforms every actual corner at list/trim.
    screen_waterline = draught-.1
    roof_allowance = .3
    if bridge is None:
        raise ValueError("background visibility screen requires bridge geometry")
    eye_height = bridge.eye_z_m-screen_waterline
    sight_range = bridge.bow_x_m-bridge.x_m+min(2*params["hull"]["loa_m"], 500.)
    if eye_height <= 0 or sight_range <= 0:
        raise ValueError("background visibility screen requires an above-water eye aft of bow")
    for stack in sorted(full.stacks, key=lambda s: s.id):
        if stack.id in stack_ids or (stack.on_deck and stack.hatch_cover_id in active_covers):
            continue
        column = []
        for index, cid in enumerate(stack.cell_ids):
            cell = cells[cid]
            if (not cell.accepts_20 or
                    (index+1)*CONTAINER_HEIGHT_STD_M > stack.max_height_m+1e-9):
                break
            if stack.on_deck:
                # D58: tall heavy deck chains exceed the synthetic securing
                # envelope even when their total stack weight is legal.
                if index >= deck_tiers:
                    excluded_deck_cells.extend(stack.cell_ids[index:])
                    break
                fore_corner = cell.lcg_m+CONTAINER_LENGTH_M[20]/2-bridge.x_m
                roof_height = cell.vcg_base_m+CONTAINER_HEIGHT_STD_M+roof_allowance-screen_waterline
                if fore_corner > 0 and (eye_height*fore_corner+sight_range*roof_height >
                                        sight_range*eye_height+1e-9):
                    # All higher tiers in this stack are also unavailable. No
                    # emitted box is clipped: only complete real cells enter LP.
                    excluded_deck_cells.extend(stack.cell_ids[index:])
                    break
            column.append(cid)
        if column:
            columns.append((stack, column))
            available.extend(column)
    audit: dict[str, Any] = dict(
        policy="balanced_continuous_background", target_solid_gm_m=target_gm_m,
        target_departure_cg_m=target_cg.tolist(), expected_decision_cg_m=list(expected_load_cg_m),
        expected_departure_mass_kg=total_mass, expected_background_mass_kg=total_mass,
        expected_booked_mass_kg=total_mass+expected_load_mass_kg,
        expected_decision_mass_kg=expected_load_mass_kg,
        conditioning_condition="background_empty_decision_region",
        required_arrival_b23_ratio=1.05, screen_km_m=km,
        deck_securing_profile=deck_profile,
        visibility_excluded_cell_ids=sorted(excluded_deck_cells),
        visibility_screen_waterline_m=screen_waterline,
        visibility_roof_allowance_m=roof_allowance,
        visibility_range_from_eye_m=sight_range,
        maximum_background_capacity_kg=float(sum(min(
            .98*(stack.max_mass_20_kg or stack.max_mass_kg),
            len(column)*(deck_mass_limit if stack.on_deck else 30480.))
            for stack, column in columns)),
        screen_mean_draught_m=draught, target_reached=False,
        expected_departure_kg_m=float(target_cg[2]),
        approximation="APPROXIMATION: continuous fixed synthetic cell masses; upright "
        "soundings omit free-surface shifts; full checker remains authoritative")
    n = len(available)
    if not n or background_mass <= 0:
        audit["failure"] = "no_background_mass_or_available_cells"
        return placements, audit
    positions = np.array([[cells[cid].lcg_m, cells[cid].tcg_m,
                           cells[cid].vcg_base_m+CONTAINER_HEIGHT_STD_M/2] for cid in available])
    scale = np.array([params["hull"]["lpp_m"], params["hull"]["beam_m"], 20.])
    # Tonnes and normalized coordinates keep equality magnitudes well scaled.
    aeq = np.vstack([np.ones(n), (positions/scale).T])
    beq = np.r_[background_mass/1000, cargo_target/1000/scale]
    reference = dict(placements)
    ref = np.array([reference.get(cid, 0.)/1000 for cid in available])
    index = {cid: i for i, cid in enumerate(available)}
    rows = sum(len(column) for _, column in columns)
    aub = lil_matrix((rows, n))
    bub, row = [], 0
    for stack, column in columns:
        for cid in column:
            aub[row, index[cid]] = 1.
        bub.append(.98*(stack.max_mass_20_kg or stack.max_mass_kg)/1000)
        row += 1
        for lower, upper in zip(column, column[1:]):
            aub[row, index[upper]] = 1.
            aub[row, index[lower]] = -1.
            bub.append(0.)
            row += 1
    # Two nonnegative deviations give |mass - C3 mass|.
    identity = eye(n, format="csr")
    deviation_eq = hstack([identity, -identity, identity], format="csr")
    from scipy.sparse import vstack
    equality = vstack([hstack([csr_matrix(aeq), csr_matrix((4, 2*n))]), deviation_eq], format="csr")
    objective = np.r_[np.arange(n)*1e-10, np.ones(2*n)]
    limits = [(0., deck_mass_limit/1000 if stacks[cells[cid].stack_id].on_deck else 30.480)
              for cid in available] + [(0., None)]*(2*n)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=OptimizeWarning,
                                message="Unrecognized options detected.*")
        inequality = hstack([aub.tocsr(), csr_matrix((rows, 2*n))], format="csr")
        right = np.array(bub)
        result = linprog(objective, A_ub=inequality,
                         b_ub=right, A_eq=equality, b_eq=np.r_[beq, ref],
                         bounds=limits, method="highs", options={"threads": 1})
        if not result.success:
            # Keep a narrow per-ship design band when exact vertical moment
            # cannot fit. Mass and horizontal moments remain exact; no stack
            # capacity or checker stability criterion is relaxed.
            zrow = equality.getrow(3)
            maximum_gm = max(target_gm_m+.12,
                .90*full.hull.max_gm_lashing_m if full.hull is not None
                and full.hull.max_gm_lashing_m is not None else target_gm_m+.12)
            minimum_gm = target_gm_m-.12
            lower = (total_mass*(km-maximum_gm)-known_moments[2])/20000
            upper = (total_mass*(km-minimum_gm)-known_moments[2])/20000
            audit["allowed_solid_gm_band_m"] = [minimum_gm, maximum_gm]
            keep_rows = [i for i in range(equality.shape[0]) if i != 3]
            fallback = dict(
                A_ub=vstack([inequality, zrow, -zrow], format="csr"),
                b_ub=np.r_[right, upper, -lower],
                A_eq=equality[keep_rows], b_eq=np.r_[beq, ref][keep_rows],
                bounds=limits, method="highs", options={"threads": 1})
            zobjective = np.asarray(zrow.toarray()).ravel()
            lowest = linprog(zobjective, **fallback)
            highest = linprog(-zobjective, **fallback)
            if lowest.success and highest.success:
                low_z = float(zobjective@lowest.x)
                high_z = float(zobjective@highest.x)
                closest = float(np.clip(beq[3], low_z, high_z))
                adjusted_beq = np.r_[beq, ref]
                adjusted_beq[3] = closest
                result = linprog(objective, A_ub=inequality, b_ub=right,
                    A_eq=equality, b_eq=adjusted_beq, bounds=limits,
                    method="highs", options={"threads": 1})
                audit["attainable_solid_gm_band_m"] = [
                    float(km-(high_z*20000+known_moments[2])/total_mass),
                    float(km-(low_z*20000+known_moments[2])/total_mass)]
            else:
                result = lowest
            audit["gm_band_fallback"] = True
    audit.update(optimizer_status=int(result.status), optimizer_message=result.message)
    if not result.success:
        return placements, audit
    values = np.maximum(0., result.x[:n])*1000
    output = [(cid, float(value)) for cid, value in zip(available, values) if value > 1e-6]
    all_moments = known_moments+positions.T@values
    cg = all_moments/total_mass
    audit.update(target_reached=True, expected_departure_cg_m=cg.tolist(),
                 requested_solid_gm_m=target_gm_m,
                 target_solid_gm_m=float(km-cg[2]),
                 expected_departure_kg_m=float(cg[2]),
                 estimated_solid_gm_after_m=float(km-cg[2]),
                 estimated_solid_gm_before_m=float(km-(known_moments[2]+sum(
                     mass*(cells[cid].vcg_base_m+CONTAINER_HEIGHT_STD_M/2)
                     for cid, mass in placements))/total_mass),
                 screen_trim_m=0., screen_list_deg=0.,
                 background_mass_residual_kg=float(values.sum()-background_mass),
                 moment_residual_kgm=(all_moments-total_mass*np.r_[target_cg[:2], cg[2]]).tolist(),
                 occupied_background_cells=len(output),
                 cargo_l1_change_kg=float(np.abs(values-ref*1000).sum()))
    return output, audit


def bound_background_mass(full: Vessel, placements: list[tuple[str, float]], *,
                          stack_ids: set[str]) -> list[tuple[str, float]]:
    """Project a C3 mass target onto physical outside cell/stack capacities.

    APPROXIMATION: continuous equivalent container masses retain the C3 relative
    weights where possible. A common multiplier fills available tier capacity;
    stack-specific multipliers stop at the declared twenty-foot support rating.
    No cell exceeds 30.48 t (hold) or 24.999 t (deck), and masses decrease upward.
    This is a cargo allocation bound, not a stability/ballast certificate.
    """
    cells = {cell.id: cell for cell in full.cells}
    stacks = {stack.id: stack for stack in full.stacks}
    active_covers = {stacks[sid].hatch_cover_id for sid in stack_ids}
    reference = dict(placements)
    target = sum(reference.values())
    columns = []
    for stack in sorted(full.stacks, key=lambda item: item.id):
        if (stack.id in stack_ids or
                (stack.on_deck and stack.hatch_cover_id in active_covers)):
            continue
        column = []
        weights = []
        for index, cid in enumerate(stack.cell_ids):
            if (not cells[cid].accepts_20 or
                    (index+1)*CONTAINER_HEIGHT_STD_M > stack.max_height_m+1e-9):
                break
            column.append(cid)
            # Previously empty real cells become the last allocation resort.
            weights.append(max(reference.get(cid, 0.), 1.))
        if column:
            weights = np.minimum.accumulate(weights)
            cell_cap = 24999. if stack.on_deck else 30480.
            cap = min(stack.max_mass_20_kg or stack.max_mass_kg, len(column)*cell_cap)
            columns.append((column, weights, cell_cap, cap))
    # Preserve vacant deck tiers for the later GM adjustment whenever the
    # already occupied cells can carry the target legally. A tiny positive
    # allocation to every vacant tier would freeze those tiers as existing
    # cargo and prevent a physically possible same-column upward transfer.
    occupied_columns = []
    for column, weights, cell_cap, cap in columns:
        count = next((i for i, cid in enumerate(column) if cid not in reference), len(column))
        if count:
            occupied_columns.append((column[:count], weights[:count], cell_cap,
                                     min(cap, count*cell_cap)))
    if sum(cap for _, _, _, cap in occupied_columns) >= target-1e-5:
        columns = occupied_columns
    capacity = sum(cap for _, _, _, cap in columns)
    if target > capacity+1e-5:
        raise ValueError("slice background mass exceeds physical outside stack capacities")
    if target <= 0:
        return []

    def allocate(multiplier, column):
        _, weights, cell_cap, cap = column
        masses = np.minimum(weights*multiplier, cell_cap)
        if masses.sum() > cap:
            local = brentq(lambda factor: np.minimum(weights*factor, cell_cap).sum()-cap,
                           0., multiplier, xtol=1e-10)
            masses = np.minimum(weights*local, cell_cap)
        return masses

    upper = max(1., max(cell_cap/min(weights) for _, weights, cell_cap, _ in columns))
    multiplier = brentq(lambda factor: sum(allocate(factor, column).sum() for column in columns)-target,
                        0., upper, xtol=1e-10)
    return [(cid, float(mass)) for column in columns
            for cid, mass in zip(column[0], allocate(multiplier, column)) if mass > 1e-6]
