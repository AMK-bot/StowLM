"""On-device proposal adapter; task 1.3's deterministic validator is mandatory.

Injected models are local callables: model(text, *, context, messages, schema).
Injected validators take (raw, documents, base_instance), exactly the task 1.3
validate_reading protocol, and return its checked dict. Neither hook has a
network fallback. The eight extracted fields come only from the fragment;
other declared Container fields may come from the matching base container.
"""
from __future__ import annotations

import importlib
from copy import deepcopy
from pathlib import Path
import json
import time

from pydantic import ValidationError

from experiments.mvp.pipeline.contract.schema import IntakeResult, Mode, Question, StageRecord, sha256_file, sha256_json
from .schema import ROW_KEYS, compact_json, context_from_instance, context_json_schema, prompt_messages, validate_answer


def _validator():
    # This import remains lazy so preparation/tests need no task 1.3 installation.
    return importlib.import_module('experiments.mvp.pipeline.intake.validation').validate_reading


def _base(instance):
    if isinstance(instance, (str, Path)):
        return json.loads(Path(instance).read_text())
    if hasattr(instance, 'model_dump'):
        return instance.model_dump(mode='json')
    if not isinstance(instance, dict):
        raise ValueError('base_instance must be JSON-able')
    return instance


def validation_input(answer, text, base):
    """Supplement only non-extraction facts, with distinct base-instance evidence.

    Validation is scoped to this fragment's one row. The full vessel, yard and
    container catalogue stay intact for the shared validator's physical checks.
    No supplement is shown to the model or used to choose the decode vocabulary.
    """
    row_id = answer['id']
    scoped = deepcopy(base)
    scoped['load_list'] = [row_id] if row_id in base.get('load_list', []) else []
    matches = [row for row in base.get('containers', []) if row.get('id') == row_id]
    supplement = {}
    if len(matches) == 1:
        supplement = {key: deepcopy(value) for key, value in matches[0].items() if key not in ROW_KEYS}
        declared_dg, original_dg = answer['dg'], matches[0].get('dg')
        if (isinstance(declared_dg, dict) and isinstance(original_dg, dict)
            and all(original_dg.get(key) == declared_dg[key] for key in ('un_number', 'imdg_class'))):
            supplement['dg'] = {key: deepcopy(value) for key, value in original_dg.items()
                                if key not in ('un_number', 'imdg_class')}
    documents = [{'id': 'private-text', 'text': text, 'synthetic': True}]
    observations = []
    # text_provenance authenticates only a whole JSON line carrying this row's id.
    base_line = compact_json({'id': row_id, **supplement})
    for values, document, quote in ((answer, 'private-text', text),
                                    (supplement, 'base-container', base_line)):
        if document == 'base-container' and values:
            documents.append({'id': document, 'text': quote, 'synthetic': True,
                              'origin': 'base_instance', 'base_instance_sha256': sha256_json(base)})
        for field, value in values.items():
            parts = [(f'dg.{key}', item) for key, item in value.items()] if field == 'dg' and isinstance(value, dict) else [(field, value)]
            observations.extend({'row_id': row_id, 'field': key, 'value': item,
                                  'document': document, 'quote': quote} for key, item in parts)
    raw = {'observations': observations, 'duplicates': [], 'metadata': {'model': 'stowlm-intake'}}
    return raw, documents, scoped


class PrivateIntake:
    def __init__(self, model=None, *, validator=None, adapter_sha256=None):
        self.model = model
        self.validator = validator
        self.adapter_sha256 = adapter_sha256

    def read_text(self, text, *, base_instance) -> IntakeResult:
        started = time.monotonic()
        base = _base(base_instance)
        answer, messages = None, []
        reason = None
        try:
            if not isinstance(text, str) or not text.strip():
                raise ValueError('empty fragment')
            context = context_from_instance(base)
            messages = prompt_messages(text, context)
            if self.model is None:
                reason = 'MODEL_UNAVAILABLE'
            else:
                answer = validate_answer(self.model(text, context=context, messages=messages,
                                                    schema=context_json_schema(context)), context)
                if 'refusal' in answer:
                    reason = answer['refusal']['code']
        except Exception:
            reason = 'INVALID_OUTPUT'
        rows, sources, questions = [], [], []
        if reason is None:
            try:
                validator = self.validator or _validator()
            except (ImportError, AttributeError):
                reason = 'VALIDATOR_UNAVAILABLE'
            else:
                raw, documents, scoped = validation_input(answer, text, base)
                try:
                    checked = validator(raw, documents, scoped)
                    rows = checked['rows']
                    questions = [Question.model_validate(q) for q in checked['questions']]
                    sources = checked['field_sources']
                    if not rows and not questions:
                        reason = 'VALIDATOR_REJECTED'
                except ValidationError as error:
                    rows, sources = [], []
                    questions = []
                    for index, issue in enumerate(error.errors(include_url=False)):
                        location = issue['loc']
                        path = location[2:] if location[:1] == ('containers',) else location
                        field = '.'.join(str(part) for part in path) or 'base_instance'
                        questions.append(Question(id=f'private-base-{index}', field=field,
                            reason='BASE_FIELD_UNAVAILABLE',
                            prompt=f'The base instance cannot supply {field}: {issue["msg"]}'))
                except Exception:
                    # The validator may be unavailable mid-deployment; never bypass it.
                    rows, sources, reason = [], [], 'VALIDATOR_REJECTED'
        if reason:
            rows, sources = [], []
            questions = [Question(id='private-intake-refusal', row=None, field=None,
                                  prompt='Provide an explicit corrected fragment or use the reviewed intake flow.',
                                  reason=reason)]
        status = 'needs_answers' if questions else 'complete'
        payload = {'status': status, 'rows': rows, 'questions': [q.model_dump(mode='json') for q in questions], 'field_sources': sources}
        record = StageRecord(stage='intake_private', mode=Mode.live,
                             input_sha256=sha256_json({'text': text, 'base_instance': base}),
                             output_sha256=sha256_json(payload), model='stowlm-intake',
                             adapter_sha256=self.adapter_sha256 or getattr(self.model, 'adapter_sha256', None),
                             prompt_sha256=sha256_json(messages),
                             source_sha256=sha256_file(Path(__file__)),
                             wall_s=time.monotonic() - started,
                             notes='Local model proposal; shared task 1.3 validator. '
                                   'Non-extraction fields sourced from matching base container; single-row scope. '
                                   + (reason or ''))
        return IntakeResult(**payload, records=[record])


class LocalGlimmerModel:
    """Explicit Claude/GPU runtime hook; never loads MLX before gate verification.

    Construct only after the one sealed score. Each call verifies the adoption
    record and complete frozen inference artifacts before importing model code.
    PrivateIntake() itself remains unloaded by default.
    """
    def __init__(self, adapter, *, results=None, model_path=None):
        self.adapter = Path(adapter)
        self.results = Path(results) if results else Path(__file__).parent / 'results/sealed-final'
        self.model_path = model_path
        self.adapter_sha256 = None

    def __call__(self, text, *, context, messages, schema):
        from .artifacts import verify_adoption
        adoption = verify_adoption(self.adapter, self.results, model_path=self.model_path)
        self.adapter_sha256 = adoption['adapter_sha256']
        from .evaluate import run_glimmer
        result = run_glimmer([{'id': 'private-text', 'text': text, 'context': context}],
                             model_path=self.model_path, adapter=self.adapter)[0]
        if result.get('decoding_error'):
            raise ValueError('local schema decoding failed')
        return result['prediction']
