"""Checker: the single source of truth (plan 4.1; parts 2.4, 3.1, 3.2, 3.3). Frozen API (plan 0.9).

Entry point: ``check(instance, plan) -> CheckReport``. Parts (each its own module and owner):
    ``rules``       static stowage rules (2.4)          ``check_static_rules``
    ``stability``   intact stability, exact (3.1)        ``check_stability``
    ``strength``    longitudinal strength, exact (3.2)   ``check_strength``
    ``visibility``  SOLAS V/22 (3.3)                     ``check_visibility``
    ``lashing``     lashing forces (3.3, D2)             ``check_lashing``

D57: stability/strength/visibility/lashing/instructions aggregate departure,
mid-voyage and arrival evidence. ``condition:<id>:<part>`` entries retain each
sample's identity, phase, fraction and results; static cargo rules run once.
``voyage`` records declaration/derivation failures and scope notes.
"""

from stowage.checker.full import check, departure_condition, report_matches_inputs
from stowage.checker.lashing import check_lashing
from stowage.checker.rules import check_static_rules
from stowage.checker.stability import check_stability
from stowage.checker.strength import check_strength
from stowage.checker.visibility import check_visibility
from stowage.versions import CHECKER_VERSION

__all__ = [
    "CHECKER_VERSION",
    "check",
    "check_lashing",
    "check_stability",
    "check_static_rules",
    "check_strength",
    "check_visibility",
    "departure_condition",
    "report_matches_inputs",
]
