"""Static, per-instance tables for the feasibility engine (plan 4.4 v2).

Everything here is fixed for an instance: the slots of the vessel, which box could ever go into which slot by the
rules that do not depend on the rest of the stow (``static``), and numeric per-box / per-stack arrays the engine
evaluates with numpy. The dynamic part (what is already aboard) lives in ``_engine``.

**Slots.** A 20' slot is one physical cell (odd bay); a 40'/45' slot is an even bay whose two neighbouring odd-bay
cells exist at the same row and tier (``stowage.conventions.cells_of_slot``). A slot is *open* when each of its
cells is the next free cell of its stack (bottom-up, no gaps) — the only places a box can be put next.

**Static mask** ``static[b, s]`` is True iff box ``b`` in slot ``s`` breaks none of the 2.4 rules that depend only
on (box, slot): length/footprint and ``accepts_*`` (``expert.physical.size_fit``), reefer plug, verified gross mass,
POD resolution, E6 when its data are missing or its deck fallback is disabled (otherwise E6 is a departure condition,
D25: the engine's C1f), E8 under/on-deck requests, the quay-crane deck-tier limit (2.3 and E3 — both
apply), the box being taller than the stack's whole envelope, a hold stack without a hatch-cover mapping, and IMDG
stowage category / Document of Compliance (2.4's ``imdg_placement_violations`` on an otherwise empty ship, called exactly and memoised
per DG box and slot attributes). Rule semantics are 2.4's (``stowage.checker.rules``); tests compare against ``placement_violations``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from stowage.checker.rules import _parameter as core_parameter
from stowage.checker.rules_special import _nonnegative
from stowage.checker.rules_special import _parameter as special_parameter
from stowage.conventions import cells_of_slot, parse_slot
from stowage.model import Instance

INF = math.inf


def _numeric(value) -> float | None:
    """2.4's ``numeric``: a finite non-negative number, else None (= the checker reports missing data)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


@dataclass
class Tables:
    """Immutable after construction; shared by engine copies."""

    instance: Instance
    # cells / stacks
    cell_ids: list[str]
    cell_index: dict[str, int]
    cell_stack: np.ndarray          # (C,) stack index
    cell_k: np.ndarray              # (C,) position in stack (0 = bottom)
    stack_ids: list[str]
    stack_index: dict[str, int]
    stack_cells: list[list[int]]    # cell indices bottom → top
    stack_deck: np.ndarray          # (J,) bool
    stack_cover: list[str | None]
    stack_lim_mixed: np.ndarray     # (J,) kg
    stack_lim_20: np.ndarray        # (J,) kg (= mixed when not given)
    stack_lim_40: np.ndarray        # (J,) kg
    stack_hmax: np.ndarray          # (J,) m
    stack_base_z: np.ndarray        # (J,) m
    # slots
    slot_ids: list[str]
    slot_index: dict[str, int]
    slot_cells: np.ndarray          # (S, 2) cell indices, -1 padded
    slot_stacks: np.ndarray         # (S, 2) stack indices, -1 padded
    slot_k: np.ndarray              # (S, 2) positions, -1 padded
    slot_n: np.ndarray              # (S,) 1 or 2
    slot_deck: np.ndarray           # (S,) bool
    cover_deck_stacks: dict[str, list[int]]
    # boxes that may be placed (load list + aboard boxes not discharged here), sorted by id
    box_ids: list[str]
    box_index: dict[str, int]
    mass: np.ndarray                # (B,) gross kg
    share: np.ndarray               # (B,) kg per supporting stack (mass / 1 or 2)
    length: np.ndarray              # (B,) ft
    height: np.ndarray              # (B,) m incl. over-height
    units: np.ndarray               # (B,) cells used: 1 or 2
    is_dg: np.ndarray               # (B,) bool
    fallback: np.ndarray            # (B,) bool: OOG → exact 2.4 call per candidate
    heat: np.ndarray                # (B,) bool: away-from-heat request → cached exact E8 heat check
    closes_stack: np.ndarray        # (B,) bool: top_stowage or over-height (nothing may go above)
    valid_mass: np.ndarray          # (B,) bool
    static: np.ndarray              # (B, S) bool
    dg_class: np.ndarray            # (B,) int signature id for DG pair memo (-1 = not DG)
    # parameters (None = missing → the checker reports checker.missing_vessel_data)
    e1_threshold: float | None
    e2_valid: bool                  # D25 class parameters usable (else 2.4 reports missing data for stacked boxes)
    e2_tol: int                     # class-rank tolerance (0 = no box above a lighter class)
    e2_natural: bool                # class order is L < M < H, so allowed classes are a mass interval
    e2_bounds: tuple                # (medium_min_kg, heavy_min_kg) VGM class boundaries
    e2_order: tuple                 # class labels in rank order
    rank: np.ndarray                # (B,) class rank of each placeable box (-1 when E2 data invalid)
    e6_threshold: float | None
    e6_fallback: bool | None        # D25: deck H allowed iff no compatible hold slot left empty at departure
    crane_limit: float | None
    # discharge / bookkeeping
    discharge: frozenset[str]
    extra: dict = field(default_factory=dict)


