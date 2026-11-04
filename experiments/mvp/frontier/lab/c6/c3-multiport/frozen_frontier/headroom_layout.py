"""External block restrictions for the D175 train-only headroom experiment.

A block is one hatch cover (the vessel's physical hatch group) times hold/deck.
Only nonempty blocks count. A fixed deck with no cover gets an explicit
``fixed:<odd-bay>`` group rather than silently disappearing. A booking is
``(POD, public D25 weight class)``; a 40/45-foot box must be permitted in *all*
blocks touched by its physical cells. Only this call's load list is restricted;
through cargo and its forced restoration remain ordinary solver behavior.

Candidate "feasible" means static packing witnessed: length acceptance, reefer
plugs, distinct footprint cells, and (for CP-generated witnesses) bottom-up
support. This is a necessary geometry/capacity screen, NEVER a checker pass or
proof that a complete rule-feasible plan exists. A permuted witness inherits
the occupied cells/support of its source. Weight order, masses, DG, OOG,
stability, terminal order, and ballast still need the normal planner/checker.

No source files are changed. The context manager scopes the same placement
screens used by instruction replanning, including the constructor fallback.
Independent audits must gate carrier, terminal and finished plans.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import importlib
import json
import random
import threading
from typing import Any, Mapping

from stowage.actions.space import Place
from stowage.conventions import cells_of_slot, format_slot, parse_slot
from stowage.solver.twostep import category_of

Category = tuple[str, str]


def booking_category(box, instance) -> Category:
    category = category_of(box, instance)
    return str(category[0]), str(category[3])


@dataclass(frozen=True)
class Block:
    id: str
    hatch_group: str
    zone: str
    cell_ids: tuple[str, ...]
    stack_ids: tuple[str, ...]

    def to_dict(self):
        return {"id": self.id, "hatch_group": self.hatch_group, "zone": self.zone,
                "cell_ids": list(self.cell_ids), "stack_ids": list(self.stack_ids),
                "capacity_teu_cells": len(self.cell_ids)}


@dataclass(frozen=True)
class BlockModel:
    blocks: tuple[Block, ...]
    cell_to_block: Mapping[str, str]
    fixed_deck_groups: tuple[str, ...] = ()

    @property
    def block_ids(self):
        return tuple(b.id for b in self.blocks)

    @classmethod
    def from_instance(cls, instance):
        stacks = {s.id: s for s in instance.vessel.stacks}
        covers = {h.id: h for h in instance.vessel.hatch_covers}
        listed = {}
        for cover in covers.values():
            for sid in cover.hold_stack_ids + cover.deck_stack_ids:
                if sid in listed and listed[sid] != cover.id:
                    raise ValueError(f"stack {sid} belongs to multiple hatch covers")
                listed[sid] = cover.id
        grouped_cells, grouped_stacks = defaultdict(list), defaultdict(set)
        cell_map, fixed = {}, set()
        for cell in instance.vessel.cells:
            stack = stacks[cell.stack_id]
            group = stack.hatch_cover_id or listed.get(stack.id)
            if stack.hatch_cover_id and listed.get(stack.id, stack.hatch_cover_id) != stack.hatch_cover_id:
                raise ValueError(f"inconsistent hatch membership for {stack.id}")
            if group is None:
                if not stack.on_deck:
                    raise ValueError(f"hold stack {stack.id} has no hatch group")
                group = f"fixed:{stack.bay:03d}"
                fixed.add(group)
            zone = "deck" if stack.on_deck else "hold"
            bid = f"{group}/{zone}"
            grouped_cells[group, zone].append(cell.id)
            grouped_stacks[group, zone].add(stack.id)
            cell_map[cell.id] = bid
        blocks = tuple(Block(f"{group}/{zone}", group, zone,
                             tuple(sorted(cells)), tuple(sorted(grouped_stacks[group, zone])))
                       for (group, zone), cells in sorted(grouped_cells.items()))
        return cls(blocks, cell_map, tuple(sorted(fixed)))

    def blocks_of_slot(self, slot):
        return frozenset(self.cell_to_block[cell] for cell in cells_of_slot(slot))

    def to_dict(self):
        return {"block_count": len(self.blocks), "blocks": [b.to_dict() for b in self.blocks],
                "fixed_deck_groups": list(self.fixed_deck_groups)}


@dataclass(frozen=True)
class BlockLayout:
    allowed: Mapping[Category, frozenset[str]]
    name: str = "layout"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {"name": self.name, "allowed": [
            {"pod": key[0], "weight_class": key[1], "blocks": sorted(value)}
            for key, value in sorted(self.allowed.items())], "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        return cls({(row["pod"], row["weight_class"]): frozenset(row["blocks"])
                    for row in value["allowed"]}, value.get("name", "layout"), value.get("metadata", {}))

    @property
    def fingerprint(self):
        data = self.to_dict()["allowed"]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:20]


layout_to_dict = BlockLayout.to_dict
layout_from_dict = BlockLayout.from_dict


def _categories(instance):
    loads = set(instance.load_list)
    return {b.id: booking_category(b, instance) for b in instance.containers if b.id in loads}


def unrestricted_layout(instance, model=None):
    model = model or BlockModel.from_instance(instance)
    return BlockLayout({cat: frozenset(model.block_ids) for cat in set(_categories(instance).values())},
                       "unrestricted", {"source": "two-step default, no restriction"})


def _plan_positions(instance, plan):
    """Replay, rather than guess from the last explicit load/restow move."""
    from stowage.moves import apply_plan
    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise ValueError("packing source plan failed physical move replay")
    positions = {p.container_id: p.slot for p in replay.final_state.ship.placements
                 if p.container_id in instance.load_list}
    missing = set(instance.load_list) - positions.keys()
    if missing:
        raise ValueError(f"packing source plan misses {sorted(missing)}")
    return positions


def _layout_from_positions(instance, positions, model, name, metadata=None):
    allowed = defaultdict(set)
    cats = _categories(instance)
    for cid, slot in positions.items():
        allowed[cats[cid]].update(model.blocks_of_slot(slot))
    return BlockLayout({cat: frozenset(allowed[cat]) for cat in set(cats.values())}, name,
                       metadata or {})


def layout_from_plan(instance, plan, model=None, name="default-derived"):
    model = model or BlockModel.from_instance(instance)
    positions = _plan_positions(instance, plan)
    return _layout_from_positions(instance, positions, model, name,
                                  {"source": "source plan departure positions", "packing_witness": positions})


def forbids(instance, layout, box_id, slot, model=None):
    if box_id not in instance.load_list:
        return None
    model = model or BlockModel.from_instance(instance)
    box = next(b for b in instance.containers if b.id == box_id)
    category = booking_category(box, instance)
    try:
        touched = model.blocks_of_slot(slot)
    except KeyError:
        return f"headroom: slot {slot} is outside vessel cells"
    disallowed = touched - layout.allowed.get(category, frozenset())
    return f"headroom: {category} forbidden in {sorted(disallowed)}" if disallowed else None


_HOOK_LOCK = threading.Lock()
_HOOK_MODULES = ("stowage.solver.twostep", "stowage.solver.twostep_terminal",
                 "stowage.solver.scalable", "stowage.solver.construct_sequence",
                 "stowage.solver.construct_v4", "stowage.solver.exact_engine")


@contextmanager
def constraint_rule(instance, layout, model=None):
    """One solve per process; unrelated threads/instances retain their screens.

    The pinned source lacks the staged through-restoration hook, but only load
    cargo is restricted here, so through restorations are intentionally exempt.
    If that hook exists in a future runtime, it is scoped the same way too.
    """
    model = model or BlockModel.from_instance(instance)
    cats, owner = _categories(instance), threading.get_ident()
    known = set(model.block_ids)
    if any(set(blocks) - known for blocks in layout.allowed.values()):
        raise ValueError("layout names a block outside this vessel")
    if not _HOOK_LOCK.acquire(blocking=False):
        raise RuntimeError("headroom restriction is already active in this process")
    originals = {}

    def wrap(original):
        def screen(inst, expander, action, *args, **kwargs):
            if (threading.get_ident() == owner and inst.id == instance.id
                    and isinstance(action, Place) and action.box in cats):
                try:
                    touched = model.blocks_of_slot(action.slot)
                except KeyError:
                    return f"headroom: slot {action.slot} is outside vessel cells"
                if touched - layout.allowed.get(cats[action.box], frozenset()):
                    return f"headroom: {cats[action.box]} forbidden in {sorted(touched)}"
            return original(inst, expander, action, *args, **kwargs)
        return screen

    try:
        for name in _HOOK_MODULES:
            module = importlib.import_module(name)
            for attribute in ("securing_geometry_violation", "restoration_placement_violation"):
                if hasattr(module, attribute):
                    original = getattr(module, attribute)
                    originals[module, attribute] = original
                    setattr(module, attribute, wrap(original))
        yield
    finally:
        for (module, attribute), original in originals.items():
            setattr(module, attribute, original)
        _HOOK_LOCK.release()


def audit_prestow(instance, prestow, layout, model=None):
    model = model or BlockModel.from_instance(instance)
    violations = []
    for slot in prestow.slots:
        reason = forbids(instance, layout, slot.representative_box, slot.slot, model)
        if reason:
            violations.append(reason)
    return violations


def audit_layout(instance, plan, layout, model=None):
    """Audit every explicit placement AND replayed final load-cargo positions."""
    model = model or BlockModel.from_instance(instance)
    violations = []
    for move in plan.moves:
        if move.kind in {"load", "restow_on", "restow_shift"} and hasattr(move, "container_id"):
            reason = forbids(instance, layout, move.container_id, move.dst, model)
            if reason:
                violations.append(f"{move.kind}: {reason}")
    try:
        positions = _plan_positions(instance, plan)
    except ValueError as exc:
        violations.append(f"final replay: {exc}")
    else:
        for cid, slot in positions.items():
            reason = forbids(instance, layout, cid, slot, model)
            if reason:
                violations.append(f"departure {cid}: {reason}")
    return violations


def _fits(box, slot, cells):
    footprint = cells_of_slot(slot)
    if any(cell not in cells for cell in footprint):
        return False
    if (box.length_ft == 20) != (len(footprint) == 1):
        return False
    if not all(getattr(cells[cell], f"accepts_{box.length_ft}") for cell in footprint):
        return False
    return not box.reefer or any(cells[cell].reefer_plug for cell in footprint)


def _slots(instance):
    cells = {cell.id: cell for cell in instance.vessel.cells}
    slots = set(cells)
    for cell in cells:
        address = parse_slot(cell)
        for bay in (address.bay - 1, address.bay + 1):
            if bay >= 2:
                slot = format_slot(bay, address.row, address.tier)
                if all(part in cells for part in cells_of_slot(slot)):
                    slots.add(slot)
    return sorted(slots)


def _cp_packing(instance, seed, time_limit_s=5.):
    """Find one static packing, not a rule-feasible stowage plan."""
    from ortools.sat.python import cp_model
    cells = {cell.id: cell for cell in instance.vessel.cells}
    boxes = {b.id: b for b in instance.containers}
    retained = {cell for p in instance.onboard.placements if p.container_id not in instance.discharge_list
                for cell in cells_of_slot(p.slot)}
    slots, model = _slots(instance), cp_model.CpModel()
    assignment, by_cell = {}, defaultdict(list)
    groups = defaultdict(list)
    for cid in instance.load_list:
        # Static fit here depends only on length and reefer status. Grouping
        # interchangeable IDs avoids O(boxes*slots) Boolean variables on medium.
        groups[boxes[cid].length_ft, bool(boxes[cid].reefer)].append(cid)
    for key, ids in groups.items():
        box = boxes[ids[0]]
        row = []
        for slot in slots:
            if not _fits(box, slot, cells) or retained.intersection(cells_of_slot(slot)):
                continue
            var = model.new_bool_var(f"p_{key}_{slot}")
            assignment[key, slot] = var
            row.append(var)
            for cell in cells_of_slot(slot):
                by_cell[cell].append(var)
        if not row:
            raise ValueError(f"no static-fit slot for packing group {key}")
        model.add(sum(row) == len(ids))
    occupied = {}
    for cell in cells:
        var = model.new_bool_var(f"occupied_{cell}")
        model.add(var == (1 if cell in retained else 0) + sum(by_cell[cell]))
        occupied[cell] = var
    for stack in instance.vessel.stacks:
        for below, above in zip(stack.cell_ids, stack.cell_ids[1:]):
            model.add(occupied[above] <= occupied[below])
    rng = random.Random(seed)
    # A seeded linear objective varies the first witness without adding rules.
    model.minimize(sum(rng.randrange(1, 10000) * var for var in assignment.values()))
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed % 2147483647
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.solve(model)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        raise ValueError(f"static packing not found ({solver.status_name(status)}); not a proof of rule infeasibility")
    selected = defaultdict(list)
    for (key, slot), var in assignment.items():
        if solver.value(var):
            selected[key].append(slot)
    return {cid: slot for key, ids in groups.items() for cid, slot in zip(ids, sorted(selected[key]))}


def _greedy_packing(instance, seed, attempts=24):
    """Cheap complete static witness before building a large CP assignment.

    Long and reefer cargo go first; only already-supported slots are chosen.
    This is a bounded heuristic. Failure falls back to CP, never to a claim
    that a layout/call is infeasible.
    """
    cells = {cell.id: cell for cell in instance.vessel.cells}
    boxes = {b.id: b for b in instance.containers}
    stacks = {s.id: s for s in instance.vessel.stacks}
    retained = {cell for p in instance.onboard.placements if p.container_id not in instance.discharge_list
                for cell in cells_of_slot(p.slot)}
    slots, rng = _slots(instance), random.Random(seed)
    footprints = {slot: frozenset(cells_of_slot(slot)) for slot in slots}
    below = {cell: frozenset(stacks[cells[cell].stack_id].cell_ids[:
                       stacks[cells[cell].stack_id].cell_ids.index(cell)]) for cell in cells}
    domains = {cid: [slot for slot in slots if _fits(boxes[cid], slot, cells)]
               for cid in instance.load_list}
    for _ in range(attempts):
        occupied, positions = set(retained), {}
        ids = list(instance.load_list)
        rng.shuffle(ids)
        ids.sort(key=lambda cid: (-boxes[cid].length_ft, not boxes[cid].reefer, len(domains[cid])))
        preference = {slot: rng.random() for slot in slots}
        for cid in ids:
            available = [slot for slot in domains[cid]
                         if not footprints[slot].intersection(occupied)
                         and all(below[cell] <= occupied for cell in footprints[slot])]
            if not available:
                break
            slot = min(available, key=lambda s: (parse_slot(s).tier, preference[s]))
            positions[cid] = slot
            occupied.update(footprints[slot])
        if len(positions) == len(instance.load_list):
            return positions
    return None


def _permuted_packing(instance, source, rng):
    """Randomized perfect matching onto source slots preserves occupied cells."""
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    targets = list(source.values())
    domain = {cid: [slot for slot in targets if _fits(boxes[cid], slot, cells)] for cid in source}
    for slots in domain.values():
        rng.shuffle(slots)
    matched = {}

    def assign(cid, visited):
        for slot in domain[cid]:
            if slot in visited:
                continue
            visited.add(slot)
            if slot not in matched or assign(matched[slot], visited):
                matched[slot] = cid
                return True
        return False

    ids = list(source)
    rng.shuffle(ids)
    ids.sort(key=lambda cid: len(domain[cid]))
    if not all(assign(cid, set()) for cid in ids):
        raise ValueError("source packing lost static fit under permutation")
    return {cid: slot for slot, cid in matched.items()}


def audit_packing(instance, positions, layout=None, model=None):
    """Independent witness check; [] means geometry only, not checker passed."""
    cells = {cell.id: cell for cell in instance.vessel.cells}
    boxes = {b.id: b for b in instance.containers}
    used = {cell for p in instance.onboard.placements if p.container_id not in instance.discharge_list
            for cell in cells_of_slot(p.slot)}
    reasons = []
    if set(positions) != set(instance.load_list):
        reasons.append("witness IDs differ from load list")
    for cid, slot in positions.items():
        if cid not in boxes or not _fits(boxes[cid], slot, cells):
            reasons.append(f"static fit: {cid} {slot}")
        if used.intersection(cells_of_slot(slot)):
            reasons.append(f"overlap: {cid} {slot}")
        used.update(cells_of_slot(slot))
        if layout is not None:
            reason = forbids(instance, layout, cid, slot, model)
            if reason:
                reasons.append(reason)
    for stack in instance.vessel.stacks:
        for below, above in zip(stack.cell_ids, stack.cell_ids[1:]):
            if above in used and below not in used:
                reasons.append(f"unsupported cell: {above}")
    return reasons


def random_layouts(instance, *, count=64, seed=0, base_plan=None, model=None):
    """Up to ``count`` UNIQUE geometry-witnessed layouts, deterministic seed.

    Randomized booking-to-slot matchings yield capacity-valid minimal masks;
    independent random extra permissions vary constraint strength. Sampling is
    capped at max(2048,count*256) proposals. If the finite space saturates, the
    shorter result and metadata expose that fact instead of duplicating rows.
    """
    model = model or BlockModel.from_instance(instance)
    if base_plan is not None:
        source = _plan_positions(instance, base_plan)
        source_method = "source plan departure positions"
    else:
        source = _greedy_packing(instance, seed)
        source_method = "greedy static geometry packing"
        if source is None:
            source = _cp_packing(instance, seed)
            source_method = "CP-SAT static geometry packing"
    witness_issues = audit_packing(instance, source, model=model)
    if witness_issues:
        raise ValueError("invalid static source packing: " + "; ".join(witness_issues))
    rng, found = random.Random(seed), {}
    max_attempts = max(2048, count * 256)
    for attempt in range(max_attempts):
        if len(found) >= count:
            break
        packing = _permuted_packing(instance, source, rng)
        layout = _layout_from_positions(instance, packing, model, f"random-{len(found):03d}")
        # Mix sparse plans and broad supersets; always retain the witness.
        probability = rng.choice((0., .1, .25, .5, .75, .9))
        allowed = {cat: frozenset(set(blocks) | {bid for bid in model.block_ids if rng.random() < probability})
                   for cat, blocks in layout.allowed.items()}
        metadata = {"source": "random static-packing permutation + permission superset",
                    "feasibility": "static packing only; planner and full checker required",
                    "packing_source": source_method,
                    "packing_witness": packing, "seed": seed, "proposal": attempt,
                    "extra_permission_probability": probability}
        candidate = BlockLayout(allowed, layout.name, metadata)
        found.setdefault(candidate.fingerprint, candidate)
    candidates = list(found.values())
    return [BlockLayout(c.allowed, c.name, {**c.metadata, "requested_unique": count,
                                           "generated_unique": len(candidates),
                                           "sampling_saturated": len(candidates) < count}) for c in candidates]


def local_neighbours(instance, layout, *, seed=0, limit=32, model=None):
    """One permission flip around an incumbent; no feasibility claim.

    Removals can require a new physical assignment; the solver must decide.
    Additions preserve any existing packing witness. Every booking retains at
    least one permitted block. Call repeatedly around improving incumbents for
    simple hill climbing; generated fingerprints support resumable dedup.
    """
    model = model or BlockModel.from_instance(instance)
    flips = [(cat, block) for cat in sorted(layout.allowed) for block in model.block_ids]
    rng = random.Random(seed)
    rng.shuffle(flips)
    out = []
    for cat, block in flips:
        allowed = {key: set(value) for key, value in layout.allowed.items()}
        removal = block in allowed[cat]
        if removal:
            if len(allowed[cat]) == 1:
                continue
            allowed[cat].remove(block)
        else:
            allowed[cat].add(block)
        out.append(BlockLayout({key: frozenset(value) for key, value in allowed.items()},
                               f"local-{len(out):03d}", {"source": "one permission flip",
                                "parent": layout.fingerprint, "operation": "remove" if removal else "add",
                                "category": cat, "block": block,
                                "feasibility": "unverified; solve required"}))
        if len(out) >= limit:
            break
    return out
