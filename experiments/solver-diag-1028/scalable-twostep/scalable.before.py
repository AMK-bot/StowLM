"""Bounded beam construction and large-neighbourhood search for ``boxslot``.

A beam keeps a few promising partial load sequences instead of committing to
one greedy choice. A bounded archive retains discarded siblings: when a
beam reaches a dead end, it replays an alternative prefix and tries again.
Decomposition here means choosing small groups of boxes
and frontier slots before asking the shared feasibility oracle about each
pair: the planner never replaces that oracle's physics or hard rules.
Large neighbourhood search (LNS) removes part of an incumbent solution and
constructs it again. We free dependency-closed suffixes starting at a hatch,
bay pair, POD or yard-block group and repair them with the public 4.6 A* solver,
then beam search. Tiny/small manifests also free the whole action sequence so
the first constructive choices cannot lock in a poor gold objective. Suffix
closure retains supports and yard-access dependencies; it does not claim an
arbitrary fixed-interior exact solve. Every stitched exact candidate replays
the original public mask and passes its full checker before acceptance.
Sources: Shaw (1998), "Using Constraint Programming and Local Search Methods
to Solve Vehicle Routing Problems", https://doi.org/10.1007/3-540-49481-2_30;
Pisinger and Ropke (2018), "Large Neighborhood Search",
https://orbit.dtu.dk/en/publications/large-neighborhood-search/.

The oracle adapter uses only the public v2 feasibility API: exact local checks
and necessary completion screens can admit dead ends, which the beam handles
by backtracking and time-sliced seeded restarts. A search portfolio tries
accessible cargo, heavy supports, POD order and fewest public legal slot pairs
(MRV); the default profile also uses MRV on the final 32 boxes. Long cargo wins
otherwise equal class/access ties to protect aligned pairs. Public
dead-end rule ids steer which preference to try next. The beam is wider over
the first thirty actions, where losing a category/slot choice is hardest to
repair later. Restarting changes search ordering, never the constraint model.
The finalized checker and production mask replay remain the
gold admission gates. D25's VGM classes, rather than exact mass ordering within
a class, guide support lookahead (the shared catalogue supplies boundaries).
E6 departure legality belongs to the oracle/checker. Search first assigns H cargo
to its public legal hold choices, before occupying those supports with lighter
cargo or committing H to deck. Restricted bookings (especially away-from-heat)
use public legal-pair scarcity early. The heavy-support restart alone puts E6
hold access ahead of all booking preferences (including heat), while the other
profiles preserve restricted-first ordering. This portfolio avoids repeating the
same restricted-cargo commitments across every restart. Hatch grouping and forced-restow flags
protect hold access. Sampled ``completion_check`` booleans rank failed E6,
restricted-class and DG capacity sub-checks ahead of move cost; passing necessary
checks is never a completion certificate. Sampling is capped at 256 calls per
solve, including restarts and LNS; exhausted diagnostics never replace the oracle. This is the fail-first/MRV principle
(Russell and Norvig, Artificial Intelligence: A Modern Approach, 4th ed., chapter6).

Category, heavy-low, far-POD-low, hatch-cover and cargo-moment preferences order search;
they never declare a placement legal. The moment preference spreads cargo about
actual decision-cell midpoint coordinates instead of always choosing aft/one-side
slots. It is a soft heuristic, not a substitute for C2 or nonlinear hydrostatics.
The beam and neighbourhood limits are
search truncations, not physical approximations. The exact move engine scores
complete candidates using D11's weighted lifts; the 3.6 lower bound permits
an early stop and gap reporting. Each exact window uses one deadline-owned
4.6 worker (explicitly num_workers=1); no CP-SAT workers are started. The
parent waits, so search consumes one active CPU core. Wall-clock cutoffs make the returned incumbent
potentially timing dependent. A result with status ``ok`` still requires
``planner.finalize`` and the full checker before gold-plan admission.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
import logging
import random
import json
import time
from typing import Any

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Action, Discharge, Place
from stowage.bounds import lower_bound
from stowage.conventions import cells_of_slot, format_slot, parse_slot
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.frozen import current_provenance
from stowage.model import Instance, MovePlan, PlannerResult
from stowage.moves import apply_plan
from stowage.rules.catalogue import load_catalogue
from stowage.solver.scalable_oracle import RequiredDataUnavailable, SearchDeadline, build_engine

SCALABLE_VERSION = "2.6.3"
_LOG = logging.getLogger(__name__)
_COMPLETION_LOOKAHEAD_LIMIT = 256


@dataclass(frozen=True)
class ScalableConfig:
    """One-core search limits; deadlines include bounds and oracle setup.

    ``candidate_boxes``/``candidate_slots`` bound the preferred shortlist. If
    it has no legal pair, probing widens to all remaining boxes and slots.
    ``branch_factor`` bounds oracle-approved successors per beam node.
    ``backtrack_limit`` bounds archived alternative prefixes. Necessary
    completion checks can admit dead ends; archived siblings permit recovery.
    ``early_beam_width`` expands the first ``early_beam_depth`` decisions,
    bounded by the branch factor so width-one configurations keep their meaning.
    ``max_restarts`` time-slices diversified category priorities; setting
    ``backtrack_limit=0`` disables recovery, including restarts. The bounded
    ``dominance_cache_size`` remembers cheapest histories for exact physical
    states (zero disables it). ``mrv_tail_boxes`` activates public legal-pair
    scarcity ordering on the default profile's last boxes; profile3 uses it
    throughout. Zero disables the default tail trigger. Public root
    dead ends stop immediately. Individual oracle calls cannot be interrupted
    by the wall-clock cutoff.

    ``exact_window_time_limit_s`` caps each ordinary dependency-closed suffix
    solve. ``exact_whole_plan_time_limit_s`` gives small whole-plan searches a
    longer contiguous A* frontier, avoiding repeated short searches that lose
    progress. Both caps are clipped to the caller's remaining total budget;
    zero disables that window kind. All available exact windows are tried
    before beam repair. ``max_neighborhoods`` counts both types of repair.
    """
    default_time_limit_s: float = 60.0
    beam_width: int = 2
    branch_factor: int = 8
    candidate_boxes: int = 16
    candidate_slots: int = 32
    max_neighborhoods: int = 12
    destroy_fraction: float = 0.25
    feasibility_config: FeasibilityConfig | None = None
    backtrack_limit: int = 128
    early_beam_width: int = 4
    early_beam_depth: int = 30
    max_restarts: int = 3
    dominance_cache_size: int = 4096
    mrv_tail_boxes: int = 32
    initial_strategy: int = 0
    exact_window_time_limit_s: float = 10.0
    exact_whole_plan_time_limit_s: float = 90.0
    exact_window_max_nodes: int = 10000
    exact_window_max_boxes: int = 16
    exact_whole_plan_boxes: int = 16

    def __post_init__(self) -> None:
        if not math.isfinite(self.default_time_limit_s) or self.default_time_limit_s < 0:
            raise ValueError("default_time_limit_s must be finite and nonnegative")
        if min(self.beam_width, self.branch_factor, self.candidate_boxes, self.candidate_slots) < 1:
            raise ValueError("beam and candidate limits must be positive")
        if self.early_beam_width < 1 or self.early_beam_depth < 0 or self.max_restarts < 0:
            raise ValueError("invalid early beam or restart limits")
        if self.initial_strategy not in range(4):
            raise ValueError("initial_strategy must be 0, 1, 2 or 3")
        if self.mrv_tail_boxes < 0:
            raise ValueError("mrv_tail_boxes must be nonnegative")
        if self.dominance_cache_size < 0:
            raise ValueError("dominance_cache_size must be nonnegative")
        if self.backtrack_limit < 0:
            raise ValueError("backtrack_limit must be nonnegative")
        if self.max_neighborhoods < 0 or not 0 < self.destroy_fraction <= 1:
            raise ValueError("invalid neighbourhood limits")
        if not math.isfinite(self.exact_window_time_limit_s) or self.exact_window_time_limit_s < 0:
            raise ValueError("exact_window_time_limit_s must be finite and nonnegative")
        if not math.isfinite(self.exact_whole_plan_time_limit_s) or self.exact_whole_plan_time_limit_s < 0:
            raise ValueError("exact_whole_plan_time_limit_s must be finite and nonnegative")
        if min(self.exact_window_max_nodes, self.exact_window_max_boxes) < 1 or self.exact_whole_plan_boxes < 0:
            raise ValueError("invalid exact window limits")


@dataclass
class _Node:
    engine: Any
    ex: Expander
    score: float = 0.0
    preference: tuple = ()
    classes: dict[str, int] | None = None
    strategy: int = 0
    moments: tuple | None = None
    legal_counts: dict[str, int] | None = None
    scarcity: tuple = (0, math.inf)
    hold_counts: dict[str, int] | None = None
    completion_preference: tuple = (0, 0, 0, 0)
    guidance: tuple = (0, 0, 0)


class ScalableSolver:
    """Planner protocol implementation; use ``complete`` and ``improve`` for repair."""
    name = "solver.scalable"
    version = SCALABLE_VERSION
    sets_ballast = False
    deterministic = False  # seeded ordering; wall-clock truncation can change the incumbent

    def __init__(self, config: ScalableConfig | None = None) -> None:
        self.config = config or ScalableConfig()

    def plan(self, instance: Instance, *, seed: int = 0,
             time_limit_s: float | None = None) -> PlannerResult:
        return complete(instance, (), seed=seed, time_limit_s=time_limit_s, config=self.config)


def complete(instance: Instance, partial_actions: Sequence[Action | dict[str, Any]], *,
             seed: int = 0, time_limit_s: float | None = None,
             config: ScalableConfig | None = None) -> PlannerResult:
    """Keep legal supplied actions in order, discard rejected ones, then complete.

    Preservation is greedy, not globally maximal: finding the largest
    feasible subset of an arbitrary corrupt sequence is itself a search task.
    If a locally legal supplied prefix reaches a dead end, bounded suffix
    relaxation releases its last actions and retries completion.
    Every retained and newly selected action passes the shared oracle checks.
    Replay against the production mask and finalized full checker is required
    before using the result as a gold plan.
    """
    return _solve(instance, partial_actions, None, seed, time_limit_s, config or ScalableConfig())


def improve(instance: Instance, plan: MovePlan, *, seed: int = 0,
            time_limit_s: float | None = None,
            config: ScalableConfig | None = None) -> PlannerResult:
    """Replay an incumbent, then try exact-window and beam suffix LNS.

    Ballast is recomputed by the finisher if the action sequence changes.
    Invalid incumbent lifts are never accepted merely because they were supplied.
    A native-mask and full-checker verified incumbent cannot be replaced by a
    cheaper beam candidate unless that candidate passes the original checker.
    Beam validation reuses the incumbent ballast when needed; a solution needing
    different ballast may instead be found by the exact-window repair finisher.
    """
    return _solve(instance, (), plan, seed, time_limit_s, config or ScalableConfig())


def _slots(instance: Instance) -> dict[int, list[str]]:
    cells = {cell.id: cell for cell in instance.vessel.cells}
    twenty = [cid for cid, cell in cells.items() if cell.accepts_20]
    long: dict[int, list[str]] = {40: [], 45: []}
    for cid in sorted(cells):
        a = parse_slot(cid)
        for even in (a.bay - 1, a.bay + 1):
            if even <= 0:
                continue
            slot = format_slot(even, a.row, a.tier)
            parts = cells_of_slot(slot)
            for length in (40, 45):
                if all(p in cells and getattr(cells[p], f"accepts_{length}") for p in parts):
                    long[length].append(slot)
    return {20: sorted(twenty), **{n: sorted(set(s)) for n, s in long.items()}}


def _pod_map(instance: Instance) -> dict[str, int]:
    calls = {}
    for i, call in enumerate(instance.rotation.calls):
        if i > instance.call_index:
            calls.setdefault(call.locode, i)
    return {b.id: calls.get(b.pod, instance.call_index) for b in instance.containers}


def _weight_ranks(instance: Instance) -> dict[str, int]:
    """D25 VGM ranks for preferences, with the same boundary override names.

    Cached cargo ``weight_class`` labels are deliberately ignored. This is
    heuristic metadata only: all actual E1/E2/E6 decisions stay in the oracle.
    """
    shared = load_catalogue().weight_classes.parameters
    overrides = instance.rule_overrides.get("expert.e2.weight_order", {})
    def boundary(name):
        value = overrides.get(name, shared[name].value)
        return value.get("value") if isinstance(value, dict) else value
    medium, heavy = boundary("medium_min_kg"), boundary("heavy_min_kg")
    return {b.id: int(b.gross_mass_kg >= medium) + int(b.gross_mass_kg >= heavy)
            for b in instance.containers}


def _needs_hold_access(ex, remaining, classes):
    """Cheap hold-access demand, not a compatibility or E6 legality test.

    Raw deck capacity misses class-H and explicit under-deck cargo. Keeping
    hatch decks clear while these boxes await loading avoids repeatedly
    removing newly loaded deck cargo. The oracle still decides which particular
    hold cells can accept each box, including DG and mixed-length support.
    """
    deck_room = sum(c not in ex.occ for stack in ex.stacks.values()
                    if stack.on_deck for c in stack.cell_ids)
    units = sum(1 if ex.boxes[c].length_ft == 20 else 2 for c in remaining)
    return units > deck_room or any(classes[c] == 2 or
        "under_deck" in ex.boxes[c].special_stowage for c in remaining)


def _cover_risk(ex, cid, parts, pods, needs_hold):
    """Estimate deck boxes that a present/future hold access must lift."""
    risk = 0
    for cover_id in {ex.stacks[ex.cells[p].stack_id].hatch_cover_id for p in parts} - {None}:
        cover = ex.covers[cover_id]
        if parse_slot(parts[0]).on_deck:
            holds = [cell for sid in cover.hold_stack_ids for cell in ex.stacks[sid].cell_ids]
            # Closing access now can force a two-lift deck-box restow later.
            # Charge two, above the one opening preference for an empty hold,
            # even when nominal deck capacity could fit all remaining cargo.
            risk += 2 * int(needs_hold and any(c not in ex.occ for c in holds))
            hold_boxes = {ex.occ[c] for c in holds if c in ex.occ}
            risk += 2 * int(any(pods[b] < pods[cid] for b in hold_boxes))
        else:
            risk += int(cover_id not in ex.covers_off)
            risk += sum(c in ex.occ for sid in cover.deck_stack_ids
                        for c in ex.stacks[sid].cell_ids)
    return risk


def _balance_context(node):
    """Cargo moments and the midpoint of the actual decision-cell region.

    This soft spatial-distribution heuristic is deliberately not a hydrostatic
    condition or a target derived from hull/ballast equilibrium. It gives C2
    room to balance trim/list by avoiding an arbitrary lowest-bay/one-side bias.
    Fixed background loads, free surfaces and strength remain oracle concerns.
    """
    if node.moments is None:
        ex = node.ex
        mass = mx = my = 0.0
        for cid, location in ex.where.items():
            if location[0] != "ship":
                continue
            parts = cells_of_slot(location[1])
            weight = ex.boxes[cid].gross_mass_kg
            mass += weight
            mx += weight * sum(ex.cells[p].lcg_m for p in parts) / len(parts)
            my += weight * sum(ex.cells[p].tcg_m for p in parts) / len(parts)
        xs = [cell.lcg_m for cell in ex.cells.values()] or [0.0]
        ys = [cell.tcg_m for cell in ex.cells.values()] or [0.0]
        node.moments = (mass, mx, my, (min(xs) + max(xs)) / 2,
                        (min(ys) + max(ys)) / 2,
                        max(1.0, max(xs) - min(xs)), max(1.0, max(ys) - min(ys)))
    return node.moments


def _moment_preference(node, cid, parts):
    """Normalized post-placement centroid offsets; smaller is preferred.

    A long box contributes its mass once at its occupied-cell average, matching
    the model's mass convention. This preference never labels a state feasible.
    """
    mass, mx, my, target_x, target_y, span_x, span_y = _balance_context(node)
    ex = node.ex
    weight = ex.boxes[cid].gross_mass_kg
    x = sum(ex.cells[p].lcg_m for p in parts) / len(parts)
    y = sum(ex.cells[p].tcg_m for p in parts) / len(parts)
    # Ship-to-ship overrides should not double count an already aboard box.
    previous = ex.where.get(cid)
    if previous is not None and previous[0] == "ship":
        old_parts = cells_of_slot(previous[1])
        mass -= weight
        mx -= weight * sum(ex.cells[p].lcg_m for p in old_parts) / len(old_parts)
        my -= weight * sum(ex.cells[p].tcg_m for p in old_parts) / len(old_parts)
    total = max(mass + weight, 1.0)
    return (abs((mx + weight * x) / total - target_x) / span_x +
            abs((my + weight * y) / total - target_y) / span_y)

def _legal_pair_counts(node, boxes):
    """Count public legal pairs per remaining box; no second constraint model.

    Minimum remaining values (MRV) means scheduling cargo with few available
    choices before those choices disappear (Russell and Norvig, AIMA, chapter6).
    Zero-choice boxes come last: loading another support may make them possible.
    The engine caches this public legal set, also used for membership probes.
    """
    if node.legal_counts is None:
        indexed_counts = getattr(node.engine, "legal_pair_counts", None)
        if indexed_counts is not None:
            node.legal_counts = indexed_counts(boxes)
        else:
            counts = dict.fromkeys(boxes, 0)
            for action in node.engine.legal_actions():
                if isinstance(action, Place) and action.box in counts:
                    counts[action.box] += 1
            node.legal_counts = counts
    return node.legal_counts

def _restricted_priority(box):
    """Booking scarcity hint; actual compatibility remains engine-owned."""
    return (int("away_from_heat" in box.special_stowage),
            int(bool(box.special_stowage)) + int(box.reefer) +
            int(box.dg is not None) + int(box.oog is not None))


def _hold_counts(node, boxes):
    """Count public legal hold choices, without reimplementing compatibility."""
    if node.hold_counts is None:
        method = getattr(node.engine, "legal_hold_counts", None)
        node.hold_counts = method(boxes) if method is not None else {}
    return node.hold_counts


def _e6_preference(node, cid, on_deck, remaining, classes):
    """Protect compatible H hold supports; this is only a search preference.

    A public zero current hold count is not proof of permanent incompatibility:
    later supports can create choices. Such boxes are therefore not forbidden.
    """
    holds = _hold_counts(node, remaining)
    if classes[cid] == 2:
        return (2 if on_deck else -1) if holds.get(cid, 0) > 0 else 0
    return int(not on_deck and any(classes[b] == 2 and holds.get(b, 0) > 0
                                   for b in remaining))


def _placement_guidance(node, cid, on_deck, remaining, classes):
    """Diversify E6-first supports versus restricted-first booking priorities."""
    heat, restricted = _restricted_priority(node.ex.boxes[cid])
    e6 = _e6_preference(node, cid, on_deck, remaining, classes)
    booking = (-heat, -int(bool(restricted)))
    return (e6, *booking) if node.strategy == 1 else (*booking, e6)


def _completion_lookahead(node, remaining_count, metrics):
    """Sample public necessary-condition booleans, never invented slack values.

    All siblings at depth one, every sixteenth decision, and the last eight
    boxes are sampled equally until the solve-wide 256-call budget is exhausted.
    Skipped eligible samples are recorded; the budget also covers restarts and
    LNS because they share metrics. This bounds extra C2 work; an unsampled node has
    neutral preference, not a fabricated success. Normal legal candidates often
    pass every sub-check already, in which case this adds no discriminatory
    information. Failed checks are counted for diagnosis and rank behind passing
    siblings; only the engine/checker decides legality.
    """
    if node.engine.depth != 1 and node.engine.depth % 16 and remaining_count > 8:
        return
    method = getattr(node.engine, "completion_check", None)
    if method is None:
        return
    if metrics.get("completion_lookahead_calls", 0) >= _COMPLETION_LOOKAHEAD_LIMIT:
        metrics["completion_lookahead_budget_skips"] = metrics.get("completion_lookahead_budget_skips", 0) + 1
        return
    checks = method()
    metrics["completion_lookahead_calls"] = metrics.get("completion_lookahead_calls", 0) + 1
    failures = metrics.setdefault("completion_subcheck_failures", {})
    for key, passed in checks.items():
        if not passed:
            failures[key] = failures.get(key, 0) + 1
    node.completion_preference = (int(not checks.get("c1f", True)),
                                  int(not checks.get("c1h", True)),
                                  int(not checks.get("c1g", True)),
                                  sum(not value for value in checks.values()))


def _candidates(instance: Instance, node: _Node, slots: dict[int, list[str]],
                cfg: ScalableConfig, deadline: float, rng: random.Random, pods: dict[str, int],
                categories: dict[str, tuple] | None = None):
    ex = node.ex
    classes = node.classes if node.classes is not None else _weight_ranks(instance)
    # Clear discharges before loading over their access paths.
    for cid in instance.discharge_list:
        if ex.where.get(cid, (None,))[0] == "ship":
            yield Discharge(cid)
    boxes = [cid for cid in instance.load_list if ex.where.get(cid, (None,))[0] != "ship"]
    boxes.extend(cid for cid in ex.buffer if cid not in boxes)
    needs_hold = _needs_hold_access(ex, boxes, classes)
    restricted = any(_restricted_priority(ex.boxes[cid])[1] for cid in boxes)
    mrv = node.strategy == 3 or (node.strategy == 0 and 0 < len(boxes) <= cfg.mrv_tail_boxes)
    legal_counts = _legal_pair_counts(node, boxes) if mrv or restricted else None
    # Resolve before list.sort: CPython temporarily empties the sorted list
    # while evaluating keys, so lazily asking with boxes there loses demand.
    hold_counts = _hold_counts(node, boxes) if node.strategy == 1 else {}
    def accessibility(cid):
        loc, key = ex.where.get(cid, ("", ""))
        return len(ex.yard[key]) - ex.yard[key].index(cid) - 1 if loc == "yard" else 0
    def boxkey(cid):
        loc, key = ex.where.get(cid, ("", ""))
        blockers = len(ex.yard[key]) - ex.yard[key].index(cid) - 1 if loc == "yard" else 0
        box = ex.boxes[cid]
        scarce = int(box.reefer) + int(box.dg is not None) + int(box.oog is not None) + len(box.special_stowage)
        # Retry priorities must change categories reaching the oracle shortlist.
        priorities = ((blockers, -classes[cid], -pods[cid], -scarce),
                      (-classes[cid], blockers, -pods[cid], -scarce),
                      (-pods[cid], blockers, -classes[cid], -scarce),
                      (-scarce, -classes[cid], blockers, -pods[cid]))
        rarity = ((legal_counts[cid] == 0, legal_counts[cid] or math.inf)
                  if legal_counts is not None else ())
        heat, restricted_class = _restricted_priority(box)
        # Only the heavy-support restart protects current compatible H hold
        # choices before booking priorities; other profiles keep heat first.
        restriction = (-heat, -int(bool(restricted_class))) if restricted else ()
        e6_first = (-int(classes[cid] == 2 and hold_counts.get(cid, 0) > 0),) if node.strategy == 1 else ()
        return (*e6_first, *restriction, *rarity, *priorities[node.strategy % 4],
                -box.length_ft, box.category or "", cid)
    boxes.sort(key=boxkey)
    # Category labels are hints, not trusted equivalence proofs. Compare all
    # booking/physical facts as well; no noninterchangeable box is hidden by
    # a stale generator label. Prefer the most accessible identical box.
    representatives = {}
    for cid in sorted(boxes, key=lambda cid: (accessibility(cid), boxkey(cid))):
        box = ex.boxes[cid]
        category_key = categories[cid] if categories is not None else (
            box.category, box.model_dump_json(exclude={"id", "category"}))
        representatives.setdefault(category_key, cid)
    representative_boxes = list(representatives.values())
    jitter = {cid: rng.random() for cid in boxes}
    representative_boxes.sort(key=lambda cid: (*boxkey(cid)[:-1], jitter[cid], cid))
    representative_set = set(representative_boxes)
    boxes = representative_boxes + [cid for cid in boxes if cid not in representative_set]
    # Geometry and existing stack occupants do not depend on the next box.
    # Inspect each length's frontier once, rather than once per category probe.
    frontiers = {}
    cover_scores = {}
    def frontier(length):
        if length not in frontiers:
            choices = []
            for slot in slots[length]:
                parts = cells_of_slot(slot)
                if any(p in ex.occ for p in parts):
                    continue
                if any(ex.cell_pos[p][1] and
                       ex.stacks[ex.cell_pos[p][0]].cell_ids[ex.cell_pos[p][1]-1] not in ex.occ
                       for p in parts):
                    continue
                address = parse_slot(slot)
                stack_boxes = {ex.occ.get(c) for p in parts
                               for c in ex.stacks[ex.cells[p].stack_id].cell_ids} - {None}
                cover_key = (address.on_deck, tuple(sorted(
                    {ex.stacks[ex.cells[p].stack_id].hatch_cover_id for p in parts} - {None})))
                lateral = sum(ex.cells[p].tcg_m for p in parts) / len(parts)
                choices.append((slot, parts, address, stack_boxes, cover_key, lateral))
            frontiers[length] = choices
        return frontiers[length]
    seen: set[Action] = set()
    for wide in (False, True):
        batches = []
        for cid in boxes if wide else boxes[:cfg.candidate_boxes]:
            if time.monotonic() >= deadline:
                return
            choices = []
            remaining_heavier = sum(classes[b] > classes[cid]
                                    for b in boxes[:cfg.candidate_boxes])
            weight_risk = remaining_heavier / max(1, min(len(boxes), cfg.candidate_boxes))
            for slot, parts, a, stack_boxes, cover_key, lateral in frontier(ex.boxes[cid].length_ft):
                # All remaining compatibility and hard rules belong to the
                # oracle. Cover risk is identical across a cover's columns.
                access_key = (pods[cid], cover_key)
                if access_key not in cover_scores:
                    cover_scores[access_key] = _cover_risk(ex, cid, parts, pods, needs_hold)
                access = cover_scores[access_key]
                # Farther POD cargo belongs below earlier departures. Weight
                # ordering leaves heavy supports for heavy cargo still ashore.
                overstow = sum(pods.get(b, math.inf) < pods[cid] for b in stack_boxes if b)
                # Filling hold supports with H cargo reduces E6 completion
                # pressure; same-class kilogram differences impose no order.
                heavy_deck_risk = int(classes[cid] == 2 and a.on_deck)
                heavy = (1 + classes[cid]) / 3.0
                e6 = _e6_preference(node, cid, a.on_deck, boxes, classes)
                choices.append(((e6, overstow, access + 2 * heavy_deck_risk,
                                 a.tier * (heavy + weight_risk),
                                 _moment_preference(node, cid, parts), a.bay, a.row), slot))
            choices.sort()
            batch = [Place(cid, slot) for _, slot in choices if Place(cid, slot) not in seen]
            if wide:
                for action in batch:
                    seen.add(action)
                    yield action
            else:
                batches.append(batch[:cfg.candidate_slots])
        # Interleave boxes so a shortlist includes different categories and
        # weights instead of exhausting the first box's many frontier slots.
        if not wide:
            # Include alternative slots for the first categories as well as
            # alternative boxes before filling the remaining wider shortlist.
            first = min(len(batches), max(1, cfg.branch_factor // 2))
            preferred_indices = [(i, j) for j in range(min(2, cfg.candidate_slots))
                                 for i in range(first)]
            preferred_indices += [(i, j) for j in range(cfg.candidate_slots)
                                  for i in range(len(batches))
                                  if (i, j) not in preferred_indices]
            for i, index in preferred_indices:
                batch = batches[i]
                if index < len(batch):
                    seen.add(batch[index])
                    yield batch[index]


def _rank(instance: Instance, node: _Node, pods: dict[str, int]) -> float:
    w = instance.objective
    counts = {"relocate": w.relocate, "restow_off": w.restow, "restow_on": w.restow,
              "restow_shift": w.restow, "hatch_cover_off": w.hatch_cover,
              "hatch_cover_on": w.hatch_cover}
    score = sum(counts.get(m.kind, 0.0) for m in node.ex.moves)
    # Search estimate only: charge each later-POD box over earlier-POD cargo
    # once, matching the future-restow unit (two lifts by default).
    blockers = set()
    for stack in instance.vessel.stacks:
        earlier = math.inf
        for cid in stack.cell_ids:
            box = node.ex.occ.get(cid)
            if box is None:
                continue
            priority = pods[box]
            if priority > earlier:
                blockers.add(box)
            earlier = min(earlier, priority)
    score += len(blockers) * w.future_restow * w.future_restow_lifts_per_box
    return score


def _action_rank(instance, node, action, pods):
    """Cheap explainable lookahead; exact D11 scoring ranks expanded children."""
    ex = node.ex
    if isinstance(action, Discharge):
        return (-1, 0, 0, 0, 0, "")
    if not isinstance(action, Place):
        return (2, 0, 0, 0, 0, str(action))
    classes = node.classes if node.classes is not None else _weight_ranks(instance)
    loc, key = ex.where.get(action.box, ("", ""))
    blockers = len(ex.yard[key]) - ex.yard[key].index(action.box) - 1 if loc == "yard" else 0
    parts = cells_of_slot(action.slot)
    address = parse_slot(action.slot)
    stack_ids = {ex.cells[p].stack_id for p in parts}
    occupants = {ex.occ[c] for sid in stack_ids for c in ex.stacks[sid].cell_ids if c in ex.occ}
    overstow = sum(pods[c] < pods[action.box] for c in occupants)
    remaining = [cid for cid in instance.load_list if ex.where.get(cid, (None,))[0] != "ship"]
    needs_hold = _needs_hold_access(ex, remaining, classes)
    cover_risk = _cover_risk(ex, action.box, parts, pods, needs_hold)
    # Preferences are unitless, deliberately separate from the reported objective.
    class_height = ((1 + classes[action.box]) / 3.0 * address.tier +
                    0.5 * _moment_preference(node, action.box, parts))
    restow_probe = getattr(node.engine, "forces_restow", None)
    forced_restow = bool(restow_probe(action)) if restow_probe else False
    heavy_deck_risk = int(classes[action.box] == 2 and address.on_deck)
    guidance = _placement_guidance(node, action.box, address.on_deck, remaining, classes)
    # Profile1 protects H hold access first; other profiles protect bookings.
    # The shared oracle has already approved every proposed action.
    return (0, *guidance,
            blockers + 2 * overstow + cover_risk + 2 * forced_restow + 2 * heavy_deck_risk,
            -classes[action.box], -pods[action.box], class_height, action.slot)


def _state_key(ex):
    """Exact physical state identity, independent of the move history.

    Cell occupancy, ordered yard contents/lengths, ordered quay buffer and
    hatch-cover destinations determine the expander's remaining mutable maps
    (locations, yard footprint and pending loads). Thus already discharged boxes
    are distinguished by their yard/buffer locations too. Canonical JSON bytes
    depend on values rather than Python object-sharing identities. This is exact
    equality, not a hash test.
    """
    state = (tuple(sorted(ex.occ.items())),
             tuple((key, ex.ylen[key], tuple(boxes)) for key, boxes in sorted(ex.yard.items())),
             tuple(ex.buffer), tuple(sorted(ex.covers_off.items())))
    return json.dumps(state, separators=(",", ":")).encode("utf-8")


def _admit_state(ex, score, seen, limit, metrics):
    """Remember the cheapest prefix to a layout; evict oldest keys deterministically.

    Equal physical states have the same future options and future-restow
    estimate, so retaining a more expensive lifting history only creates cycles.
    This is graph-search dominance (Russell and Norvig, Artificial Intelligence:
    A Modern Approach, 4th ed., chapter 3), bounded here to protect feeder RAM.
    """
    if not limit:
        return True
    key = _state_key(ex)
    previous = seen.get(key)
    if previous is not None and score >= previous:
        metrics["dominance_prunes"] = metrics.get("dominance_prunes", 0) + 1
        return False
    if key not in seen and len(seen) >= limit:
        del seen[next(iter(seen))]
        metrics["dominance_evictions"] = metrics.get("dominance_evictions", 0) + 1
    seen[key] = score
    metrics["dominance_cache_peak"] = max(metrics.get("dominance_cache_peak", 0), len(seen))
    return True

def _construct_attempt(instance, start, slots, cfg, deadline, rng, metrics, pods, progress=None):
    """Beam search with a bounded archive of discarded siblings.

    Ordinary beams lose every branch outside their width. Here a dead end
    resumes the deepest archived sibling, replaying its prefix from ``start``.
    The archive stores action tuples, not full vessel snapshots. A bounded exact
    transposition table rejects equal/higher-cost revisits of the same physical
    layout, preventing forced-restow/reload cycles from consuming the budget.
    Both archive and table are bounded even for thousand-box feeders. This is bounded backtracking, not
    a claim of exhaustive search.
    """
    if start.engine.is_complete():
        return [start], start
    if start.classes is None:
        start.classes = _weight_ranks(instance)
    beam, deepest, archive = [start], start, []
    seen = {}
    start.score = _rank(instance, start, pods)
    _admit_state(start.ex, start.score, seen, cfg.dominance_cache_size, metrics)
    categories = {b.id: (b.category, b.model_dump_json(exclude={"id", "category"}))
                  for b in instance.containers}
    base_depth = start.engine.depth
    depth_limit = base_depth + 2 * (len(instance.load_list) + len(instance.discharge_list) +
                                    len(instance.onboard.placements)) + 32
    serial = 0
    reported_depth = base_depth
    while (beam or archive) and time.monotonic() < deadline:
        if deepest.engine.depth >= reported_depth + 25:
            reported_depth = deepest.engine.depth
            _LOG.info("search depth=%d probes=%d backtracks=%d", reported_depth,
                      metrics["candidate_probes"], metrics["backtracks"])
        width = (max(cfg.beam_width, min(cfg.early_beam_width, cfg.branch_factor - 1))
                 if deepest.engine.depth < cfg.early_beam_depth else cfg.beam_width)
        metrics["max_beam_width"] = max(metrics.get("max_beam_width", 0), width)
        if not beam:
            # Restore latest/deepest first; replay uses the same frozen API.
            _, _, _, path = archive.pop()
            eng, ex = start.engine.copy(), start.ex.copy()
            for action in path[base_depth:]:
                if time.monotonic() >= deadline:
                    return [], deepest
                eng.push(action)
                ex.step(action)
            beam = [_Node(eng, ex, classes=start.classes, strategy=start.strategy)]
            metrics["backtracks"] += 1
        children, completed = [], []
        for node in beam:
            if node.engine.is_complete():
                completed.append(node)
                continue
            if node.engine.depth >= depth_limit:
                metrics["search_dead_ends"] += 1
                continue
            preferred = _candidates(instance, node, slots, cfg, deadline, rng, pods, categories)
            # is_legal is public membership in the frozen legal_actions set;
            # shortlist order changes search only, never legality.
            if hasattr(node.engine, "legal_candidates"):
                def counted():
                    for action in preferred:
                        metrics["candidate_probes"] += 1
                        yield action
                legal = node.engine.legal_candidates(counted())
            else:
                def screened():
                    for action in preferred:
                        metrics["candidate_probes"] += 1
                        if node.engine.is_legal(action):
                            yield action
                legal = screened()
            proposals = []
            probe_limit = max(cfg.branch_factor, width + 1)
            for action in legal:
                if time.monotonic() >= deadline:
                    break
                metrics["legal_candidates"] += 1
                proposals.append(action)
                if len(proposals) >= probe_limit:
                    break
            # Explicit overrides may be the only route out. Ask the complete
            # action set only after the normal Place/Discharge frontier fails.
            if not proposals and time.monotonic() < deadline:
                fallback = (node.engine.iter_legal_actions(deadline)
                            if hasattr(node.engine, "iter_legal_actions")
                            else node.engine.legal_actions())
                for action in fallback:
                    if time.monotonic() >= deadline:
                        break
                    proposals.append(action)
                    if len(proposals) >= probe_limit:
                        break
            # Randomise only equivalent heuristic ties (usually same-tier
            # slots). Seeded tie ordering gives LNS a different repair route
            # while forced-lift, POD and heavy-low priorities remain intact.
            priorities = {}
            for action in proposals:
                priority = _action_rank(instance, node, action, pods)
                priorities[action] = (*priority[:-1], rng.random(), priority[-1])
            proposals.sort(key=priorities.__getitem__)
            # At least one extra sibling is necessary to backtrack at width 1.
            for action in proposals[:probe_limit]:
                if time.monotonic() >= deadline:
                    break
                eng, ex = node.engine.copy(), node.ex.copy()
                eng.push(action)
                ex.step(action)
                rarity = ((node.legal_counts.get(action.box, 0) == 0,
                           node.legal_counts.get(action.box, 0) or math.inf)
                          if node.legal_counts is not None and isinstance(action, Place) else (0, math.inf))
                child = _Node(eng, ex, preference=priorities[action], classes=node.classes,
                              strategy=node.strategy, scarcity=rarity)
                metrics["deepest_depth"] = max(metrics.get("deepest_depth", 0), eng.depth)
                child.score = _rank(instance, child, pods)
                if isinstance(action, Place):
                    pending = [cid for cid in instance.load_list
                               if node.ex.where.get(cid, (None,))[0] != "ship"]
                    child.guidance = _placement_guidance(
                        node, action.box, parse_slot(action.slot).on_deck, pending, node.classes)
                remaining_count = sum(ex.where.get(cid, (None,))[0] != "ship"
                                      for cid in instance.load_list)
                _completion_lookahead(child, remaining_count, metrics)
                if progress is not None:
                    progress(child)
                if not _admit_state(ex, child.score, seen, cfg.dominance_cache_size, metrics):
                    continue
                if eng.is_complete():
                    completed.append(child)
                else:
                    children.append(child)
                if eng.depth > deepest.engine.depth:
                    deepest = child
            if not proposals:
                metrics["search_dead_ends"] += 1
                reason = node.engine.dead_end()
                if reason is not None:
                    histogram = metrics.setdefault("dead_end_rule_ids", {})
                    for rule_id in reason.rule_ids:
                        histogram[rule_id] = histogram.get(rule_id, 0) + 1
        if completed:
            return completed, deepest
        children.sort(key=lambda n: (n.completion_preference, n.guidance, n.scarcity, n.score, n.preference, tuple(n.engine.space.sort_key(a) for a in n.engine.actions())))
        beam = children[:width]
        if cfg.backtrack_limit:
            for child in children[width:]:
                serial += 1
                archive.append((child.engine.depth, -child.score, serial, tuple(child.engine.actions())))
            archive.sort(key=lambda item: item[:3])
            archive = archive[-cfg.backtrack_limit:]
    return [], deepest


def _construct(instance, start, slots, cfg, deadline, rng, metrics, pods, progress=None):
    """Give diversified attempts a fair share of the remaining wall time.

    Each restart copies the supplied prefix through the public oracle API and
    changes category priorities: accessible cargo, heavy supports, POD order or
    scarce reefer/DG/special cells. These preferences never alter hard rules.
    """
    deepest, used, strategy = start, set(), start.strategy
    if progress is not None:
        start.score = _rank(instance, start, pods)
        progress(start)
    if not start.engine.is_complete():
        reason = start.engine.dead_end()
        if reason is not None:
            metrics["search_dead_ends"] += 1
            histogram = metrics.setdefault("dead_end_rule_ids", {})
            for rule_id in reason.rule_ids:
                histogram[rule_id] = histogram.get(rule_id, 0) + 1
            metrics.setdefault("construction_attempts", []).append({
                "strategy": strategy, "start_depth": start.engine.depth,
                "deepest_depth": start.engine.depth, "outcome": "prefix_dead_end",
                "rule_ids": list(reason.rule_ids), "elapsed_s": 0.0})
            return [], deepest
    attempts = 1 + (cfg.max_restarts if cfg.backtrack_limit else 0)
    for attempt in range(attempts):
        now = time.monotonic()
        if now >= deadline:
            break
        remaining_attempts = attempts - attempt
        attempt_deadline = now + (deadline - now) / remaining_attempts
        node = _Node(start.engine.copy(), start.ex.copy(), classes=start.classes,
                     strategy=strategy)
        used.add(strategy)
        if attempt:
            metrics["restarts"] += 1
        metrics.setdefault("restart_strategies", []).append(strategy)
        completed, partial = _construct_attempt(
            instance, node, slots, cfg, attempt_deadline, rng, metrics, pods, progress)
        metrics.setdefault("construction_attempts", []).append({
            "strategy": strategy, "start_depth": start.engine.depth,
            "deepest_depth": partial.engine.depth,
            "outcome": "complete" if completed else
                       "timeout" if time.monotonic() >= attempt_deadline else "exhausted",
            "elapsed_s": time.monotonic() - now})
        if partial.engine.depth > deepest.engine.depth:
            deepest = partial
        if completed:
            return completed, deepest
        rules = metrics.get("dead_end_rule_ids", {})
        if "checker.missing_vessel_data" in rules:
            break
        # Diagnostic rules select a preference; they never override legality.
        suggested = (3 if any("reefer" in r or "imdg" in r or "capacity" in r or "heat" in r for r in rules)
                     else 1 if any("weight" in r or "heavy" in r or "e6" in r for r in rules)
                     else 2 if any("hatch" in r or "restow" in r for r in rules)
                     else (strategy + 1) % 4)
        strategy = next((s for s in (suggested, 0, 1, 2, 3) if s not in used), suggested)
    return [], deepest

def _solve(instance, supplied, incumbent, seed, time_limit_s, cfg):
    started = time.monotonic()
    budget = cfg.default_time_limit_s if time_limit_s is None else time_limit_s
    metrics = {"deterministic": 0, "num_workers": 1, "candidate_probes": 0, "legal_candidates": 0,
               "search_dead_ends": 0, "backtracks": 0, "restarts": 0, "neighborhoods": 0, "retained_actions": 0,
               "discarded_actions": 0, "prefix_relaxations": 0, "checker_passed": False,
               "requires_finalize_and_check": True}
    provenance = current_provenance(created_by="solver.scalable", seed=seed)
    provenance = provenance.model_copy(update={"versions": {**provenance.versions, "solver.scalable": SCALABLE_VERSION}}).merge(
        instance.provenance, *([incumbent.provenance] if incumbent is not None else []),
        parent_ids=[instance.id] + ([incumbent.id] if incumbent is not None else []))
    best = None
    best_value = math.inf
    deepest = None
    retained_partial, partial_key = None, None
    space = get_action_space("boxslot")
    def retain_progress(node):
        # Raw depth counts reloads/restows too. Repair needs the physical state
        # with the most manifest cargo aboard, then the cheapest/shortest path.
        nonlocal retained_partial, partial_key
        loaded = sum(node.ex.where.get(cid, (None,))[0] == "ship" for cid in instance.load_list)
        key = (loaded, -node.score, -node.engine.depth)
        if partial_key is None or key > partial_key:
            retained_partial = _Node(node.engine.copy(), node.ex.copy(), score=node.score)
            partial_key = key
            metrics["partial_loaded"] = loaded
            metrics["partial_depth"] = node.engine.depth

    def result(status, message=None):
        nonlocal provenance
        metrics["deepest_depth"] = max(metrics.get("deepest_depth", 0),
                                       deepest.engine.depth if deepest is not None else 0)
        plan = best
        partial = retained_partial if retained_partial is not None else deepest
        if plan is None and partial is not None:
            # A failed/expired construction still returns its best physical
            # partial for repair; raw deepest can merely be a restow cycle.
            try:
                plan = space.expand(instance, partial.engine.actions())
            except ValueError:
                plan = None
        if plan is not None:
            provenance = provenance.merge(plan.provenance)
            plan = plan.model_copy(update={"provenance": provenance})
        if best is not None:
            metrics["objective"] = best_value
            lb = metrics["lower_bound"]
            metrics["gap"] = (best_value - lb) / best_value if best_value else 0.0
        return PlannerResult(status=status, plan=plan, planner="solver.scalable",
                             planner_version=SCALABLE_VERSION, seed=seed,
                             wall_time_s=time.monotonic()-started, metrics=metrics,
                             message=message, provenance=provenance)
    try:
        if not math.isfinite(budget) or budget < 0:
            return result("error", "time_limit_s must be finite and nonnegative")
        deadline = started + budget
        if budget == 0:
            return result("timeout", "No planning time available")
        metrics["lower_bound"] = lower_bound(instance, time_limit_s=budget).weighted
        if time.monotonic() >= deadline:
            return result("timeout", "Time limit during lower-bound computation")
        feasibility_config = cfg.feasibility_config
        empty_engine = None
        def new_engine():
            nonlocal empty_engine
            if time.monotonic() >= deadline:
                raise SearchDeadline([{"function": "new_engine"}])
            if empty_engine is not None:
                metrics["oracle_root_reuses"] = metrics.get("oracle_root_reuses", 0) + 1
                return empty_engine.copy()
            engine = build_engine(instance, space, config=feasibility_config,
                                  factory=make_feasibility_engine, deadline=deadline)
            metrics["oracle_initializations"] = metrics.get("oracle_initializations", 0) + 1
            if getattr(engine, "mode", "").startswith("native-v2"):
                # The instance/config are fixed for this solve. Retain an
                # untouched empty public state, so LNS/replay roots avoid both
                # repeated metadata probing and native backend initialization.
                # The cache dies with this solve; mutated later inputs revalidate.
                empty_engine = engine.copy()
            return engine
        root = _Node(new_engine(), Expander(instance), strategy=cfg.initial_strategy)
        _LOG.info("oracle ready instance=%s elapsed_s=%.3f", instance.id, time.monotonic() - started)
        metrics["oracle_mode"] = getattr(root.engine, "mode", "shared")
        provenance = provenance.model_copy(update={"versions": {
            **provenance.versions, "scalable-oracle": metrics["oracle_mode"]}})
        deepest = root
        pods = _pod_map(instance)
        root.score = _rank(instance, root, pods)
        retain_progress(root)
        slots = _slots(instance)
        rng = random.Random(seed)
        actions = space.from_plan(instance, incumbent) if incumbent is not None else supplied
        for item in actions:
            if time.monotonic() >= deadline:
                return result("timeout", "Time limit during partial-plan replay")
            try:
                action = space.decode(item) if isinstance(item, dict) else item
                legal = root.engine.is_legal(action)
            except (ValueError, TypeError, KeyError):
                legal = False
            if legal:
                root.engine.push(action)
                root.ex.step(action)
                root.score = _rank(instance, root, pods)
                retain_progress(root)
                metrics["retained_actions"] += 1
            else:
                metrics["discarded_actions"] += 1
        verified_incumbent = False
        if (incumbent is not None and incumbent.ballast is not None and
                metrics["oracle_mode"].startswith("native-v2")):
            from stowage.checker import check
            metrics["incumbent_mask_verified"] = (
                metrics["discarded_actions"] == 0 and root.engine.is_complete())
            try:
                incumbent_report = check(instance, incumbent)
                provenance = provenance.merge(incumbent_report.provenance)
                metrics["incumbent_checker_passed"] = incumbent_report.passed
                verified_incumbent = (incumbent_report.passed and
                                      metrics["incumbent_mask_verified"])
            except Exception as exc:
                metrics["incumbent_checker_passed"] = False
                metrics["incumbent_checker_error"] = f"{type(exc).__name__}: {exc}"
            metrics["verified_incumbent"] = verified_incumbent
            if verified_incumbent:
                # Retain the known feasible plan before any further timed
                # search. Public replay above remains mandatory, including
                # when the checker accepts a plan outside the C2 mask domain.
                best = incumbent
                best_value = apply_plan(instance, incumbent).count.weighted_nonproductive
                if best_value < metrics["lower_bound"] - 1e-9:
                    raise ValueError("Objective below lower bound: bound/objective contract mismatch")
                if time.monotonic() >= deadline:
                    return result("ok", "Time limit reached; retained verified incumbent")
        def consider(node):
            nonlocal best, best_value, provenance
            candidate = node.engine.to_plan()
            if incumbent is not None and incumbent.ballast is not None and candidate.moves == incumbent.moves:
                candidate = candidate.model_copy(update={"ballast": incumbent.ballast})
            provenance = provenance.merge(candidate.provenance)
            replay = apply_plan(instance, candidate)
            if not replay.legal:
                raise ValueError("Oracle-complete plan failed physical move replay")
            value = replay.count.weighted_nonproductive
            if value < metrics["lower_bound"] - 1e-9:
                raise ValueError("Objective below lower bound: bound/objective contract mismatch")
            if value < best_value:
                if verified_incumbent:
                    from stowage.checker import check
                    if candidate.ballast is None:
                        candidate = candidate.model_copy(update={"ballast": incumbent.ballast})
                    # Conservative search admission, not a physics change:
                    # a candidate requiring different ballast is deferred to
                    # exact-window repair rather than displacing valid gold.
                    metrics["beam_checker_attempts"] = metrics.get("beam_checker_attempts", 0)+1
                    try:
                        report = check(instance, candidate)
                        provenance = provenance.merge(report.provenance)
                        accepted = report.passed
                        if not accepted:
                            histogram = metrics.setdefault("beam_checker_rejection_rule_ids", {})
                            for violation in report.violations:
                                if violation.severity == "hard":
                                    histogram[violation.rule_id] = histogram.get(violation.rule_id, 0)+1
                    except Exception as exc:
                        accepted = False
                        metrics["beam_checker_error"] = f"{type(exc).__name__}: {exc}"
                    if not accepted:
                        metrics["beam_checker_rejections"] = metrics.get("beam_checker_rejections", 0)+1
                        return
                best, best_value = candidate, value
                _LOG.info("complete candidate instance=%s objective=%.3f lower_bound=%.3f elapsed_s=%.3f",
                          instance.id, value, metrics["lower_bound"], time.monotonic() - started)
        completed, deepest = _construct(instance, root, slots, cfg, deadline, rng, metrics, pods, retain_progress)
        # Local necessary conditions do not certify that a supplied prefix can
        # finish. Only relax it after its bounded construction actually fails;
        # an already complete incumbent avoids this extra replay entirely.
        released = 0
        while (not completed and root.engine.depth and
               metrics["prefix_relaxations"] < cfg.backtrack_limit and
               time.monotonic() < deadline):
            previous = root.engine.actions()
            # Try the longest retained prefixes first, then widen cuts so a
            # long corrupt prefix can still reach the root in bounded retries.
            cut_size = min(len(previous), 1 if released < 8 else 2 ** min(released - 7, 20))
            retained = previous[:-cut_size]
            try:
                eng = root.engine.copy()
                for _ in range(cut_size):
                    eng.pop()
            except (AttributeError, NotImplementedError):
                # Lightweight public-protocol test oracles may omit pop.
                eng = new_engine()
                for action in retained:
                    if time.monotonic() >= deadline:
                        return result("timeout", "Time limit during prefix repair")
                    eng.push(action)
            ex = Expander(instance)
            for action in retained:
                if time.monotonic() >= deadline:
                    return result("timeout", "Time limit during prefix repair")
                ex.step(action)
            root = _Node(eng, ex, strategy=cfg.initial_strategy)
            released += 1
            metrics["prefix_relaxations"] += 1
            metrics["retained_actions"] -= cut_size
            metrics["discarded_actions"] += cut_size
            completed, partial = _construct(instance, root, slots, cfg, deadline, rng, metrics, pods, retain_progress)
            if partial.engine.depth > deepest.engine.depth:
                deepest = partial
        for node in completed:
            consider(node)
        exact_visited = set()
        for turn in range(cfg.max_neighborhoods):
            if best is None or best_value <= metrics["lower_bound"] + 1e-9 or time.monotonic() >= deadline:
                break
            acts = space.from_plan(instance, best)
            if not acts:
                break
            if ((cfg.exact_window_time_limit_s or cfg.exact_whole_plan_time_limit_s) and
                    metrics["oracle_mode"].startswith("native-v2")):
                from stowage.solver.scalable_exact import exact_windows, repair_exact_window
                windows = exact_windows(instance, acts, max_boxes=cfg.exact_window_max_boxes,
                                        whole_plan_boxes=cfg.exact_whole_plan_boxes)
                signature = tuple(space.sort_key(action) for action in acts)
                manifest_boxes = len(set(instance.load_list) | set(instance.discharge_list))
                def window_cap(window):
                    whole = window.cut == 0 and manifest_boxes <= cfg.exact_whole_plan_boxes
                    return (cfg.exact_whole_plan_time_limit_s if whole else
                            cfg.exact_window_time_limit_s)
                window = next((w for w in windows if window_cap(w) > 0 and
                               (signature, w.cut) not in exact_visited), None)
                if window is not None:
                    exact_visited.add((signature, window.cut))
                    window_budget = min(window_cap(window), deadline-time.monotonic())
                    if window_budget > 0:
                        metrics["neighborhoods"] += 1
                        try:
                            candidate, diagnostic = repair_exact_window(
                                instance, best, window, time_limit_s=window_budget,
                                max_nodes=cfg.exact_window_max_nodes, seed=seed+turn,
                                feasibility_config=feasibility_config, engine_factory=new_engine)
                        except Exception as exc:
                            # A failed/unknown repair never replaces a complete incumbent.
                            candidate, diagnostic = None, {"kind": window.kind, "cut": window.cut,
                                "accepted": False, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
                        metrics.setdefault("exact_windows", []).append(diagnostic)
                        if window.cut == 0:
                            metrics["exact_root_status"] = diagnostic.get("status")
                            metrics["exact_root_objective"] = diagnostic.get("exact_objective")
                            metrics["exact_root_lower_bound"] = diagnostic.get("exact_lower_bound")
                            metrics["exact_root_optimality_scope"] = "configured_engine_weighted_lifts"
                            metrics["exact_root_production_engine"] = (
                                feasibility_config is None or feasibility_config == FeasibilityConfig())
                        if candidate is not None:
                            best, best_value = candidate, diagnostic["objective"]
                            provenance = provenance.merge(candidate.provenance)
                            metrics["exact_window_improvements"] = metrics.get("exact_window_improvements", 0)+1
                            acts = space.from_plan(instance, best)
                        exact_objective = metrics.get("exact_root_objective")
                        if exact_objective is not None:
                            metrics["gap_to_exact_incumbent"] = ((best_value-exact_objective)/exact_objective
                                                                   if exact_objective else 0.0 if not best_value else None)
                        if (metrics.get("exact_root_status") == "OPTIMAL" and
                                exact_objective is not None and best_value <= exact_objective+1e-9):
                            metrics["exact_root_bound_reached"] = True
                            break
                        if best_value <= metrics["lower_bound"] + 1e-9 or time.monotonic() >= deadline:
                            break
                        # Try every available exact window before spending
                        # another slice on beam repair. In particular, oracle
                        # setup and prefix replay must not starve window two.
                        continue
            anchor = rng.randrange(len(acts))
            chosen = acts[anchor]
            mode = turn % 3
            if mode == 0 and isinstance(chosen, Place):
                key = parse_slot(chosen.slot).bay
                cut = next(i for i, a in enumerate(acts) if isinstance(a, Place) and parse_slot(a.slot).bay == key)
            elif mode == 1:
                key = pods[chosen.box]
                cut = next(i for i, a in enumerate(acts) if pods[a.box] == key)
            else:
                initial = {cid: s.key.split("-")[0] for s in instance.yard.stacks for cid in s.boxes}
                key = initial.get(chosen.box)
                cut = next(i for i, a in enumerate(acts) if initial.get(a.box) == key)
            cut = min(cut, max(0, len(acts)-max(1, math.ceil(len(acts)*cfg.destroy_fraction))))
            node = _Node(new_engine(), Expander(instance), strategy=cfg.initial_strategy)
            for a in acts[:cut]:
                if time.monotonic() >= deadline:
                    break
                if not node.engine.is_legal(a):
                    raise ValueError("Incumbent prefix no longer legal in shared oracle")
                node.engine.push(a)
                node.ex.step(a)
            metrics["neighborhoods"] += 1
            # A beam repair must leave time for other exact windows instead of
            # spending the entire remaining solve on the first failed suffix.
            repair_deadline = (min(deadline, time.monotonic()+cfg.exact_window_time_limit_s)
                               if cfg.exact_window_time_limit_s else deadline)
            completed, partial = _construct(instance, node, slots, cfg, repair_deadline, rng, metrics, pods, retain_progress)
            if partial.engine.depth > deepest.engine.depth:
                deepest = partial
            for candidate in completed:
                consider(candidate)
        if best is not None:
            return result("ok", "Candidate requires ballast finishing and full checker")
        status = "timeout" if time.monotonic() >= deadline else "dead_end"
        message = "No complete candidate found within the bounded search"
        if status == "dead_end" and deepest is not None:
            reason = deepest.engine.dead_end()
            if reason is not None:
                metrics["oracle_dead_end"] = {"depth": reason.depth, "reason": reason.reason,
                                              "rule_ids": list(reason.rule_ids)}
                message = reason.reason
        return result(status, message)
    except SearchDeadline as exc:
        metrics["deadline_enforced"] = True
        metrics["deadline_stack"] = exc.args[0] if exc.args else []
        if exc.required_data_violations is not None:
            metrics["required_data_violations"] = exc.required_data_violations
            metrics["required_data_rule_ids"] = sorted({v["rule_id"]
                for entry in exc.required_data_violations for v in entry["violations"]})
            metrics["required_data_probes"] = exc.required_data_probes
        return result("ok" if best is not None else "timeout",
                      "Search deadline reached; retained best complete candidate" if best is not None
                      else "Search deadline reached inside oracle initialization or search")
    except RequiredDataUnavailable as exc:
        metrics["required_data_violations"] = exc.violations
        metrics["required_data_rule_ids"] = list(exc.rule_ids)
        return result("error", str(exc))
    except NotImplementedError as exc:
        return result("error", f"Required shared feasibility component is unavailable: {exc}")
    except Exception as exc:  # Planner contract: hard instances return a status, not an exception.
        return result("error", f"{type(exc).__name__}: {exc}")
