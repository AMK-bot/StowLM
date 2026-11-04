"""C3 export-mix loading context for small planning regions on a loaded ship.

The sampling and heavy-first placement policy is the reviewer's C3 condition in
``experiments/physics-review/design/harness.py`` and ``conditions.md``: dry20,
triangular export masses, 20% empties, hold tiers before deck tiers, >=25 t in
holds, and 97% departure consumables. The current cargo input defines the draw.
The reviewer's 2200 kg empty mass is retained for this condition (not substituted
for the generator's separate 2350 kg equipment tare).

Generator handoff (3.5/4.3), after selecting the FINAL decision cells and boxes::

    from stowage.vessel import recondition_slice, departure_tank_masses
    vessel = recondition_slice(
        selected_vessel,
        expected_load_mass_kg=sum(box.gross_mass_kg for box in load_boxes),
        seed=instance_seed,
    )
    ship = ShipState(vessel_id=vessel.id,
                     tank_masses_kg=departure_tank_masses(vessel))

``load_boxes`` means only the cargo being planned, not yard blockers. Run this
after any six-cell selector or other geometry restriction, including selectors
which retain the full vessel id. Reconditioning replaces previous background
cargo while preserving original fixed stores. Keep both the returned vessel and
the returned tank masses in the instance; never add consumables to fixed_loads.
The booked tank initializer sets 97% consumables and the audited low double-bottom
ballast recipe. The separate empty-background witness may replace absent booking
cargo with additional ballast to retain weather-table draught; it is never the
actual booking initializer and never consumes the reserved booking deadweight; the finisher may subsequently change adjustable ballast. This
initialization alone is not a full-checker acceptance certificate.

Geometry-only parents (``with_hull=False``) are diagnostic inputs. A booking
that cannot be balanced retains a labelled unconditioned C3 background rather
than fabricating hull/stability evidence. Production parents reject it.

APPROXIMATION: the rest of the ship is a fixed synthetic condition, not an
approved loading manual. D42 records each allocated cell as an equivalent
standard-height ISO20 background box; its continuous mass remains the D29 mass.
Unbooked presets remain provisional C3 geometry/design diagnostics. Once the
final booking mass is explicitly supplied, D58 reserves real low ballast before
allocating cargo deadweight. A bounded
linear programme balances the background with an EMPTY decision region and a
per-ship solid-GM design screen; the generator's checker-and-finisher gate must
then establish arrival IS Code B 2.3 ratio >= 1.05 and every D57 full-checker
criterion. Booking mass reserves cargo deadweight and its D48 heavy-low centre
remains an audit reference, never stabilizing cargo present in the empty region.
All cargo remains on actual outside cells, with decreasing tier masses, a 2%
stack-rating reserve and per-ship deck tier/mass caps for securing: two tiers
capped at 24.999 t on the 500, three at 10 t on the 800, four at 6 t on the 1700.
APPROXIMATION: each deck box independently draws a triangular 70–100% of its
qualified cap (mode 90%), sorted heavy-low within its stack, then the LP balances
mass and moments below those per-box caps. Every occupied equivalent ISO20
retains at least the C3 2.2 t tare, enforced inside the moment fit. The
lighter taller decks preserve feeder utilisation while keeping design-GM rod
loads legal; they are synthetic background envelopes, not booking restrictions.
Fixed loads retain mass and moments, with per-box support evidence for the checker
to assess individual securing and visibility.
For unbooked provisional presets only, when a solid-GM screen exceeds 2.5 m,
same-column mass transfers into available deck tiers target 2.0 m, preserving mass and longitudinal/transverse moments.
This labelled synthetic adjustment uses estimated decision-cargo centres and
liquid centres; it does not replace the checker's free-surface/stability checks.
Deck cargo on an active removable cover is excluded, so it cannot obstruct a
cover lift. Each background deck stack has one ``FixedLoad`` with its combined
mass, mass-weighted centre and full occupied ISO20 ``silhouette`` box. Hold cargo
has no exposed silhouette. Geometry adds no extra mass: windage and visibility
consumers must use the union/occlusion of boxes, not sum overlapping projections.
Consumables remain live liquid tanks: callers must use departure_tank_masses(),
not add their mass to fixed_loads or freeze their free-surface effects.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from random import Random
from pathlib import Path
from typing import Any

from stowage.conventions import CONTAINER_HEIGHT_STD_M, CONTAINER_LENGTH_M, CONTAINER_WIDTH_M, parse_slot
from stowage.model import FixedCargoBox, Provenance, Vessel
from stowage.model.vessel import FixedLoad, FixedLoadSilhouette
from stowage.toml_cache import load_bytes as load_toml_bytes
from stowage.params import load_param_file, params_dir

CONDITION_VERSION = "c3-fixed-background-14"
CONDITION_PREFIX = "stowage.slice_condition:"
PRESET_SEEDS = {"tiny": 7, "small": 17, "medium": 29, "custom": 41}
EXPORT_MEAN_KG_PER_TEU = 15768.0


def slice_condition(vessel: Vessel) -> dict[str, Any] | None:
    """Return the audited loading-condition assumptions stored with a slice."""
    records = [source for source in vessel.sources if source.startswith(CONDITION_PREFIX)]
    return json.loads(records[-1][len(CONDITION_PREFIX):]) if records else None


def departure_tank_masses(vessel: Vessel) -> dict[str, float]:
    """Initialize 97% consumables and the D58 audited stability-ballast recipe.

    Older/unconditioned vessels retain zero initial adjustable ballast when
    rule_freeze_v1 is OFF. ON initializes each explicit anti-heeling pair tank
    at half its current capacity; the background builder reserves that mass.
    The recipe is live liquid mass, never fixed cargo. D64 fixes the pair total
    while allowing transfer; other ballast remains adjustable by the finisher.
    Berth trim at the actual port is prepared separately by prepare_arrival.
    """
    condition = slice_condition(vessel)
    ballast = condition.get("departure_ballast_masses_kg", {}) if condition else {}
    masses = {tank.id: ballast.get(tank.id, 0.0) if tank.adjustable else
              0.97 * tank.capacity_m3 * tank.content_density_kg_m3 for tank in vessel.tanks}
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        # These masses are also reserved by stability_ballast_masses before
        # allocating background cargo; this assignment does not add them twice.
        from stowage.vessel.fleet_berth import anti_heeling_prefill
        masses.update(anti_heeling_prefill(vessel))
    return masses


def empty_background_witness_tank_masses(vessel: Vessel) -> dict[str, float]:
    """Initialize the separate D58 EMPTY-plan ballast witness, never a booking.

    Missing witness metadata retains the booked departure recipe. A witness may
    carry additional ballast replacing absent decision cargo; that extra mass
    must not be initialized on the actual booked ship or consume its deadweight.
    Under D64 rev3.1 this is a seagoing assessment: HEEL is ordinary ballast.
    Its fills never replace the actual in-port half-full arrival declaration.
    """
    masses = departure_tank_masses(vessel)
    condition = slice_condition(vessel)
    witness = (condition or {}).get("empty_background_witness_ballast_masses_kg", {})
    masses.update({tank.id: witness[tank.id] for tank in vessel.tanks
                   if tank.adjustable and tank.id in witness})
    return masses


LOADED_REFERENCE_VERSION = "d64-rev3.1-loaded-reference-v1"


def loaded_reference_witness_vessel(vessel: Vessel, booking_mass_kg: float | None = None) -> Vessel | None:
    """D64 rev3.1 Correction: the medium/full seagoing witness carries its booking.

    An empty decision region is a light ship no real feeder sails as (B/d above
    the IS Code A 2.3.5 weather table on the 1700 medium). The witness instead
    places the slice's own D48 booking reference on its decision cells, as
    standard-height ISO20 fixed cargo exactly like the D29 background, scaled to
    the actual booked mass when given. The full checker judges it unchanged.
    APPROXIMATION: equivalent cell masses, not the actual mixed-length stow;
    they never initialize a plan or add mass to the emitted instance.
    Returns None when the slice records no booking reference.
    """
    info = slice_condition(vessel) or {}
    reference = info.get("booking_reference") or {}
    rows = [row for row in reference.get("cells", []) if row["mass_kg"] > 0]
    total = sum(row["mass_kg"] for row in rows)
    if not rows or total <= 0:
        return None
    scale = 1. if booking_mass_kg is None else booking_mass_kg/total
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("loaded reference needs a positive finite booking mass")
    cells = {cell.id: cell for cell in vessel.cells}
    stacks = {stack.id: stack for stack in vessel.stacks}
    provenance = Provenance(created_by="vessel.fleet_conditions.loaded_reference",
                            versions={"slice_condition": CONDITION_VERSION,
                                      "loaded_reference": LOADED_REFERENCE_VERSION},
                            parents=[vessel.id, "APPROXIMATION: D48 booking reference as ISO20 fixed cargo"])
    hold, deck = [], {}
    for row in rows:
        cell = cells[row["cell_id"]]
        stack = stacks[cell.stack_id]
        mass = row["mass_kg"]*scale
        z = cell.vcg_base_m+CONTAINER_HEIGHT_STD_M/2
        box = FixedCargoBox(cell_ids=[cell.id], length_ft=20, height_m=CONTAINER_HEIGHT_STD_M,
                            gross_mass_kg=mass, x_m=cell.lcg_m, y_m=cell.tcg_m, z_m=z,
                            stack_id=cell.stack_id, tier=parse_slot(cell.id).tier, provenance=provenance)
        silhouette = (FixedLoadSilhouette(
            x_min_m=cell.lcg_m-CONTAINER_LENGTH_M[20]/2, x_max_m=cell.lcg_m+CONTAINER_LENGTH_M[20]/2,
            y_min_m=cell.tcg_m-CONTAINER_WIDTH_M/2, y_max_m=cell.tcg_m+CONTAINER_WIDTH_M/2,
            z_min_m=cell.vcg_base_m, z_max_m=cell.vcg_base_m+CONTAINER_HEIGHT_STD_M)
            if stack.on_deck else None)
        point = FixedLoad(x_m=cell.lcg_m, y_m=cell.tcg_m, z_m=z, mass_kg=mass, kind="cargo",
                          silhouette=silhouette, cargo_boxes=[box])
        (deck.setdefault(stack.id, []) if stack.on_deck else hold).append(point)
    loads = hold+[_deck_background_loads(deck[sid]) for sid in sorted(deck)]
    # D42 attribution evidence: the checker rebuilds fixed loads from the
    # condition's background cells, so the reference cells join them in the
    # same order as ``loads`` (hold cells, then deck stacks by id).
    ordered = ([row for row in rows if not stacks[cells[row["cell_id"]].stack_id].on_deck]
               + sorted((row for row in rows if stacks[cells[row["cell_id"]].stack_id].on_deck),
                        key=lambda row: (cells[row["cell_id"]].stack_id, row["cell_id"])))
    condition = dict(info, background_cells=[*info.get("background_cells", []), *(
        dict(cell_id=row["cell_id"], stack_id=cells[row["cell_id"]].stack_id, mass_kg=row["mass_kg"]*scale)
        for row in ordered)])
    audit = dict(version=LOADED_REFERENCE_VERSION, booking_reference_mass_kg=total,
                 booking_mass_kg=total*scale, cells=len(rows), assessment_context="seagoing_witness")
    # The witness plan is empty, so the whole decision region becomes fixed
    # support geometry (D42 reciprocal catalogs); covers keep both memberships.
    fixed_covers = {cover.id: cover for cover in vessel.fixed_load_hatch_covers}
    for cover in vessel.hatch_covers:
        old = fixed_covers.get(cover.id)
        fixed_covers[cover.id] = cover.model_copy(update={
            "hold_stack_ids": sorted({*cover.hold_stack_ids, *(old.hold_stack_ids if old else [])}),
            "deck_stack_ids": sorted({*cover.deck_stack_ids, *(old.deck_stack_ids if old else [])})})
    data = vessel.model_dump()
    data.update(cells=[], stacks=[],
                hatch_covers=[cover.model_copy(update={"hold_stack_ids": [], "deck_stack_ids": []}).model_dump()
                              for cover in vessel.hatch_covers],
                fixed_load_cells=[cell.model_dump() for cell in [*vessel.fixed_load_cells, *vessel.cells]],
                fixed_load_stacks=[stack.model_dump() for stack in [*vessel.fixed_load_stacks, *(
                    stack.model_copy(update={"dg_doc_area_id": None}) for stack in vessel.stacks)]],
                # DoC areas govern decision DG cargo only; the witness carries none.
                dg_doc_areas=[],
                fixed_load_hatch_covers=[cover.model_dump() for cover in fixed_covers.values()],
                fixed_loads=[load.model_dump() for load in [*vessel.fixed_loads, *loads]],
                sources=[*(source for source in vessel.sources if not source.startswith(CONDITION_PREFIX)),
                         CONDITION_PREFIX+json.dumps(condition, sort_keys=True, separators=(",", ":")),
                         "stowage.loaded_reference:"+json.dumps(audit, sort_keys=True)])
    return Vessel.model_validate(data)


@lru_cache(maxsize=12)
def _raw_context(name: str, payload: bytes) -> tuple[dict, float]:
    from stowage.vessel.hydrostatics import HullGeometry

    params = load_toml_bytes(payload)
    geometry = HullGeometry.from_params(params)
    h = params["hull"]
    displacement = geometry.submerged(h["design_draught_m"], h["design_draught_m"],
                                      density_kg_m3=h.get("water_density_kg_m3", 1025.0)).displacement_kg
    return params, displacement


def _nonlabel_parameter_fingerprint(params: dict) -> str:
    """Bind every declared input except the five D33 display-label locations."""
    tree = dict(params)
    for field in ('id', 'name', 'nominal_teu', 'teu_tolerance_fraction'):
        tree.pop(field, None)
    tree['hull'] = {key: value for key, value in tree.get('hull', {}).items()
                    if key != 'hull_offsets_ref'}
    return hashlib.sha256(json.dumps(tree, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _verify_parent_parameter_sources(vessel: Vessel, root: str, payload: bytes) -> None:
    """Admit a pinned legacy label only while every other input is unchanged."""
    from stowage.conventions import LEGACY_FLEET_PARAMETER_PINS, canonical_vessel_id

    actual = hashlib.sha256(payload).hexdigest()
    for source in vessel.sources:
        prefix, separator, declared = source.partition('.toml sha256:')
        if not separator or not prefix.startswith('params/fleet/'):
            continue
        label = prefix.removeprefix('params/fleet/')
        if canonical_vessel_id(label) != root:
            raise ValueError("vessel parameter source has a different root vessel")
        if declared == actual:
            continue
        pin = LEGACY_FLEET_PARAMETER_PINS.get(root)
        if (label != root and pin is not None and declared == pin[0]
                and _nonlabel_parameter_fingerprint(load_toml_bytes(payload)) == pin[1]):
            continue
        raise ValueError("vessel parameters changed; reload the full vessel before slicing")


def _parent(vessel: Vessel) -> tuple[Vessel, dict | None, float | None, list]:
    """Recover whole geometry for repeated slicing; never accumulate cargo twice."""
    from stowage.conventions import canonical_vessel_id
    from stowage.vessel.fleet import _parameter_bytes, list_fleet, load_vessel

    previous = slice_condition(vessel)
    base = previous["base_fixed_loads"] if previous else vessel.fixed_loads
    root = canonical_vessel_id(vessel.slice_of or vessel.id)
    params = None
    if root in list_fleet():
        payload = _parameter_bytes(root)
        _verify_parent_parameter_sources(vessel, root, payload)
        params, design = _raw_context(root, payload)
        # Some six-cell selectors retain the full vessel id and slice_of=None.
        # Detect their actual geometry, rather than mistaking six cells for an
        # entire ship and synthesizing no outside cargo.
        complete = ({cell.id for cell in vessel.cells} == {cell["id"] for cell in params["cells"]})
        full = load_vessel(root, with_hull=False) if vessel.slice_of or not complete else vessel
        full = full.model_copy(update={"hull": vessel.hull, "tanks": vessel.tanks,
                                         "voyage_policy": vessel.voyage_policy})
    else:
        from stowage.vessel.fleet_slices import recover_parent

        full = recover_parent(vessel)
    if params is None and full.hull is not None:
        h = full.hull
        rows = h.hydrostatics
        import numpy as np
        design = float(np.interp(h.design_draught_m, [r.draught_m for r in rows],
                                 [r.displacement_kg for r in rows]))
    elif params is None:
        design = None  # Pure geometry cannot certify a departure band.
    return full, params, design, [FixedLoad.model_validate(load) for load in base]


def _deck_background_loads(points: list[FixedLoad]) -> FixedLoad:
    """Combine one occupied stack without changing any first mass moment."""
    mass = sum(point.mass_kg for point in points)
    boxes = [point.silhouette for point in points]
    return FixedLoad(
        x_m=sum(point.x_m*point.mass_kg for point in points)/mass,
        y_m=sum(point.y_m*point.mass_kg for point in points)/mass,
        z_m=sum(point.z_m*point.mass_kg for point in points)/mass,
        mass_kg=mass, kind="cargo",
        cargo_boxes=([box for point in points for box in point.cargo_boxes]
                     if all(point.cargo_boxes is not None for point in points) else None),
        silhouette=FixedLoadSilhouette(**{
            f"{axis}_{bound}_m": aggregate(getattr(box, f"{axis}_{bound}_m") for box in boxes)
            for axis in "xyz" for bound, aggregate in (("min", min), ("max", max))}),
    )


def _c3_placements(vessel: Vessel, seed: int, budget: float, *, lpp_m: float) -> list[tuple[str, float]]:
    """Production port of the reviewer's export_mass_sampler + place(C3)."""
    profile = load_param_file("cargo", "feeder_export")
    equipment = next(item for item in profile["equipment"] if item.get("iso_size_type", "").startswith("22G"))
    distribution = equipment["loaded_weight_kg"]
    lo, mode, hi = (distribution[key]["value"] for key in ("min", "mode", "max"))
    rng = Random(seed)
    masses, total = [], 0.0
    for _ in vessel.cells:
        mass = 2200.0 if rng.random() < equipment["empty_share"]["value"] else min(rng.triangular(lo, hi, mode), 30480.0)
        if total + mass > budget:
            break
        masses.append(mass)
        total += mass
    cells = {cell.id: cell for cell in vessel.cells}
    entries = []
    for stack in vessel.stacks:
        for index, cid in enumerate(stack.cell_ids):
            cell = cells[cid]
            if cell.accepts_20:
                key = (int(stack.on_deck), index, round(abs(cell.lcg_m - 0.485*lpp_m), 3),
                       round(abs(cell.tcg_m), 3), cell.tcg_m)
                entries.append((key, stack.id, cell.id))
    entries.sort()
    stacks = {stack.id: stack for stack in vessel.stacks}
    occupied, used_mass, used_tiers = set(), dict.fromkeys(stacks, 0.0), dict.fromkeys(stacks, 0)
    placed = []
    for mass in sorted(masses, reverse=True):
        for key, sid, cid in entries:
            stack = stacks[sid]
            if cid in occupied or key[1] != used_tiers[sid] or (stack.on_deck and mass >= 25000):
                continue
            if used_mass[sid] + mass > (stack.max_mass_20_kg or stack.max_mass_kg) + 1e-6:
                continue
            if (used_tiers[sid] + 1)*CONTAINER_HEIGHT_STD_M > stack.max_height_m + 1e-9:
                continue
            occupied.add(cid)
            used_mass[sid] += mass
            used_tiers[sid] += 1
            placed.append((cid, mass))
            break
    return placed


