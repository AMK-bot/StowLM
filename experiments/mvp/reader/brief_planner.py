"""Always-read standing preferences and hard voyage instructions, then Frontier.

This is the D220 public entry point. Even an empty brief visits the parser.
Rejected sentences are visible in evidence and never acquire a guessed meaning.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
import json

from llm.instructions.apply import NotApplicable
from llm.instructions.schema import Refusal
from experiments.mvp.reader.constraints import InfeasibleInstruction
from experiments.mvp.reader.schema import OnePortPerHatch, answer_envelope, context_from_instance, validate_answer
from experiments.mvp.reader.pipeline import compile_constraint
from experiments.mvp.reader.frontier_driver import plan_and_check


@dataclass
class VoyageBrief:
    standing: str = 'One port per hatch.'
    voyage: tuple[str, ...] = ()


@dataclass
class Intersection:
    instance: object
    parts: list

    def forbids(self, box, slot):
        return next((reason for part in self.parts if (reason := part.forbids(box, slot))), None)

    def violations(self, plan):
        return [reason for part in self.parts for reason in part.violations(plan)]


def read_brief(instance, brief, *, parser):
    """Parse each sentence; one empty standing sentence still calls the reader.

    Standing one_port_per_hatch enables a start, never a hard allocation. Other
    accepted standing restrictions are explicitly recorded as unsupported soft
    preferences. Voyage constraints are never silently demoted to preferences.
    """
    if isinstance(brief, str):
        brief = VoyageBrief(standing=brief)
    context = context_from_instance(instance)
    records, hard, blocked = [], [], False
    for role, sentences in (('standing', brief.standing.splitlines() or ['']), ('voyage', brief.voyage)):
        for sentence in sentences:
            start = perf_counter()
            row = dict(role=role, sentence=sentence, binds=False)
            try:
                answer, evidence = parser.parse(sentence, context)
                row['parser'] = evidence
                # Do not trust third-party parser implementations to have validated.
                answer = validate_answer(json.dumps(answer_envelope(answer)), context)
                row['parsed'] = answer_envelope(answer)
                if isinstance(answer, Refusal):
                    row['status'] = 'refused'
                elif role == 'standing':
                    if isinstance(answer, OnePortPerHatch):
                        blocked = True
                        row['status'] = 'preference_enabled'
                    else:
                        row['status'] = 'unsupported_preference'
                else:
                    hard.append(answer)
                    row.update(status='accepted', binds=True)
            except ValueError as error:
                row.update(status='malformed', parsed=None, error_type=type(error).__name__)
                row['parser'] = getattr(error, 'evidence', row.get('parser', {}))
            row['wall_s'] = perf_counter() - start
            records.append(row)
    return dict(sentences=records, port_blocked_start=blocked,
                reader_required=True, standing_is_preference=True), hard


def execute_brief(instance, parsed, hard, *, search_s=60., finalize_s=300., seed=0,
                  workers=1, constructor_s=120.):
    """Execute a previously read brief; only read_brief produces this evidence."""
    if parsed.get('reader_required') is not True or not parsed.get('sentences'):
        raise ValueError('A reader-produced brief is required before planning')
    started = perf_counter()
    record = dict(parsed, repaired=False, passed=False, claim_scope='parsed_constraint_execution',
                  sentence_compliance_verified=False, compilation=[])
    parts = []
    try:
        for answer in hard:
            compiled, evidence = compile_constraint(instance, answer, workers=workers, seed=seed)
            record['compilation'].append(evidence)
            # Necessary geometry screens use only the parsed constraint. They
            # prove failure, never replace an instruction with an easier one.
            from experiments.mvp.reader.select_binding import per_box_slot_screen
            if hasattr(compiled, 'capacity_shortfall'):
                shortfall = compiled.capacity_shortfall()
                if shortfall:
                    raise InfeasibleInstruction(shortfall)
            no_slots = per_box_slot_screen(instance, compiled)
            if no_slots:
                raise InfeasibleInstruction('no instruction-allowed physical footprint: ' + ', '.join(no_slots))
            parts.append(compiled)
    except (InfeasibleInstruction, NotApplicable) as error:
        record.update(status='constraint_unavailable', error=str(error), wall_s=perf_counter()-started)
        return None, record
    constrained = Intersection(instance, parts) if parts else None
    result, details = plan_and_check(instance, constrained, search_s=search_s, finalize_s=finalize_s,
                                     seed=seed, workers=workers, constructor_s=constructor_s,
                                     enable_port_blocked=parsed['port_blocked_start'])
    record.update(planner=details, passed=result is not None,
                  status='checked_plan' if result is not None else 'no_checked_plan',
                  wall_s=perf_counter()-started)
    return result, record


def plan_brief(instance, brief, *, parser, **kwargs):
    """Plain-English brief -> reader -> constraints -> Frontier -> full checker.

    No parser-free branch, repair, or unconstrained retry exists here. A rejected
    sentence does not bind and remains in evidence alongside any checked result.
    """
    started = perf_counter()
    parsed, hard = read_brief(instance, brief, parser=parser)
    result, evidence = execute_brief(instance, parsed, hard, **kwargs)
    evidence['wall_s'] = perf_counter()-started
    return result, evidence
