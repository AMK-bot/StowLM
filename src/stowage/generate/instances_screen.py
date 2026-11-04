"""Optimistic necessary conditions for synthetic Stage A bookings.

A necessary condition can disprove feasibility; passing it is never a loading
plan. We relax packing by counting physical cells and distinct supporting
stacks, and allow each cargo type its own candidate positions. Relaxation is
standard OR preprocessing (Wolsey, Integer Programming, 1998, chapter 1).
IMDG eligibility follows the source-traced 42-24 DGL and checker 2.4. No
shipment-specific certificate is invented. Identical cargo eligibility profiles
reuse a per-call filter trace; every demand unit and rejection reason remains
counted. Exact segregation, OOG neighbours,
strength and equilibrium remain the checker/solver's responsibility.
"""
from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Mapping
import math

from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M, parse_slot
from stowage.model import Container, PortProfile, Vessel
from stowage.rules.imdg.dgl import lookup_dgl


STOWAGE = "imdg.stowage.3_2"


def _hazards(dg):
    yield dg
    for item in dg.additional:
        yield from _hazards(item)


def _dg_policy(box):
    """Return required DoC classes, deck-only flag, unresolved provision flag."""
    classes, deck, unresolved = set(), False, False
    if box.dg is None:
        return classes, deck, unresolved
    for dg in _hazards(box.dg):
        classes.update((dg.imdg_class, *dg.subsidiary_risks))
        try:
            entry = lookup_dgl(int(dg.un_number), dg.packing_group)
        except (KeyError, ValueError):
            unresolved = True
            continue
        classes.update(entry.subsidiary_risks)
        codes = set(entry.stowage_codes) | set(dg.stowage_codes) | set(dg.handling_codes)
        unresolved |= bool(entry.unresolved_provisions) or entry.stowage_category not in "ABCDE"
        unresolved |= (dg.imdg_class != entry.hazard_class
                       or dg.stowage_category != entry.stowage_category
                       or not set(entry.subsidiary_risks) <= set(dg.subsidiary_risks))
        # SW16/SW19 have conditional alternatives. Keep the most permissive
        # supported alternative; shipment facts still need checker verification.
        unresolved |= bool(codes - {"SW1", "SW2", "SW6", "SW11", "SW16", "SW19", "H1", "H5"})
        deck |= entry.stowage_category in {"C", "D"}
    return classes, deck, unresolved


def count_unresolved_provisions(boxes: Iterable[Container]) -> int:
    """Count boxes whose DG declaration cannot clear the sampled DGL policy.

    Counts each box once, including mixed DG cargo, unsupported categories or
    codes, unknown DGL entries and declarations inconsistent with their DGL
    row. This is distinct from DoC position/quantity failures: an ordinary DG
    box with no eligible stack is not counted here.
    """
    return sum(bool(_dg_policy(box)[2]) for box in boxes)


def _heat_clearance(port):
    if port is not None:
        for rule in port.port_rules:
            if rule.rule_id == "expert.e8.away_from_heat":
                for name in ("minimum_clearance", "heat_clearance_m"):
                    value = rule.params.get(name)
                    if isinstance(value, dict):
                        value = value.get("value")
                    if isinstance(value, (int, float)) and value >= 0:
                        return float(value)
    return None