def class_params(instance: Instance) -> dict:
    """D25 E2 parameters exactly as 2.4 reads and validates them (``rules._core``): VGM boundaries, an integer
    class-rank tolerance, ``order_mode == 'weight_class'`` and a permutation of the catalogue labels."""
    from stowage.checker.rules import _catalogue_data

    rule = "expert.e2.weight_order"
    med = _numeric(core_parameter(instance, rule, "medium_min_kg"))
    heavy = _numeric(core_parameter(instance, rule, "heavy_min_kg"))
    tol = _numeric(core_parameter(instance, rule, "tolerance"))
    order = core_parameter(instance, rule, "class_order")
    labels = list(_catalogue_data().weight_classes.parameters["labels"].value)
    mode = core_parameter(instance, rule, "order_mode")
    valid = med is not None and heavy is not None and tol is not None
    valid &= bool(valid and 0 < med < heavy)
    valid &= mode == "weight_class" and isinstance(order, list) and len(order) == len(labels) \
        and set(map(str, order)) == set(labels)
    valid &= tol is not None and tol == int(tol)
    order_t = tuple(map(str, order)) if isinstance(order, list) else tuple(labels)
    return dict(valid=bool(valid), tol=int(tol) if valid else 0, natural=bool(valid and list(order_t) == labels),
                bounds=(med or 0.0, heavy or 0.0), order=order_t, labels=tuple(labels))


def class_rank(params: dict, mass: float) -> int:
    """2.4's rank: index in the class order of the label picked by the VGM boundaries; -1 if E2 data invalid."""
    if not params["valid"]:
        return -1
    med, heavy = params["bounds"]
    label = params["labels"][int(mass >= med) + int(mass >= heavy)]
    return params["order"].index(label)


