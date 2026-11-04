"""Apply a parsed constraint to an instance as a set of forbidden placements (tighten-only).

``apply(instance, constraint)`` returns a :class:`ConstrainedInstance`: the *unchanged* instance plus the
constraints and a ``forbids(box, slot)`` predicate. Removing slots is the only effect, so a constrained plan is
always a plan of the original instance, and the full checker judges it exactly as it judges any other plan.

APPROXIMATION: the constraint does not travel inside ``Instance``. ``Instance.plan_constraints`` exists, but the
checker's ``check_plan_constraints`` hook fails closed for every kind until the checker owner implements them, and
editing vessel cells would break the fleet-parameter digests the checker verifies. So the solver (and, in task 9.3,
StowLM's feasibility mask) is restricted through ``forbids``, and :meth:`ConstrainedInstance.honoured` verifies the
finished plan independently. The checker still gates every plan under the demo rule set.

APPROXIMATION: ``close_bay`` forbids loading (and restowing) boxes into the bay; hatch-cover lifts and discharges in
that bay are not restricted (stage A has no discharges).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from stowage.conventions import cells_of_slot, odd_bays_of, parse_slot
from stowage.model import Instance, MovePlan

from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers

Constraint = CloseBay | MaxTiers | DgKeepAway


class NotApplicable(ValueError):
    """The constraint names something this instance doesn't have (e.g. a bay outside the vessel)."""


def _bays(instance: Instance) -> list[int]:
    return sorted({parse_slot(c.id).bay for c in instance.vessel.cells})


def _closed_cells(instance: Instance, constraint: MaxTiers | CloseBay) -> set[str]:
    cells = [c.id for c in instance.vessel.cells]
    if isinstance(constraint, CloseBay):
        bays = set(odd_bays_of(constraint.bay))
        return {c for c in cells if parse_slot(c).bay in bays}
    bays = None if constraint.bay is None else set(odd_bays_of(constraint.bay))
    stacks: dict[tuple[int, int], list[str]] = defaultdict(list)
    for c in cells:
        a = parse_slot(c)
        if (constraint.zone == "deck") == a.on_deck and (bays is None or a.bay in bays):
            stacks[(a.bay, a.row)].append(c)
    closed = set()
    for stack in stacks.values():
        stack.sort(key=lambda c: parse_slot(c).tier)
        closed.update(stack[constraint.max_tiers:])
    return closed


def _dg_classes(container) -> set[str]:
    """Every primary class and subsidiary risk in the box's whole DG tree (mixed loads: all ``additional``
    entries at every depth, as the checker traverses them; review rev-v4-a P1)."""
    found: set[str] = set()
    stack = [container.dg] if container.dg is not None else []
    while stack:
        dg = stack.pop()
        found.add(dg.imdg_class)
        found.update(dg.subsidiary_risks)
        stack.extend(dg.additional)
    return found


def _class_matches(wanted: str, classes: Iterable[str]) -> bool:
    # "2" matches every division of class 2; "2.1" matches only 2.1. Compatibility letters (1.4S) are ignored.
    return any(c == wanted or c.startswith(wanted + ".") or c.rstrip("ABCDEFGHJKLNS") == wanted for c in classes)


