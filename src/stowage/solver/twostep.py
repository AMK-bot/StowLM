"""Industry two-step baseline: cargo-only carrier plan, then terminal optimisation.

Following the expert's written process (docs/domain.md E10 and decisions.md D14),
the carrier ignores the actual yard. Tiny keeps depth-first pre-stow; small and
medium first use bounded hold-first greedy search on a canonical singleton yard,
then reuse the bounded MRV constructor if that search fails. The greedy search caches
per-box legal domains and scores slots from prefix cargo aggregates. Pre-stow first fills
compatible hold space with class-H cargo (D25/E6), then considers deck H and
lighter cargo. Within a class it tries furthest POD and heaviest cargo first,
low hold slots and POD-compatible hatch order,
and backtracks when exact local rules or necessary remaining-cargo bounds fail.
Special bookings, DG, OOG, stack limits and capacity matching retain their shared
checks. D79 defaults C2 OFF on this small/medium path; the task 3.7 completion LP
runs only when explicitly enabled there. Tiny's configured checks are unchanged.

The terminal keeps the booked category pattern, with bounded mask-approved slot
relocations for carrier approval (D73). Small/medium empty-ship calls try fast
yard-top and representative-box category replays; tiny and nonempty-ship calls
retain one-worker CP-SAT assignment and loading order. CP rejects prefixes with
no-good cuts. All proposals replay through the same feasibility API. Actual expanded moves,
including future restows and covers, rank accepted candidates. Ballast belongs to
task 4.2's finisher; only the full checker can certify the final plan.

APPROXIMATION: CP's original-yard objective is a lower-fidelity search surrogate
for repeated relocations; true complete move counts select incumbents. Finite
search is not an optimality proof. Cargo-moment tie-breaks are heuristics; C2,
when enabled, uses the shared completion LP. See the terminal
and adapter modules for their precise limits. Sources: expert E10/D14; OR-Tools
CP-SAT documentation (constraint programming with integer variables); Hall (1935)
for matching; Bertsimas & Tsitsiklis, Introduction to Linear Optimization, ch. 1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from functools import lru_cache
from time import monotonic, perf_counter

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place
from stowage.conventions import cells_of_slot, parse_slot
from stowage.solver.twostep_adapter import make_planning_engine as make_feasibility_engine
from stowage.frozen import current_provenance
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.model import Instance, MovePlan, Placement, PlannerResult, Yard, YardBlock, YardStack, YardState
from stowage.solver.securing_geometry import (
    level_four_tiebreak, securing_geometry_violation, securing_state_violations,
)

VERSION = "4.4.0"


@lru_cache(maxsize=1)
def _default_weight_classes():
    from stowage.rules.catalogue import load_catalogue
    return load_catalogue().weight_classes


def category_of(box, instance=None) -> tuple:
    """Terminal booking facts and D25 weight class, including under D73.

    ISO equipment type is always part of the booked category (E10/D73),
    independently of the optional rule freeze. The public catalogue owns L/M/H
    thresholds. Instance E2 threshold overrides
    use the same public schema validation as the default catalogue. Actual mass
    remains part of every oracle placement check, including stack and strength
    limits. D73 permits distinct masses within one booked class to substitute;
    the shared mask, move engine and eventual full checker enforce actual kg.
    """
    freeze = rule_freeze_v1_enabled()
    classes = _default_weight_classes()
    overrides = instance.rule_overrides.get("expert.e2.weight_order", {}) if instance is not None else {}
    if not freeze and any(name in overrides for name in ("medium_min_kg", "heavy_min_kg")):
        parameters = dict(classes.parameters)
        for name in ("medium_min_kg", "heavy_min_kg"):
            if name in overrides:
                value = overrides[name]
                if isinstance(value, dict):
                    value = value.get("value")
                parameters[name] = parameters[name].model_copy(update={"value": value})
        classes = type(classes).model_validate({**classes.model_dump(), "parameters": parameters})
    weight_key = classes.classify(box.gross_mass_kg)
    return (box.pod, box.length_ft, box.height_m, weight_key,
            box.reefer, box.reefer_setpoint_c,
            box.dg.model_dump_json() if box.dg is not None else None,
            tuple(sorted(str(s) for s in box.special_stowage)),
            box.oog.model_dump_json() if box.oog is not None else None,
            box.iso_type)


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
    search_metrics: dict | None = None


def _canonical_instance(instance: Instance) -> Instance:
    """Discard the real yard, including its non-manifest cargo and dimensions.

    Each load-list box is on its own accessible stack, with ample empty positions
    for discharges too when yard handling equipment exists.
    Synthetic geometry removes retrieval decisions from carrier planning. The
    original port's ship-side rules, water, crane and limits are retained.
    """
    boxes = {b.id: b for b in instance.containers}
    load_ids = set(instance.load_list)
    yard_load_ids = {cid for cid in load_ids
                     if not (rule_freeze_v1_enabled() and boxes[cid].direct_delivery)}
    yard_ids = yard_load_ids | set(instance.discharge_list)
    blocks, stacks = [], []
    for length in (20, 40, 45):
        ids = sorted(cid for cid in yard_ids if boxes[cid].length_ft == length)
        if not ids:
            continue
        # No blocks or equipment is a valid no-load call. Without equipment,
        # ordinary discharges have no destination and the oracle rejects them.
        if not instance.port.equipment and (rule_freeze_v1_enabled() or not instance.load_list):
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
            if cid not in yard_load_ids:
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
    if rule_freeze_v1_enabled():
        return (False, -_pod(instance, box), -box.gross_mass_kg, box.length_ft == 20, box.id)
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
    if rule_freeze_v1_enabled():
        y = sum(c.tcg_m for c in target) / len(target)
        far_first = y if instance.port.berthing_side == "port" else -y
        return (int(parse_slot(action.slot).on_deck), far_first, overstows, height,
                abs(my) / max(mass, 1.0), abs(mx) / max(mass, 1.0), action.slot)
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


def _carrier_action_score(instance, expander, trace, action, *, context):
    """Rank an active carrier prefix by real moves, then checker preferences.

    APPROXIMATION: this greedily compares the completed current prefix, with
    its mandatory cover returns and the engine's D15 voyage projection. Cargo
    still ashore can change later costs; this is search guidance, not a bound
    or an optimality claim. The terminal still measures its full actual plan.
    """
    from stowage.moves import apply_plan

    trial = expander.copy()
    try:
        trial.step(action)
        trial.finish()
    except ValueError:
        return None  # physical expansion owns this rejection
    replay = apply_plan(instance, MovePlan(
        id=f"{instance.id}.carrier-prefix", instance_id=instance.id,
        call_index=instance.call_index, moves=trial.moves))
    # A partial prefix may still owe a discharge or quay restoration. Such an
    # unfinished departure is unknown, never permission to hide a legal choice.
    moves = replay.count.weighted_nonproductive if replay.legal else math.inf
    soft = level_four_tiebreak(instance, trial.world_state().ship)
    cells = context[1]
    footprint = [cells[c] for c in cells_of_slot(action.slot)]
    y = sum(c.tcg_m for c in footprint) / len(footprint)
    far = y if instance.port.berthing_side == "port" else -y
    return (moves, *soft, far, _slot_score(instance, trace, action, context=context),
            _cargo_key(instance, expander.boxes[action.box]))


def _preplan(instance, *, beam_width: int, deadline: float, feasibility_config=None, seed=0) -> PreStowPlan:
    if monotonic() >= deadline:
        return PreStowPlan(status="timeout", message="Cargo-only pre-stow time limit")
    canonical = _canonical_instance(instance)
    space = get_action_space("boxslot")
    kwargs = {"config": feasibility_config} if feasibility_config is not None else {}
    if _use_fast_prestow(instance):
        from stowage.feasibility import FeasibilityConfig
        if feasibility_config is None:
            feasibility_config = FeasibilityConfig(extra={"global_screen": 0})
        kwargs = {"config": feasibility_config}
        now = monotonic()
        greedy_budget = min(10., .4 * max(0., deadline - now))
        greedy_deadline = min(deadline, now + greedy_budget)
        # APPROXIMATION: greedy gets at most 10 seconds or 40% of the remaining
        # carrier budget. A fast failure gives all unused time to the MRV
        # constructor. This switches search only, never repairs a checker failure.
        stage = perf_counter()
        try:
            root = make_feasibility_engine(canonical, space, **kwargs)
            primary = _fast_prestow(canonical, root, beam_width=beam_width,
                                    deadline=greedy_deadline)
            if primary.status == "ok" and monotonic() >= greedy_deadline:
                primary = PreStowPlan(status="timeout", message="Greedy completion exceeded its initial slice")
        except Exception as exc:
            primary = PreStowPlan(status="error", message=f"Greedy carrier {type(exc).__name__}: {exc}")
        search_metrics = {
            "preplan_greedy_primary_status": primary.status,
            "preplan_greedy_primary_message": primary.message,
            "preplan_greedy_primary_time_s": perf_counter() - stage,
            "preplan_greedy_primary_budget_s": greedy_budget,
            "preplan_constructor_fallback_used": False,
            "constructor_status": "not_started", "constructor_shared_search_family": False,
            "effective_global_screen": feasibility_config.extra.get("global_screen", 1),
            "preplan_search_method": "bounded_greedy", "preplan_method": "bounded_greedy"}
        if primary.status == "ok":
            return replace(primary, search_metrics=search_metrics)
        if monotonic() >= deadline:
            return PreStowPlan(status="timeout", message="Cargo-only greedy search exhausted pre-stow deadline",
                               search_metrics=search_metrics)
        from stowage.solver.twostep_construct import construct_preplan
        stage = perf_counter()
        fallback, constructor_metrics = construct_preplan(canonical, deadline=deadline,
            seed=seed, feasibility_config=feasibility_config)
        if fallback.status == "ok" and monotonic() >= deadline:
            fallback = PreStowPlan(status="timeout", message="Constructor completion exceeded pre-stow deadline")
        search_metrics.update(constructor_metrics)
        search_metrics.update(preplan_method="canonical_constructor_after_greedy",
            preplan_search_method="canonical_constructor_after_greedy",
            preplan_constructor_fallback_used=True, preplan_constructor_status=fallback.status,
            preplan_constructor_message=fallback.message,
            preplan_constructor_fallback_time_s=perf_counter() - stage)
        return replace(fallback, search_metrics=search_metrics)
    root = make_feasibility_engine(canonical, space, **kwargs)
    boxes = {b.id: b for b in canonical.containers}
    original = {p.container_id: p.slot for p in canonical.onboard.placements
                if p.container_id not in canonical.discharge_list}

    dead_reasons = set()
    model_unavailable = []
    cargo_keys = {cid: _cargo_key(canonical, box) for cid, box in boxes.items()}

    def choices(engine, trace, expander):
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
        if rule_freeze_v1_enabled():
            # Compare across boxes as well as slots: a mass/POD search hint
            # must never outrank actual moves or the checker's soft metrics.
            ranked = []
            for cid, values in grouped.items():
                for value in values:
                    if monotonic() >= deadline:
                        return
                    action = engine.decode_index(int(value)) if indexed else value
                    score = _carrier_action_score(canonical, expander, trace, action, context=context)
                    if score is not None:
                        ranked.append((score, space.sort_key(action), action))
            yield from (action for _, _, action in sorted(ranked))
            return
        for cid in sorted(grouped, key=box_priority):
            heavy = not cargo_keys[cid][0]
            values = grouped[cid]
            # A hold-compatible H must take hold before any of its deck choices.
            # Hard on-deck bookings have no compatible hold and retain their
            # legal deck choices. No rule is cloned: compatibility is the public
            # oracle's legal set, and departure E6 is checked below by 2.4.
            if (not rule_freeze_v1_enabled() and heavy and has_hold[cid]
                    and "on_deck" not in boxes[cid].special_stowage):
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
            reasons = securing_state_violations(canonical, expander)
            if reasons:
                dead_reasons.update(reasons)
                stack.pop()
                continue
            from stowage.checker.rules import e6_completion_possible
            ship = canonical.onboard.model_copy(update={"placements": [
                Placement(container_id=cid, slot=location[1])
                for cid, location in expander.where.items() if location[0] == "ship"]})
            if (not rule_freeze_v1_enabled()
                    and not e6_completion_possible(canonical, ship, (), call_index=canonical.call_index)):
                dead_reasons.add("Departure has compatible empty hold space for deck H [expert.e6.heavy_under_deck]")
                stack.pop()
                continue
            places = tuple(Place(cid, expander.location(cid)[1]) for cid in sorted(instance.load_list))
            return PreStowPlan(tuple(SlotCategory(a.slot, category_of(boxes[a.box], canonical), a.box)
                                     for a in places),
                               tuple(a for a in trace if isinstance(a, Place) and a.box in instance.load_list))
        if iterator is None:
            iterator = iter(choices(engine, trace, expander))
            stack[-1] = engine, trace, iterator, expander
        advanced = False
        for action in iterator:
            if monotonic() >= deadline:
                return PreStowPlan(status="timeout", message="Cargo-only pre-stow time limit")
            if not engine.is_legal(action):
                continue
            if securing_geometry_violation(canonical, expander, action):
                dead_reasons.add("Unsupported cargo securing geometry [iacs.member_irs.container_securing]")
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


def _use_fast_prestow(instance):
    # Tiny retains the exhaustive carrier ordering used for its existing rows.
    return (instance.size_class in {"small", "medium"} and len(instance.load_list) > 6
            and not rule_freeze_v1_enabled())


def _fast_prestow(canonical, root, *, beam_width, deadline):
    """Yard-blind greedy carrier with bounded alternatives at each prefix.

    APPROXIMATION: keep at most ``4 * beam_width`` slots per chosen box. This
    bounded backtracking search can miss a completion; exhaustion is reported
    as a search failure, never as a proof of infeasibility. Exact local/C1/C2
    queries come from the two-step adapter's cached per-prefix box domains.
    Cargo moments and POD order rank candidates, not certify departure physics.
    """
    boxes = {b.id: b for b in canonical.containers}
    load = set(canonical.load_list)
    original = {p.container_id: p.slot for p in canonical.onboard.placements
                if p.container_id not in canonical.discharge_list}
    cells = {c.id: c for c in canonical.vessel.cells}
    stacks = {s.id: s for s in canonical.vessel.stacks}
    hull = canonical.vessel.hull
    center = hull.lpp_m / 2 if hull is not None else 0.
    pods = {cid: _pod(canonical, box) for cid, box in boxes.items()}
    cargo = {cid: _cargo_key(canonical, box) for cid, box in boxes.items()}
    ranks = {cid: {"H": 0, "M": 1, "L": 2}.get(category_of(box, canonical)[3], 3)
             for cid, box in boxes.items()}
    # Static geometry is shared across every prefix and every candidate score.
    geometry = {}
    def shape(slot):
        if slot not in geometry:
            footprint = [cells[c] for c in cells_of_slot(slot)]
            js = tuple(c.stack_id for c in footprint)
            geometry[slot] = (js, tuple({stacks[j].hatch_cover_id for j in js} - {None}),
                sum(c.lcg_m for c in footprint) / len(footprint) - center,
                sum(c.tcg_m for c in footprint) / len(footprint),
                sum(c.vcg_base_m for c in footprint) / len(footprint),
                parse_slot(slot).on_deck)
        return geometry[slot]

    reasons = set()
    slot_limit = max(4, 4 * beam_width)
    space = get_action_space("boxslot")

    def choices(engine, expander):
        # All inputs are canonical: no original yard position or retrieval cost
        # can affect the carrier's choices, including its tie-breaks.
        pending = [cid for cid in load | set(original)
                   if expander.where.get(cid, (None,))[0] != "ship"]
        def priority(cid):
            b = boxes[cid]
            k = cargo[cid]
            return (cid in load, ranks[cid],
                    not bool(b.dg or b.reefer or b.oog or b.special_stowage),
                    b.length_ft == 20, *k[1:])
        pending.sort(key=priority)
        discharges = (engine.legal_discharges() if canonical.discharge_list
                      and callable(getattr(engine, "legal_discharges", None)) else ())
        fallback = None
        if not callable(getattr(engine, "legal_slots_for_box", None)):
            fallback = tuple(engine.legal_actions())
            discharges = tuple(a for a in fallback if isinstance(a, Discharge))
        yield from sorted(discharges, key=space.sort_key)

        # Prefix aggregates make a slot score O(its footprint), rather than
        # rescanning every earlier cargo placement for each candidate slot.
        mx = my = mass = 0.
        stack_pod, hatch_pod = {}, {}
        for cid, location in expander.where.items():
            if location[0] != "ship":
                continue
            js, covers, x, y, _, deck = shape(location[1])
            m = boxes[cid].gross_mass_kg
            mx += m * x
            my += m * y
            mass += m
            for j in js:
                stack_pod[j] = min(stack_pod.get(j, math.inf), pods[cid])
            if not deck:
                for cover in covers:
                    hatch_pod[cover] = min(hatch_pod.get(cover, math.inf), pods[cid])

        domains = {}
        def ranked(cid, slots):
            b = boxes[cid]
            def score(slot):
                js, covers, x, y, height, deck = shape(slot)
                overstows = sum(pods[cid] > stack_pod.get(j, math.inf) for j in js)
                if deck:
                    overstows += sum(pods[cid] > hatch_pod.get(c, math.inf) for c in covers)
                action = Place(cid, slot)
                restow = (bool(engine.forces_restow(action))
                          if callable(getattr(engine, "forces_restow", None)) else False)
                m = b.gross_mass_kg
                return (restow, overstows, deck, height,
                        abs(my + m*y) / max(mass+m, 1.),
                        abs(mx + m*x) / max(mass+m, 1.), slot)
            accepted = 0
            for slot in sorted(slots, key=score):
                if monotonic() >= deadline:
                    return
                action = Place(cid, slot)
                reason = securing_geometry_violation(canonical, expander, action)
                if reason:
                    reasons.add(reason)
                    continue
                yield action
                accepted += 1
                if accepted >= slot_limit:
                    break

        # Complete the holds before putting any cargo over their covers. A
        # deck-only H booking must wait for a medium under-deck booking too;
        # otherwise later hold access needlessly unloads an entire deck prefix.
        # Both phases reuse the same exact cached box domains at this prefix.
        has_hold_frontier = getattr(engine, "has_open_hold_slots", None)
        phases = ((False, True) if not callable(has_hold_frontier) or has_hold_frontier()
                  else (True,))
        # A geometrically full hold cannot accept any legal placement. Skip
        # scanning the remaining manifest for hold domains at every deck lift;
        # the optional structural hook never certifies an action as legal.
        for deck in phases:
            for cid in pending:
                if monotonic() >= deadline:
                    return
                if cid not in domains:
                    slots = (engine.legal_slots_for_box(cid) if fallback is None else
                             tuple(a.slot for a in fallback if isinstance(a, Place) and a.box == cid))
                    if cid not in load:
                        slots = tuple(s for s in slots if s == original[cid])
                    domains[cid] = slots
                slots = tuple(s for s in domains[cid] if shape(s)[-1] == deck)
                if slots:
                    yield from ranked(cid, slots)

    # Retain only bounded candidate iterators, not full manifest × slot masks.
    nodes = [(root, (), Expander(canonical), None)]
    seen = set()
    while nodes:
        if monotonic() >= deadline:
            return PreStowPlan(status="timeout", message="Bounded cargo-only pre-stow time limit")
        engine, trace, expander, iterator = nodes[-1]
        if engine.is_complete():
            violations = securing_state_violations(canonical, expander)
            if violations:
                reasons.update(violations)
                nodes.pop()
                continue
            from stowage.checker.rules import e6_completion_possible
            ship = canonical.onboard.model_copy(update={"placements": [
                Placement(container_id=cid, slot=location[1])
                for cid, location in expander.where.items() if location[0] == "ship"]})
            if not e6_completion_possible(canonical, ship, (), call_index=canonical.call_index):
                reasons.add("Departure has compatible empty hold space for deck H [expert.e6.heavy_under_deck]")
                nodes.pop()
                continue
            places = tuple(Place(cid, expander.location(cid)[1]) for cid in sorted(load))
            return PreStowPlan(tuple(SlotCategory(a.slot, category_of(boxes[a.box], canonical), a.box)
                                     for a in places),
                               tuple(a for a in trace if isinstance(a, Place) and a.box in load))
        if iterator is None:
            iterator = iter(choices(engine, expander))
            nodes[-1] = engine, trace, expander, iterator
        for action in iterator:
            if monotonic() >= deadline:
                return PreStowPlan(status="timeout", message="Bounded cargo-only pre-stow time limit")
            # Queries are cached on this unchanged prefix; push remains the
            # authoritative legality guard, including for protocol-only doubles.
            child = engine.copy()
            child.push(action)
            child_expander = expander.copy()
            child_expander.step(action)
            signature = (tuple(sorted(child_expander.where.items())),
                         tuple(sorted(child_expander.covers_off.items())), tuple(child_expander.buffer))
            if signature in seen:
                continue
            seen.add(signature)
            nodes.append((child, trace + (action,), child_expander, None))
            break
        else:
            nodes.pop()
    detail = ": " + "; ".join(sorted(reasons)) if reasons else ""
    return PreStowPlan(status="dead_end", message="Bounded cargo-only pre-stow exhausted its candidates" + detail)


def preplan(instance: Instance, *, seed: int = 0, time_limit_s: float | None = None,
            beam_width: int = 4, feasibility_config=None) -> PreStowPlan:
    """Build auditable cargo-only slot categories with seeded constructor search.

    Missing shared feasibility implementation raises NotImplementedError here;
    the Planner catches it and returns an explicit dependency error.
    """
    if beam_width < 1:
        raise ValueError("beam_width must be positive")
    limit = 30.0 if time_limit_s is None else time_limit_s
    if not math.isfinite(limit) or limit < 0:
        raise ValueError("time_limit_s must be finite and nonnegative")
    deadline = monotonic() + limit
    return _preplan(instance, beam_width=beam_width, deadline=deadline, feasibility_config=feasibility_config, seed=seed)


class TwoStepPlanner:
    """Cargo-only bounded greedy then constructor fallback, followed by D73 terminal.

    ``beam_width`` bounds greedy slot alternatives; tiny retains exhaustive
    DFS. Greedy failure and constructor fallback remain separate measured fields.
    Metrics include CP solves, no-good cuts, true objective,
    pre-stow categories, accepted carrier-approval adjustments and completion
    failures. ``ok`` is complete
    cargo, pending task 4.2 ballast and the independent full checker.
    """
    name = "baseline.twostep"
    version = VERSION
    sets_ballast = False
    # Wall-clock cutoffs and best-so-far terminal candidates can vary with
    # execution speed, even with one CP worker and a fixed random seed.
    deterministic = False

    def __init__(self, *, beam_width: int = 4, feasibility_config=None):
        if beam_width < 1:
            raise ValueError("beam_width must be positive")
        self.beam_width = beam_width
        self.feasibility_config = feasibility_config

    def preplan(self, instance: Instance, *, seed: int = 0,
                time_limit_s: float | None = None) -> PreStowPlan:
        return preplan(instance, seed=seed, time_limit_s=time_limit_s, beam_width=self.beam_width, feasibility_config=self.feasibility_config)

    def plan(self, instance: Instance, *, seed: int = 0, time_limit_s: float | None = None) -> PlannerResult:
        from stowage.solver.twostep_terminal import terminal_plan
        start = monotonic()
        limit = 60.0 if time_limit_s is None else time_limit_s
        valid_budget = math.isfinite(limit) and limit >= 0
        deadline = start + limit if valid_budget else start
        effective_config = self.feasibility_config
        if effective_config is None and _use_fast_prestow(instance):
            from stowage.feasibility import FeasibilityConfig
            effective_config = FeasibilityConfig(extra={"global_screen": 0})
        provenance = current_provenance(created_by="solver.twostep", seed=seed).merge(instance.provenance)
        metrics = {"deterministic": 0, "num_workers": 1, "fixed_slot_failure": 0,
                   "preplan_status": "not_started", "terminal_status": "not_started",
                   "preplan_method": "bounded_greedy" if _use_fast_prestow(instance) else "dfs",
                   "preplan_time_s": 0., "terminal_time_s": 0.,
                   "terminal_expansions": 0, "terminal_max_legal_choices": 0,
                   "feasibility_adapter": "twostep-scoped-v4-with-public-fallback"}
        plan = None
        try:
            if not valid_budget:
                status, message = "error", "time_limit_s must be finite and nonnegative"
            else:
                metrics["time_budget_s"] = limit
                stage_start = perf_counter()
                try:
                    prestow = _preplan(instance, beam_width=self.beam_width, deadline=start + limit * 0.5, feasibility_config=effective_config, seed=seed)
                finally:
                    metrics["preplan_time_s"] = perf_counter() - stage_start
                metrics.update(preplan_status=prestow.status, preplan_message=prestow.message,
                    preplan_slots=[{"slot": s.slot, "category": s.category,
                                    "representative_box": s.representative_box} for s in prestow.slots])
                metrics.update(prestow.search_metrics or {})
                status, message = prestow.status, prestow.message
                if prestow.status == "ok":
                    stage_start = perf_counter()
                    try:
                        plan, terminal_metrics, status, message = terminal_plan(instance, prestow,
                            deadline=deadline, seed=seed, engine_factory=make_feasibility_engine, feasibility_config=effective_config)
                    finally:
                        metrics["terminal_time_s"] = perf_counter() - stage_start
                    metrics.update(terminal_metrics)
        except NotImplementedError as exc:
            status, message = "error", f"Shared feasibility checks unavailable: {exc}"
        except Exception as exc:
            status, message = "error", f"{type(exc).__name__}: {exc}"
        if plan is not None:
            plan = plan.model_copy(update={"provenance": plan.provenance.merge(provenance)})
        elapsed = monotonic() - start
        if valid_budget:
            # Public oracle calls are indivisible. Expose their actual overrun
            # rather than presenting a requested budget as a hard wall limit.
            metrics["budget_overrun_s"] = max(0.0, elapsed - limit)
        return PlannerResult(status=status, plan=plan, planner=self.name, planner_version=self.version,
            seed=seed, wall_time_s=elapsed, metrics=metrics, message=message,
            provenance=provenance)


Planner = TwoStepPlanner