def screen_load_list(
    vessel: Vessel, boxes: Iterable[Container], *, port: PortProfile | None = None,
    tank_masses_kg: Mapping[str, float] | None = None,
) -> list[str]:
    """Return sorted, unique catalogue reasons disproving a Stage A booking.

    The ship is empty of cargo, apart from ``fixed_loads``. Supplied tank masses
    are fixed; absent masses are optimistically zero. Forty/forty-five foot
    boxes consume two physical cells (not 2.25 for 45-foot equipment). Only
    necessary capacity bounds are tested: candidate positions may overlap.
    Unsupported DGL provisions fail closed because this API has no external
    shipment-verification input. Under-deck/OOG heights use the full empty
    stack envelope, which deliberately allows any possible heights below.
    """
    boxes = list(boxes)
    reasons = set()
    cells = {c.id: c for c in vessel.cells}
    stacks = {s.id: s for s in vessel.stacks}
    units = lambda b: 1 if b.length_ft == 20 else 2
    if sum(map(units, boxes)) > len(cells):
        reasons.add("expert.physical.slot_occupancy")
    upper = {s.id: max(s.max_mass_kg, s.max_mass_20_kg or 0, s.max_mass_40_kg or 0)
             for s in vessel.stacks}
    if sum(b.gross_mass_kg for b in boxes) > sum(upper.values()):
        reasons.add("expert.physical.stack_weight")

    # All odd-bay adjacent pairs are retained. Restricting to bay % 4 == 1
    # incorrectly rejects ships whose accepting cells start at bay 003.
    positions = {}
    for length in (20, 40, 45):
        candidates = []
        for cell in vessel.cells:
            if not getattr(cell, f"accepts_{length}"):
                continue
            a = parse_slot(cell.id)
            physical = [cell]
            if length != 20:
                partner = cells.get(f"{a.bay + 2:03d}{a.row:02d}{a.tier:02d}")
                if partner is None or not getattr(partner, f"accepts_{length}"):
                    continue
                physical.append(partner)
            if port and any(stacks[c.stack_id].on_deck and
                            stacks[c.stack_id].cell_ids.index(c.id) >= port.quay_crane.max_on_deck_tiers
                            for c in physical):
                continue
            candidates.append(tuple(physical))
        positions[length] = candidates

    # Group constraints have a union bound: demanded physical units cannot
    # exceed the distinct eligible cells. This catches multiple reefers/special
    # bookings competing for one region without claiming a full packing proof.
    # All caches live for one call: mutable vessel/port models cannot leave stale
    # geometry behind. A trace stores every filter reached, including empty
    # candidate lists, so replay preserves original reason attribution exactly.
    groups = {}

    def add_group(key, box, choices):
        demand, alternatives = groups.setdefault(key, [0, {}])
        groups[key][0] = demand + units(box)
        # Retaining each list makes its identity stable for this call.
        alternatives[id(choices)] = choices

    dg_policies = {}
    eligibility = {}
    mass_thresholds = {length: sorted({min(upper[c.stack_id] for c in p)
                                      for p in candidates})
                       for length, candidates in positions.items()}
    clearance = _heat_clearance(port)
    final_choices = []
    for box in boxes:
        choices = positions[box.length_ft]
        add_group(("expert.physical.size_fit", box.length_ft), box, choices)
        declaration = box.dg.model_dump_json() if box.dg else None
        if declaration not in dg_policies:
            dg_policies[declaration] = _dg_policy(box)
        classes, deck, unresolved = dg_policies[declaration]
        extra = box.oog.over_height_m if box.oog else 0
        # Every weight predicate compares mass/len(position) with a stack
        # bound. Equal ranks therefore give exactly equal boolean outcomes.
        rank = bisect_left(mass_thresholds[box.length_ft],
                           box.gross_mass_kg / (1 if box.length_ft == 20 else 2))
        key = (box.length_ft, box.reefer, frozenset(box.special_stowage),
               frozenset(classes), deck, unresolved, box.height_m, extra, rank)
        if key in eligibility:
            trace, failed, choices = eligibility[key]
            for rule_kind, candidates in trace:
                add_group(rule_kind, box, candidates)
            reasons.update(failed)
            final_choices.append((box, choices))
            continue
        trace, failed = [], set()
        filters = []
        if box.reefer:
            filters.append(("expert.physical.reefer_plug", lambda p: any(c.reefer_plug for c in p)))
        for name in ("under_deck", "on_deck"):
            if name in box.special_stowage:
                filters.append((f"expert.e8.{name}", lambda p, n=name: all(
                    stacks[c.stack_id].on_deck == (n == "on_deck") for c in p)))
        if unresolved:
            reasons.add(STOWAGE)
            eligibility[key] = ([], {STOWAGE}, [])
            final_choices.append((box, []))
            continue
        if classes:
            filters.append((STOWAGE, lambda p, cls=classes, d=deck: all(
                stacks[c.stack_id].dg_classes_allowed is not None
                and cls <= set(stacks[c.stack_id].dg_classes_allowed)
                and (not d or stacks[c.stack_id].on_deck) for c in p)))
        filters.append(("expert.e9.over_height" if extra else "expert.physical.tier_height",
                        lambda p, h=box.height_m + extra: all(stacks[c.stack_id].max_height_m >= h for c in p)))
        filters.append(("expert.physical.stack_weight", lambda p, m=box.gross_mass_kg: all(
            upper[c.stack_id] >= m / len(p) for c in p)))
        if "away_from_heat" in box.special_stowage and clearance is not None:
            def away(p, b=box):
                x = sum(c.lcg_m for c in p) / len(p)
                y = sum(c.tcg_m for c in p) / len(p)
                zlo = max(stacks[c.stack_id].base_z_m for c in p)
                zhi = min(stacks[c.stack_id].base_z_m + stacks[c.stack_id].max_height_m for c in p) - b.height_m
                for heat in vessel.heat_sources:
                    dx = max(heat.x_min_m - (x + CONTAINER_LENGTH_M[b.length_ft]/2),
                             x - CONTAINER_LENGTH_M[b.length_ft]/2 - heat.x_max_m, 0)
                    dy = max(heat.y_min_m - (y + CONTAINER_WIDTH_M/2),
                             y - CONTAINER_WIDTH_M/2 - heat.y_max_m, 0)
                    # Maximum separation over all possible floors is an upper
                    # bound, so no actual height arrangement is excluded.
                    dz = max(heat.z_min_m - (zlo + b.height_m), zhi - heat.z_max_m, 0)
                    if math.sqrt(dx*dx + dy*dy + dz*dz) < clearance:
                        return False
                return True
            filters.append(("expert.e8.away_from_heat", away))
        for rule, predicate in filters:
            if not choices:
                break  # Preserve the first cause; downstream filters saw no candidates.
            choices = [p for p in choices if predicate(p)]
            group_key = (rule, box.length_ft)
            add_group(group_key, box, choices)
            trace.append((group_key, choices))
            if not choices:
                reasons.add(rule)
                failed.add(rule)
        if choices and ("top_stowage" in box.special_stowage or extra):
            group_key = ("expert.e8.top_stowage" if not extra else "expert.e9.over_height", "top")
            add_group(group_key, box, choices)
            trace.append((group_key, choices))

        eligibility[key] = (trace, failed, choices)
        final_choices.append((box, choices))

    # OOG overhang has no absolute hull-edge prohibition in checker 2.4. It
    # blocks neighbouring cargo. Prove pairwise impossibility only when even
    # freely chosen stack floors cannot separate their occupied envelopes.
    # A fixed work cap keeps this preprocessing bounded; skipped pairs are
    # deliberately retained for the solver/checker, never declared feasible.
    pair_budget = 100_000

    def envelope(box, physical):
        oog = box.oog
        x = sum(c.lcg_m for c in physical) / len(physical)
        y = sum(c.tcg_m for c in physical) / len(physical)
        height = box.height_m + (oog.over_height_m if oog else 0)
        lo = max(stacks[c.stack_id].base_z_m for c in physical)
        hi = min(stacks[c.stack_id].base_z_m + stacks[c.stack_id].max_height_m for c in physical) - height
        return ((x-CONTAINER_LENGTH_M[box.length_ft]/2-(oog.over_length_aft_m if oog else 0),
                 x+CONTAINER_LENGTH_M[box.length_ft]/2+(oog.over_length_fore_m if oog else 0)),
                (y-CONTAINER_WIDTH_M/2-(oog.over_width_starboard_m if oog else 0),
                 y+CONTAINER_WIDTH_M/2+(oog.over_width_port_m if oog else 0)), lo, hi, height)

    for i, (a, pa) in enumerate(final_choices):
        for b, pb in final_choices[i+1:]:
            if not any(box.oog and any((box.oog.over_width_port_m, box.oog.over_width_starboard_m,
                                       box.oog.over_length_fore_m, box.oog.over_length_aft_m)) for box in (a, b)):
                continue
            cost = len(pa) * len(pb)
            if not cost or cost > pair_budget:
                continue
            pair_budget -= cost
            possible = False
            for p in pa:
                ea = envelope(a, p)
                for q in pb:
                    if {c.id for c in p} & {c.id for c in q}:
                        continue
                    eb = envelope(b, q)
                    horizontal = any(ea[k][1] <= eb[k][0] or eb[k][1] <= ea[k][0] for k in (0, 1))
                    vertical = ea[3] >= eb[2]+eb[4] or eb[3] >= ea[2]+ea[4]
                    if horizontal or vertical:
                        possible = True
                        break
                if possible:
                    break
            if not possible:
                reasons.add("expert.e9.over_width_length")

    for (rule, kind), (demand, alternatives) in groups.items():
        available = {c.stack_id if kind == "top" else c.id
                     for candidates in alternatives.values() for p in candidates for c in p}
        if demand > len(available):
            reasons.add(rule)
    if sum(b.reefer for b in boxes) > sum(c.reefer_plug for c in vessel.cells):
        reasons.add("expert.physical.reefer_plug")

    hull = vessel.hull
    if hull is None or not hull.hydrostatics:
        reasons.add("checker.missing_vessel_data")
    else:
        mass = hull.lightship_mass_kg + sum(p[3] for p in vessel.fixed_loads) + sum(
            b.gross_mass_kg for b in boxes) + sum((tank_masses_kg or {}).values())
        density = port.water_density_kg_m3 if port else hull.water_density_kg_m3
        # APPROXIMATION: the tabulated hull envelope is only a sanity bound,
        # not an equilibrium calculation (Biran & López-Pulido, 2014, ch. 2).
        # Inflate cap by maximum transverse waterplane rise to avoid rejecting
        # a legal listed condition merely because its even-keel mass differs.
        for cap, rule in ((hull.max_draught_m, "iscode.loading.draft"),
                          (port.max_draught_m if port else None, "port.call.draft")):
            if cap is None:
                continue
            cap += hull.beam_m / 2 * abs(math.tan(hull.max_list_rad))
            rows = hull.hydrostatics
            if cap > rows[-1].draught_m:
                continue  # table does not bound the admissible envelope
            # Round UP to a measured row: linear interpolation need not be
            # an upper bound for a nonlinear displacement curve.
            maximum = next(row.displacement_kg for row in rows if row.draught_m >= cap)
            if mass > maximum * density / hull.water_density_kg_m3:
                reasons.update((rule, "iscode.loading.displacement"))
    return sorted(reasons)
