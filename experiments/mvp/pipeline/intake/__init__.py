"""Synthetic document intake: Spark proposes; deterministic validation gates rows."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

# Pytest imports this package before its local conftest; repository experiment
# namespaces must resolve under both the pytest and python -m entry points.
_root = str(Path(__file__).resolve().parents[4])
if _root not in sys.path:
    sys.path.insert(0, _root)

from experiments.mvp.pipeline.contract.schema import (
    IntakeResult, Mode, Question, StageRecord, sha256_file, sha256_json,
)
from stowage.model.instance import Instance


def _documents(documents):
    out = []
    for i, document in enumerate(documents):
        item = dict(document) if isinstance(document, dict) else {'path': str(document)}
        item.setdefault('id', f'document-{i + 1}')
        if 'content' in item and 'text' not in item:
            item['text'] = item['content']
        if item.get('path'):
            path = Path(item['path'])
            item['sha256'] = sha256_file(path)
            if path.suffix.lower() in {'.txt', '.csv', '.eml', '.json'} and 'text' not in item:
                item['text'] = path.read_text()
        out.append(item)
    if len({item['id'] for item in out}) != len(out):
        raise ValueError('Document ids must be unique')
    return out


def _base(base_instance):
    if isinstance(base_instance, (str, Path)):
        return Instance.model_validate_json(Path(base_instance).read_text()).model_dump(mode='json')
    if isinstance(base_instance, Instance):
        return base_instance.model_dump(mode='json')
    return Instance.model_validate(base_instance).model_dump(mode='json')


def _finish(raw, documents, base, *, records=(), mode=Mode.live, wall_s=0., cost_usd=0., answers=None, planner_confirmations=None, duplicate_resolutions=()):
    from .validation import validate_reading
    checked = validate_reading(raw, documents, base, planner_confirmations=planner_confirmations,
                               duplicate_resolutions=duplicate_resolutions)
    questions = [Question.model_validate(q) for q in checked['questions']]
    rows = checked['rows']
    # A JSON receipt keeps the original proposals and limit snapshot available
    # after a round trip through the shared IntakeResult wire shape.
    context = {'base_instance': base, 'documents': documents, 'raw': raw,
               'row_ids': checked['row_ids'], 'planner_confirmations': planner_confirmations or {}, 'duplicate_resolutions': list(duplicate_resolutions)}
    note = {'intake_context': context, 'findings': checked.get('findings', []), 'answers': answers or {}}
    record = StageRecord(
        stage='intake', mode=mode,
        input_sha256=sha256_json({'documents': documents, 'base_instance': base, 'answers': answers or {}}),
        output_sha256=sha256_json({'rows': rows, 'questions': [q.model_dump(mode='json') for q in questions],
                                   'field_sources': checked['field_sources']}),
        model=raw.get('metadata', {}).get('model', 'muse-spark-1.3'),
        prompt_sha256=raw.get('metadata', {}).get('prompt_sha256'),
        source_sha256=sha256_json({name: sha256_file(Path(__file__).with_name(name))
                                  for name in ('__init__.py', 'validation.py', 'provenance.py', 'text_provenance.py', 'spark_reader.py')}),
        wall_s=wall_s, cost_usd=cost_usd,
        notes=json.dumps(note, ensure_ascii=False, separators=(',', ':')),
    )
    return IntakeResult(status='needs_answers' if questions else 'complete', rows=rows,
                        questions=questions, field_sources=checked['field_sources'],
                        records=[*records, record])


def read_documents(documents, *, base_instance, client=None) -> IntakeResult:
    """Read text/CSV/images; return partial rows and explicit blocking questions.

    ``client=None`` makes a paid Spark call. Pass ``RecordedClient`` to replay.
    The base supplies identities and limits, never replacement cargo facts.
    """
    from .spark_reader import read, RecordedClient
    legacy = isinstance(client, RecordedClient) and client.recording.get('schema_version') != 'stowlm.intake-interpretation/7'
    if not legacy:
        from .interpretation import read as interpret,SchemaRejection
        from .realistic import finish
        docs, base = _documents(documents), _base(base_instance)
        try:
            raw=interpret(docs,client=client)
        except SchemaRejection as error:
            if error.metadata.get('policy_version',7)<8:
                raise
            from .realistic import rejected
            return rejected(error,docs,base)
        return finish(raw,docs,base)
    started = time.monotonic()
    docs, base = _documents(documents), _base(base_instance)
    raw = read(docs, client=client)
    meta = raw.get('metadata', {})
    mode = Mode(meta.get('mode', 'live'))
    return _finish(raw, docs, base, mode=mode, wall_s=time.monotonic() - started,
                   cost_usd=float(meta.get('cost_usd', 0.)))


def answer_questions(result, answers) -> IntakeResult:
    """Apply explicit planner values by question id, then run every check again.

    Answers are ``{question_id: value}`` or ``{question_id: {'value': value,
    'quote': 'planner evidence'}}``. Duplicate ids need the value ``keep_one``.
    An explicit ``field`` may name the questioned field's parent, for example
    correcting an unsupported DG declaration to declared ``dg=None``.
    Missing answers remain open. Invalid answers create fresh questions.
    """
    started = time.monotonic()
    result = IntakeResult.model_validate(result)
    if not isinstance(answers, dict):
        raise ValueError('Answers must map question ids to values')
    known = {q.id: q for q in result.questions}
    if set(answers) - set(known):
        raise ValueError('An answer names an unknown question')
    context = None
    for record in reversed(result.records):
        try:
            context = json.loads(record.notes).get('intake_context')
        except (ValueError, AttributeError):
            continue
        if context:
            break
    if context is None:
        raise ValueError('The intake validation receipt is missing')
    if context.get('version') == 7:
        from .realistic import answer
        return answer(result, answers, context)
    raw, docs = context['raw'], context['documents']
    confirmations = context.get('planner_confirmations', {})
    resolutions = context.get('duplicate_resolutions', [])
    # Legacy raw resolutions were model-controlled and cannot grant authority.
    raw.pop('duplicate_resolutions', None)

    def answer_document(qid):
        name = f'planner-answer-{len(result.records)}-{qid}'
        while any(d.get('id') == name for d in docs):
            name += '-confirmed'
        return name
    row_ids = context.get('row_ids') or sorted(
        {o['row_id'] for o in raw.get('observations', []) if isinstance(o, dict) and o.get('row_id')}
        | set(context['base_instance']['load_list'])
        | {d['row_id'] for d in raw.get('duplicates', []) if isinstance(d, dict) and d.get('row_id')})
    identity_map = {}
    for qid, answer in answers.items():
        q = known[qid]
        field = answer.get('field', q.field) if isinstance(answer, dict) and 'value' in answer else q.field
        if field != q.field and (not isinstance(field, str) or not isinstance(q.field, str)
                                or not q.field.startswith(field + '.')):
            raise ValueError('A correction field must be the questioned field or its parent')
        if field != q.field:
            q = q.model_copy(update={'field': field})
        if q.row is None or q.row >= len(result.rows):
            raise ValueError('This question requires a corrected document')
        original_id = row_ids[q.row]
        row_id = identity_map.get(original_id, original_id)
        value = answer.get('value') if isinstance(answer, dict) and 'value' in answer else answer
        quote = answer.get('quote', '') if isinstance(answer, dict) and 'value' in answer else ''
        if q.field is None:
            if q.reason != 'missing_row' or not isinstance(value, dict):
                raise ValueError('This question requires a corrected document')
            document = answer_document(qid)
            declaration = f'Planner supplied row: {json.dumps(value, ensure_ascii=False)}'
            evidence = quote + '\n' + declaration if quote else declaration
            docs.append({'id': document, 'text': evidence, 'synthetic': True, 'origin': 'planner_answer'})
            confirmations[document] = {'row_id': row_id, 'fields': value}
            raw['observations'] = [o for o in raw.get('observations', []) if o.get('row_id') != row_id]
            raw['observations'].extend({'row_id': row_id, 'field': field, 'value': child,
                                        'document': document, 'quote': evidence} for field, child in value.items())
            continue
        if q.reason == 'duplicate_id' or q.field == '_duplicate':
            if value != 'keep_one':
                raise ValueError('Confirm duplicate removal with keep_one')
            raw['duplicates'] = [d for d in raw.get('duplicates', []) if d.get('row_id') != row_id]
            resolutions.append({'row_id': row_id, 'document': '*', 'action': 'keep_one'})
            continue
        remaining = []
        for observation in raw.get('observations', []):
            field = observation.get('field', '')
            if observation.get('row_id') != row_id:
                remaining.append(observation)
            elif field == q.field or field.startswith(q.field + '.'):
                continue
            elif q.field.startswith(field + '.') and isinstance(observation.get('value'), (dict, list)):
                # Split only the branch being corrected, preserving other
                # declared objects/lists and their source evidence verbatim.
                def unaffected(value, prefix):
                    if prefix == q.field:
                        return
                    if not q.field.startswith(prefix + '.'):
                        yield prefix, value
                    elif isinstance(value, (dict, list)):
                        items = value.items() if isinstance(value, dict) else enumerate(value)
                        for key, child in items:
                            yield from unaffected(child, prefix + '.' + str(key))
                for name, child in unaffected(observation['value'], field):
                    remaining.append({**observation, 'field': name, 'value': child})
            else:
                remaining.append(observation)
        raw['observations'] = remaining
        if q.field == 'id' and isinstance(value, str) and value != row_id:
            for observation in raw['observations']:
                if observation.get('row_id') == row_id:
                    observation['row_id'] = value
            for item in [*raw.get('duplicates', []), *resolutions]:
                if item.get('row_id') == row_id:
                    item['row_id'] = value
            for confirmation in confirmations.values():
                if confirmation.get('row_id') == row_id:
                    confirmation['row_id'] = value
            identity_map[original_id] = value
            row_id = value
        document = answer_document(qid)
        declaration = f'Planner confirmed {row_id}: {json.dumps({q.field: value}, ensure_ascii=False)}'
        evidence = quote + '\n' + declaration if quote else declaration
        docs.append({'id': document, 'text': evidence, 'synthetic': True, 'origin': 'planner_answer'})
        confirmations[document] = {'row_id': row_id, 'fields': {q.field: value}}
        raw['observations'].append({'row_id': row_id, 'field': q.field, 'value': value,
                                    'document': document, 'quote': evidence})
    return _finish(raw, docs, context['base_instance'], records=result.records,
                   mode=result.records[0].mode, wall_s=time.monotonic() - started, answers=answers,
                   planner_confirmations=confirmations, duplicate_resolutions=resolutions)


from .realistic import parser_baseline, route_special_stowage, accept_reader_confirmation

__all__ = ['read_documents', 'answer_questions', 'IntakeResult', 'parser_baseline', 'route_special_stowage', 'accept_reader_confirmation']
