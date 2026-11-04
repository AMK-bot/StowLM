"""Score a frozen synthetic split; paid readings are recorded separately."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from stowage.model.instance import Instance

from . import answer_questions, read_documents
from .spark_reader import RecordedClient

HERE = Path(__file__).resolve().parent


def assemble_rows(base, rows):
    """Replicate the runner contract: replace cargo attributes, retain yard/ship."""
    base = Instance.model_validate(base)
    value = base.model_dump(mode='json')
    incoming = {row['id']: row for row in rows}
    if len(incoming) != len(rows):
        raise ValueError('Duplicate intake rows')
    value['containers'] = [incoming.get(c['id'], c) for c in value['containers'] if c['id'] not in incoming]
    value['containers'].extend(rows)
    value['load_list'] = sorted(incoming)
    return Instance.model_validate(value)


def _leaves(value, prefix=''):
    out = {}
    for key, item in value.items():
        field = f'{prefix}.{key}' if prefix else key
        if isinstance(item, dict) and item:
            out.update(_leaves(item, field))
        else:
            out[field] = item
    return out


def accuracy(result, truth):
    expected = {row['id']: row for row in truth}
    rows = {row.get('id'): row for row in result.rows}
    correct = total = silent_wrong = row_exact = accepted = 0
    for row_id, target in expected.items():
        actual = rows.get(row_id, {})
        blocked = {q.field for q in result.questions if q.row is not None and
                   q.row < len(result.rows) and result.rows[q.row].get('id') == row_id}
        target_fields, actual_fields = _leaves(target), _leaves(actual)
        row_exact += actual == target and not blocked
        for field, value in target_fields.items():
            total += 1
            is_blocked = any(f is None or f == field or field.startswith(f + '.') or f.startswith(field + '.') for f in blocked)
            if field in actual_fields and not is_blocked:
                accepted += 1
                good = actual_fields[field] == value
                correct += good
                silent_wrong += not good
        for field in actual_fields.keys() - target_fields.keys():
            if not any(f is None or f == field or field.startswith(f + '.') or f.startswith(field + '.') for f in blocked):
                silent_wrong += 1
    unexpected_rows = [r for r in rows if r not in expected]
    for row_id in unexpected_rows:
        index = next(i for i, row in enumerate(result.rows) if row.get('id') == row_id)
        if not any(q.row == index and q.field in (None, 'id') for q in result.questions):
            silent_wrong += 1
    return {'fields_correct': correct, 'fields_total': total, 'fields_accepted': accepted,
            'field_accuracy': correct / total if total else 0.,
            'rows_exact': int(row_exact), 'rows_total': len(expected),
            'row_exact_accuracy': row_exact / len(expected) if expected else 0.,
            'silent_wrong_values': int(silent_wrong)}


def score_case(case, *, raw_path=None, save=True):
    case = Path(case).resolve()
    manifest_path = case.parent / 'freeze.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    frozen_eval = (case.name.startswith('heldout') or case.parent.name.startswith('heldout')
                   or str(manifest.get('evaluation_split', '')).startswith('heldout'))
    raw_path = Path(raw_path or case / 'spark_raw.json')
    if frozen_eval and raw_path.resolve() != (case / 'spark_raw.json').resolve():
        raise ValueError('Held-out recording substitution is forbidden; use development scoring')
    if frozen_eval and save and (case / 'metrics.json').exists():
        raise ValueError('The held-out score is frozen; replay its receipts without rescoring')
    if frozen_eval:
        from .evaluate_v2 import verify_frozen
        verified = verify_frozen(case.parent)
        frozen_case = next((item for item in verified['cases'] if item['split'] == case.name), None)
        if frozen_case is None:
            raise ValueError('Case is absent from the held-out input freeze')
    expected = json.loads((case / 'expected.json').read_text())
    truth = expected['rows']
    base = json.loads((case / 'base_instance.json').read_text())
    documents = json.loads((case / 'documents.json').read_text())
    for document in documents:
        if document.get('path') and not Path(document['path']).is_absolute() and not Path(document['path']).exists():
            document['path'] = str(case / document['path'])
    if frozen_eval:
        from .spark_reader import document_content, extraction_prompt
        import hashlib
        for document in documents:
            if document.get('path'):
                path = Path(document['path']).resolve()
                pinned_paths = {(case / name).resolve() for name in frozen_case.get('files', {})}
                if path not in pinned_paths:
                    raise ValueError('A held-out document path is absent from the input freeze')
        raw = json.loads(raw_path.read_text())
        metadata = raw.get('metadata', {})
        if metadata.get('documents_sha256') != sha256_json(document_content(documents)):
            raise ValueError('The held-out recording does not bind these frozen documents')
        if metadata.get('prompt_sha256') != hashlib.sha256(extraction_prompt().encode()).hexdigest():
            raise ValueError('The held-out recording used a different prompt')
        response = json.loads((case / 'spark_response.json').read_text())
        if (not metadata.get('request_id') or response.get('request_id') != metadata['request_id']
                or not metadata.get('raw_text') or response.get('text') != metadata['raw_text']):
            raise ValueError('The held-out recording does not match its captured response')
        text = response['text'].strip()
        if text.startswith('```') and text.endswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        proposal = json.loads(text)
        proposal.setdefault('duplicates', [])
        proposal.setdefault('notes', [])
        if proposal != {key: value for key, value in raw.items() if key != 'metadata'}:
            raise ValueError('The held-out recording proposal changed after capture')
    started = time.monotonic()
    result = read_documents(documents, base_instance=base, client=RecordedClient(raw_path))
    initial = accuracy(result, truth)
    # Planner answers in fixture are ground truth, explicitly distinct from the model.
    by_id = {row['id']: row for row in truth}
    answers = {}
    for question in result.questions:
        if question.row is None:
            continue
        row_id = result.rows[question.row].get('id')
        if question.reason == 'missing_row' and row_id in by_id:
            answers[question.id] = {'value': by_id[row_id], 'quote': f'Synthetic planner supplies full row {row_id}'}
        elif question.reason == 'duplicate_id':
            answers[question.id] = 'keep_one'
        elif row_id in by_id and question.field is not None:
            value = by_id[row_id]
            correction_field = question.field
            try:
                traversed = []
                for part in question.field.split('.'):
                    if value is None:
                        correction_field = '.'.join(traversed)
                        break
                    if isinstance(value, list) and int(part) >= len(value):
                        correction_field = '.'.join(traversed)
                        break
                    value = value[int(part)] if isinstance(value, list) else value[part]
                    traversed.append(part)
            except (KeyError, TypeError, IndexError, ValueError):
                continue
            answers[question.id] = {'value': value, 'quote': f'Synthetic planner correction: {row_id} {correction_field} = {json.dumps(value, ensure_ascii=False)}'}
            if correction_field != question.field:
                answers[question.id]['field'] = correction_field
    final = answer_questions(result, answers)
    traps = expected.get('traps', [])
    caught = []
    for trap in traps:
        row_id = trap.get('row_id', trap.get('id'))
        field = trap.get('field')
        matches = [q.id for q in result.questions if q.row is not None and q.row < len(result.rows)
                   and result.rows[q.row].get('id') == row_id and
                   (field is None or q.field == field or (q.field or '').startswith(field + '.') or field.startswith((q.field or '') + '.'))]
        caught.append({'trap': trap, 'caught': bool(matches), 'questions': matches})
    equal = False
    if final.status == 'complete':
        equal = sha256_json(assemble_rows(base, final.rows).model_dump(mode='json')) == sha256_json(Instance.model_validate(base).model_dump(mode='json'))
    raw = json.loads(raw_path.read_text())
    reading = raw.get('metadata', {})
    trap_ids = {trap.get('row_id', trap.get('id')) for trap in traps}
    controls = [row for row in truth if row['id'] not in trap_ids]
    initial_rows = {row.get('id'): row for row in result.rows}
    control_exact = sum(initial_rows.get(row['id']) == row and not any(
        q.row is not None and result.rows[q.row].get('id') == row['id'] for q in result.questions) for row in controls)
    metrics = {'label': 'measured', 'split': case.name, 'reading_sha256': sha256_file(raw_path),
               'before_answers': initial, 'after_answers': accuracy(final, truth),
               'traps_caught': sum(t['caught'] for t in caught), 'traps_total': len(traps),
               'traps': caught, 'assembled_sha256_equals_base': equal,
               'reading_wall_s': reading.get('wall_s'), 'reading_cost_usd': reading.get('cost_usd'),
               'clean_controls': {'rows_exact': control_exact, 'rows_total': len(controls)},
               'fixture_design': {'trap_rows': len(trap_ids), 'rows_requiring_questions': len(trap_ids),
                                  'safe_row_exact_ceiling_before_answers': len(controls),
                                  'source': 'expected.json traps; every planted trap requires a question or rejection'},
               'offline_validation_wall_s': time.monotonic() - started,
               'status': final.status}
    if save:
        for name, value in [('reading', result.model_dump(mode='json')), ('answers', answers),
                            ('final', final.model_dump(mode='json')), ('metrics', metrics)]:
            target = case / f'{name}.json'
            temporary = target.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
            temporary.replace(target)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('--raw', type=Path)
    args = parser.parse_args()
    from .admission import wait_for_slot
    wait_for_slot()
    print(json.dumps(score_case(args.case, raw_path=args.raw), indent=2))


if __name__ == '__main__':
    main()
