"""Incremental feasibility engine (frozen interface, plan 0.9; owner 4.4).

Purpose: given a partial plan (a sequence of 3.8 actions), return actions passing exact local legality
and necessary completion conditions (D23/D49), including the D55 sight-plane height cap. These conditions are relaxations: accepted prefixes can reach
packing or full-checker dead ends. The mask (5.1), solvers (4.6–4.8) and repair (5.7) use the engine as a legality
screen; callers must backtrack or repair and finish every final plan through the full checker.

Guarantees:
    - **Local exactness:** L1/L2 below match the move engine and static checker, including forced hatch restows.
    - **Necessary completion screening:** C1 and necessary-direction C2 reject violations of their relaxed model.
      C2's implication to exact modeled constraints is empirical within 3.7's qualified domain (D49), not a
      domain-wide proof or a certificate of a discrete completion. The full checker remains the only certifier.
    - **Determinism:** ``legal_actions()`` returns actions sorted by ``space.sort_key``; same instance + same pushes
      → same output, no RNG.

Latency targets (full feeder): ``is_legal`` ≤ 0.5 ms, ``push``/``pop`` ≤ 1 ms. After ``is_complete()``,
``legal_actions()`` is empty (a plan ends when every required box is placed; 3.8 decides whether an explicit
end action exists, and if so it is the only legal action when complete).

Rule-freeze-v1 activation (C1–C6/D69): exact gross-mass order replaces D25 classes,
E6 is soft, OOG killed cells reserve capacity, truck delivery and port refusals
are screened, and every candidate delegates physical crossing and self-blocking
to ``Expander.can_expand`` with exhaustive static target domains (including
temporarily unavailable slots). DG eligibility is the union of admissible support
heights; actual DG placements receive the shared checker's placement assessment.
The catalogue activation context must remain fixed for an engine's lifetime.
D64 additionally requires ``checker.full.IN_PORT_EVERY_LIFT_ENABLED is True``;
until the checker sets that flag it is off. Its rev-3 screen omits displacement
and every seagoing lower bound, and defers outside the qualified D49 domain.
The mask and solvers should consume this API directly, including its index-array
view; duplicating the former E6/class/height gates can introduce false rejections.
For box-then-slot decoding, ``legal_box_ids()`` batches a column over unresolved
boxes and stops after the first feasible slot for each box;
``legal_slots_for_box(box_id)`` evaluates only that box's full slot domain.
These additive APIs have the same Place projection as ``legal_actions()``.
``screen_diagnostics()`` exposes JSON-ready rejection counts by catalogue rule
(distinct box/slot pairs per prefix), rules active at the dead state, probe
counts and whether lazy evaluation is still partial.
D69 is necessary relative to the Stage A action domain, which excludes shifts.
D64 also requires ``in_port.D64_COST_PRUNING_QUALIFIED``; it remains false until
qualified physical-prefix pruning and cost have been demonstrated.

State semantics: a stack. ``push`` applies an action (raises ``IllegalActionError`` if it isn't legal), ``pop``
undoes the last one; both O(size of the incremental update), no recomputation from scratch. ``copy`` returns an
independent engine (for beam search / batched sampling); copying should be O(state size) and cheap relative to
generation (target < 1 ms for a full feeder).

Performance budget: generation runs ~31 tok/s single-stream, ~9 tokens per action (docs/compute.md, plan 3.8), so
one action every ~0.3 s; with batch 16 the mask must answer 16 ``legal_actions`` calls per batched step. Target:
p95 ``legal_actions`` ≤ 5 ms on a full-size feeder instance (≤ 80 ms per batched step), measured and reported by
4.4/5.1.

Semantics (v2, leader decision 4.4)
-----------------------------------
``legal_actions()`` = the actions that pass, exactly:
    L1  the expander step (3.8) is physically legal under 2.3's rules: open slot (next free cell of every
        supporting stack), length/footprint, no 20' on 40', quay-crane deck reach, hatch covers handled lazily by
        the expander, yard blockers relocatable by the default rule (a count test, exact trial when tight);
    L2  every 2.4 static rule for the box against the boxes already aboard: stack weight (E7, 20/40/mixed limit),
        stack height, weight order by D25 class (E2: no box above a lighter class L < M < H), heavy (class H) over
        non-heavy (E1), reefer plug, E8 requests
        (under/on deck, top stowage, away from heat), OOG (E9), quay tier (E3), IMDG stowage category, Document of
        Compliance and segregation against every DG box aboard;
    V1  D55 visibility: an on-deck candidate's roof must fit the content-cached per-stack absolute
        sight-plane upper bound over the full D40 signed trim/list/draught envelope. This is a necessary
        screen; the actual-state full visibility checker still decides. Hold cargo is not capped. Missing
        geometry or an uncertified sight wedge gives an infinite cap, never an invented rejection.
and the necessary completion conditions (the remaining boxes are placed into free cells, no undo):
    C1  capacity (``capacity.py``, ``_engine.py``):
        c1a class matching (Hall / max-flow; per candidate exact via a one-cell-removal table and a warm-started
            flow repair); c1b weight-order nested Hall (D25 class caps as VGM bounds), for all boxes and for 20'
            boxes vs stacks without a long box (no 20' above a 40'); c1c/c1e long boxes vs stack pairs by count and
            nested by cap, with the aligned pair bound min(top) − max(fill); c1d aggregate stack-mass room;
            c1h the c1b/c1e checks per restricted compatibility class (reefer plugs, under/on-deck requests, DoC
            areas, ...) over only that class's compatible usable cells; c1f E6 at departure (D25): a hold stack a
            deck H box could still use stops blocking with one "blocker" box (40', lighter class, non-heavy) or
            k_j heavy boxes (cells / mass / height exhausted, best case); blocker units go to the largest k_j and
            the rest must fit the non-blocker units; a placement that completes the plan is judged by 2.4's exact
            E6 departure rule; c1g DG segregation capacity: per DG class, the free slots that are statically
            allowed (stowage category, DoC areas) and segregated (2.4's 7.2.4/7.4.3 pair check) from every DG box
            aboard must number at least the class's boxes still to load (they only shrink under the no-undo model);
    C2  the 3.7 linear stability/strength rows (``direction="necessary"``, default margin scale 1) admit a fractional completion of the
        remaining mass plus ballast (``global_bounds.py``; candidates screened by a ship-wide shift and a local
        donor shift of the reference completion, then exact slot LPs for every unresolved slot).
        Only a proven-infeasible solver status rejects. An inconclusive solve admits the affected state/actions
        and increments ``diagnostics['lp_indeterminate']``; it supplies no fractional-completion witness.
The engine is **sound with respect to this model**; the model is necessary, not sufficient, so dead ends occur.
They are reported (``dead_end()``), measured (random-rollout dead-end rate per rung,
``experiments/feasibility/README.md``) and handled by callers (backtrack; 5.7 repair). The checker + ballast
finisher judge every final plan.

Action set: ``Place`` for every remaining box (yard or quay buffer) × open slot, and ``Discharge(box)`` (default
target) in stage B. A placement into a hold slot whose hatch cover carries deck cargo is legal (the expander
restows the deck boxes to the quay); ``forces_restow(action)`` (not in the protocol) flags such actions. Explicit
overrides (``Relocate``, ``Discharge.to``, ``RestowOff``, ship-to-ship shifts) are not listed (3.8's study: optimal
tiny plans needed none); ``is_legal`` is False for them.

The D55 cap is included even in the ``completion_screens=0`` diagnostic mode; that mode now means
L1/L2 plus the necessary visibility height screen. Hull-free local-rule fixtures have infinite caps.

Exactness notes (labelled): C1 counts usable cells per stack with the shortest/lightest box of the instance and
the largest stack-mass limit still compatible with the stack's length mix (both optimistic → necessary). For a
placement that forces restows, the expander is replayed and L2 is evaluated after the deck boxes have left;
C1 and C2 use the actual child state, with those boxes remaining to reload. A ``Discharge`` is likewise screened
on its post-state. C2 uses a constant per-cell cap (largest per-cell share in the instance). Fast reference shifts
only certify acceptance; every unresolved slot receives an exact LP, cached per prefix. ``extra['lp_budget']``
is retained as a soft diagnostic threshold and never authorizes rejection without the slot LP.

Config (``FeasibilityConfig.extra``): ``global_screen`` (1; 0 = diagnostic, C2 off — results must say so),
``lp_budget`` (2, soft diagnostic threshold), ``recenter_below`` (0.5: re-centre the C2 reference when fewer than this share of candidates
pass the closed-form screen). A vessel with a hull whose 3.7 model can't be built fails closed (dead end at depth
0, ``checker.missing_vessel_data``); a hull-free fixture skips C2.

Index-array access (extras, additive, not in the frozen protocol; for the 5.1 mask): ``legal_index()`` returns the
legal ``Place`` actions as a sorted read-only int64 array of ``box_index * n_slots + slot_index`` (same set and
order as the ``Place`` part of ``legal_actions()``, without building action objects); ``legal_pairs()`` splits it;
``legal_discharges()`` lists the stage-B ``Discharge`` actions (they follow the places in ``legal_actions()``);
``index_boxes`` / ``index_slots`` / ``n_slots`` decode indices; ``decode_index(flat)`` / ``index_of(action)``
convert one action. ``is_legal`` and ``push`` work without ``legal_actions()`` ever being called.

Use from solvers (4.6/4.7/4.8): generate plans only through ``legal_actions`` (gold plans then replay through the
mask by construction); ``copy()`` is O(depth) pointer copies (snapshots are shared and immutable), so beam search
copies freely; on ``dead_end()`` pop one or more actions and try the next candidate (DFS with backtracking), or hand
the prefix to 5.7 repair. ``to_plan()`` returns the expanded plan without ballast (the finisher 4.2 adds it).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from stowage.actions import A, ActionSpace
from stowage.frozen import frozen
from stowage.model import Instance, MovePlan


class IllegalActionError(ValueError):
    """``push`` of an action that ``legal_actions()`` would not return."""


@dataclass(frozen=True)
class DeadEnd:
    """The partial plan can't be completed. ``depth`` = actions pushed when detected; ``rule_ids`` = catalogue ids
    of rules active at the dead state (best effort); ``reason`` = one sentence.
    The rule list does not identify a minimal set of causes.
    """

    depth: int
    reason: str
    rule_ids: tuple[str, ...] = ()


@dataclass
class FeasibilityStats:
    """Counters since construction (copies start from the parent's counters)."""

    legal_calls: int = 0
    legal_time_s: float = 0.0
    max_legal_time_s: float = 0.0
    pushes: int = 0
    pops: int = 0
    dead_ends: int = 0


@dataclass(frozen=True)
class FeasibilityConfig:
    """Tuning knobs. ``stability_margin_scale`` multiplies the 3.7 necessary-direction error bounds (>= 1
    widens the relaxation; qualification is empirical, D49). ``lookahead`` is the engine-specific depth of completability checks (0 = local checks + global
    bounds only). ``extra``: engine-specific settings, recorded in provenance."""

    stability_margin_scale: float = 1.0
    lookahead: int = 0
    extra: dict[str, float] = field(default_factory=dict)


@runtime_checkable
class FeasibilityEngine(Protocol[A]):
    """The incremental engine for one instance, over action type ``A``."""

    instance: Instance
    space: ActionSpace[A]

    def legal_actions(self) -> Sequence[A]:
        """Next actions passing exact local rules and necessary completion screens, sorted by ``space.sort_key``.
        Empty iff complete or a
        dead end (check ``is_complete`` / ``dead_end``)."""
        ...

    def is_legal(self, action: A) -> bool:
        """``action in legal_actions()`` — may be faster than listing them all."""
        ...

    def push(self, action: A) -> None:
        """Apply ``action``; raises IllegalActionError if not legal (state unchanged then)."""
        ...

    def pop(self) -> A:
        """Undo the last push and return it. Raises IndexError at depth 0."""
        ...

    @property
    def depth(self) -> int: ...

    def actions(self) -> list[A]:
        """The partial plan so far."""
        ...

    def is_complete(self) -> bool:
        """Every load-list box loaded (and stage-B discharges done); the expanded plan is then ready for ballast
        finishing and checking."""
        ...

    def dead_end(self) -> DeadEnd | None:
        """Why the current partial plan can't be completed, or None."""
        ...

    def to_plan(self) -> MovePlan:
        """Expand the current (complete) action sequence via ``space.expand``; provenance records the engine.
        Ballast is left None (the 4.2 finisher sets it)."""
        ...

    def copy(self) -> FeasibilityEngine[A]: ...

    def stats(self) -> FeasibilityStats: ...


@frozen("feasibility.make_engine", owner="4.4", implemented=True)
def make_feasibility_engine(
    instance: Instance, space: ActionSpace[A], *, config: FeasibilityConfig | None = None
) -> FeasibilityEngine[A]:
    """Build the engine at depth 0 for ``instance``. Precomputation (linear model, segregation neighbourhoods,
    candidate slots) happens here; target < 2 s for a full feeder. An instance detectably infeasible before any
    action gives an engine whose ``dead_end()`` is set at depth 0 (never an exception), so callers stay uniform."""
    from ._engine import IncrementalEngine

    return IncrementalEngine(instance, space, config or FeasibilityConfig())


@frozen("feasibility.legal_actions_batch", owner="4.4", implemented=True)
def legal_actions_batch(engines: Sequence[FeasibilityEngine[A]]) -> list[Sequence[A]]:
    """``[e.legal_actions() for e in engines]``, possibly vectorised/threaded. The mask (5.1) calls this once per
    batched step. Same results and order as the per-engine calls. Engines may belong to different instances."""
    return [engine.legal_actions() for engine in engines]
