"""Review a reader answer before planning, with proof-bound unhappy paths.

No alternative is applied here. The caller retains an impossible hard instruction
and returns no plan, or displays a refusal/clarification whose sentence does not
bind. Unresolved voyage readings block a new plan until the planner explicitly
corrects or removes the sentence. This per-sentence review is explanatory only; submit the whole brief through
public_brief for authenticated execution. Standing preferences remain optional.
"""
from __future__ import annotations
import json
from llm.instructions.schema import Refusal
from experiments.mvp.reader.pipeline import ParserOutputError
from experiments.mvp.reader.schema import OnePortPerHatch, answer_envelope, context_from_instance, validate_answer
from experiments.mvp.reader.refusal_evidence import build_refusal_evidence


def review_answer(instance, answer, *, parser_evidence, explainer, role='voyage',
                  stored_witness=None, rule_details=None, clarification_question=None):
    if role not in ('standing','voyage'):raise ValueError('Unknown brief role')
    answer=validate_answer(json.dumps(answer_envelope(answer)),context_from_instance(instance))
    record=dict(parsed=answer_envelope(answer),parser=parser_evidence,role=role,binds=False,
                no_planner_called=True,automatically_applied=False,alternative_applied=False)
    if not isinstance(answer,Refusal) and role=='standing':
        record.update(status='preference_enabled' if isinstance(answer,OnePortPerHatch) else 'unsupported_preference',
                      port_blocked_start=isinstance(answer,OnePortPerHatch),plan_allowed=True)
        return record
    if isinstance(answer,OnePortPerHatch):
        record.update(status='accepted',binds=True,plan_allowed=True,
                      proof_scope='hatch allocation compiler and full checker still required')
        return record
    if isinstance(answer,Refusal) and answer.code=='loosening' and rule_details is None:
        record.update(status='needs_rule_details',plan_allowed=role=='standing',
                      refusal='I cannot relax an existing rule.',question='Which existing rule were you asking to change?')
        if role=='voyage':
            record.update(requires_reading_resolution=True,
                          release_condition='explicit_sentence_correction_or_removal')
        return record
    proof=build_refusal_evidence(instance,answer,stored_witness=stored_witness,
                                rule_details=rule_details,clarification_question=clarification_question)
    record['proof']=proof
    if proof['status']=='screen_passed':
        record.update(status='accepted',binds=True,plan_allowed=True,
                      proof_scope='necessary static screen only; plan and full checker still required')
        return record
    text,evidence=explainer.explain(proof)
    record.update(status=proof['status'],explanation=text,explainer=evidence,
                  binds=not isinstance(answer,Refusal),
                  plan_allowed=isinstance(answer,Refusal) and role=='standing')
    if isinstance(answer,Refusal) and role=='voyage':
        record.update(requires_reading_resolution=True,
                      release_condition='explicit_sentence_correction_or_removal')
    # Rejected voyage sentences never bind, but their unresolved intent still
    # blocks release. Other accepted sentences cannot silently discard them.
    return record


def review_sentence(instance,sentence,*,parser,explainer,role='voyage',**kwargs):
    if role not in ('standing','voyage'):raise ValueError('Unknown brief role')
    try:
        answer,provider=parser.parse(sentence,context_from_instance(instance))
    except ParserOutputError as error:
        record=dict(sentence=sentence,status='malformed',role=role,binds=False,plan_allowed=role=='standing',
                    no_planner_called=True,parser=error.evidence,automatically_applied=False,
                    question='Please restate the restriction with the exact bay and any tier zone or distance.')
        if role=='voyage':
            record.update(requires_reading_resolution=True,
                          release_condition='explicit_sentence_correction_or_removal')
        return record
    return dict(sentence=sentence,**review_answer(instance,answer,parser_evidence=provider,
                                                explainer=explainer,role=role,**kwargs))
