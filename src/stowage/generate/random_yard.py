"""Fast synthetic stage-A problems, independent of the event simulator.

A blocking ratio measures target boxes with a *later retrieval cohort* above
at least one of them, divided by target count. The load list leaves in cohort 0;
non-load background leaves in cohort 1. POD is a ship destination, not a yard
retrieval time. Equal-category boxes are interchangeable only within a cohort:
exchange an accessible target for another identity of the same category, never
for background reserved for a later ship. No arbitrary ordering within a cohort
creates a blocker. This is the unrestricted block relocation interpretation in
Tanaka & Voss (2019), doi:10.1016/j.ejor.2019.06.014 (retrieval priorities).

APPROXIMATION: these are deliberately controlled two-cohort stress instances,
not samples of terminal traffic. Each target starts in a separate stack; a
selected fraction have one later-cohort dry box above them. Targets/locations
are shuffled. Thus the realised share differs by at most 0.5 / n_targets.
No feasibility claim follows from screening: all physics and cargo rules still
need the full checker. Parameter conditioning is recorded in the audit report.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from importlib.metadata import version as package_version
import json
import math
import os
from pathlib import Path
from random import Random
import sys
import tempfile

from stowage.conventions import canonical_vessel_id, make_container_id, parse_slot
from stowage.frozen import current_provenance
from stowage.generate.background_acceptance import accept_background_candidate
from stowage.generate.cargo import generate_load_list
from stowage.generate.random_export import booking_tank_masses, export_rejections, full_export_policy
from stowage.generate.random_voyage import build_voyage_spec
from stowage.io import load_instance, save_instance
from stowage.model import Instance
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import ShipState
from stowage.model.vessel import Vessel
from stowage.model.yard import YardStack, YardState
from stowage.params import load_param_file
from stowage.ports.profiles import load_port, scaled_yard
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1_enabled
from stowage.vessel.fleet_conditions import departure_tank_masses
from stowage.vessel.fleet_slices import _slice_stacks, load_fleet_preset, recondition_slice, slice_preset

from stowage.versions import RANDOM_YARD_VERSION

# Staged locally: the registry remains outside the plan-3.5 ownership fence.
LEGACY_RANDOM_YARD_VERSION = "8.0.0"
# 9.2.0: ON emits the gated D64 arrival copy; full bookings reserve the HEEL half (D64 rev3.1).
# 9.3.0: D77 booking target f = 0.815 and C-condition departure-ballast floor.
RULE_FREEZE_RANDOM_YARD_VERSION = "9.3.0"


def effective_random_yard_version() -> str:
    """Keep off-state seeded artifacts on their existing generator epoch."""
    return RULE_FREEZE_RANDOM_YARD_VERSION if rule_freeze_v1_enabled() else LEGACY_RANDOM_YARD_VERSION


def _full_vessel(name: str) -> Vessel:
    """Use the builder's content-key cache and revalidate inputs on every call.

    A second cache keyed only by name would mask parameter/dependency changes
    and skip validation of the pinned hull source in a long-lived process.
    """
    return load_fleet_preset(name, "full")


@dataclass(frozen=True)
class SizeRung:
    """Legacy diagnostic workload limits; yard bays are 20ft units.

    ``boxes`` remains an import-compatible ceiling, not an exact quantity.
    Callers such as 4.3 must derive/tag the actual count rather than treating
    this field as a fixed specification. D36 random-yard slice bookings instead
    scale the full-ship policy and can exceed this diagnostic ceiling; their
    synthetic yard expands to retain all targets and two spare ground stacks.
    """
    boxes: int
    yard_blocks: int
    yard_bays: int
    yard_rows: int
    pod_count: int
    fill_fraction: float = 0.5


SIZE_LADDER = {
    "tiny": SizeRung(6, 1, 16, 1, 2, 1.0),
    "small": SizeRung(36, 1, 24, 4, 2),
    "medium": SizeRung(150, 3, 24, 6, 2),
    "full": SizeRung(2000, 4, 24, 6, 2, 0.9),
}


@dataclass
class RandomGenerationReport:
    attempts: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    conditioning: list[str] = field(default_factory=list)
    requested_blocking_ratio: float = 0.0
    realised_blocking_ratio: float = 0.0
    blocked_targets: int = 0
    targets: int = 0
    accepted: bool = False
    capacity_teu: int = 0
    teu_budget: int = 0
    length_slot_budgets: dict[int, int] = field(default_factory=dict)
    requested_targets: int = 0
    realised_teu: float = 0.0
    completion_checks: int = 0
    completion_rejections: dict[str, int] = field(default_factory=dict)
    c2_screened: bool = False
    c2_requested: bool = False
    c2_checks: int = 0
    c2_direction: str | None = None
    c2_errors: dict[str, int] = field(default_factory=dict)
    shipment_redraws: int = 0
    full_export: dict = field(default_factory=dict)
    booking_utilisation: dict[str, float] = field(default_factory=dict)
    booking_slack: float | None = None
    booking_mass_fraction: float | None = None
    balancing_rejections: int = 0
    background_checks: int = 0
    background_cache_hits: int = 0
    background_seconds: float = 0.0
    background_rejections: dict[str, int] = field(default_factory=dict)
    background_last: dict = field(default_factory=dict)
    booking_reference: dict = field(default_factory=dict)
    cargo_draws: dict = field(default_factory=dict)
    d62_crossing: dict = field(default_factory=dict)
    # Gate no-verdict reasons (timeouts etc.), counted separately from physical
    # background_rejections; the gate raises after counting and never redraws.
    background_unknowns: dict[str, int] = field(default_factory=dict)

    @property
    def screening_rate(self) -> float:
        """Fraction of full candidate attempts rejected (single-box redraws separate)."""
        return (self.attempts - int(self.accepted)) / self.attempts if self.attempts else 0.0


def _length_slots(vessel: Vessel) -> dict[int, int]:
    cells = {cell.id: cell for cell in vessel.cells}
    counts = {20: sum(cell.accepts_20 for cell in cells.values()), 40: 0, 45: 0}
    for cell in cells.values():
        a = parse_slot(cell.id)
        partner = cells.get(f"{a.bay + 2:03d}{a.row:02d}{a.tier:02d}")
        if a.bay % 4 == 1 and partner is not None:
            for length in (40, 45):
                counts[length] += bool(getattr(cell, f"accepts_{length}")
                                       and getattr(partner, f"accepts_{length}"))
    return counts


def capacity_load_size(vessel: Vessel, rung: SizeRung, params, *,
                       full_vessel: Vessel | None = None) -> tuple[int, int, dict[int, int]]:
    """Count demand from actual TEU and per-length slots, with spare capacity.

    Without ``full_vessel``, small/medium half-fill remains the legacy diagnostic
    contract. With it, D36 scales full-ship demand to a slice, preserving its
    mass-limited or TEU-limited target. Whole-ship exports additionally use the
    mass budget and design band in random_export. Expected mix sizes the draw; realised TEU
    and per-length counts must also meet these limits (otherwise redraw).
    A pass is only a necessary capacity condition, not a stowage certificate.
    """
    # D36: transfer the full class's booking distribution to this region. The
    # cargo profile supplies equipment/POD/empty/mass draws; the existing full
    # export initializer supplies its utilisation target (not measured traffic).
    # APPROXIMATION: full-export utilisation is a point target with stochastic
    # cargo draws, not an independently calibrated utilisation distribution.
    reference = full_export_policy(full_vessel, params) if full_vessel is not None else None
    regional = reference is not None and len(vessel.cells) < reference.capacity_teu
    fraction = 0.9 if regional else rung.fill_fraction
    budget = math.floor(len(vessel.cells) * fraction)
    limits = {length: math.floor(n * fraction)
              for length, n in _length_slots(vessel).items()}
    shares = {20: 0.0, 40: 0.0, 45: 0.0}
    for equipment in params["equipment"]:
        length = {"2": 20, "4": 40, "L": 45}[equipment["iso_size_type"][0]]
        shares[length] += float(equipment["share"]["value"])
    total = sum(shares.values())
    expected_teu = sum(length / 20 * share for length, share in shares.items()) / total
    count = (math.floor(reference.target_count * len(vessel.cells) / reference.capacity_teu)
             if regional else min(rung.boxes, math.floor(budget / expected_teu)))
    if regional and not reference.mass_limited:
        # Rounding a six-cell region down must not make its unchanged 75% TEU
        # minimum unattainable. Round up at that boundary, never lower the band.
        count = max(count, math.ceil(0.75 * len(vessel.cells) / expected_teu))
    policy = full_export_policy(vessel, params) if rung.fill_fraction >= 0.75 else None
    if policy is not None:
        count = min(count, policy.target_count)
    for length, share in shares.items():
        if share > 0 and limits[length] > 0:
            count = min(count, math.floor(limits[length] * total / share))
    if count < 1:
        raise ValueError("expert.physical.slot_occupancy: no capacity-budgeted load fits this region")
    return count, budget, limits


def validate_booking_slack(booking_slack):
    if booking_slack is not None and (type(booking_slack) not in (int, float)
            or not math.isfinite(booking_slack) or not 0 <= booking_slack < 1):
        raise ValueError("booking_slack must be finite and in [0, 1), or None")


def apply_booking_slack(vessel, params, sizing, booking_slack):
    """Cap synthetic demand against geometric decision-cell TEU capacity.

    ASSUMPTION: feeders can sail below full legal capacity. This opt-in booking
    distribution is not a physical rule change. Decision cells are a measured
    geometric upper bound, not a visibility/support feasibility certificate.
    Existing demand budgets still apply; this cap can only lower them.
    """
    validate_booking_slack(booking_slack)
    if booking_slack is None:
        return sizing
    from stowage.generate.random_export import expected_cargo_mix

    count, budget, limits = sizing
    budget = min(budget, math.floor(len(vessel.cells) * (1 - booking_slack)))
    mean_teu, _ = expected_cargo_mix(params)
    count = min(count, math.floor(budget / mean_teu))
    if count < 1:
        raise ValueError("booking_slack leaves no capacity-budgeted load in this region")
    return count, budget, limits


BOOKING_MASS_BASIS = "scaled_full_export_cargo_budget_at_realised_mass"


def validate_booking_mass_fraction(fraction):
    if fraction is not None and (type(fraction) not in (int, float)
            or not math.isfinite(fraction) or not 0 < fraction <= 1):
        raise ValueError("booking_mass_fraction must be finite and in (0, 1], or None")


def regional_booking_mass_budget(vessel, reference, mass_kg):
    """Use exactly the denominator of the existing cargo_mass_fraction audit.

    This is a synthetic full-export reference scaled to this decision region,
    evaluated at the realised gross booking mass. It is not a stack rating or
    a proof of physical/legal loading capacity. Ballast makes it mass-dependent.
    """
    if reference is None:
        raise ValueError("booking_mass_fraction requires a full-export cargo budget reference")
    capacity = reference.capacity_teu
    if type(capacity) not in (int, float) or not math.isfinite(capacity) or capacity <= 0:
        raise ValueError("booking_mass_fraction requires a positive finite reference capacity")
    scale = len(vessel.cells) / capacity
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("booking_mass_fraction requires a positive finite region scale")
    budget = scale * reference.cargo_budget_kg(mass_kg / scale)
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("booking_mass_fraction requires a positive regional cargo budget")
    return budget


def apply_booking_mass_fraction(vessel, params, sizing, fraction, *, full_vessel=None):
    """Reduce box count using expected gross; reject realised excess separately.

    ASSUMPTION: feeders may sail with lighter bookings. Preserve equipment and
    individual-box mass draws, reducing only their count. The existing reference
    budget increases piecewise with cargo mass as its bounded ballast decreases;
    mass/budget is monotone, so integer bisection finds the largest allowed count.
    """
    validate_booking_mass_fraction(fraction)
    if fraction is None:
        return sizing
    from stowage.generate.random_export import expected_cargo_mix

    reference = full_export_policy(full_vessel if full_vessel is not None else vessel, params)
    _, mean_mass = expected_cargo_mix(params)
    low, high = 0, sizing[0]
    while low < high:
        count = (low + high + 1) // 2
        mass = count * mean_mass
        if mass <= fraction * regional_booking_mass_budget(vessel, reference, mass):
            low = count
        else:
            high = count - 1
    if low < 1:
        raise ValueError("booking_mass_fraction leaves no capacity-budgeted load in this region")
    return low, sizing[1], sizing[2]


_DEFAULT_CAPACITY_LOAD_SIZE = capacity_load_size


def _export_yard(port, rung, target_count):
    """Size a synthetic full-call yard without changing equipment restrictions."""
    bays = min(rung.yard_bays, min(b.n_bays for b in port.yard.blocks))
    rows = min(rung.yard_rows, min(b.n_rows for b in port.yard.blocks))
    blocks = max(rung.yard_blocks, math.ceil((target_count + 2) / ((bays // 2) * rows)))
    expanded = port.model_copy(deep=True)
    original = list(expanded.yard.blocks)
    # APPROXIMATION: repeat synthetic storage blocks to supply a demand-sized
    # snapshot, never extra ship capacity or relaxed DG/reefer/tier restrictions.
    for index in range(max(0, blocks - len(original))):
        expanded.yard.blocks.append(original[-1].model_copy(update={
            "id": f"X{index:03d}",
            "x_m": original[-1].x_m + (index + 1) * (bays * 6.058 + 20),
        }, deep=True))
    return scaled_yard(expanded, blocks=blocks, bays=bays, rows=rows)


def completion_failures(instance: Instance) -> tuple[str, ...]:
    """Cheap depth-0 C1 necessary conditions; explicitly omit C2 and action search.

    C1a uses Hall/max-flow matching: every group of remaining boxes must have
    enough compatible free cells (Hall 1935, as explained in feasibility/capacity.py).
    The other C1 bounds cover weight classes, long pairs and mass room. Do not
    call dead_end()/legal_actions(): those enumerate next actions, not this check.
    """
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine

    engine = make_feasibility_engine(instance, get_action_space("boxslot"),
        config=FeasibilityConfig(lookahead=0, extra={"global_screen": 0}))
    return tuple(sorted(key for key, ok in engine.completion_check().items()
                        if key.startswith("c1") and not ok))


def blocking_ratio(instance: Instance) -> float:
    """Category-aware two-cohort blocked share, independent of container id order."""
    targets = set(instance.load_list)
    blocked = 0
    for stack in instance.yard.stacks:
        later_above = False
        for cid in reversed(stack.boxes):
            if cid in targets:
                blocked += later_above
            else:
                later_above = True
    return blocked / len(targets) if targets else 0.0


def screen_load_list(vessel: Vessel, boxes) -> list[str]:
    """Cheap necessary conditions, tagged with existing catalogue rule ids.

    Forty/forty-five slots use disjoint odd-bay pairs (001/003, 005/007,
    ...); counting both overlapping even slots would inflate capacity. Mixed
    length packing, heights, DG, stability and local stack masses are unchecked.
    The aggregate mass screen uses the largest applicable stack rating, making
    it an upper bound rather than an unsafe proof of packing feasibility.
    """
    reasons = []
    cells = {cell.id: cell for cell in vessel.cells}
    if sum(box.length_ft / 20 for box in boxes) > len(cells):
        reasons.append("expert.physical.slot_occupancy")
    for length in (20, 40, 45):
        count = sum(box.length_ft == length for box in boxes)
        if length == 20:
            available = sum(cell.accepts_20 for cell in cells.values())
        else:
            available = 0
            for cell in cells.values():
                a = parse_slot(cell.id)
                partner = cells.get(f"{a.bay + 2:03d}{a.row:02d}{a.tier:02d}")
                if (a.bay % 4 == 1 and partner is not None
                        and getattr(cell, f"accepts_{length}")
                        and getattr(partner, f"accepts_{length}")):
                    available += 1
        if count > available:
            reasons.append("expert.physical.size_fit")
    if sum(box.reefer for box in boxes) > sum(c.reefer_plug for c in cells.values()):
        reasons.append("expert.physical.reefer_plug")
    upper_mass = sum(max(s.max_mass_kg, s.max_mass_20_kg or 0,
                         s.max_mass_40_kg or 0) for s in vessel.stacks)
    if sum(box.gross_mass_kg for box in boxes) > upper_mass:
        reasons.append("expert.physical.stack_weight")
    return reasons


def _mixed_hatch_stacks(vessel: Vessel):
    """Find the first physical hatch row retaining paired hold and deck stacks.

    D20 removes holds at KCS fine ends. Select existing geometry by membership,
    rather than assuming the first hatch still contains a hold. Source:
    experiments/fleet-params/kcs-migration-review.md, retained-cell audit.
    """
    by_id = {s.id: s for s in vessel.stacks}
    for cover in vessel.hatch_covers:
        hold = [by_id[sid] for sid in cover.hold_stack_ids if sid in by_id]
        deck = [by_id[sid] for sid in cover.deck_stack_ids if sid in by_id]
        for row in sorted({s.row for s in hold} & {s.row for s in deck}):
            below = {s.bay: s for s in hold if s.row == row and len(s.cell_ids) >= 2}
            above = {s.bay: s for s in deck if s.row == row and s.cell_ids}
            for bay in sorted(below.keys() & above.keys()):
                if bay % 4 == 1 and bay + 2 in below and bay + 2 in above:
                    return [below[bay], below[bay + 2]], [above[bay], above[bay + 2]]
    raise ValueError("tiny needs an existing hatch row with paired two-tier holds and decks")


def _decision_region(vessel: Vessel, size: str) -> Vessel:
    """Use the fleet owner's current D27 preset; split identity stays the rung."""
    return slice_preset(vessel, size)


