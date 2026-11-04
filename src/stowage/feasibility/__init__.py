"""Incremental feasibility engine (plan 4.4). Frozen API (plan 0.9) in ``stowage.feasibility.engine``."""

from stowage.feasibility.engine import (
    DeadEnd,
    FeasibilityConfig,
    FeasibilityEngine,
    FeasibilityStats,
    IllegalActionError,
    legal_actions_batch,
    make_feasibility_engine,
)
from stowage.versions import FEASIBILITY_VERSION

__all__ = [
    "FEASIBILITY_VERSION",
    "DeadEnd",
    "FeasibilityConfig",
    "FeasibilityEngine",
    "FeasibilityStats",
    "IllegalActionError",
    "legal_actions_batch",
    "make_feasibility_engine",
]
