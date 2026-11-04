"""Move engine (frozen interface, plan 0.9; owner 2.3).

What "physically possible" means (each rejection is tagged with a catalogue ``moves.*`` / physical rule id from 1.2):
    - only the top box of a yard stack or ship stack can be lifted;
    - ship stacks fill bottom-up with no gaps, the box length fits the target cells (20/40/45, ``Cell.accepts_*``),
      no 20' box on top of a 40' box (verify), one box per cell;
    - hold stacks are reachable only while their hatch cover is off; a cover can be lifted only when every deck stack
      on it is empty; all covers must be back on at the end of a plan;
    - yard stacks never exceed the effective tier limit (min of block.max_tiers, port.yard_max_tiers, equipment);
      on-deck loads never exceed ``port.quay_crane.max_on_deck_tiers`` (E3); a yard stack holds one length class;
    - the box being moved must be where ``src`` says, and ``dst`` must be the exact position it lands in;
    - productive moves only for the right boxes: load ⇒ box on the load list; discharge ⇒ box on the discharge
      list and POD is this call
      (temporary removals must be ``restow_off``); quay buffer empty at the end of the plan.
    With rule-freeze-v1 active, port DG refusal/direct delivery and C6 crane
    height/crossing are lift rules checked here; ``crossing_paths`` is the shared
    geometry API for masks, expanders and solvers. Other rule-catalogue *stowage* rules (weights, reefers, OOG, stability) are NOT the move engine's job — the checker
    (2.4, 3.x) judges the departure state. Reefer plugs and OOG blocked cells are stowage rules too → checker.
    The split: move engine = "can a crane do this lift now"; checker = "is the resulting stow allowed".
    Yard callers must additionally enforce DG zoning and powered reefer rows on each storage destination,
    including custom retrieval hooks; physical replay does not certify those yard stowage restrictions.
    Simulator-only gates are supported by ``apply_move``; ``apply_plan`` rejects them so they cannot evade D11.
    The catalogue currently has no dedicated source/list/call or departure-completion ids: these use
    ``expert.moves.top_access`` with specific messages; unfinished covers use ``expert.moves.hatch_clear``.
    Equipment limits apply at both ends of a lift: a crane must reach the source as well as the destination.

Counting: one ``Move`` = one lift. ``MoveCount`` (``stowage.model.results``) tallies by type and adds the
**projection of later-port lifts** caused by the departure stow (``project_future_moves``):
    for each later call c in rotation order, starting from the departure ship state and removing each call's
    discharges as we go (ASSUMPTION: D15 assumes no new loads — stage A; stage D adds forecasts in its own solver):
      1. D_c = boxes aboard with POD = c.
      2. A box b ∉ D_c is *blocking* if it sits in the same ship stack above a box of D_c, or (40'/45') rests on
         a stack that has a D_c box below it, or sits on a deck stack of a hatch cover whose hold stacks contain a
         D_c box.
      3. restowed_boxes(c) = number of blocking boxes; restow_lifts(c) = restowed_boxes ×
         ``ObjectiveWeights.future_restow_lifts_per_box`` (ASSUMPTION: default 2, pending expert Q1; D15).
      4. hatch_cover_lifts(c) = 2 × number of covers whose hold stacks contain a D_c box (off + on).
      5. Remove D_c; retain the blocking boxes' coordinates; continue with c+1.
    APPROXIMATION: D15's fixed-coordinate projection is an abstract scenario count. Retained coordinates can
    lose support after a discharge; it neither constructs executable future restows nor certifies a minimum
    over arbitrary future plans. An executable policy would need new positions and future cargo information.

Determinism and purity: every function is pure (inputs are never mutated) and deterministic. Complexity targets:
``apply_move`` O(stack height) amortised; ``apply_plan`` O(len(moves) × stack height) — fast enough to replay a
1,000-box plan in well under 100 ms; the feasibility engine (4.4) keeps its own incremental state and only uses these
functions for validation and tests.
"""

from __future__ import annotations