def _tiny_region(vessel: Vessel) -> Vessel:
    """Retain four hold and two deck cells at one cover, whole hull intact."""
    hold, deck = _mixed_hatch_stacks(vessel)
    # Use 2.2's shared slice accounting for omitted holds, whole-ship moments,
    # hatch references and DoC areas before restricting the decision-cell count.
    vessel = _slice_stacks(vessel, {s.id for s in hold + deck}, name=vessel.id)
    stacks = [s.model_copy(update={"cell_ids": s.cell_ids[:2]}) for s in hold]
    stacks += [s.model_copy(update={"cell_ids": s.cell_ids[:1]}) for s in deck]
    selected = {cid for s in stacks for cid in s.cell_ids}
    if len(selected) != 6:
        raise ValueError("tiny needs two two-tier hold stacks and two deck stacks")
    ids = {s.id for s in stacks}
    covers = [h.model_copy(update={"hold_stack_ids": [s for s in h.hold_stack_ids if s in ids],
                                    "deck_stack_ids": [s for s in h.deck_stack_ids if s in ids]})
              for h in vessel.hatch_covers if any(s in ids for s in h.hold_stack_ids)]
    payload = vessel.model_dump()
    payload.update(cells=[c.model_dump() for c in vessel.cells if c.id in selected],
                   stacks=[s.model_dump() for s in stacks], nominal_teu=6,
                   hatch_covers=[h.model_dump() for h in covers],
                   dg_doc_areas=[{**area.model_dump(),
                                  "stack_ids": [sid for sid in area.stack_ids if sid in ids]}
                                 for area in vessel.dg_doc_areas
                                 if any(sid in ids for sid in area.stack_ids)],
                   sources=[*vessel.sources, "APPROXIMATION: tiny decision region restricted to four hold and two deck cells; other cells sharing its hatch are empty"])
    return Vessel.model_validate(payload)


