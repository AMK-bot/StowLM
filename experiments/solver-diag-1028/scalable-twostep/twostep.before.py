"""Industry two-step baseline: cargo-only carrier plan, then terminal optimisation.

Following the expert's written process (docs/domain.md E10 and decisions.md D14),
the carrier ignores the actual yard. Its depth-first pre-stow first fills
compatible hold space with class-H cargo (D25/E6), then considers deck H and
lighter cargo. Within a class it tries furthest POD and heaviest cargo first,
low hold slots and POD-compatible hatch order,
and backtracks when exact local rules or necessary remaining-cargo bounds fail.
Special bookings, DG, OOG, stack limits, capacity matching and the task 3.7 linear
stability/strength completion LP retain their shared checks and measured margins.

The terminal keeps every pre-stow category and slot. A one-worker CP-SAT model
assigns real category-compatible boxes and orders loading, minimizing original
stack rehandle indicators. It replays proposals through the same feasibility API
and adds no-good cuts to backtrack from rejected prefixes. Actual expanded moves,
including future restows and covers, rank accepted candidates. Ballast belongs to
task 4.2's finisher; only the full checker can certify the final plan.

APPROXIMATION: CP's original-yard objective is a lower-fidelity search surrogate
for repeated relocations; true complete move counts select incumbents. Finite
search is not an optimality proof. Cargo-moment tie-breaks are heuristics, while
linear feasibility uses the real margin-tightened completion LP. See the terminal
and adapter modules for their precise limits. Sources: expert E10/D14; OR-Tools
CP-SAT documentation (constraint programming with integer variables); Hall (1935)
for matching; Bertsimas & Tsitsiklis, Introduction to Linear Optimization, ch. 1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from time import monotonic

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place
from stowage.conventions import cells_of_slot, parse_slot
from stowage.solver.twostep_adapter import make_planning_engine as make_feasibility_engine
from stowage.frozen import current_provenance
from stowage.model import Instance, Placement, PlannerResult, Yard, YardBlock, YardStack, YardState

VERSION = "3.1.1"


@lru_cache(maxsize=1)
def _default_weight_classes():
    from stowage.rules.catalogue import load_catalogue
    return load_catalogue().weight_classes


def category_of(box, instance=None) -> tuple:
    """Booking facts and D25 VGM-derived class; never trust cached cargo labels.

    The public catalogue owns L/M/H thresholds. Instance E2 threshold overrides
    use the same public schema validation as the default catalogue. Actual mass
    remains part of every oracle placement check, including stack and strength
    limits; two boxes in one class need not have equal mass.
    """
    classes = _default_weight_classes()
    overrides = instance.rule_overrides.get("expert.e2.weight_order", {}) if instance is not None else {}
    if any(name in overrides for name in ("medium_min_kg", "heavy_min_kg")):
        parameters = dict(classes.parameters)
        for name in ("medium_min_kg", "heavy_min_kg"):
            if name in overrides:
                value = overrides[name]
                if isinstance(value, dict):
                    value = value.get("value")
                parameters[name] = parameters[name].model_copy(update={"value": value})
        classes = type(classes).model_validate({**classes.model_dump(), "parameters": parameters})
    return (box.pod, box.length_ft, box.height_m, classes.classify(box.gross_mass_kg),
            box.reefer, box.reefer_setpoint_c,
            box.dg.model_dump_json() if box.dg is not None else None,
            tuple(sorted(str(s) for s in box.special_stowage)),
            box.oog.model_dump_json() if box.oog is not None else None)


@dataclass(frozen=True)
class SlotCategory:
    slot: str
    category: tuple
    representative_box: str


@dataclass(frozen=True)
class PreStowPlan:
    slots: tuple[SlotCategory, ...] = ()
    actions: tuple[Place, ...] = ()
    status: str = "ok"
    message: str | None = None


def _canonical_instance(instance: Instance) -> Instance:
    """Discard the real yard, including its non-manifest cargo and dimensions.

    Each load-list box is on its own accessible stack, with ample empty positions
    for discharges too when yard handling equipment exists.
    Synthetic geometry removes retrieval decisions from carrier planning. The
    original port's ship-side rules, water, crane and limits are retained.
    """
    boxes = {b.id: b for b in instance.containers}
    load_ids = set(instance.load_list)
    yard_ids = load_ids | set(instance.discharge_list)
    blocks, stacks = [], []
    for length in (20, 40, 45):
        ids = sorted(cid for cid in yard_ids if boxes[cid].length_ft == length)
        if not ids:
            continue
        # No blocks or equipment is a valid no-load call. Without equipment,
        # discharges have no destination and the oracle must reject them.
        if not instance.port.equipment and not instance.load_list:
            continue
        equipment_id = instance.port.equipment[0].id
        per_row = 400  # spare ground positions; fits the three-digit bay convention
        rows = max(1, math.ceil(len(ids) / per_row))
        if rows > 99:
            raise ValueError("Canonical yard exceeds supported 39,600 boxes per size class")
        bid = f"T{length}"
        bays = min(999, max(4, 2 * min(len(ids), per_row) + 2))
        blocks.append(YardBlock(id=bid, n_bays=bays, n_rows=rows, max_tiers=1,
                                equipment_id=equipment_id, reefer_rows=list(range(1, rows + 1)),
                                dg_block=True, allowed_lengths_ft=[length]))
        for i, cid in enumerate(ids):
            if cid not in load_ids:
                continue
            row, bay = i // per_row + 1, 2 * (i % per_row) + 1
            stacks.append(YardStack(key=f"{bid}-{bay:03d}-{row:02d}", length_ft=length, boxes=[cid]))
    yard = Yard(id="twostep-canonical", blocks=blocks)
    port = instance.port.model_copy(update={"yard": yard})
    retained = set(instance.load_list) | {p.container_id for p in instance.onboard.placements}
    return instance.model_copy(update={"port": port,
        "yard": YardState(yard_id=yard.id, stacks=stacks),
        "containers": [boxes[cid] for cid in sorted(retained)]})


def _pod(instance, box) -> int:
    return next((c.index for c in instance.rotation.calls
                 if c.index > instance.call_index and c.locode == box.pod), instance.call_index)


def _cargo_key(instance, box):
    # The departure E6 rule makes filling the hold with H a prerequisite for
    # deck H, even when lighter cargo has a more distant POD. Use VGM classes,
    # including instance threshold overrides, rather than cached cargo labels.
    return (category_of(box, instance)[3] != "H", -_pod(instance, box),
            -box.gross_mass_kg, box.length_ft == 20, box.id)


def _slot_score(instance, actions, action, *, context=None):
    """Carrier priorities: avoid overstows, heavy low, balance cargo moments."""
    # APPROXIMATION: cargo moments are a search tie-break, not a stability or
    # strength certificate. The shared oracle alone enforces the physical model.
    if context is None:
        context = _slot_context(instance, actions)
    boxes, cells, stacks, previous = context
    b = boxes[action.box]
    footprint = cells_of_slot(action.slot)
    target = [cells[c] for c in footprint]
    target_stacks = {cells[c].stack_id for c in footprint}
    covers = {stacks[s].hatch_cover_id for s in target_stacks} - {None}
    candidate_pod = _pod(instance, b)
    overstows = 0
    mx = my = mass = 0.0
    hull = instance.vessel.hull
    center_x = hull.lpp_m / 2 if hull is not None else 0.0
    for cid, slot in previous + [(action.box, action.slot)]:
        box = boxes[cid]
        supports = [cells[c] for c in cells_of_slot(slot)]
        x = sum(c.lcg_m for c in supports) / len(supports)
        y = sum(c.tcg_m for c in supports) / len(supports)
        mx += box.gross_mass_kg * (x - center_x)
        my += box.gross_mass_kg * y
        mass += box.gross_mass_kg
        if cid == action.box:
            continue
        for c in supports:
            stack = stacks[c.stack_id]
            # A later POD above an earlier POD forces a future restow. Deck
            # cargo above an earlier hold POD also blocks opening its hatch.
            if candidate_pod > _pod(instance, box):
                if c.stack_id in target_stacks and parse_slot(slot).tier < parse_slot(action.slot).tier:
                    overstows += 1
                elif parse_slot(action.slot).on_deck and not parse_slot(slot).on_deck and stack.hatch_cover_id in covers:
                    overstows += 1
    height = sum(c.vcg_base_m for c in target) / len(target)
    return (overstows, int(parse_slot(action.slot).on_deck), height,
            abs(my) / max(mass, 1.0), abs(mx) / max(mass, 1.0), action.slot)


def _slot_context(instance, actions):
    """Share vessel and cargo dictionaries across all scores at one search node."""
    previous = [(p.container_id, p.slot) for p in instance.onboard.placements
                if p.container_id not in instance.discharge_list]
    previous += [(a.box, a.slot) for a in actions if isinstance(a, Place)]
    return ({b.id: b for b in instance.containers},
            {c.id: c for c in instance.vessel.cells},
            {s.id: s for s in instance.vessel.stacks}, previous)


def _preplan(instance, *, beam_width: int, deadline: float) -> PreStowPlan:
    if monotonic() >= deadline:
        return PreStowPlan(status="timeout", message="Cargo-only pre-stow time limit")
    canonical = _canonical_instance(instance)
    space = get_action_space("boxslot")
    root = make_feasibility_engine(canonical, space)
    boxes = {b.id: b for b in canonical.containers}
    original = {p.container_id: p.slot for p in canonical.onboard.placements
                if p.container_id not in canonical.discharge_list}

    dead_reasons = set()
    model_unavailable = []
    cargo_keys = {cid: _cargo_key(canonical, box) for cid, box in boxes.items()}

    def choices(engine, trace):
        # v2.3 exposes exactly the public legal Place set as flat indices. Group
        # array slices first, decoding only the box currently being explored;
        # this avoids millions of action objects on production-size ships.
        indexed = callable(getattr(engine, "legal_index", None))
        grouped = {}
        discharges = []
        if indexed:
            flat = engine.legal_index()
            n_slots = engine.n_slots
            slot_ids = engine.index_slots
            for bi, cid in enumerate(engine.index_boxes):
                lo = int(flat.searchsorted(bi * n_slots))
                hi = int(flat.searchsorted((bi + 1) * n_slots))
                if hi > lo:
                    indices = flat[lo:hi]
                    if cid not in canonical.load_list:
                        indices = indices[[slot_ids[int(i) % n_slots] == original.get(cid) for i in indices]]
                    if len(indices):
                        grouped[cid] = indices
            discharges = list(engine.legal_discharges())
        else:
            # Protocol-only oracles remain supported; the index API is additive.
            for action in engine.legal_actions():
                if isinstance(action, Discharge):
                    discharges.append(action)
                elif isinstance(action, Place):
                    if action.box not in canonical.load_list and original.get(action.box) != action.slot:
                        continue
                    grouped.setdefault(action.box, []).append(action)
        if not grouped and not discharges:
            dead = engine.dead_end()
            if dead is not None:
                dead_reasons.add(dead.reason + (f" [{', '.join(dead.rule_ids)}]" if dead.rule_ids else ""))
                if "checker.missing_vessel_data" in dead.rule_ids:
                    model_unavailable.append(dead.reason)
        def is_hold(value):
            slot = slot_ids[int(value) % n_slots] if indexed else value.slot
            return not parse_slot(slot).on_deck

        has_hold = {cid: any(is_hold(value) for value in values)
                    for cid, values in grouped.items()}
        yield from sorted(discharges, key=space.sort_key)
        def box_priority(cid):
            key = cargo_keys[cid]
            return (key[0], not has_hold[cid] if not key[0] else False, *key[1:])

        context = _slot_context(canonical, trace)
        for cid in sorted(grouped, key=box_priority):
            heavy = not cargo_keys[cid][0]
            values = grouped[cid]
            # A hold-compatible H must take hold before any of its deck choices.
            # Hard on-deck bookings have no compatible hold and retain their
            # legal deck choices. No rule is cloned: compatibility is the public
            # oracle's legal set, and departure E6 is checked below by 2.4.
            if heavy and has_hold[cid] and "on_deck" not in boxes[cid].special_stowage:
                values = [value for value in values if is_hold(value)]
            actions = (engine.decode_index(int(value)) for value in values) if indexed else values
            for action in sorted(actions, key=lambda a: (
                    bool(engine.forces_restow(a)) if callable(getattr(engine, "forces_restow", None)) else False,
                    _slot_score(canonical, trace, a, context=context))):
                yield action

    # Explicit DFS remembers untried alternatives at every depth. Unlike beam
    # pruning, a later dead end can return all the way to an earlier bad slot.
    stack = [(root, (), None, Expander(canonical))]
    seen = set()
    while stack:
        if monotonic() >= deadline:
            return PreStowPlan(status="timeout", message="Cargo-only pre-stow time limit")
        engine, trace, iterator, expander = stack[-1]
        if engine.is_complete():
            from stowage.checker.rules import e6_completion_possible
            ship = canonical.onboard.model_copy(update={"placements": [
                Placement(container_id=cid, slot=location[1])
                for cid, location in expander.where.items() if location[0] == "ship"]})
            if not e6_completion_possible(canonical, ship, (), call_index=canonical.call_index):
                dead_reasons.add("Departure has compatible empty hold space for deck H [expert.e6.heavy_under_deck]")
                stack.pop()
                continue
            places = tuple(Place(cid, expander.location(cid)[1]) for cid in sorted(instance.load_list))
            return PreStowPlan(tuple(SlotCategory(a.slot, category_of(boxes[a.box], canonical), a.box)
                                     for a in places),
                               tuple(a for a in trace if isinstance(a, Place) and a.box in instance.load_list))
        if iterator is None:
            iterator = iter(choices(engine, trace))
            stack[-1] = engine, trace, iterator, expander
        advanced = False
        for action in iterator:
            if monotonic() >= deadline:
                return PreStowPlan(status="timeout", message="Cargo-only pre-stow time limit")
            if not engine.is_legal(action):
                continue
            child = engine.copy()
            child.push(action)
            child_trace = trace + (action,)
            child_expander = expander.copy()
            child_expander.step(action)
            signature = (tuple(sorted(child_expander.where.items())),
                         tuple((k, tuple(v)) for k, v in sorted(child_expander.yard.items())),
                         tuple(sorted(child_expander.covers_off.items())), tuple(child_expander.buffer))
            if signature in seen:
                continue
            seen.add(signature)
            stack.append((child, child_trace, None, child_expander))
            advanced = True
            break
        if not advanced:
            stack.pop()
    detail = ": " + "; ".join(sorted(dead_reasons)) if dead_reasons else ""
    if model_unavailable:
        return PreStowPlan(status="error", message="Public feasibility model unavailable" + detail)
    return PreStowPlan(status="dead_end", message="Cargo-only pre-stow search exhausted legal alternatives" + detail)


def preplan(instance: Instance, *, seed: int = 0, time_limit_s: float | None = None,
            beam_width: int = 4) -> PreStowPlan:
    """Build auditable cargo-only slot categories; seed reserved for protocol parity.

    Missing shared feasibility implementation raises NotImplementedError here;
    the Planner catches it and returns an explicit dependency error.
    """
    del seed
    if beam_width < 1:
        raise ValueError("beam_width must be positive")
    deadline = monotonic() + (30.0 if time_limit_s is None else max(0.0, time_limit_s))
    return _preplan(instance, beam_width=beam_width, deadline=deadline)


class TwoStepPlanner:
    """Cargo-only DFS followed by fixed-slot CP-SAT assignment and sequencing.

    ``beam_width`` remains accepted for compatibility; pre-stow now backtracks
    without beam pruning. Metrics include CP solves, no-good cuts, true objective,
    pre-stow categories and fixed-slot completion failures. ``ok`` is complete
    cargo, pending task 4.2 ballast and the independent full checker.
    """
    name = "baseline.twostep"
    version = VERSION
    sets_ballast = False
    # Wall-clock cutoffs and best-so-far terminal candidates can vary with
    # execution speed, even with one CP worker and a fixed random seed.
    deterministic = False

    def __init__(self, *, beam_width: int = 4):
        if beam_width < 1:
            raise ValueError("beam_width must be positive")
        self.beam_width = beam_width

    def preplan(self, instance: Instance, *, seed: int = 0,
                time_limit_s: float | None = None) -> PreStowPlan:
        return preplan(instance, seed=seed, time_limit_s=time_limit_s, beam_width=self.beam_width)

    def plan(self, instance: Instance, *, seed: int = 0, time_limit_s: float | None = None) -> PlannerResult:
        from stowage.solver.twostep_terminal import terminal_plan
        start = monotonic()
        limit = 60.0 if time_limit_s is None else max(0.0, time_limit_s)
        deadline = start + limit
        provenance = current_provenance(created_by="solver.twostep", seed=seed).merge(instance.provenance)
        metrics = {"deterministic": 0, "num_workers": 1, "fixed_slot_failure": 0,
                   "preplan_status": "not_started", "terminal_status": "not_started",
                   "terminal_expansions": 0, "terminal_max_legal_choices": 0,
                   "feasibility_adapter": "public-v2"}
        plan = None
        try:
            prestow = _preplan(instance, beam_width=self.beam_width, deadline=start + limit * 0.5)
            metrics.update(preplan_status=prestow.status, preplan_message=prestow.message,
                preplan_slots=[{"slot": s.slot, "category": s.category,
                                "representative_box": s.representative_box} for s in prestow.slots])
            status, message = prestow.status, prestow.message
            if prestow.status == "ok":
                plan, terminal_metrics, status, message = terminal_plan(instance, prestow,
                    deadline=deadline, seed=seed, engine_factory=make_feasibility_engine)
                metrics.update(terminal_metrics)
        except NotImplementedError as exc:
            status, message = "error", f"Shared feasibility checks unavailable: {exc}"
        except Exception as exc:
            status, message = "error", f"{type(exc).__name__}: {exc}"
        if plan is not None:
            plan = plan.model_copy(update={"provenance": plan.provenance.merge(provenance)})
        return PlannerResult(status=status, plan=plan, planner=self.name, planner_version=self.version,
            seed=seed, wall_time_s=monotonic() - start, metrics=metrics, message=message,
            provenance=provenance)


Planner = TwoStepPlanner
