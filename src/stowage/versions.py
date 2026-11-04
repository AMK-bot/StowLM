"""Component version strings that every artifact records (plan 0.9; pinned by GRPO, plan 6.4).

Rule: a component's owner bumps its own constant whenever a change can alter its output for the same input
(a new rule, a different move count, a different mask). Bump the minor for additive changes, the major for anything
that changes existing results. Edit only your own line (ownership map in docs/architecture.md).

``SCHEMA_VERSION`` is the JSON schema of Instance / MovePlan / PlannerResult files (plan 1.1 owns it). Readers refuse
a file whose major differs (``stowage.io``).
"""

from __future__ import annotations

from typing import Final

SCHEMA_VERSION: Final = "1.9"  # owner 1.1 — Instance/MovePlan/PlannerResult JSON
MOVE_ENGINE_VERSION: Final = "2.0"  # owner 2.3 — stowage.moves
CHECKER_VERSION: Final = "2.2"  # owner 4.1 (parts 2.4, 3.1, 3.2, 3.3) — stowage.checker; wave 2: exact caches, S1 fail-closed, D33 aliases
FEASIBILITY_VERSION: Final = "2.5"  # owner 4.4 — D55 visibility and native LP cold confirmation
LINEAR_MODEL_VERSION: Final = "3.5"  # owner 3.7 — stowage.linear
BALLAST_VERSION: Final = "4.0"  # owner 4.2 — stowage.solver.ballast; wave 2: ok needs all 4 parts at all 3 D57 states
ACTION_SPACE_VERSION: Final = "1.0.1"  # owner 3.8 — stowage.actions
BOUNDS_VERSION: Final = "1.0"  # owner 3.6 — stowage.bounds
INFRASTRUCTURE_VERSION: Final = "3.0"  # owner 0.9 — bound final-plan lineage and shipping gates
SCALABLE_VERSION: Final = "3.0.0"  # owner 4.7 — stowage.solver.scalable; wave 2: securing screen, deadlines
RESEQUENCER_VERSION: Final = "2.0"  # owner D75 — stowage.planner_resequence; in versions only with rule-freeze-v1 on


YARD_SIMULATOR_VERSION: Final = "3.0.0"  # D33 current fleet cap also applies to legacy templates
CARGO_GENERATOR_VERSION: Final = "7.0.0"  # APPROXIMATION: D60 conditional gross-mass tail truncation, CSC plates unchanged
RANDOM_YARD_VERSION: Final = "8.0.0"  # D61 exact gross-kg background stack order, including solver residuals
SIMULATED_INSTANCES_VERSION: Final = "10.0.0"  # D61 exact gross-kg background stack order, including solver residuals


def component_versions() -> dict[str, str]:
    """All component versions plus the rule-catalogue version (if the catalogue is importable)."""
    versions = {
        "schema": SCHEMA_VERSION,
        "move_engine": MOVE_ENGINE_VERSION,
        "checker": CHECKER_VERSION,
        "feasibility": FEASIBILITY_VERSION,
        "linear_model": LINEAR_MODEL_VERSION,
        "ballast": BALLAST_VERSION,
        "action_space": ACTION_SPACE_VERSION,
        "infrastructure": INFRASTRUCTURE_VERSION,
        "solver.scalable": SCALABLE_VERSION,
    }
    versions["rule_catalogue"] = _catalogue_version()
    # The D75 resequencer runs only with rule-freeze-v1 on; switch off keeps the legacy identity unchanged.
    try:
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        if rule_freeze_v1_enabled():
            versions["resequencer"] = RESEQUENCER_VERSION
    except (OSError, ModuleNotFoundError):
        pass
    return versions


def _catalogue_version() -> str:
    try:
        from stowage.rules.catalogue import catalogue_snapshot
        return catalogue_snapshot().content_sha256
    except (OSError, ModuleNotFoundError):
        return "unknown"
