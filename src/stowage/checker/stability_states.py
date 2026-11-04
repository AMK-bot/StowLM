"""Incremental exact cargo-operation states for the in-port replay driver.

The move engine remains the sole authority on legality and state transitions.
Keeping its transactional indices avoids rebuilding each prefix from arrival;
this module changes no stability criterion, tank fill, or ballast policy.
"""
from __future__ import annotations

from stowage.model import Instance, Move
from stowage.model.ship import WorldState
from stowage.moves import initial_state
from stowage.moves._physical import Replay


class InPortStateBuilder:
    """Apply one exact lift at a time and return independent world snapshots.

    ``state`` optionally resumes a previously evaluated world. The instance and
    rule-activation context must remain fixed for this builder's lifetime, just
    as during the engine's ``apply_plan``. Activation belongs to the calling
    batch/replay context; constructing a builder does not enable any rule.

    ``push`` raises the same ``IllegalMoveError`` as ``apply_move``. The engine
    validates each lift before committing it, so a rejected lift leaves the
    world unchanged and callers can continue with another move. Returned
    snapshots share no mutable state with either the engine or earlier results.
    """

    def __init__(self, instance: Instance, state: WorldState | None = None) -> None:
        self._replay = Replay(instance, initial_state(instance) if state is None else state)

    @property
    def state(self) -> WorldState:
        """A fresh snapshot of the current world, safe for a caller to modify."""
        return self._replay.state()

    def push(self, move: Move, *, move_index: int | None = None) -> WorldState:
        """Apply one lift atomically, preserving the move engine's diagnostics."""
        self._replay.move(move, move_index=move_index)
        return self._replay.state()
