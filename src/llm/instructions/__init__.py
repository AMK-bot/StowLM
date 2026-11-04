"""Instructions reader (plan v4 task 5.10; v3 19.2/21.2): a planner's sentence -> tighten-only constraint.

    from llm.instructions import parse, apply, replan
    outcome = parse("Crane 1 is down, nothing goes into bay 01")    # Constraint | Refusal (Muse Spark)
    constrained = apply(instance, outcome)                           # forbidden placements; instance unchanged
    run = replan(constrained)                                        # solver + full checker + honoured check

``replan(..., planner_factory=...)`` is the seam for task 9.3: StowLM's mask consults ``constrained.forbids``.
"""

from llm.instructions.apply import ConstrainedInstance, NotApplicable, apply, describe
from llm.instructions.parser import JobGuard, parse, parse_detailed
from llm.instructions.replan import Replan, constraint_rule, replan
from llm.instructions.schema import (SCHEMA_VERSION, CloseBay, DgKeepAway, MaxTiers, Refusal, schema_json,
                                     validate_answer)

__all__ = ["SCHEMA_VERSION", "CloseBay", "MaxTiers", "DgKeepAway", "Refusal", "ConstrainedInstance", "NotApplicable",
           "JobGuard", "Replan", "apply", "constraint_rule", "describe", "parse", "parse_detailed", "replan",
           "schema_json", "validate_answer"]