from stowage.frozen import frozen
from stowage.model import ApplyResult, FutureMoves, Instance, Move, MoveCount, MovePlan, ShipState, Violation
from stowage.model.ship import WorldState


class IllegalMoveError(ValueError):
    """Raised by ``apply_move`` for a physically impossible move. ``violation`` carries the catalogue rule id."""

    def __init__(self, violation: Violation) -> None:
        super().__init__(violation.message)
        self.violation = violation


@frozen("moves.initial_state", owner="2.3", implemented=True)
def initial_state(instance: Instance) -> WorldState:
    """The world at the start of ``instance.call_index``: ``instance.yard`` + ``instance.onboard``."""
    return WorldState(call_index=instance.call_index, yard=instance.yard.model_copy(deep=True),
                      ship=instance.onboard.model_copy(deep=True))


@frozen("moves.apply_move", owner="2.3", implemented=True)
def apply_move(instance: Instance, state: WorldState, move: Move, *, move_index: int | None = None) -> WorldState:
    """Apply one lift and return the new state (input unchanged).

    Raises ``IllegalMoveError`` (with a Violation tagged by catalogue id, ``move_index`` filled in) if the move is
    physically impossible in ``state``. Never returns a partially applied state.
    Supports simulator gate events; planning callers use ``apply_plan`` to enforce their exclusion.
    Does not validate yard DG zoning or powered reefer rows; the yard caller owns those destination checks.
    """
    from stowage.moves._physical import Replay

    replay = Replay(instance, state)
    replay.move(move, move_index=move_index)
    return replay.state()


@frozen("moves.apply_plan", owner="2.3", implemented=True)
def apply_plan(instance: Instance, plan: MovePlan, *, stop_at_first: bool = True) -> ApplyResult:
    """Replay ``plan.moves`` from ``initial_state(instance)``.

    Never raises for an illegal plan: returns ``ApplyResult(legal=False, violations=[...])`` with the offending move
    index. With ``stop_at_first=False`` an illegal move is skipped, recorded, and replay continues (diagnostics for
    the UI and repair). Also checks the end-of-plan conditions (quay buffer empty, covers on, every discharge-list box
    discharged). Raises ValueError only for a plan/instance mismatch (``plan.instance_id``/``call_index``).
    ``count`` includes ``project_future_moves`` on the final ship state.
    The returned ``ObservedApplyResult`` additionally exposes serializable
    ``d62_crossing`` current-call observations; switch off is explicitly
    not_applicable with zero counts. Under A8 known yard limits remain observable
    even when ship H is unknown. The objective MoveCount is unchanged.
    Simulator-only ``gate_in`` / ``gate_out`` events are illegal here; use ``apply_move`` for simulator events.
    """
    from stowage.moves._physical import Replay
    from stowage.moves._observations import ObservedApplyResult

    if plan.instance_id != instance.id or plan.call_index != instance.call_index:
        raise ValueError("plan instance_id and call_index must match the instance")
    replay = Replay(instance, initial_state(instance), observe_crossing=True)
    observer = replay.crossing_observer
    violations: list[Violation] = []
    counts = dict(loads=0, discharges=0, relocations=0, restow_lifts=0,
                  hatch_cover_lifts=0, gate_moves=0)
    fields = {"load": "loads", "discharge": "discharges", "relocate": "relocations",
              "restow_shift": "restow_lifts", "restow_off": "restow_lifts",
              "restow_on": "restow_lifts", "hatch_cover_off": "hatch_cover_lifts",
              "hatch_cover_on": "hatch_cover_lifts", "gate_in": "gate_moves", "gate_out": "gate_moves"}
    discharged: set[str] = set()
    executed = 0
    for index, move in enumerate(plan.moves):
        rehandle = observer.rehandle_candidate(replay, move)
        try:
            if move.kind in ("gate_in", "gate_out"):
                raise IllegalMoveError(Violation(
                    rule_id="expert.moves.top_access", severity="hard", call_index=instance.call_index,
                    move_index=index, containers=[move.container_id],
                    message="Gate moves are simulator-only and cannot appear in a planning MovePlan."))
            replay.move(move, move_index=index)
        except IllegalMoveError as error:
            observer.rejected(error.violation.rule_id)
            violations.append(error.violation)
            if stop_at_first:
                break
            continue
        executed += 1
        observer.committed(rehandle)
        counts[fields[move.kind]] += 1
        if move.kind == "discharge":
            discharged.add(move.container_id)
    state = replay.state()
    # Departure checks have no move index: they describe missing work, not a bad lift.
    if state.yard.quay_buffer:
        violations.append(Violation(
            rule_id="expert.moves.top_access", severity="hard", call_index=instance.call_index,
            containers=list(state.yard.quay_buffer), message="Temporary quay removals must be restowed before departure."))
    if state.ship.covers_off:
        violations.append(Violation(
            rule_id="expert.moves.hatch_clear", severity="hard", call_index=instance.call_index,
            message="All hatch covers must be replaced before departure."))
    missing = sorted(set(instance.discharge_list) - discharged)
    if missing:
        violations.append(Violation(
            rule_id="expert.moves.top_access", severity="hard", call_index=instance.call_index,
            containers=missing, message="Every discharge-list box must be discharged before departure."))
    try:
        future = project_future_moves(instance, state.ship, from_call=instance.call_index)
    except IllegalMoveError as error:
        # A later-call rejection or unsupported projection prevents accepting
        # this departure plan. Preserve the diagnostic, never-raises contract;
        # such a scenario has no certified projected objective value.
        violations.append(error.violation)
        future = []
    w = instance.objective
    weighted = (w.relocate * counts["relocations"] + w.restow * counts["restow_lifts"]
                + w.hatch_cover * counts["hatch_cover_lifts"]
                + w.future_restow * sum(f.restow_lifts for f in future)
                + w.future_hatch_cover * sum(f.hatch_cover_lifts for f in future))
    # ApplyResult's diagnostic contract reports every processed move; lift counts
    # still include only successful moves, since a rejected lift never happened.
    processed = executed if stop_at_first else len(plan.moves)
    return ObservedApplyResult(legal=not violations, executed=processed, final_state=state,
                       count=MoveCount(**counts, future=future, weighted_nonproductive=weighted),
                       violations=violations, d62_crossing=observer.metric)