def _dg_closed(instance: Instance, constraint: DgKeepAway) -> tuple[set[str], set[str]]:
    """(boxes the rule applies to, cells they may not use)."""
    boxes = {c.id for c in instance.containers if c.dg is not None and _class_matches(constraint.imdg_class,
                                                                                    _dg_classes(c))}
    odd_bays = sorted({parse_slot(c.id).bay for c in instance.vessel.cells})
    if constraint.away_from.kind == "bay":
        target = constraint.away_from.bay
        anchors = set(odd_bays_of(target))
        # Bay distance in 20-ft bays: odd bays two numbers apart are adjacent (01 -> 03 is 1 bay).
        closed = {c.id for c in instance.vessel.cells
                  if min(abs(parse_slot(c.id).bay - a) // 2 for a in anchors) < constraint.min_bays}
        return boxes, closed
    regions = [r for r in instance.vessel.exclusion_regions if r.kind == "accommodation"]
    if not regions:
        raise NotApplicable("this vessel has no accommodation region")
    # APPROXIMATION: "k bays" = k times this vessel's 20-ft bay pitch (median LCG step between adjacent odd bays),
    # measured from the accommodation's longitudinal extent to the cell centre.
    lcg = {b: sorted(c.lcg_m for c in instance.vessel.cells if parse_slot(c.id).bay == b)[0] for b in odd_bays}
    steps = sorted(abs(lcg[b] - lcg[a]) for a, b in zip(odd_bays, odd_bays[1:]) if b - a == 2)
    pitch = steps[len(steps) // 2] if steps else 6.1  # 6.1 m: nominal 20-ft bay incl. cell guides (assumption)
    closed = set()
    for c in instance.vessel.cells:
        gap = min(max(r.bounds.x_min_m - c.lcg_m, c.lcg_m - r.bounds.x_max_m, 0.0) for r in regions)
        if gap < constraint.min_bays * pitch:
            closed.add(c.id)
    return boxes, closed


@dataclass
class ConstrainedInstance:
    """The original instance plus tighten-only constraints, expressed as forbidden (box, cell) placements."""

    instance: Instance
    constraints: list[Constraint] = field(default_factory=list)
    _all_boxes: set[str] = field(default_factory=set)        # cells closed to every box
    _per_box: dict[str, set[str]] = field(default_factory=dict)  # cells closed to particular boxes

    def forbids(self, box: str, slot: str) -> str | None:
        """Reason a placement of ``box`` at ship ``slot`` breaks a constraint, else None. Used by the solver hook
        and (task 9.3) StowLM's mask; a 40-ft slot is forbidden if either of its cells is."""
        cells = cells_of_slot(slot)
        if any(c in self._all_boxes for c in cells):
            return f"instruction: slot {slot} is closed"
        if any(c in self._per_box.get(box, ()) for c in cells):
            return f"instruction: {box} may not be stowed at {slot}"
        return None

    def open_cells(self, box: str | None = None) -> set[str]:
        closed = self._all_boxes | (self._per_box.get(box, set()) if box else set())
        return {c.id for c in self.instance.vessel.cells} - closed

    def violations(self, plan: MovePlan) -> list[str]:
        """Every placement in ``plan`` that breaks a constraint (loads and restow-ons)."""
        out = []
        for move in plan.moves:
            if move.kind in ("load", "restow_on", "restow_shift") and hasattr(move, "container_id"):
                reason = self.forbids(move.container_id, move.dst)
                if reason:
                    out.append(f"{move.kind} {move.container_id} -> {move.dst}: {reason}")
        return out

    def honoured(self, plan: MovePlan) -> bool:
        return not self.violations(plan)

    def capacity_shortfall(self) -> str | None:
        """A cheap necessary check before solving: enough open cells for the boxes to load (in TEU)."""
        load = [c for c in self.instance.containers if c.id in set(self.instance.load_list)]
        teu = sum(1 if c.length_ft == 20 else 2 for c in load)
        open_cells = len(self.open_cells())
        if teu > open_cells:
            return (f"the load list needs {teu} TEU of cells but only {open_cells} remain open "
                    f"({len(self.instance.vessel.cells) - open_cells} closed by the instruction)")
        for box in self._per_box:
            if box in self.instance.load_list and not self.open_cells(box):
                return f"{box} has no slot left that keeps it away as instructed"
        return None


def apply(instance: Instance, constraint: Constraint, base: ConstrainedInstance | None = None) -> ConstrainedInstance:
    """Tighten ``instance`` (or an already constrained one) by ``constraint``. Raises NotApplicable when the
    constraint refers to a bay this vessel doesn't have."""
    out = ConstrainedInstance(instance, list(base.constraints) if base else [],
                              set(base._all_boxes) if base else set(),
                              {k: set(v) for k, v in base._per_box.items()} if base else {})
    bays = _bays(instance)
    named = getattr(constraint, "bay", None)
    if isinstance(constraint, DgKeepAway) and constraint.away_from.kind == "bay":
        named = constraint.away_from.bay
    if named is not None and not set(odd_bays_of(named)) & set(bays):
        raise NotApplicable(f"bay {named} is not on this vessel (bays here: "
                            f"{', '.join(f'{b:02d}' for b in bays)})")
    if isinstance(constraint, DgKeepAway):
        boxes, cells = _dg_closed(instance, constraint)
        for box in boxes:
            out._per_box.setdefault(box, set()).update(cells)
    else:
        out._all_boxes |= _closed_cells(instance, constraint)
    out.constraints.append(constraint)
    return out


def describe(constrained: ConstrainedInstance) -> str:
    """One line on what the constraints remove, for the demo."""
    total = len(constrained.instance.vessel.cells)
    closed = sorted(constrained._all_boxes)
    parts = [f"{len(closed)} of {total} cells closed to all boxes" + (f" ({', '.join(closed)})" if closed else "")]
    for box, cells in sorted(constrained._per_box.items()):
        parts.append(f"{box}: {len(cells)} cells closed")
    if constrained.constraints and not closed and not constrained._per_box:
        parts.append("no box on this load list is affected")
    return "; ".join(parts)
