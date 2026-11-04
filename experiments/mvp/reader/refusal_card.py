"""One-shot demo: live reading gates release of a recorded, proof-bound card."""
from __future__ import annotations
import json
from experiments.mvp.reader.refusal_demo import RUN, ROOT, verify
from experiments.mvp.reader.schema import canonicalize, context_from_instance
from experiments.mvp.reader.pipeline import ParserOutputError


def read_recorded_card(case_id,*,parser):
    from stowage.io import load_instance
    manifest=verify()
    case=next(c for c in manifest['cases'] if c['case_id']==case_id)
    record=json.loads((RUN/'examples'/f'{case_id}.json').read_text())
    from experiments.mvp.reader.refusal_explainer import proof_sha256, validate_explanation, render_explanation
    if record['case_id']!=case_id or not record.get('passed'):
        raise ValueError('Recorded refusal card is not a validated example')
    provider=record['explainer']
    if provider.get('validated') is not True or provider['proof_sha256']!=proof_sha256(record['proof']):
        raise ValueError('Recorded card proof does not match Spark evidence')
    value=validate_explanation(json.dumps(provider['generated_selection']),record['proof'])
    if render_explanation(value,record['proof'])!=record['explanation']:
        raise ValueError('Recorded explanation text changed')
    if canonicalize(record['proof']['parsed'])!=canonicalize(record['parsed']['parsed']):
        raise ValueError('Recorded proof and reading disagree')
    instance=load_instance(ROOT/case['input'])
    try:answer,provider=parser.parse(case['sentence'],context_from_instance(instance))
    except ParserOutputError as error:
        return dict(case_id=case_id,status='malformed',parser=error.evidence,
                    cached_explanation_released=False,question='Please restate the instruction.')
    if not record.get('passed') or canonicalize(answer)!=canonicalize(record['parsed']['parsed']):
        return dict(case_id=case_id,status='reading_mismatch',parser=provider,
                    parsed=canonicalize(answer),cached_explanation_released=False,
                    question='Please confirm the reading before continuing.')
    return dict(case_id=case_id,status=record['proof']['status'],parser=provider,parsed=canonicalize(answer),
                cached_explanation_released=True,explanation=record['explanation'],proof=record['proof'],
                explainer=record['explainer'],label='Muse Spark — recorded, bound to the shown proof',
                no_planner_called=True,alternative_applied=False)
