"""Bound Spark sidecars: prefetched explicitly, replay-validated without network.

A cache is explanation evidence, never a checker approval. Every read verifies
the complete current instance, plan and report, then runs the current grounding
validator through PlanExplainer with an in-memory response client.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

from app.validation import report_validation
from llm.spark.client import MODEL, SparkClient
from llm.spark.explain import EXPLAINER_VERSION, PlanExplainer, explanation_facts, template_explanation

FORMAT_VERSION = 1
MAX_SIDECAR_BYTES = 2_000_000


def explanation_sidecar_path(plan_path: str | Path) -> Path:
    path = Path(plan_path)
    return path.with_name(path.name + '.spark-explanation.json')


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value.model_dump(mode='json'), sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _bindings(instance, plan, report) -> dict:
    return {name: _digest(value) for name, value in
            (('instance_sha256', instance), ('plan_sha256', plan), ('report_sha256', report))}


class _RecordedResponse:
    def __init__(self, text):
        self.text = text

    def complete(self, *args, **kwargs):
        return SimpleNamespace(text=self.text)


class _CaptureResponse:
    def __init__(self, client):
        self.client, self.text = client, None

    def complete(self, *args, **kwargs):
        client = self.client if self.client is not None else SparkClient()
        response = client.complete(*args, **kwargs)
        self.text = response.text
        return response


def _safe_spark(text, instance, plan, report) -> bool:
    if not isinstance(text, str) or not text.startswith('[Muse Spark '):
        return False
    incomplete = not report_validation(report, instance=instance, plan=plan)['validated']
    # Older explainer versions printed a pass before their stub/not-run warning.
    # Such cached wording stays hidden until the authoritative verdict is fixed.
    return not (incomplete and re.search(r'Checker:\s*PASS\b', text))


def prefetch_explanation(instance, plan, report, *, explainer: PlanExplainer,
                         output_path: str | Path, generated_at: str | None = None) -> dict:
    """Explicit offline preparation step; its supplied client may make paid calls.

    The page never invokes this function. The normal Spark client retains its
    closed credit gate. Tests supply only recorded-response clients.
    """
    date = generated_at or datetime.now(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(date)
    if parsed.tzinfo is None:
        raise ValueError('Prefetch date must include a timezone')
    capture = _CaptureResponse(explainer.client)
    run = PlanExplainer(client=capture, use_api=explainer.use_api)
    text = run.explain(instance, plan, report)
    accepted = _safe_spark(text, instance, plan, report)
    problems = list(run.last_rejection)
    if not accepted and not problems:
        problems = ['No validated Spark explanation was returned']
    artifact = dict(format_version=FORMAT_VERSION, model=MODEL,
        explainer_version=EXPLAINER_VERSION, generated_at=date,
        bindings=_bindings(instance, plan, report), response_text=capture.text,
        text=text, validator={'accepted': accepted, 'problems': problems})
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(artifact, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)
    return artifact


def recorded_selection_problem(entry, instance, plan, report, baseline) -> str | None:
    """Why an explain-cache entry cannot be shown against the current reports, or None.

    The stage gate first checks both complete report digests. Statements and text are re-rendered here
    from the current reports of both plans and must match exactly. ``complete`` is served by the in-memory
    recorded answer; nothing reaches the network.
    """
    facts = explanation_facts(instance, plan, report, baseline=baseline)
    if facts['statements'] != entry.get('statements') or facts['verdict'] != entry.get('verdict'):
        return 'cached statements differ from the current reports of this plan and its baseline'
    if entry.get('mode') == 'spark':
        raw = (entry.get('spark') or {}).get('raw')
        if not isinstance(raw, str) or (entry.get('spark') or {}).get('model') != MODEL:
            return 'cached Spark answer or model is not the current one'
        run = PlanExplainer(client=_RecordedResponse(raw))
        text = run.explain(instance, plan, report, baseline=baseline)
        if run.last_rejection or not _safe_spark(text, instance, plan, report):
            return 'cached Spark selection no longer validates'
    elif entry.get('mode') == 'template':
        text = template_explanation(instance, plan, report, baseline=baseline)
    else:
        return 'cached explanation mode is unknown'
    return None if text == entry.get('text') else 'cached text differs from its re-rendering'


def load_stored_explanation(path: str | Path, instance, plan, report) -> dict | None:
    """Return revalidated prose, otherwise None for the caller's template fallback."""
    try:
        path = Path(path)
        if path.stat().st_size > MAX_SIDECAR_BYTES:
            return None
        artifact = json.loads(path.read_text())
        if not isinstance(artifact, dict):
            return None
        if (artifact.get('format_version') != FORMAT_VERSION or artifact.get('model') != MODEL or
                artifact.get('explainer_version') != EXPLAINER_VERSION or
                artifact.get('bindings') != _bindings(instance, plan, report)):
            return None
        if datetime.fromisoformat(artifact['generated_at']).tzinfo is None:
            return None
        if artifact.get('validator') != {'accepted': True, 'problems': []}:
            return None
        raw = artifact.get('response_text')
        if not isinstance(raw, str):
            return None
        # This injected response client cannot perform network requests. The
        # current explainer owns validation and the deterministic verdict text.
        run = PlanExplainer(client=_RecordedResponse(raw))
        text = run.explain(instance, plan, report)
        if run.last_rejection or not _safe_spark(text, instance, plan, report) or text != artifact.get('text'):
            return None
        return {'text': text, 'model': MODEL, 'generated_at': artifact['generated_at'],
                'validator': {'accepted': True, 'problems': []}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        return None