def background_condition(vessel: Vessel, stack_ids: set[str], *, preset: str = "custom",
                         expected_load_mass_kg: float | None = None, seed: int | None = None) -> tuple[list, str]:
    """Return fixed point loads plus deterministic, versioned audit metadata."""
    full, params, design, base = _parent(vessel)
    if any(load.kind == "cargo" and load.mass_kg > 0 and load.cargo_boxes is None for load in base):
        raise ValueError("base fixed cargo lacks per-box D42 support evidence; reload an explicitly represented parent")
    # A named parent can already reserve original registry columns as fixed
    # cargo. Registry recovery restores those columns for geometry, but must
    # not make their capacity available for a second synthetic allocation.
    # Keep the full parent for provenance/catalog recovery and give every
    # allocation stage the same view with all base-box support columns removed.
    # Reserving columns is distinct from decision work: do not mark their hatch
    # covers active or exclude other deck columns merely for sharing that cover.
    support_cells = {cell.id: cell for cell in
                     [*full.cells, *full.fixed_load_cells, *vessel.fixed_load_cells]}
    reserved_columns = set()
    for load in base:
        for box in load.cargo_boxes or []:
            for cid in box.cell_ids:
                if cid not in support_cells:
                    raise ValueError(f"base fixed cargo support geometry unavailable for {cid}")
                reserved_columns.add(support_cells[cid].stack_id)
    allocation_full = full
    if reserved_columns.intersection(stack.id for stack in full.stacks):
        # This local view is consumed only by allocation helpers, which use
        # cells/stacks directly; no caller or physical support catalog changes.
        allocation_full = full.model_copy(update={
            "cells": [cell for cell in full.cells if cell.stack_id not in reserved_columns],
            "stacks": [stack for stack in full.stacks if stack.id not in reserved_columns],
        })
    explicit_mass = expected_load_mass_kg is not None
    if explicit_mass and (not math.isfinite(expected_load_mass_kg) or expected_load_mass_kg < 0):
        raise ValueError("expected slice cargo mass must be finite and nonnegative")
    previous = slice_condition(vessel)
    seed = seed if seed is not None else (previous["seed"] if previous and previous["preset"] == preset
                                          else PRESET_SEEDS[preset])
    h = full.hull
    lightship = h.lightship_mass_kg if h else (params["hull"]["lightship_mass_kg"] if params else 0.0)
    if params is not None:
        consumables = sum(0.97 * tank["capacity_m3"] * tank["content_density_kg_m3"]
                          for tank in params["tanks"] if not tank.get("adjustable", False))
        lpp = params["hull"]["lpp_m"]
    else:
        consumables = sum(departure_tank_masses(full).values())
        lpp = h.lpp_m if h else max(cell.lcg_m for cell in full.cells) / 0.9
    target = 0.97 * design if design is not None else None
    ballast_reserve = None
    ballast_masses = {}
    ballast_mass = 0.
    if explicit_mass and params is not None:
        from stowage.vessel.fleet_balance import (ballast_moment_reserve,
            stability_ballast_masses, stability_deck_profile)

        ballast_reserve = ballast_moment_reserve(params, design)
        target = ballast_reserve["recommended_departure_mass_kg"]
        ballast_masses = stability_ballast_masses(params)
        ballast_mass = sum(ballast_masses.values())
    budget = max(0.0, target - lightship - consumables - ballast_mass - sum(load.mass_kg for load in base)) if target else float("inf")
    booking_ref = None
    if explicit_mass:
        from stowage.vessel.fleet_booking import booking_reference

        # APPROXIMATION: D48's heavy-low equivalent booking is a design
        # reference only. Anchor it to the unconditioned preset C3 profile,
        # independently of the actual background draw and ballast headroom.
        # Keep actual cropped decision cells/columns, not the recovered root's
        # omitted tiers. Capacity/prefix checks happen before the CG is used.
        reference_seed = PRESET_SEEDS[preset]
        reference_budget = (max(0., .97*design-lightship-consumables
                                -sum(load.mass_kg for load in base))
                            if design is not None else float("inf"))
        reference_draw = _c3_placements(allocation_full, reference_seed,
                                        reference_budget, lpp_m=lpp)
        region = vessel.model_copy(update={
            "cells": [cell for cell in vessel.cells if cell.stack_id in stack_ids],
            "stacks": [stack for stack in vessel.stacks if stack.id in stack_ids],
        })
        try:
            booking_ref = booking_reference(region, reference_draw,
                expected_load_mass_kg=expected_load_mass_kg, reference_seed=reference_seed)
        except ValueError as exc:
            # Preserve the public allocation-failure prefix used by generator
            # rejection accounting; an oversized reference is never fabricated.
            raise ValueError("cannot balance slice background within physical cargo capacities: "
                             f"D48 booking reference: {exc}") from exc
        booking_ref["anchor_budget_kg"] = (reference_budget
                                           if math.isfinite(reference_budget) else None)
    placements = _c3_placements(allocation_full, seed, budget, lpp_m=lpp)
    cells = {cell.id: cell for cell in full.cells}
    stacks = {stack.id: stack for stack in full.stacks}
    active_covers = {stacks[sid].hatch_cover_id for sid in stack_ids}
    kept = [(cid, mass) for cid, mass in placements if cells[cid].stack_id not in stack_ids
            and not (stacks[cells[cid].stack_id].on_deck and
                     stacks[cells[cid].stack_id].hatch_cover_id in active_covers)]
    own_c3 = sum(mass for cid, mass in placements if cells[cid].stack_id in stack_ids)
    if not explicit_mass:
        # Tiny-chain's default six empty dry20s; other rungs reserve the C3
        # cargo in their decision cells. Actual bookings can recondition later.
        expected_load_mass_kg = 6 * 2350.0 if preset == "tiny" else own_c3
    if not math.isfinite(expected_load_mass_kg) or expected_load_mass_kg < 0:
        raise ValueError("expected slice cargo mass must be finite and nonnegative")
    desired = max(0.0, budget - expected_load_mass_kg)
    raw_mass = sum(mass for _, mass in kept)
    # Fixture/foreign geometry may already be a fragment with no recoverable
    # full cargo region. Preserve its C3 point masses; do not invent enormous
    # loads to reach an unrelated whole-ship displacement.
    scale = (desired / raw_mass if params is not None else 1.0) if raw_mass else 0.0
    scaled = [(cid, mass*scale) for cid, mass in kept if mass*scale > 0]
    if params is not None:
        from stowage.vessel.fleet_balance import bound_background_mass

        if scaled and not explicit_mass:
            scaled = bound_background_mass(allocation_full, scaled, stack_ids=stack_ids)
    gm_screen = None
    geometry_only_deferred = False
    if params is not None and scaled:
        selected_mass = [(cells[cid], mass) for cid, mass in placements
                         if cells[cid].stack_id in stack_ids]
        if explicit_mass:
            own_cg = tuple(booking_ref["cg_m"])
        else:
            # Preserve unbooked preset conditions; D48 applies when the actual
            # booking mass is supplied. Tiny's default is its six empty boxes.
            if preset == "tiny" or not selected_mass:
                selected = [cell for cell in vessel.cells if cell.stack_id in stack_ids]
                selected_mass = [(cell, 1.0) for cell in selected]
            own_weight = sum(mass for _, mass in selected_mass)
            own_cg = tuple(sum(mass*getattr(cell, attr) for cell, mass in selected_mass)/own_weight
                           for attr in ("lcg_m", "tcg_m", "vcg_base_m"))
            own_cg = (*own_cg[:2], own_cg[2]+CONTAINER_HEIGHT_STD_M/2)
        if explicit_mass:
            from stowage.vessel.fleet_balance import balance_background

            scaled, gm_screen = balance_background(
                allocation_full, params, scaled, stack_ids=stack_ids,
                expected_load_mass_kg=expected_load_mass_kg, expected_load_cg_m=own_cg,
                base_loads=base, ballast_masses_kg=ballast_masses, seed=seed)
            # A sparse outside region cannot always support near-full ship
            # displacement legally. Reduce departure mass, never stack ratings
            # or the physical empty-background moment constraints.
            # APPROXIMATION: search the synthetic loading-manual condition at
            # 1%-of-design mass intervals, down to 85% of design displacement.
            mass_attempts = [target]
            while not gm_screen["target_reached"] and target > .85*design+1e-6:
                target = max(.85*design, target-.01*design)
                mass_attempts.append(target)
                budget = max(0., target-lightship-consumables-ballast_mass-sum(load.mass_kg for load in base))
                desired = max(0., budget-expected_load_mass_kg)
                scale = desired/raw_mass if raw_mass else 0.
                trial = [(cid, mass*scale) for cid, mass in kept if mass*scale > 0]
                scaled, gm_screen = balance_background(
                    allocation_full, params, trial, stack_ids=stack_ids,
                    expected_load_mass_kg=expected_load_mass_kg, expected_load_cg_m=own_cg,
                    base_loads=base, ballast_masses_kg=ballast_masses, seed=seed)
            gm_screen["departure_mass_attempts_kg"] = mass_attempts
            ballast_reserve["selected_departure_mass_kg"] = target
            ballast_reserve["actual_displacement_headroom_kg"] = design-target
            if not gm_screen["target_reached"]:
                if vessel.hull is not None:
                    raise ValueError("cannot balance slice background within physical cargo capacities: "
                                     + str(gm_screen.get("optimizer_message", gm_screen.get("failure"))))
                # Geometry diagnostics deliberately omit the hull. Retain a
                # useful mass-scaled fixture, explicitly without a successful
                # physics condition; do not disguise it as an accepted balance.
                geometry_only_deferred = True
                gm_screen["conditioning_status"] = "deferred_missing_hull"
                gm_screen["failed_departure_mass_kg"] = target
                gm_screen["deferred_reason"] = (
                    "Geometry-only vessel: no feasible booking-reference background "
                    "was found; missing hull prevents physics acceptance")
                target = mass_attempts[0]
                budget = max(0., target-lightship-consumables-ballast_mass-sum(load.mass_kg for load in base))
                desired = max(0., budget-expected_load_mass_kg)
                scale = desired/raw_mass if raw_mass else 0.
                trial = [(cid, mass*scale) for cid, mass in kept if mass*scale > 0]
                try:
                    scaled = bound_background_mass(allocation_full, trial, stack_ids=stack_ids)
                except ValueError:
                    # A missing-hull diagnostic may lack enough outside cargo
                    # room even for the mass target. Preserve only the available
                    # physical C3 cargo; report its actual departure below.
                    scaled = bound_background_mass(allocation_full, kept, stack_ids=stack_ids)
                    target = (lightship+consumables+ballast_mass+sum(load.mass_kg for load in base)
                              +sum(mass for _, mass in scaled)+expected_load_mass_kg)
                    gm_screen["deferred_reason"] += "; target exceeds outside stack capacity"
                ballast_reserve["selected_departure_mass_kg"] = target
                ballast_reserve["actual_displacement_headroom_kg"] = design-target
        else:
            from stowage.vessel.fleet_gm import condition_deck_tiers

            # Unbooked presets are C3 geometry/design diagnostics. Only the
            # explicit final booking enables D58 conditioning and acceptance.
            scaled, gm_screen = condition_deck_tiers(
                allocation_full, params, scaled, stack_ids=stack_ids,
                expected_load_mass_kg=expected_load_mass_kg, expected_load_cg_m=own_cg,
                base_loads=base)
    # Keep physical support identity after cells become fixed point loads. The
    # checker must audit these records against the original parent geometry;
    # aggregate mass or a silhouette alone cannot establish a stack rating.
    background_cells = [dict(cell_id=cid, stack_id=cells[cid].stack_id, mass_kg=mass)
                        for cid, mass in scaled]
    background_stacks = []
    for sid in sorted({cells[cid].stack_id for cid, _ in scaled}):
        stack = stacks[sid]
        values = {cid: mass for cid, mass in scaled if cells[cid].stack_id == sid}
        cap = stack.max_mass_20_kg or stack.max_mass_kg
        if sum(values.values()) > cap+1e-5:
            raise ValueError(f"slice background overloads physical stack {sid}")
        limit = 24999. if stack.on_deck else 30480.
        if any(mass > limit+1e-5 for mass in values.values()):
            raise ValueError(f"slice background overloads physical cell in {sid}")
        ordered = [values.get(cid, 0.) for cid in stack.cell_ids]
        if any(upper > lower for lower, upper in zip(ordered, ordered[1:])):
            raise ValueError(f"slice background violates tier support masses in {sid}")
        background_stacks.append(dict(stack_id=sid, max_mass_20_kg=cap,
                                      mass_kg=sum(values.values()),
                                      max_cell_mass_kg=limit,
                                      on_deck=stack.on_deck))
    # APPROXIMATION: D42's equivalent ISO20 records describe the allocated
    # continuous D29 mass; they do not introduce a second mass or real cargo.
    profile_sha = hashlib.sha256((params_dir("cargo")/"feeder_export.toml").read_bytes()).hexdigest()
    box_provenance = Provenance(
        created_by="vessel.fleet_conditions", seed=seed,
        versions={"slice_condition": CONDITION_VERSION},
        parents=[full.id, "experiments/physics-review/design/harness.py:C3",
                 f"params/cargo/feeder_export.toml sha256:{profile_sha}",
                 "APPROXIMATION: synthetic D29 equivalent cell mass represented as a standard-height ISO20 box",
                 *(source for source in full.sources if source.startswith("params/fleet/"))],
    ).merge_nested(vessel, full)
    cargo: list[FixedLoad] = []
    deck_stacks: dict[str, list[FixedLoad]] = {}
    for cid, mass in scaled:
        cell = cells[cid]
        stack = stacks[cell.stack_id]
        silhouette = None
        if stack.on_deck:
            silhouette = FixedLoadSilhouette(
                x_min_m=cell.lcg_m-CONTAINER_LENGTH_M[20]/2,
                x_max_m=cell.lcg_m+CONTAINER_LENGTH_M[20]/2,
                y_min_m=cell.tcg_m-CONTAINER_WIDTH_M/2,
                y_max_m=cell.tcg_m+CONTAINER_WIDTH_M/2,
                z_min_m=cell.vcg_base_m, z_max_m=cell.vcg_base_m+CONTAINER_HEIGHT_STD_M)
        box = FixedCargoBox(
            cell_ids=[cid], length_ft=20, height_m=CONTAINER_HEIGHT_STD_M,
            gross_mass_kg=mass, x_m=cell.lcg_m, y_m=cell.tcg_m,
            z_m=cell.vcg_base_m+CONTAINER_HEIGHT_STD_M/2,
            stack_id=cell.stack_id, tier=parse_slot(cid).tier,
            provenance=box_provenance,
        )
        point = FixedLoad(x_m=cell.lcg_m, y_m=cell.tcg_m,
                          z_m=cell.vcg_base_m+CONTAINER_HEIGHT_STD_M/2,
                          mass_kg=mass, kind="cargo", silhouette=silhouette, cargo_boxes=[box])
        if stack.on_deck:
            deck_stacks.setdefault(stack.id, []).append(point)
        else:
            cargo.append(point)
    cargo.extend(_deck_background_loads(deck_stacks[sid]) for sid in sorted(deck_stacks))
    loads = base + cargo
    actual = lightship + consumables + ballast_mass + sum(load.mass_kg for load in loads) + expected_load_mass_kg
    witness_ballast = dict(ballast_masses)
    witness_audit = None
    booking_sizing = None
    booking_budget = None
    if explicit_mass and params is not None:
        from stowage.vessel.fleet_balance import (empty_background_ballast_witness,
                                                 booking_reference_ballast_sizing)

        witness_ballast, witness_audit = empty_background_ballast_witness(
            full, params, loads, ballast_masses)
        booking_sizing = booking_reference_ballast_sizing(full, params, loads,
            booking_mass_kg=expected_load_mass_kg, booking_cg_m=tuple(booking_ref["cg_m"]),
            booking_ballast=ballast_masses)
        if full.hull is not None and booking_sizing.get("status") != "sized":
            raise ValueError("cannot balance slice background within physical cargo capacities: "
                             "D36/D48 booking-reference ballast mass/moment sizing: "
                             + str(booking_sizing.get("status")))
        maximum = full.hull.max_displacement_kg if full.hull is not None else design
        booking_budget = dict(
            lightship_mass_kg=lightship, consumables_mass_kg=consumables,
            fixed_load_mass_kg=sum(load.mass_kg for load in loads),
            booking_mass_kg=expected_load_mass_kg, booking_ballast_mass_kg=ballast_mass,
            expected_booked_displacement_kg=actual, maximum_displacement_kg=maximum,
            remaining_displacement_headroom_kg=maximum-actual,
            within_displacement_limit=actual <= maximum+1e-5,
            empty_witness_ballast_included=False, actual_plan_certificate=False)
    info = dict(version=CONDITION_VERSION, preset=preset, seed=seed,
                base_fixed_loads=[load.model_dump(mode="json") for load in base],
                implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                cargo_profile_sha256=profile_sha,
                target_displacement_kg=target, design_displacement_kg=design,
                expected_load_mass_kg=expected_load_mass_kg, consumables_mass_kg=consumables,
                booking_reference=booking_ref,
                departure_ballast_masses_kg=ballast_masses,
                empty_background_witness_ballast_masses_kg=witness_ballast,
                empty_background_witness_sizing=witness_audit,
                booking_reference_ballast_sizing=booking_sizing,
                booking_departure_budget=booking_budget,
                stability_ballast_mass_kg=ballast_mass,
                required_arrival_b23_ratio=1.05 if explicit_mass else None,
                stability_target=dict(arrival_b23_ratio_min=1.05,
                    conditions=["departure", "mid_voyage", "arrival"],
                    design_screen="per_ship_empty_background_solid_GM",
                    acceptance="ballast_finisher_then_every_full_checker_part",
                    real_ballast_policy="full_symmetric_double_bottoms",
                    stack_rating_fraction=.98, **stability_deck_profile(params))
                    if explicit_mass and params is not None else None,
                unscaled_decision_c3_mass_kg=own_c3,
                expected_load_policy=("explicit_booking_mass" if explicit_mass else
                                      "six_empty_dry20" if preset == "tiny" else "C3_selected_cells"),
                fixed_cargo_mass_kg=sum(load.mass_kg for load in cargo),
                fixed_load_mass_kg=sum(load.mass_kg for load in loads),
                expected_displacement_kg=actual, cargo_scale=scale,
                background_teu_count=len(scaled),
                background_cells=background_cells, background_stacks=background_stacks,
                background_silhouette_blocks=len(deck_stacks),
                cargo_t_per_teu=(sum(load.mass_kg for load in cargo) / len(scaled) / 1000 if cargo else 0.0),
                gm_screen=gm_screen,
                ballast_moment_reserve=ballast_reserve,
                departure_policy=("geometry_only_unconditioned" if geometry_only_deferred else
                                  "empty_background_even_keel" if explicit_mass and params is not None
                                  else "preset_C3"),
                expected_load_t_per_teu=EXPORT_MEAN_KG_PER_TEU/1000,
                achieved_design_band=design is not None and 0.9*design <= actual <= design,
                omitted_deck_windage_represented=True,
                source="experiments/physics-review/design/harness.py:C3; conditions.md; params/cargo/feeder_export.toml")
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if explicit_mass and params is not None and rule_freeze_v1_enabled():
        info["empty_background_witness_context"] = dict(
            assessment_context="seagoing_witness", policy_version="D64-rev3.1-seagoing-witness-v1",
            heel_policy="ordinary_ballast_without_in_port_pair_total",
            initializes_actual_arrival=False, consumes_booking_headroom=False)
    return loads, CONDITION_PREFIX + json.dumps(info, sort_keys=True, separators=(",", ":"))