def build_tables(instance: Instance) -> Tables:
    vessel = instance.vessel
    cells = sorted(vessel.cells, key=lambda c: c.id)
    cell_ids = [c.id for c in cells]
    cell_index = {cid: i for i, cid in enumerate(cell_ids)}
    stacks = sorted(vessel.stacks, key=lambda s: s.id)
    stack_ids = [s.id for s in stacks]
    stack_index = {sid: j for j, sid in enumerate(stack_ids)}
    cell_stack = np.full(len(cells), -1, dtype=np.int64)
    cell_k = np.full(len(cells), -1, dtype=np.int64)
    stack_cells = []
    for j, st in enumerate(stacks):
        ids = [cell_index[c] for c in st.cell_ids if c in cell_index]
        stack_cells.append(ids)
        for k, ci in enumerate(ids):
            cell_stack[ci], cell_k[ci] = j, k
    covers = {h.id: h for h in vessel.hatch_covers}

    def lim(v, fallback):
        return float(v) if v is not None else float(fallback)

    # slots
    slots: list[str] = []
    for c in cell_ids:
        slots.append(c)
        a = parse_slot(c)
        for bay in (a.bay - 1, a.bay + 1):
            if bay >= 2 and bay % 2 == 0:
                sid = f"{bay:03d}{a.row:02d}{a.tier:02d}"
                if all(x in cell_index for x in cells_of_slot(sid)):
                    slots.append(sid)
    slot_ids = sorted(set(slots))
    slot_index = {s: i for i, s in enumerate(slot_ids)}
    S = len(slot_ids)
    slot_cells = np.full((S, 2), -1, dtype=np.int64)
    for i, s in enumerate(slot_ids):
        for n, c in enumerate(cells_of_slot(s)):
            slot_cells[i, n] = cell_index[c]
    slot_n = (slot_cells >= 0).sum(axis=1)
    slot_stacks = np.where(slot_cells >= 0, cell_stack[np.maximum(slot_cells, 0)], -1)
    slot_k = np.where(slot_cells >= 0, cell_k[np.maximum(slot_cells, 0)], -1)
    stack_deck = np.array([s.on_deck for s in stacks], dtype=bool)
    slot_deck = np.array([parse_slot(s).on_deck for s in slot_ids], dtype=bool)

    # parameters, exactly as 2.4 reads them
    e1 = _numeric(core_parameter(instance, "expert.e1.heavy_bottom", "heavy_threshold"))
    e2v = class_params(instance)
    e6 = _numeric(core_parameter(instance, "expert.e6.heavy_under_deck", "heavy_threshold"))
    fb = core_parameter(instance, "expert.e6.heavy_under_deck", "allow_on_deck_if_no_compatible_hold_cell")
    fb = fb if isinstance(fb, bool) else None
    override = special_parameter(instance, "port.e3.quay_height", "max_on_deck_tiers", "max_tiers")
    crane = _nonnegative(override) if override is not None else _nonnegative(instance.port.quay_crane.max_on_deck_tiers)

    # boxes
    discharge = frozenset(instance.discharge_list)
    placeable = set(instance.load_list) | {p.container_id for p in instance.onboard.placements}
    placeable -= discharge
    boxes = sorted((b for b in instance.containers if b.id in placeable), key=lambda b: b.id)
    B = len(boxes)
    mass = np.array([float(b.gross_mass_kg) for b in boxes])
    length = np.array([b.length_ft for b in boxes])
    units = np.where(length == 20, 1, 2)
    share = mass / units
    height = np.array([b.height_m + (b.oog.over_height_m if b.oog else 0.0) for b in boxes])
    valid = np.array([math.isfinite(b.gross_mass_kg) and b.gross_mass_kg > 0 and b.gross_mass_kg >= b.tare_kg
                      for b in boxes], dtype=bool)
    is_dg = np.array([b.dg is not None for b in boxes], dtype=bool)
    fallback = np.array([b.oog is not None for b in boxes], dtype=bool)
    heat = np.array(["away_from_heat" in b.special_stowage and b.oog is None for b in boxes], dtype=bool)
    closes = np.array(["top_stowage" in b.special_stowage or bool(b.oog and b.oog.over_height_m) for b in boxes],
                      dtype=bool)

    # static mask
    calls = instance.rotation.calls
    resolves = np.array([any(c.index > instance.call_index and c.locode == b.pod for c in calls) for b in boxes],
                        dtype=bool)
    acc = {L: np.array([getattr(c, f"accepts_{L}") for c in cells], dtype=bool) for L in (20, 40, 45)}
    plug = np.array([c.reefer_plug for c in cells], dtype=bool)
    sc = np.maximum(slot_cells, 0)
    pad = slot_cells < 0
    slot_acc = {L: np.all(acc[L][sc] | pad, axis=1) for L in (20, 40, 45)}
    slot_plug = np.any(plug[sc] & ~pad, axis=1)
    is20slot = slot_n == 1
    # quay-crane reach: tier ordinal within the stack (2.3 uses index + 1 <= port limit; E3 the same with override)
    port_limit = instance.port.quay_crane.max_on_deck_tiers
    ordinal = np.max(np.where(pad, -1, slot_k), axis=1) + 1
    reach = ~slot_deck | (ordinal <= port_limit)
    if crane is None:
        reach &= ~slot_deck
    else:
        reach &= ~slot_deck | (ordinal <= crane)
    # stacks: hold stack without cover mapping → missing data for any box there
    stack_ok = np.array([s.on_deck or s.hatch_cover_id in covers for s in stacks], dtype=bool)
    slot_stack_ok = np.all(np.where(pad, True, stack_ok[np.maximum(slot_stacks, 0)]), axis=1)
    hmax = np.array([s.max_height_m for s in stacks])
    slot_hmax = np.min(np.where(pad, INF, hmax[np.maximum(slot_stacks, 0)]), axis=1)

    static = np.zeros((B, S), dtype=bool)
    for L in (20, 40, 45):
        rows = length == L
        cols = slot_acc[L] & (is20slot if L == 20 else ~is20slot)
        static[np.ix_(rows, cols)] = True
    static &= (reach & slot_stack_ok)[None, :]
    static &= (valid & resolves)[:, None]
    reefer = np.array([b.reefer for b in boxes], dtype=bool)
    static[reefer] &= slot_plug[None, :]
    static &= height[:, None] <= slot_hmax[None, :] + 1e-12
    under = np.array(["under_deck" in b.special_stowage for b in boxes], dtype=bool)
    on = np.array(["on_deck" in b.special_stowage for b in boxes], dtype=bool)
    static[under] &= ~slot_deck[None, :]
    static[on] &= slot_deck[None, :]
    if e6 is None or fb is None:
        static &= ~slot_deck[None, :]      # every on-deck box gets missing E6 data at departure
    elif fb is False:
        static[valid & (mass >= e6)] &= ~slot_deck[None, :]
    rank = np.array([class_rank(e2v, m) for m in mass], dtype=np.int64)

    # IMDG stowage + Document of Compliance (exact 2.4 function on an otherwise empty ship, so no DG pairs),
    # memoised on everything it reads about the slot
    from stowage.checker.rules_imdg import imdg_placement_violations

    cellmap = {c.id: c for c in vessel.cells}
    stackmap = {s.id: s for s in vessel.stacks}
    empty = instance.onboard.model_copy(update={"placements": []})
    closed_facts = instance.rule_overrides.get("imdg.segregation.7_4_3", {}).get("slots", {})
    dg_sig: dict[tuple, int] = {}
    dg_class = np.full(B, -1, dtype=np.int64)
    facts = instance.rule_overrides.get("imdg.stowage.3_2", {}).get("containers", {})
    areas = {a.id: set(a.stack_ids) for a in getattr(vessel, "dg_doc_areas", [])}
    for bi, b in enumerate(boxes):
        if b.dg is None:
            continue
        dg_class[bi] = dg_sig.setdefault((b.dg.model_dump_json(), b.iso_type, b.length_ft,
                                          b.id if b.id in facts else None), len(dg_sig))
        memo: dict[tuple, bool] = {}
        for si in np.flatnonzero(static[bi]):
            s = slot_ids[si]
            sts = [stackmap[cellmap[c].stack_id] for c in cells_of_slot(s)]
            key = (bool(slot_deck[si]),
                   tuple((tuple(st.dg_classes_allowed) if st.dg_classes_allowed is not None else None,
                          getattr(st, "dg_doc_area_id", None),
                          st.id in areas.get(getattr(st, "dg_doc_area_id", None), ())) for st in sts),
                   repr(closed_facts.get(s)))
            if key not in memo:
                memo[key] = not any(v.severity == "hard" for v in
                                    imdg_placement_violations(instance, empty, b, s, call_index=instance.call_index))
            static[bi, si] = memo[key]

    result = Tables(
        instance=instance, cell_ids=cell_ids, cell_index=cell_index, cell_stack=cell_stack, cell_k=cell_k,
        stack_ids=stack_ids, stack_index=stack_index, stack_cells=stack_cells, stack_deck=stack_deck,
        stack_cover=[s.hatch_cover_id for s in stacks],
        stack_lim_mixed=np.array([float(s.max_mass_kg) for s in stacks]),
        stack_lim_20=np.array([lim(s.max_mass_20_kg, s.max_mass_kg) for s in stacks]),
        stack_lim_40=np.array([lim(s.max_mass_40_kg, s.max_mass_kg) for s in stacks]),
        stack_hmax=hmax, stack_base_z=np.array([s.base_z_m for s in stacks]),
        slot_ids=slot_ids, slot_index=slot_index, slot_cells=slot_cells, slot_stacks=slot_stacks, slot_k=slot_k,
        slot_n=slot_n, slot_deck=slot_deck,
        cover_deck_stacks={h.id: [stack_index[s] for s in h.deck_stack_ids if s in stack_index]
                           for h in vessel.hatch_covers},
        box_ids=[b.id for b in boxes], box_index={b.id: i for i, b in enumerate(boxes)}, mass=mass, share=share,
        length=length, height=height, units=units, is_dg=is_dg, fallback=fallback, heat=heat, closes_stack=closes,
        valid_mass=valid, static=static, dg_class=dg_class, e1_threshold=e1, e2_valid=e2v["valid"],
        e2_tol=e2v["tol"], e2_natural=e2v["natural"], e2_bounds=e2v["bounds"], e2_order=e2v["order"], rank=rank,
        e6_threshold=e6, e6_fallback=fb,
        crane_limit=crane, discharge=discharge)
    # D55 is a necessary visibility screen, distinct from the actual-state full checker. Missing
    # loading-manual/geometry evidence leaves an infinite cap rather than inventing a rejection certificate.
    from .visibility_bounds import compute_visibility_caps
    visibility = compute_visibility_caps(instance)
    tops = np.array([visibility.top_by_stack.get(s, np.inf) for s in stack_ids])
    tops.flags.writeable = False
    result.extra["stack_visibility_top"] = tops
    result.extra["visibility_cap_key"] = visibility.cache_key
    result.extra["visibility_cap_diagnostics"] = visibility.diagnostics
    return result