@frozen("moves.count_moves", owner="2.3", implemented=True)
def count_moves(instance: Instance, plan: MovePlan) -> MoveCount:
    """``apply_plan(instance, plan).count`` but raises ValueError if the plan is illegal (an illegal plan has no
    meaningful objective value)."""
    result = apply_plan(instance, plan)
    if not result.legal:
        detail = "; ".join(f"{v.rule_id}: {v.message}" for v in result.violations)
        raise ValueError(f"illegal plan has no objective value: {detail}")
    return result.count


@frozen("moves.project_future_moves", owner="2.3", implemented=True)
def project_future_moves(instance: Instance, ship: ShipState, *, from_call: int) -> list[FutureMoves]:
    """D15 abstract fixed-coordinate scenario after ``from_call`` (algorithm and limitations above).

    ASSUMPTION: no future loads, with provisional Q1 restow-lifts-per-box weights.
    APPROXIMATION: retained coordinates need not be supported after discharges; these counts do not certify
    executable future operations or a minimum over arbitrary future plans.
    One ``FutureMoves`` per later call, in rotation order (zeros included).
    Under rule-freeze-v1, crossing blockers use that same configured off/on
    policy. A permanent shift might cost less, but requires an explicit future
    placement policy. Raises ``IllegalMoveError`` for a required later lift
    refused by the resolved port or outside its known crane height, invalid
    later port data, or a direct-delivery blocker requiring unsupported off/on
    storage. ``apply_plan`` converts this to an ordinary hard diagnostic.
    Unsupported means no future shift policy was certified, not that every
    possible future policy is infeasible.
    """
    from stowage.moves._projection import project

    return project(instance, ship, from_call=from_call)
