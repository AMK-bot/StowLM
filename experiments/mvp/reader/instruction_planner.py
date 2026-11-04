"""Public sentence-to-frontier entry point; the parser is the replaceable seam."""
from __future__ import annotations

from time import perf_counter

from llm.instructions.schema import Refusal
from llm.instructions.apply import NotApplicable
from experiments.mvp.reader.constraints import InfeasibleInstruction
from experiments.mvp.reader.schema import answer_envelope, context_from_instance
from experiments.mvp.reader.pipeline import compile_constraint
from experiments.mvp.reader.frontier_driver import plan_and_check


def plan_sentence(instance, sentence, *, parser, search_s=60., finalize_s=300., seed=0, workers=1):
    """Return (checked result or None, evidence), deriving all constraint details.

    ``parser.parse(sentence, context)`` returns a validated reader answer plus
    provider evidence. Supply SparkParser today or a Glimmer parser later.
    Refusals stop before search. No manual hatch mapping or expected label enters.
    This entry point does not repair or retry failed plans.
    """
    started = perf_counter()
    answer, provider = parser.parse(sentence, context_from_instance(instance))
    record = {'sentence': sentence, 'parsed': answer_envelope(answer), 'parser': provider,
              'repaired': False, 'sentence_compliance_verified': False,
              'claim_scope': 'parsed_constraint_execution'}
    if isinstance(answer, Refusal):
        record.update(passed=False, refused=True, status='refused', wall_s=perf_counter()-started)
        return None, record
    try:
        constrained, allocation = compile_constraint(instance, answer, workers=workers, seed=seed)
    except (InfeasibleInstruction, NotApplicable) as exc:
        record.update(passed=False, status='constraint_unavailable', error=str(exc),
                      wall_s=perf_counter()-started)
        return None, record
    result, details = plan_and_check(instance, constrained, search_s=search_s,
                                     finalize_s=finalize_s, seed=seed, workers=workers)
    record.update(compilation=allocation, planner=details, passed=result is not None,
                  status='checked_plan' if result is not None else 'no_checked_plan',
                  wall_s=perf_counter()-started)
    return result, record
