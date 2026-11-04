"""Move engine: physical legality + move counting — the objective function (plan 2.3).

Frozen API (plan 0.9) in ``stowage.moves.engine``; re-exported here.
"""

from stowage.moves.engine import (
    IllegalMoveError,
    apply_move,
    apply_plan,
    count_moves,
    initial_state,
    project_future_moves,
)
from stowage.moves._paths import CrossingPath, crossing_paths
from stowage.moves._port_rules import port_move_violation
from stowage.moves._sequencing import pending_lift_rows
from stowage.moves._observations import CrossingObservations, ObservedApplyResult
from stowage.versions import MOVE_ENGINE_VERSION

__all__ = [
    "MOVE_ENGINE_VERSION",
    "IllegalMoveError",
    "apply_move",
    "apply_plan",
    "count_moves",
    "initial_state",
    "project_future_moves",
    "CrossingPath",
    "crossing_paths",
    "port_move_violation",
    "pending_lift_rows",
    "CrossingObservations",
    "ObservedApplyResult",
]
