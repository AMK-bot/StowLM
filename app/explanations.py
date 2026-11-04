"""Select current, bound offline explanation evidence; never make a model call."""
from __future__ import annotations
import re

from app.validation import report_validation


def explanation_for_replay(replay, *, source_path=None):
    from llm.spark.explain import template_explanation
    from stowage.checker import report_matches_inputs
    from stowage.model import Instance, MovePlan, CheckReport
    from app.stored_explanations import explanation_sidecar_path, load_stored_explanation
    empty = {'source': 'unavailable', 'text': None, 'model': None,
             'generated_at': None, 'validator': None}
    try:
        instance = Instance.model_validate(replay['instance'])
        plan = MovePlan.model_validate(replay['plan'])
        report = CheckReport.model_validate(replay['checker']['report'])
        if not report_matches_inputs(report, instance, plan):
            return empty
    except (ValueError, TypeError, KeyError):
        return empty
    validation = report_validation(report, instance=instance, plan=plan,
                                   planner_result=replay.get('planner_result'))
    def unsafe(text):
        return validation['status'] == 'NOT VALIDATED' and re.search(r'Checker:\s*PASS\b', text, re.I)
    if source_path:
        stored = load_stored_explanation(explanation_sidecar_path(source_path), instance, plan, report)
        if stored and not unsafe(stored['text']):
            return {**stored, 'source': 'spark'}
    try:
        text = template_explanation(instance, plan, report)
        # Preserve the explainer's returned verdict verbatim. Until the upstream
        # fix lands, suppress an older false PASS instead of rewriting its prose.
        if unsafe(text):
            text = 'NOT VALIDATED: explanation unavailable because its verdict does not match the displayed validation state.'
    except (ValueError, TypeError, KeyError):
        text = 'Deterministic explanation unavailable; consult the checker diagnostics.'
    return {**empty, 'source': 'template', 'text': text}
