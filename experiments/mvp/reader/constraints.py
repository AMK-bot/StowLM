"""Executable conservative instruction seam for mask and Frontier 0.9 search.

The caller supplies a strict physical-cover -> POD allocation. This module does
not optimize that allocation, run plan_and_check, or certify a binding replan.
"""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass

import numpy as np

from llm.instructions.apply import ConstrainedInstance
from llm.instructions.mask_filter import InstructionEngine
from stowage.conventions import cells_of_slot


class InfeasibleInstruction(ValueError):
    """The proposed cover allocation conflicts with fixed state or geometry."""


@dataclass
class CompiledHatchConstraint:
    instance: object
    cover_ports: dict[str, str]
    cell_covers: dict[str, str | None]
    box_ports: dict[str, str]
    base: ConstrainedInstance

    def forbids(self, box: str, slot: str) -> str | None:
        previous = self.base.forbids(box, slot)
        if previous:
            return previous
        if box not in self.box_ports:
            return "instruction: unknown container"
        try:
            footprint = cells_of_slot(slot)
        except ValueError:
            return "instruction: invalid slot"
        for cell in footprint:
            if cell not in self.cell_covers:
                return "instruction: cell outside vessel"
            cover = self.cell_covers[cell]
            # A fixed deck with no cover is outside a physical hatch-cover rule.
            if cover is not None and self.cover_ports.get(cover) != self.box_ports[box]:
                return f"instruction: {cover} is not allocated to {self.box_ports[box]}"
        return None

    def audit_positions(self, positions: dict[str, str]) -> list[str]:
        """Independent physical-cover/POD audit, including every supplied box."""
        ports = {}
        errors = []
        for box, slot in positions.items():
            if box not in self.box_ports:
                errors.append(f"unknown container {box}")
                continue
            try:
                footprint = cells_of_slot(slot)
            except ValueError:
                errors.append(f"invalid slot {slot}")
                continue
            reason = self.base.forbids(box, slot)
            if reason:
                errors.append(reason)
            for cell in footprint:
                if cell not in self.cell_covers:
                    errors.append(f"unknown cell {cell}")
                    continue
                cover = self.cell_covers[cell]
                if cover is not None:
                    ports.setdefault(cover, set()).add(self.box_ports[box])
                    if self.cover_ports.get(cover) != self.box_ports[box]:
                        errors.append(f"{box}: allocation mismatch at {cover}")
        errors.extend(f"mixed discharge ports at {cover}: {sorted(pods)}"
                      for cover, pods in ports.items() if len(pods) > 1)
        return errors

    def violations(self, plan) -> list[str]:
        from stowage.moves import apply_plan
        errors = []
        for move in plan.moves:
            if move.kind in {"load", "restow_on", "restow_shift"} and hasattr(move, "container_id"):
                reason = self.forbids(move.container_id, move.dst)
                if reason:
                    errors.append(reason)
        try:
            replay = apply_plan(self.instance, plan)
        except (ValueError, KeyError, TypeError):
            return errors + ["instruction audit: invalid plan replay"]
        if not replay.legal:
            return errors + ["instruction audit: illegal plan replay"]
        positions = {p.container_id: p.slot for p in replay.final_state.ship.placements}
        errors.extend(self.audit_positions(positions))
        missing = set(self.instance.load_list) - positions.keys()
        if missing:
            errors.append(f"missing load containers: {sorted(missing)}")
        discharged = set(self.instance.discharge_list)
        for placement in self.instance.onboard.placements:
            if placement.container_id not in discharged and positions.get(placement.container_id) != placement.slot:
                errors.append(f"retained cargo changed final slot: {placement.container_id}")
        return errors

    def honoured(self, plan) -> bool:
        return not self.violations(plan)


def compile_one_port_per_hatch(instance, cover_ports: dict[str, str], *, base=None) -> CompiledHatchConstraint:
    """Compile a conservative cover assignment; fail closed on retained conflicts.

    Omitted covers are closed to every POD. No soft-sharing fallback is allowed.
    Prior instructions are intersected, never replaced or weakened.
    """
    if base is not None and base.instance.id != instance.id:
        raise ValueError("base constraints belong to another instance")
    covers = {h.id: h for h in instance.vessel.hatch_covers}
    if not covers or set(cover_ports) - covers.keys():
        raise InfeasibleInstruction("unknown or absent physical hatch cover")
    boxes = {b.id: str(b.pod) for b in instance.containers}
    known_ports = set(boxes.values()) | {str(c.locode) for c in instance.rotation.calls}
    if any(not isinstance(pod, str) or pod not in known_ports for pod in cover_ports.values()):
        raise InfeasibleInstruction("unknown discharge port")
    stacks = {s.id: s for s in instance.vessel.stacks}
    listed = {}
    for cover in covers.values():
        for sid in cover.hold_stack_ids + cover.deck_stack_ids:
            if sid in listed and listed[sid] != cover.id:
                raise InfeasibleInstruction("stack assigned to multiple physical covers")
            listed[sid] = cover.id
    cell_covers = {}
    for cell in instance.vessel.cells:
        stack = stacks[cell.stack_id]
        cover = stack.hatch_cover_id or listed.get(stack.id)
        if (cover is not None and cover not in covers) or (stack.hatch_cover_id and listed.get(stack.id, cover) != cover):
            raise InfeasibleInstruction("inconsistent physical cover membership")
        if cover is None and not stack.on_deck:
            raise InfeasibleInstruction("hold cell has no physical cover")
        cell_covers[cell.id] = cover
    out = CompiledHatchConstraint(instance, dict(cover_ports), cell_covers, boxes,
                                  base if base is not None else ConstrainedInstance(instance))
    retained = {p.container_id: p.slot for p in instance.onboard.placements
                if p.container_id not in set(instance.discharge_list)}
    if out.audit_positions(retained):
        raise InfeasibleInstruction("cover allocation conflicts with retained onboard cargo")
    return out


class ReaderEngine(InstructionEngine):
    """Standard instruction mask plus copied static table for Frontier's master."""
    def __init__(self, inner, constrained, table=None, static_tables=None):
        super().__init__(inner, constrained, table)
        if static_tables is None:
            static_tables = copy(inner.T)
            static = np.array(inner.T.static, dtype=bool, copy=True)
            for b, box in enumerate(inner.index_boxes):
                for s, slot in enumerate(inner.index_slots):
                    if constrained.forbids(box, slot):
                        static[b, s] = False
            static.setflags(write=False)
            static_tables.static = static
        self.T = static_tables

    def copy(self):
        return ReaderEngine(self.inner.copy(), self.constrained, self.table, self.T)


def wrap_engine(engine, constrained) -> ReaderEngine:
    if engine.instance.id != constrained.instance.id:
        raise ValueError("engine and constraint belong to different instances")
    return ReaderEngine(engine, constrained)


def constrain_frontier_problem(problem, constrained):
    """Mutate only this experiment Problem: mask/master restrictions + score gate.

    Supply it to alns.search(..., problem=problem). The stock plan_and_check
    creates another Problem, so must not be used as an instruction-aware driver.
    Re-audit finished output with constrained.honoured and the full checker.
    """
    problem.engine = wrap_engine(problem.engine, constrained)
    previous_score = problem.score

    def score(actions):
        actions = tuple(actions)
        if any(constrained.forbids(a.box, a.slot) for a in actions):
            return None
        return previous_score(actions)

    problem.score = score
    return problem