def generate_random_instance(
    size: str = "tiny", *, seed: int, blocking_ratio: float = 0.3,
    vessel_name: str = "feeder_500", port_name: str = "laem_chabang_like",
    report: RandomGenerationReport | None = None, max_attempts: int = 200,
    c2_screen: bool = False, cargo_profile: str = "feeder_export",
    d62_crossing_share: float = 0.25, booking_slack: float | None = None,
    booking_mass_fraction: float | None = None,
) -> Instance:
    """Generate one clean, deterministic instance, using 2.2/2.5/2.6 builders.

    Tiny has at most six 20ft targets in four hold/two deck cells and one hatch.
    The region-sized booking has at most 6! complete ship assignments and 6!
    loading orders; this finite bound does not prove physical solvability.
    Each blocker needs at least one relocation; moving
    it to either of two reserved empty stacks attains that yard lower bound.
    This bounds a canonical brute-force solver, not every cyclic move sequence.
    Tiny conditions equipment to dry20, retaining the source loaded/empty mix,
    DG prevalence, exact masses and checker physics. Other rungs retain the complete cargo mix;
    unsupported DGL draws and necessary-condition failures are reported/redrawn.
    ``c2_screen=True`` additionally requests D49's necessary-direction fractional
    completion LP. Missing qualification fails closed; a pass is not a plan.
    """
    validate_booking_slack(booking_slack)
    validate_booking_mass_fraction(booking_mass_fraction)
    freeze = rule_freeze_v1_enabled()
    generator_version = effective_random_yard_version()
    if cargo_profile not in {"feeder_export", "dg_stress"}:
        raise ValueError("cargo_profile must be feeder_export or dg_stress")
    if not freeze and cargo_profile != "feeder_export":
        raise ValueError("DG-stress random instances require rule-freeze-v1 activation")
    if isinstance(d62_crossing_share, bool) or not math.isfinite(d62_crossing_share) or not 0 <= d62_crossing_share <= 1:
        raise ValueError("d62_crossing_share must be finite and in [0, 1]")
    vessel_name = canonical_vessel_id(vessel_name)
    if size not in SIZE_LADDER:
        raise ValueError(f"unknown size {size!r}; choose {tuple(SIZE_LADDER)}")
    if not math.isfinite(blocking_ratio) or not 0 <= blocking_ratio <= 1:
        raise ValueError("blocking_ratio must be finite and in [0, 1]")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if type(c2_screen) is not bool:
        raise ValueError("c2_screen must be a boolean")
    rung = SIZE_LADDER[size]
    audit = report if report is not None else RandomGenerationReport()
    audit.__dict__.update(RandomGenerationReport().__dict__)
    audit.requested_blocking_ratio = blocking_ratio
    audit.c2_requested = c2_screen
    parent = _full_vessel(vessel_name)
    vessel = _decision_region(parent, size)
    if size == "tiny":
        vessel = _tiny_region(vessel)
    port = load_port(port_name)
    crossing_selected = False
    draws = None
    if freeze:
        from stowage.generate.random_cargo import RandomCargoDraws, prepare_random_cargo
        from stowage.generate.random_crossing import crossing_bucket_selected

        crossing_selected = crossing_bucket_selected(seed, share=d62_crossing_share)
        draws = RandomCargoDraws()
    yard_rung = replace(rung, yard_rows=max(2, rung.yard_rows)) if crossing_selected else rung
    if size != "full":
        # A8 crossing needs a lane-near and lane-far row. Expand only a selected
        # synthetic stress footprint, within the source port's row geometry.
        rows = min(yard_rung.yard_rows, min(block.n_rows for block in port.yard.blocks)) if freeze else rung.yard_rows
        bays = min(rung.yard_bays, min(block.n_bays for block in port.yard.blocks)) if freeze else rung.yard_bays
        port = scaled_yard(port, blocks=rung.yard_blocks, bays=bays, rows=rows)
    if freeze:
        params, rotation = prepare_random_cargo(port, cargo_profile=cargo_profile)
        audit.conditioning.append("APPROXIMATION: activated random cargo uses synthetic whole-kg VGM, cargo-owned shipment declarations and handling-port rules")
        if cargo_profile == "dg_stress":
            audit.conditioning.append("APPROXIMATION: D70 separate DG-stress demand profile; not the main feeder-export mixture")
    else:
        params = deepcopy(load_param_file("cargo", "feeder_export"))
        route = deepcopy(params["rotations"][0])
        # Preserve the original seeded off-state route, including absent
        # archetypes. Cargo owns enrichment after activation.
        calls = list(route["port_calls"])
        calls[0] = port.locode
        rotation = Rotation(service=route["id"], calls=[PortCall(locode=p, index=i) for i, p in enumerate(calls)])
        route["port_calls"] = calls
        params["rotations"] = [route]
    if size == "tiny":
        params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
        audit.conditioning.append("APPROXIMATION: tiny conditions equipment to dry20, retaining source loaded/empty mix and DG sampling; region-sized booking in four hold/two deck cells")
    # The canonical adapter installs a three-argument capacity hook for an
    # explicitly requested diagnostic quantity. Preserve that existing hook
    # contract; default bookings alone opt into D36's regional distribution.
    regional_policy = capacity_load_size is _DEFAULT_CAPACITY_LOAD_SIZE
    if regional_policy:
        target_count, teu_budget, length_budgets = capacity_load_size(vessel, rung, params, full_vessel=parent)
    else:
        target_count, teu_budget, length_budgets = capacity_load_size(vessel, rung, params)
    if booking_slack is not None:
        target_count, teu_budget, length_budgets = apply_booking_slack(
            vessel, params, (target_count, teu_budget, length_budgets), booking_slack)
        audit.booking_slack = float(booking_slack)
        audit.conditioning.append("ASSUMPTION: feeders may sail below full capacity; booking_slack caps synthetic demand against geometric decision-cell TEU, not checker-legal capacity")
    if booking_mass_fraction is not None:
        target_count, teu_budget, length_budgets = apply_booking_mass_fraction(
            vessel, params, (target_count, teu_budget, length_budgets), booking_mass_fraction,
            full_vessel=parent)
        audit.booking_mass_fraction = float(booking_mass_fraction)
        audit.conditioning.append("ASSUMPTION: feeders may sail lighter; booking_mass_fraction conditions booking count and realised mass against the scaled full-export reference budget, not legal loading capacity")
    booking_policy = "D36_scaled_full_export_v1" if regional_policy else "explicit_capacity_override"
    reference = full_export_policy(parent, params)
    if reference is not None:
        audit.booking_reference = {"policy": booking_policy,
            "full_capacity_teu": reference.capacity_teu,
            "full_target_count": reference.target_count,
            "requested_teu_fraction": reference.target_count * reference.mean_teu / reference.capacity_teu,
            "region_capacity_teu": len(vessel.cells)}
    if (size == "full" or target_count > rung.boxes
            or (freeze and sum((block.n_bays // 2)*block.n_rows for block in port.yard.blocks) < target_count + 2)):
        port = _export_yard(port, yard_rung if freeze else rung, target_count)
    audit.capacity_teu = len(vessel.cells)
    audit.teu_budget, audit.length_slot_budgets = teu_budget, length_budgets
    audit.requested_targets = target_count
    audit.conditioning.append(f"{booking_policy} demand: {target_count} targets, {teu_budget} TEU budget; C1 depth-0 on, C2 {'necessary (D49)' if c2_screen else 'off'}")
    # D50: the model derives 10% remaining consumables by the vessel's declared
    # group/order policy. Declare identities/environment, never arrival fills.
    voyage = build_voyage_spec(rotation, 0, port, seed=seed)
    from stowage.generate.cargo import UnsupportedCargoError
    redraw = None
    for attempt in range(max_attempts):
        audit.attempts += 1
        try:
            if redraw is None:
                boxes = (draws.draw(rotation, 0, target_count, seed=seed * 1_000_000 + attempt, params=params)
                         if freeze else generate_load_list(rotation, 0, target_count, seed=seed * 1_000_000 + attempt, params=params))
            else:
                boxes, positions = redraw
                boxes = list(boxes)
                for index in positions:
                    draw_seed = Random(f"{seed}:{attempt}:shipment:{index}").getrandbits(64)
                    replacement = (draws.draw(rotation, 0, 1, seed=draw_seed,
                        id_offset=index, params=params, kind="shipment_redraw")[0] if freeze else
                        generate_load_list(rotation, 0, 1, seed=draw_seed, id_offset=index, params=params)[0])
                    if freeze:
                        draws.replace_id(replacement.id, boxes[index].id)
                    boxes[index] = replacement.model_copy(update={"id": boxes[index].id})
                    audit.shipment_redraws += 1
            redraw = None
        except UnsupportedCargoError:
            key = "imdg.stowage.3_2"
            audit.rejections[key] = audit.rejections.get(key, 0) + 1
            continue
        finally:
            if freeze:
                audit.cargo_draws = draws.snapshot(attempts=audit.attempts)
        if booking_mass_fraction is not None:
            mass = sum(box.gross_mass_kg for box in boxes)
            if mass > booking_mass_fraction * regional_booking_mass_budget(vessel, reference, mass):
                key = "booking.mass_fraction"
                audit.rejections[key] = audit.rejections.get(key, 0) + 1
                continue
        # D29: always start from the immutable FINAL geometry. Screening hooks
        # must see this candidate's actual booking condition, not a preset mass.
        # Yard blockers are excluded; consumables stay in live tanks, not loads.
        try:
            candidate_vessel = recondition_slice(vessel,
                expected_load_mass_kg=sum(box.gross_mass_kg for box in boxes),
                seed=Random(f"{seed}:{attempt}:slice").getrandbits(64))
        except ValueError as error:
            if not str(error).startswith("cannot balance slice background within physical cargo capacities"):
                raise
            audit.balancing_rejections += 1
            key = "background.construction"
            audit.rejections[key] = audit.rejections.get(key, 0) + 1
            continue
        # Keep this booking recipe through screening and emission. The gate
        # may check a heavier empty-plan witness without consuming its headroom.
        candidate_tanks = booking_tank_masses(candidate_vessel, boxes, params)
        background_probe = Instance(id=f"random-background.{size}.{seed}", stage="A", size_class=size,
            seed=seed, vessel=candidate_vessel, rotation=rotation, port=port, call_index=0,
            yard=YardState(yard_id=port.yard.id),
            onboard=ShipState(vessel_id=candidate_vessel.id,
                tank_masses_kg=candidate_tanks),
            containers=[], load_list=[], voyage=voyage)
        if freeze:
            # Bind with the final identity and physical scope before even the
            # cheap screens. The final yard snapshot is bound again below.
            draws.bind(background_probe.model_copy(update={
                "id": f"random.{size}.{seed}.{blocking_ratio:g}", "containers": boxes}, deep=True))
        reasons = screen_load_list(candidate_vessel, boxes)
        reasons.extend(export_rejections(candidate_vessel, boxes, params,
            full_vessel=parent if regional_policy else None,
            **({"minimum_utilisation": False} if booking_slack is not None or booking_mass_fraction is not None else {})))
        if sum(box.length_ft / 20 for box in boxes) > teu_budget:
            reasons.append("expert.physical.slot_occupancy")
        if any(sum(box.length_ft == length for box in boxes) > limit
               for length, limit in length_budgets.items()):
            reasons.append("expert.physical.size_fit")
        if reasons:
            for reason in set(reasons):
                audit.rejections[reason] = audit.rejections.get(reason, 0) + 1
            if "imdg.stowage.3_2" in reasons:
                # APPROXIMATION: extend the same sequential shipment
                # conditioning to adapter screens that reject unsupported DG
                # before C1. A single-shipment screen identifies the offenders;
                # no declaration is edited, and the next complete candidate
                # must pass every adapter screen, mass budget and C1 bound.
                positions = [index for index, box in enumerate(boxes)
                             if box.dg is not None and "imdg.stowage.3_2" in
                             screen_load_list(candidate_vessel, [box])]
                if positions:
                    redraw = (boxes, positions)
            continue
        rng = Random(seed)
        rng.shuffle(boxes)
        n_blocked = int(math.floor(blocking_ratio * len(boxes) + 0.5))
        eligible_blockers = [box.id for box in boxes if not (freeze and box.direct_delivery)]
        n_blocked = min(n_blocked, len(eligible_blockers))
        chosen = set(rng.sample(eligible_blockers, n_blocked))
        stacks = []
        background = []
        weight_classes = load_catalogue().weight_classes
        # Reserve two ground bays per target, avoiding all long-box overlaps. Row 1
        # is powered; final block permits DG. Keep capability checks on candidates.
        candidates = [(b, bay, row) for b in port.yard.blocks
                      for bay in range(1, b.n_bays, 2) for row in range(1, b.n_rows + 1)]
        rng.shuffle(candidates)
        for box in sorted(boxes, key=lambda b: (not b.reefer, b.dg is None, b.id)):
            if freeze and box.direct_delivery:
                continue
            match = next((i for i, (b, _, row) in enumerate(candidates)
                          if box.length_ft in b.allowed_lengths_ft
                          and (not box.reefer or row in b.reefer_rows)
                          and (box.dg is None or b.dg_block)), None)
            if match is None:
                raise ValueError("expert.physical.slot_occupancy: no legal footprint for sampled cargo")
            block, bay, row = candidates.pop(match)
            contents = [box.id]
            if box.id in chosen:
                # APPROXIMATION: dry, empty background is a later vessel cohort.
                # Clone size/height/tare only; special cargo facts never propagate.
                blocker = box.model_copy(update={"id": make_container_id("QZWU", len(background)),
                    "iso_type": {20: "22G1", 40: "42G1", 45: "L5G1"}[box.length_ft],
                    "height_m": 2.896 if box.length_ft == 45 else 2.591,
                    "gross_mass_kg": box.tare_kg, "empty": True, "reefer": False,
                    "reefer_setpoint_c": None, "dg": None, "oog": None,
                    "special_stowage": [], "weight_class": weight_classes.classify(box.tare_kg),
                    "category": f"later:dry:{box.length_ft}:{box.tare_kg.hex()}"})
                if freeze:
                    gross = float(math.ceil(box.tare_kg))
                    blocker = blocker.model_copy(update={"direct_delivery": False,
                        "gross_mass_kg": gross, "weight_class": weight_classes.classify(gross),
                        "category": f"later:dry:{blocker.iso_type}:mass_kg={gross.hex()}:empty"})
                background.append(blocker)
                contents.append(blocker.id)
            stacks.append(YardStack(key=f"{block.id}-{bay:03d}-{row:02d}", length_ft=box.length_ft, boxes=contents))
        provenance = current_provenance(seed=seed, created_by="generate.random_yard")
        provenance.versions["random_yard"] = generator_version
        if freeze:
            from stowage.generate.cargo import effective_cargo_generator_version
            from stowage.yard import effective_yard_simulator_version

            provenance.versions["cargo_generator"] = effective_cargo_generator_version()
            provenance.versions["yard_simulator"] = effective_yard_simulator_version()
        teu = sum(box.length_ft / 20 for box in boxes)
        mass = sum(box.gross_mass_kg for box in boxes)
        utilisation = {"teu_fraction": teu / len(vessel.cells)}
        if reference is not None:
            scale = len(vessel.cells) / reference.capacity_teu
            utilisation["cargo_mass_fraction"] = mass / (
                scale * reference.cargo_budget_kg(mass / scale))
        if booking_mass_fraction is not None:
            mass_budget = regional_booking_mass_budget(vessel, reference, mass)
            utilisation.update(cargo_mass_kg=mass, cargo_mass_budget_kg=mass_budget,
                               booking_mass_cap_kg=booking_mass_fraction * mass_budget)
        family_booking = {"region_capacity_teu": str(len(vessel.cells)),
            "booking_policy": booking_policy,
            "booking_utilisation_teu": repr(utilisation["teu_fraction"])}
        if booking_slack is not None:
            family_booking.update(booking_slack=repr(float(booking_slack)),
                booking_capacity_basis="decision_cells_geometric_teu")
        if booking_mass_fraction is not None:
            family_booking.update(booking_mass_fraction=repr(float(booking_mass_fraction)),
                booking_mass_basis=BOOKING_MASS_BASIS, booking_cargo_mass_kg=repr(mass),
                booking_mass_budget_kg=repr(mass_budget),
                booking_mass_cap_kg=repr(booking_mass_fraction * mass_budget))
        if "cargo_mass_fraction" in utilisation:
            family_booking["booking_utilisation_mass"] = repr(utilisation["cargo_mass_fraction"])
            family_booking["booking_requested_teu_fraction"] = repr(audit.booking_reference["requested_teu_fraction"])
        instance = Instance(id=f"random.{size}.{seed}.{blocking_ratio:g}", stage="A", size_class=size,
            family={"vessel": vessel_name, "port": port_name, "size": size,
                    "blocking_ratio": repr(float(blocking_ratio)), "seed": str(seed),
                    "generator": generator_version, **({"cargo_profile": cargo_profile} if freeze else {}), **family_booking,
                    **({"c2_screen": "necessary"} if c2_screen else {})}, seed=seed, vessel=candidate_vessel,
            rotation=rotation, port=port, call_index=0,
            yard=YardState(yard_id=port.yard.id, stacks=stacks),
            onboard=ShipState(vessel_id=candidate_vessel.id,
                tank_masses_kg=candidate_tanks), containers=boxes + background,
            load_list=[b.id for b in boxes], provenance=provenance, voyage=voyage)
        if freeze:
            from stowage.generate.random_crossing import condition_crossing_bucket, crossing_probe, ship_crossing_probe

            crossing = {"selected": crossing_selected, "requested_share": d62_crossing_share,
                "approximation": "APPROXIMATION: controlled crossing stress mixture, not measured terminal traffic",
                "probe_definition": "Real apply_plan attempted wrong-order lifts; counters are diagnostics, never accepted plans"}
            counts = dict(audit.d62_crossing.get("candidate_counts", {}))
            counts["screened_candidates"] = counts.get("screened_candidates", 0) + 1
            if crossing_selected:
                counts["selected"] = counts.get("selected", 0) + 1
                crossing["ship_probe"] = ship_crossing_probe(instance)
                instance, crossing["yard_bucket"] = condition_crossing_bucket(instance)
                crossing["yard_probe"] = crossing_probe(instance)
                applied = bool(crossing["yard_bucket"].get("applied"))
                probe = crossing["yard_probe"]
                delivered = (applied and probe.get("observed")
                    and probe.get("yard_lifts_at_or_above_h", 0) > 0
                    and probe.get("rejected_crossing_lifts", 0) > 0
                    and probe.get("probe_rule") == "port.e3.yard_crossing")
                counts["applied"] = counts.get("applied", 0) + int(applied)
                counts["observed"] = counts.get("observed", 0) + int(bool(delivered))
            crossing["candidate_counts"] = counts
            crossing["bucket_rejection_reasons"] = dict(audit.d62_crossing.get("bucket_rejection_reasons", {}))
            audit.d62_crossing = crossing
            if crossing_selected and not delivered:
                # Selection is a requested stress dose, not evidence that it
                # was delivered. Preserve its seed membership while redrawing
                # incompatible manifests; never emit an unchanged bucket.
                key = "d62.bucket_not_delivered"
                audit.rejections[key] = audit.rejections.get(key, 0) + 1
                reason = crossing["yard_bucket"].get("reason", probe.get("reason", "crossing_not_observed"))
                histogram = crossing["bucket_rejection_reasons"]
                histogram[reason] = histogram.get(reason, 0) + 1
                continue
            instance = instance.model_copy(update={"family": {**instance.family,
                "d62_crossing_requested_share": repr(d62_crossing_share),
                "d62_crossing_selected": str(crossing_selected).lower()}})
            instance = draws.bind(instance)
        audit.completion_checks += 1
        failed = completion_failures(instance)
        if failed:
            for reason in failed:
                audit.completion_rejections[reason] = audit.completion_rejections.get(reason, 0) + 1
            # Completion bounds can reject DG/weight classes despite spare
            # TEU; they do not establish a physical slot-occupancy violation.
            key = "completion.c1"
            audit.rejections[key] = audit.rejections.get(key, 0) + 1
            if "c1a" in failed:
                # APPROXIMATION: sequential shipment conditioning, rather than
                # independent whole-list rejection. Keep compatible bookings
                # and redraw individually impossible DG shipments from 2.5.
                # No DG fact is changed or waived. The next whole candidate
                # must pass the original capacity budgets and every C1 bound.
                positions = []
                for index, box in enumerate(boxes):
                    if box.dg is None:
                        continue
                    audit.completion_checks += 1
                    single = instance.model_copy(update={"load_list": [box.id]})
                    if "c1a" in completion_failures(single):
                        positions.append(index)
                if positions:
                    redraw = (boxes, positions)
            continue
        if c2_screen:
            from stowage.generate.random_screen import necessary_completion_failures

            diagnostics = {}
            audit.c2_checks += 1
            audit.c2_screened = True
            audit.c2_direction = "necessary"
            c2_failed = necessary_completion_failures(instance, diagnostics=diagnostics)
            for reason in c2_failed:
                audit.completion_rejections[reason] = audit.completion_rejections.get(reason, 0) + 1
            if "c2.unavailable" in c2_failed:
                error = diagnostics.get("error", "necessary linear model unavailable")
                audit.c2_errors[error] = audit.c2_errors.get(error, 0) + 1
                key = "checker.missing_vessel_data"
                audit.rejections[key] = audit.rejections.get(key, 0) + 1
                raise ValueError(f"C2 unavailable: {error}")
            if c2_failed:
                # The joint necessary screen does not isolate a single rule.
                # Its separate c2 counter identifies this completion rejection.
                key = "expert.physical.slot_occupancy"
                audit.rejections[key] = audit.rejections.get(key, 0) + 1
                continue
        # Run the same empty-plan full check only after this candidate survives
        # the cargo and completion screens. Unique backgrounds from rejected
        # bookings cannot share acceptance cache entries, so checking them
        # earlier repeats expensive D57 physics without enabling emission.
        # The probe retains exactly this candidate's vessel and booking tanks;
        # its separate witness still must pass before any instance is returned.
        if not freeze:
            # OFF: the historical empty-demand probe gate, byte-identical.
            if accept_background_candidate(background_probe, audit) is None:
                continue
        else:
            # D64/rev31 4.3: gate the complete final candidate with its actual
            # demand and emit exactly the certified prepared-arrival copy. The
            # booked departure tanks stay in audit.background_last. UNKNOWN
            # (no physical verdict) raises BackgroundAcceptanceUnknown from
            # the gate and propagates: it is never a redraw, so no further
            # attempt is drawn and this attempt stays in the denominator.
            accepted = accept_background_candidate(instance, audit)
            if accepted is None:
                continue
            instance = accepted
        if freeze:
            from stowage.generate.random_cargo import admitted_cargo_audit

            audit.cargo_draws["admitted"] = admitted_cargo_audit(instance, params)
            audit.d62_crossing["emitted_bucket"] = crossing_selected
        audit.accepted = True
        audit.booking_utilisation = utilisation
        audit.realised_teu = sum(box.length_ft / 20 for box in boxes)
        policy = full_export_policy(candidate_vessel, params)
        if policy and policy.is_export_count(len(boxes)):
            audit.full_export = policy.audit(sum(box.gross_mass_kg for box in boxes), audit.realised_teu)
        audit.targets, audit.blocked_targets = len(boxes), n_blocked
        audit.realised_blocking_ratio = globals()["blocking_ratio"](instance)
        return instance
    raise ValueError(f"screening exhausted {max_attempts} attempts: {audit.rejections}; C1: {audit.completion_rejections}")


def _report_payload(audit: RandomGenerationReport) -> dict:
    payload = asdict(audit)
    if payload.get("booking_mass_fraction") is None:
        payload.pop("booking_mass_fraction", None)
    if payload.get("booking_slack") is None:
        payload.pop("booking_slack", None)
    if not rule_freeze_v1_enabled():
        payload.pop("cargo_draws")
        payload.pop("d62_crossing")
        # Keep historical OFF audit bytes; a nonempty OFF count is retained.
        if not payload["background_unknowns"]:
            payload.pop("background_unknowns")
    return payload


def _json_digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _resume_inputs(vessel_name: str, port_name: str, *, cargo_profile: str = "feeder_export",
                   d62_crossing_share: float = .25) -> dict:
    """Fresh content identities, independent of mtimes or process-level caches.

    Conservative package source/data hashing also catches unversioned changes
    in transitive generation, geometry and necessary-condition dependencies.
    This is artifact invalidation, not a physical feasibility certificate.
    """
    from stowage.generate.cargo import CARGO_GENERATOR_VERSION, effective_cargo_generator_version
    from stowage.ports.profiles import list_ports
    from stowage.params import repo_root
    from stowage.vessel.fleet_conditions import CONDITION_VERSION

    vessel_name = canonical_vessel_id(vessel_name)
    root = repo_root()
    package = Path(__file__).resolve().parents[1]
    parameters = {"cargo": load_param_file("cargo", cargo_profile),
                  "fleet": load_param_file("fleet", vessel_name),
                  "ports": load_param_file("ports", port_name)}
    inputs = {path for path in package.rglob("*")
              if path.is_file() and path.suffix in {".py", ".toml", ".json"}}
    # These design records participate in vessel construction/cache identity.
    inputs.update(path for path in (root / "experiments/physics-review/design/harness.py",
                                   root / "experiments/physics-review/design/conditions.md") if path.is_file())
    offsets = parameters["fleet"].get("hull_form", {}).get("offsets_path")
    if offsets:
        inputs.add(root / offsets)
    return {"format": 1, **({"activated_random": {"cargo_profile": cargo_profile,
                "d62_crossing_share": d62_crossing_share}} if rule_freeze_v1_enabled() else {}), "parameters": {kind: _json_digest(value) for kind, value in parameters.items()},
            "voyage_port_parameters": {name: _json_digest(load_param_file("ports", name))
                                       for name in list_ports()},
            "sources": {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
                        for path in sorted(inputs)},
            "versions": {**current_provenance().versions, "random_yard": effective_random_yard_version(),
                         "cargo_generator": (effective_cargo_generator_version() if rule_freeze_v1_enabled() else CARGO_GENERATOR_VERSION), "vessel_condition": CONDITION_VERSION},
            "runtime": {"python": sys.version, **{name: package_version(name)
                        for name in ("numpy", "scipy", "pydantic", "ortools")}},
            "physics_environment": {name: os.environ.get(name) for name in
                ("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "STOWAGE_STABILITY_LIQUID_DIRECT")}}


def _validate_resume_audit(path: Path, sidecar: Path, instance: Instance, inputs: dict) -> None:
    """Bind a complete accepted audit and its input snapshot to exact shard bytes."""
    try:
        payload = json.loads(sidecar.read_text())
        binding = payload.pop("_resume")
        if binding["inputs"] != inputs:
            raise ValueError("generation inputs changed")
        expected_fields = set(_report_payload(RandomGenerationReport())) | {"screening_rate"}
        if "booking_slack" in instance.family:
            expected_fields.add("booking_slack")
            if payload.get("booking_slack") != float(instance.family["booking_slack"]):
                raise ValueError("booking slack audit/family mismatch")
        if "booking_mass_fraction" in instance.family:
            expected_fields.add("booking_mass_fraction")
            if payload.get("booking_mass_fraction") != float(instance.family["booking_mass_fraction"]):
                raise ValueError("booking mass fraction audit/family mismatch")
        if (set(payload) != expected_fields or binding["format"] != 1
                or binding["instance_sha256"] != sha256(path.read_bytes()).hexdigest()
                or binding["audit_sha256"] != _json_digest(payload)
                or payload["accepted"] is not True or type(payload["attempts"]) is not int
                or payload["attempts"] < 1 or payload["targets"] != len(instance.load_list)
                or payload["screening_rate"] != (payload["attempts"] - 1) / payload["attempts"]):
            raise ValueError("invalid or unbound audit")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError(f"resume audit mismatch: {sidecar}: {error}") from error


def _write_audit(sidecar: Path, audit: RandomGenerationReport, path: Path, inputs: dict) -> None:
    payload = {**_report_payload(audit), "screening_rate": audit.screening_rate}
    payload["_resume"] = {"format": 1, "inputs": inputs,
                          "instance_sha256": sha256(path.read_bytes()).hexdigest(),
                          "audit_sha256": _json_digest(payload)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=sidecar.parent,
                                         prefix=".audit-", suffix=".partial", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
        temporary.replace(sidecar)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """One JSON instance + audit sidecar per shard; resume verifies full inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", choices=[*SIZE_LADDER, "all"], default="all")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--blocking-ratio", type=float, default=0.3)
    parser.add_argument("--cargo-profile", choices=["feeder_export", "dg_stress"], default="feeder_export")
    parser.add_argument("--d62-crossing-share", type=float, default=.25)
    parser.add_argument("--c2-screen", action="store_true", help="D49 necessary-direction completion screen")
    parser.add_argument("--vessel", type=canonical_vessel_id, default="feeder_500")
    parser.add_argument("--port", default="laem_chabang_like")
    parser.add_argument("--output", type=Path, default=Path("experiments/instances"))
    parser.add_argument("--booking-slack", type=float, default=None,
                        help="synthetic demand TEU cap = (1-slack) * geometric decision cells; default unchanged")
    parser.add_argument("--booking-mass-fraction", type=float, default=None,
                        help="synthetic booking mass / scaled full-export reference cargo budget; default unchanged")
    args = parser.parse_args(argv)
    from stowage.generate.random_yard import validate_booking_slack, validate_booking_mass_fraction
    try:
        validate_booking_slack(args.booking_slack)
        validate_booking_mass_fraction(args.booking_mass_fraction)
    except ValueError as exc:
        parser.error(str(exc))
    # Profile compatibility is an input contract, including on a resume hit.
    # Do not let an existing ordinary shard silently satisfy a stress request.
    if not rule_freeze_v1_enabled() and args.cargo_profile != "feeder_export":
        raise ValueError("DG-stress random instances require rule-freeze-v1 activation")
    if args.count < 1:
        parser.error("count must be positive")
    if args.count * (len(SIZE_LADDER) if args.size == "all" else 1) > 200:
        parser.error("more than 200 shards requires measured compute admission; split admitted runs explicitly")
    summary = Counter()
    for size in SIZE_LADDER if args.size == "all" else [args.size]:
        directory = args.output / size
        directory.mkdir(parents=True, exist_ok=True)
        for seed in range(args.seed, args.seed + args.count):
            path = directory / f"seed-{seed:08d}.json"
            sidecar = path.with_suffix(".audit.json")
            expected = {"vessel": args.vessel, "port": args.port, "size": size,
                "blocking_ratio": repr(float(args.blocking_ratio)), "seed": str(seed), "generator": effective_random_yard_version()}
            if args.booking_slack is not None:
                expected["booking_slack"] = repr(float(args.booking_slack))
            if args.booking_mass_fraction is not None:
                expected["booking_mass_fraction"] = repr(float(args.booking_mass_fraction))
            if args.c2_screen:
                expected["c2_screen"] = "necessary"
            if rule_freeze_v1_enabled():
                expected.update(cargo_profile=args.cargo_profile,
                                d62_crossing_requested_share=repr(args.d62_crossing_share))
            resume_options = (dict(cargo_profile=args.cargo_profile, d62_crossing_share=args.d62_crossing_share)
                              if rule_freeze_v1_enabled() else {})
            inputs = _resume_inputs(args.vessel, args.port, **resume_options)
            if path.exists():
                instance = load_instance(path)
                if (any(instance.family.get(key) != value for key, value in expected.items())
                        or (args.booking_mass_fraction is None and "booking_mass_fraction" in instance.family)
                        or (args.booking_slack is None and "booking_slack" in instance.family)
                        or (not args.c2_screen and "c2_screen" in instance.family)
                        or instance.provenance.tainted):
                    raise ValueError(f"resume configuration mismatch: {path}")
                if sidecar.exists():
                    _validate_resume_audit(path, sidecar, instance, inputs)
                    summary["resumed"] += 1
                    continue
            audit = RandomGenerationReport()
            instance = generate_random_instance(size, seed=seed, blocking_ratio=args.blocking_ratio,
                vessel_name=args.vessel, port_name=args.port, report=audit,
                **({"booking_slack": args.booking_slack} if args.booking_slack is not None else {}),
                **({"booking_mass_fraction": args.booking_mass_fraction} if args.booking_mass_fraction is not None else {}),
                **({"c2_screen": True} if args.c2_screen else {}),
                **({"cargo_profile": args.cargo_profile, "d62_crossing_share": args.d62_crossing_share}
                   if rule_freeze_v1_enabled() or args.cargo_profile != "feeder_export" else {}))
            if _resume_inputs(args.vessel, args.port, **resume_options) != inputs:
                raise ValueError("generation inputs changed while building shard; retry after inputs are stable")
            temp = path.with_suffix(".partial.json")
            save_instance(instance, temp)
            temp.replace(path)
            _write_audit(sidecar, audit, path, inputs)
            summary["generated"] += 1
            summary["attempts"] += audit.attempts
            summary["screened_out"] += audit.attempts - 1
    print(json.dumps(dict(summary), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
