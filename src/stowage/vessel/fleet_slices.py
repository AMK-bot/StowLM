"""Small decision regions aboard a whole ship, rather than shortened hulls.

A slice keeps the parent's whole lightship, hydrostatics, strength stations, tanks,
bridge and heat sources. Only the cargo cells available to the planner shrink.
Cargo outside that region becomes fixed point loads: these contribute mass and
moments to stability and longitudinal strength, but cannot be moved by a planner.
This is the weight/moment accounting described by ``model.vessel.Vessel`` and
the longitudinal load grouping in Larsen & Pacino (2020), *A heuristic and a
benchmark for the stowage planning problem*. It is not a checker approval.

APPROXIMATION: the rest of the ship is a fixed synthetic C3 export-mix condition,
scaled toward design draught, with heavy cargo in holds. Deck stacks sharing an
active cover remain empty. See fleet_conditions.py for seeds, expected decision
cargo and 97% consumable initialization. Each fixed deck stack carries an occupied
silhouette box for windage and visibility; hold loads remain unexposed points.
Point loads preserve discrete cargo moments, not its continuous weight profile.
The whole lightship already includes hatch covers; do not add them again.
DG Document-of-Compliance areas keep their permissions, equipment assumptions
and sources, with stack membership restricted to the retained decision region.
Medium regions spread their bay pairs from the first to the last cargo bay.
This leaves background cargo positions on both sides of the decision region's
mean longitudinal position; a low-volume booking in a forward-only half-ship
region could not otherwise balance realistic background cargo near design
draught. The selected pairs remain explicit real cells, not a shortened hull.
Geometry-only parents remain geometry-only and require missing-data rejection
by physics checkers. No fabricated hull or permissive limits are introduced.

Generator API (3.5/4.3): select the final decision geometry, then call
``recondition_slice(vessel, expected_load_mass_kg=sum(box.gross_mass_kg for box
in load_boxes), seed=instance_seed)``. Use the returned vessel and initialize
``ShipState.tank_masses_kg`` with ``departure_tank_masses(vessel)`` from
``stowage.vessel``. This must follow canonical six-cell selection; otherwise a
full-parent fallback can discard the intended loaded-ship context. Only actual
load-list boxes contribute to expected mass. Yard blockers do not. The complete
example is in ``fleet_conditions.py``; initialize the audited low-ballast recipe,
which remains adjustable by the finisher. Liquids must not also enter fixed_loads.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
import zlib

from stowage.conventions import canonical_vessel_id
from stowage.model import Vessel

SLICE_PRESETS = ("tiny", "small", "medium", "full")
_PARENT_PREFIX = "stowage.slice_parent:v1:"


def recover_parent(vessel: Vessel) -> Vessel:
    """Recover an imported/foreign slice's original geometry after serialization.

    Named feeders are recovered from pinned parameters by ``fleet_conditions``.
    Foreign vessels have no such registry, so their first slice stores a compact,
    lossless JSON snapshot. This is model data, never pickle or executable input.
    Existing slices require both the snapshot and their loading-condition audit.
    Without the audit, synthetic background cannot safely be distinguished from
    original stores; without the snapshot, omitted cells cannot be reconstructed.
    """
    from stowage.vessel.fleet_conditions import slice_condition

    records = [source for source in vessel.sources if source.startswith(_PARENT_PREFIX)]
    previous = slice_condition(vessel)
    if records:
        try:
            payload = zlib.decompress(base64.b64decode(records[-1][len(_PARENT_PREFIX):], validate=True))
            parent = Vessel.model_validate_json(payload)
        except (ValueError, zlib.error) as exc:
            raise ValueError("invalid slice parent snapshot; reload the full vessel before slicing") from exc
        if canonical_vessel_id(parent.slice_of or parent.id) != canonical_vessel_id(vessel.slice_of or vessel.id):
            raise ValueError("slice parent snapshot has a different root vessel")
        if previous is None:
            raise ValueError("slice condition metadata unavailable; reload the full vessel before slicing")
        return parent.model_copy(update={"hull": vessel.hull, "tanks": vessel.tanks,
                                         "voyage_policy": vessel.voyage_policy})
    if vessel.slice_of is not None or previous is not None:
        raise ValueError("original parent geometry unavailable; reload the full vessel before slicing")
    return vessel


def _parent_record(vessel: Vessel) -> str | None:
    from stowage.vessel.fleet import list_fleet

    if canonical_vessel_id(vessel.slice_of or vessel.id) in list_fleet():
        return None
    records = [source for source in vessel.sources if source.startswith(_PARENT_PREFIX)]
    if records:
        return records[-1]
    # Keep one root snapshot, not one per slice; the compression only avoids
    # repeating a large imported vessel's verbose cell/stack JSON on disk.
    payload = zlib.compress(recover_parent(vessel).model_dump_json().encode(), level=6)
    return _PARENT_PREFIX + base64.b64encode(payload).decode("ascii")


def _fixed_support_payload(vessel: Vessel, stack_ids: set[str]) -> dict[str, list[dict]]:
    """Keep whole outside columns as non-decision D42 support geometry.

    Unoccupied declared cells are retained: deleting them would change the
    physical bottom-up prefix checked for background boxes. Reconstruct earlier
    synthetic supports from the root, while preserving original fixed supports
    (including custom stores/cargo supports absent from the named fleet file).
    The catalogs describe existing geometry and add no mass.
    """
    from stowage.vessel.fleet_conditions import _parent

    full, _, _, base = _parent(vessel)
    catalogs = {field: {} for field in ("cells", "stacks", "hatch_covers")}

    def merge(field, items):
        for item in items:
            previous = catalogs[field].get(item.id)
            if previous is not None:
                if field == "hatch_covers":
                    membership = {"hold_stack_ids", "deck_stack_ids"}
                    if previous.model_dump(exclude=membership) != item.model_dump(exclude=membership):
                        raise ValueError(f"conflicting fixed cargo hatch cover geometry {item.id}")
                    item = item.model_copy(update={
                        key: sorted(set(getattr(previous, key)) | set(getattr(item, key)))
                        for key in membership})
                elif previous != item:
                    raise ValueError(f"conflicting fixed cargo {field} geometry {item.id}")
            catalogs[field][item.id] = item

    def crop_covers(covers, retained):
        for cover in covers:
            hold = [sid for sid in cover.hold_stack_ids if sid in retained]
            deck = [sid for sid in cover.deck_stack_ids if sid in retained]
            if hold or deck:
                yield cover.model_copy(update={"hold_stack_ids": hold, "deck_stack_ids": deck})

    for field in catalogs:
        merge(field, getattr(full, f"fixed_load_{field}"))

    full_stacks = {stack.id for stack in full.stacks}
    outside = full_stacks - stack_ids
    merge("cells", (cell for cell in full.cells if cell.stack_id in outside))
    merge("stacks", (stack for stack in full.stacks if stack.id in outside))
    merge("hatch_covers", crop_covers(full.hatch_covers, outside))

    # Named-parent recovery reloads its original parameter geometry. Original
    # fixed supports added by a caller may exist only on the current vessel.
    # Retain those and each base box's entire support column, but rebuild old
    # synthetic outside columns from the root instead of carrying stale copies.
    support_cells = {cell.id: cell for cell in [*full.fixed_load_cells, *vessel.fixed_load_cells]}
    base_stacks = set()
    for load in base:
        for box in load.cargo_boxes or []:
            for cid in box.cell_ids:
                if cid not in support_cells:
                    raise ValueError(f"original fixed cargo support geometry unavailable for {cid}")
                base_stacks.add(support_cells[cid].stack_id)
    inherited = {stack.id for stack in vessel.fixed_load_stacks
                 if stack.id not in full_stacks or stack.id in base_stacks}
    merge("cells", (cell for cell in vessel.fixed_load_cells if cell.stack_id in inherited))
    merge("stacks", (stack for stack in vessel.fixed_load_stacks if stack.id in inherited))
    merge("hatch_covers", crop_covers(vessel.fixed_load_hatch_covers, inherited))
    return {f"fixed_load_{field}": [items[key].model_dump() for key in sorted(items)]
            for field, items in catalogs.items()}


def _slice_stacks(vessel: Vessel, stack_ids: set[str], *, name: str,
                  preset: str = "custom", expected_load_mass_kg: float | None = None,
                  seed: int | None = None) -> Vessel:
    stacks = [stack for stack in vessel.stacks if stack.id in stack_ids]
    if not stacks:
        raise ValueError("a vessel slice must retain at least one stack")
    cells = [cell for cell in vessel.cells if cell.stack_id in stack_ids]
    covers = []
    for cover in vessel.hatch_covers:
        hold = [sid for sid in cover.hold_stack_ids if sid in stack_ids]
        deck = [sid for sid in cover.deck_stack_ids if sid in stack_ids]
        if hold or deck:
            # The entire physical cover remains aboard; only decision stacks change.
            covers.append(cover.model_copy(update={"hold_stack_ids": hold, "deck_stack_ids": deck}))
    from stowage.vessel.fleet_conditions import CONDITION_PREFIX, background_condition

    background, condition = background_condition(vessel, stack_ids, preset=preset,
                                                  expected_load_mass_kg=expected_load_mass_kg, seed=seed)
    parent_record = _parent_record(vessel)
    payload = vessel.model_dump()
    payload.update(_fixed_support_payload(vessel, stack_ids))
    payload.update(id=name, name=name, nominal_teu=len(cells),
                   cells=[cell.model_dump() for cell in cells],
                   stacks=[stack.model_dump() for stack in stacks],
                   hatch_covers=[cover.model_dump() for cover in covers],
                   dg_doc_areas=[{**area.model_dump(),
                                  "stack_ids": [sid for sid in area.stack_ids if sid in stack_ids]}
                                 for area in vessel.dg_doc_areas
                                 if any(sid in stack_ids for sid in area.stack_ids)],
                   slice_of=vessel.slice_of or vessel.id, fixed_loads=background,
                   sources=[*(source for source in vessel.sources
                              if not source.startswith((CONDITION_PREFIX, _PARENT_PREFIX))),
                            *([parent_record] if parent_record is not None else []),
                            condition,
                            "APPROXIMATION: slice background is a fixed synthetic C3 export-mix condition; "
                            "97% consumables require departure_tank_masses; background deck stacks "
                            "carry occupied silhouette boxes; whole hull retained"])
    return Vessel.model_validate(payload)


def slice_vessel(vessel: Vessel, bays: Sequence[int], *, name: str) -> Vessel:
    """Retain the specified odd physical bays, with whole-ship physics and fixed cargo.

    Even (forty-foot slot) bays and unknown bays are errors. A single odd bay is
    valid for twenty-foot boxes; paired long boxes still require both cells.
    Repeated slicing rebuilds the background without duplicating fixed cargo.
    Foreign slices lacking their parent geometry or condition audit are rejected; reload
    their full parent and slice it again to obtain recoverable slices.
    ``name`` is the resulting vessel slug and display name.
    """
    selected = set(bays)
    available = {stack.bay for stack in vessel.stacks}
    if not selected or any(type(bay) is not int or bay % 2 != 1 for bay in selected):
        raise ValueError("bays must be a nonempty sequence of odd integer physical bays")
    if not selected <= available:
        raise ValueError(f"unknown physical bays: {sorted(selected - available)}")
    return _slice_stacks(vessel, {stack.id for stack in vessel.stacks if stack.bay in selected}, name=name)


def slice_preset(vessel: Vessel, size: str, *, name: str | None = None) -> Vessel:
    """Return tiny/small/medium/full decision regions in a deterministic size ladder.

    Tiny retains one paired row of a hatch with both hold and deck cells
    (two odd bays, two hold stacks and two deck stacks),
    small the first two physical bays with every row, medium half the bay pairs
    spread evenly along the ship (including the first and last when possible),
    and full every cell. Medium keeps the first pair, preserving the nesting of
    tiny and small regions on the feeders. Pairing follows the BBBRRTT even-slot convention.
    Full is an independent validated copy without adding background cargo.
    """
    if size not in SLICE_PRESETS:
        raise ValueError(f"unknown size preset {size!r}; expected {SLICE_PRESETS}")
    if size == "full":
        payload = vessel.model_dump()
        if name is not None:
            payload.update(id=name, name=name)
        return Vessel.model_validate(payload)
    result_name = name or f"{vessel.id}.{size}"
    bays = sorted({stack.bay for stack in vessel.stacks})
    if size == "tiny":
        by_id = {stack.id: stack for stack in vessel.stacks}
        for cover in vessel.hatch_covers:
            hold = [by_id[sid] for sid in cover.hold_stack_ids if by_id[sid].cell_ids]
            deck = [by_id[sid] for sid in cover.deck_stack_ids if by_id[sid].cell_ids]
            rows = sorted({stack.row for stack in hold} & {stack.row for stack in deck})
            for row in rows:
                bays_in_both = ({stack.bay for stack in hold if stack.row == row}
                                & {stack.bay for stack in deck if stack.row == row})
                for bay in sorted(bays_in_both):
                    if bay + 2 not in bays_in_both:
                        continue
                    selected = {stack.id for stack in hold + deck
                                if stack.row == row and stack.bay in (bay, bay + 2)}
                    return _slice_stacks(vessel, selected, name=result_name, preset=size)
        raise ValueError("tiny preset requires paired hold and deck stacks under one hatch cover")
    if size == "small":
        selected_bays = set(bays[:2])
    else:
        pairs: dict[int, list[int]] = {}
        for bay in bays:
            pairs.setdefault((bay-1)//4, []).append(bay)
        bay_pairs = [pairs[key] for key in sorted(pairs)]
        pair_count = len(bay_pairs)
        kept_count = (pair_count+1)//2
        indices = ({round(i*(pair_count-1)/(kept_count-1)) for i in range(kept_count)}
                   if kept_count > 1 else {0})
        selected_bays = {bay for index in indices for bay in bay_pairs[index]}
    return _slice_stacks(vessel, {stack.id for stack in vessel.stacks if stack.bay in selected_bays},
                         name=result_name, preset=size)


def recondition_slice(vessel: Vessel, *, expected_load_mass_kg: float,
                      seed: int | None = None) -> Vessel:
    """Rebuild the background after the actual decision cargo mass is known.

    Instance generators should call this on their final selected geometry,
    then initialize live tanks with ``departure_tank_masses``. This also repairs
    the full-parent fallback used by the canonical six-cell tiny selector.
    D48 declares a bounded heavy-low equivalent booking CG on the actual decision
    cells, recorded in the condition audit. That reference is not a stow or
    additional cargo. Background
    mass reserves deadweight for the booking; its CG remains an audit reference.
    The EMPTY decision-region background targets upright even keel and a per-ship
    stability screen, with real low double-bottom ballast and moment headroom.
    The generator requires arrival B 2.3 ratio >= 1.05 and full D57 acceptance.
    Empty-plan acceptance uses a separate audited witness recipe when ballast
    must replace missing booking cargo to retain IS Code A 2.3.5 draught. The
    actual booked initializer and its deadweight budget retain the original
    double-bottom recipe; empty-witness ballast is never added to that budget.
    This synthetic condition is not a checker acceptance certificate.
    For a production parent, raises ValueError when the booking-reference condition cannot fit
    the outside cargo cells within mass, moment and stack-capacity bounds;
    this is not a proof that every possible stow of those bookings is infeasible.
    Geometry-only parents instead retain an explicitly unconditioned diagnostic
    background (audit policy ``geometry_only_unconditioned``), without a hull
    or any physics acceptance claim.
    """
    from stowage.vessel.fleet_conditions import slice_condition

    info = slice_condition(vessel)
    preset = info["preset"] if info else "custom"
    return _slice_stacks(vessel, {stack.id for stack in vessel.stacks}, name=vessel.id,
                         preset=preset, expected_load_mass_kg=expected_load_mass_kg, seed=seed)


def load_fleet_preset(name: str, size: str, *, with_hull: bool = True) -> Vessel:
    """Load a feeder and apply a named size preset (for instance generators)."""
    from stowage.vessel.fleet import _parameter_bytes, load_vessel
    from stowage.vessel.fleet_cache import cached_vessel
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    if size not in SLICE_PRESETS:
        raise ValueError(f"unknown size preset {size!r}; expected {SLICE_PRESETS}")
    name = canonical_vessel_id(name)
    if size == "full":
        return load_vessel(name, with_hull=with_hull)
    cache_slice = f"{size}.rule-freeze-v1" if rule_freeze_v1_enabled() else size
    return cached_vessel(name, slice_name=cache_slice, with_hull=with_hull,
                         params_bytes=_parameter_bytes(name),
                         build=lambda: slice_preset(load_vessel(name, with_hull=with_hull), size))
