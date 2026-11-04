"""Scoring against known state. Unknown observations count as abstentions."""
from __future__ import annotations


def _state(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if value is None:
        return {'stacks': []}
    if 'state' in value:
        return value['state'] or {'stacks': []}
    return value


def _stacks(value):
    rows = _state(value).get('stacks', [])
    indexed = {row['key']: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError('duplicate observed stack key')
    return indexed


def _observation(value):
    """A malformed model reading is one abstention, never a partially scored read."""
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if value is None:
        value = {'stacks': []}
    if not isinstance(value, dict):
        return {}, ['observation_not_object']
    if 'state' in value:
        value = value['state'] if value['state'] is not None else {'stacks': []}
    if not isinstance(value, dict):
        return {}, ['state_not_object']
    rows = value.get('stacks')
    if not isinstance(rows, list):
        return {}, ['stacks_not_list']
    indexed, errors = {}, []
    for index, row in enumerate(rows):
        field = f'stacks.{index}'
        if not isinstance(row, dict):
            errors.append(field + ':not_object')
            continue
        key = row.get('key')
        if not isinstance(key, str) or not key:
            errors.append(field + '.key:invalid')
        elif key in indexed:
            errors.append(field + '.key:duplicate')
        else:
            indexed[key] = row
        for name in ('height', 'tier_count', 'tiers'):
            if name in row and row[name] is not None:
                height = row[name]
                if type(height) is not int or height < 1:
                    errors.append(field + f'.{name}:invalid')
        boxes = row.get('boxes')
        if boxes is not None:
            if not isinstance(boxes, list):
                errors.append(field + '.boxes:not_list')
            else:
                if not boxes:
                    errors.append(field + '.boxes:empty_stack')
                if any(box is not None and (not isinstance(box, str) or not box) for box in boxes):
                    errors.append(field + '.boxes:invalid_id')
                height = _height(row)
                if type(height) is int and height != len(boxes):
                    errors.append(field + '.boxes:height_mismatch')
        length = row.get('length_ft')
        if length is not None and (type(length) is not int or length not in (20, 40, 45)):
            errors.append(field + '.length_ft:invalid')
    return ({}, errors) if errors else (indexed, [])


def _height(stack):
    if stack is None:
        return None
    for name in ('height', 'tier_count', 'tiers'):
        if name in stack:
            return stack[name]
    boxes = stack.get('boxes')
    return len(boxes) if isinstance(boxes, list) else None


def _fraction(correct, total):
    return {'correct': correct, 'total': total, 'accuracy': correct / total if total else 1.0}


def score_yard(truth, observation, *, accepted=False, wall_s=0.0, cost_usd=0.0,
               detected_box_count=None) -> dict:
    """Score one image, retaining denominators and count error.

    ``observation`` is raw visual evidence {stacks:[{key,height,boxes,length_ft}]}.
    Unknown heights/ids are None. Box lists run bottom to top. ``accepted`` is
    True only for a complete accepted YardState, or a collection of accepted
    stack keys. Missing true and extra predicted stacks both enter denominators.
    Box count accuracy is max(0,1-|pred-true|/true); exact count is also reported.
    Unknown counts score zero, even when ground truth is empty. Malformed raw
    observations become full abstentions with truth-only denominators; an
    independently supplied valid SAM count is still scored and retained.
    """
    expected = _stacks(truth)
    observed, errors = _observation(observation)
    if detected_box_count is not None and (type(detected_box_count) is not int or detected_box_count < 0):
        errors.append('detected_box_count:invalid')
        observed, detected_box_count = {}, None
    keys = sorted(set(expected) | set(observed))
    accepted_keys = set(keys) if accepted is True else set(accepted or [])
    true_count = sum(len(s['boxes']) for s in expected.values())
    heights = [_height(s) for s in observed.values()]
    if errors and detected_box_count is None:
        count = None
    elif detected_box_count is None:
        count = sum(heights) if all(h is not None for h in heights) else None
    else:
        count = detected_box_count
    error = abs(count - true_count) if count is not None else None
    count_accuracy = (max(0., 1. - error / true_count) if true_count else float(count == 0)) if error is not None else 0.
    height_correct = id_correct = id_total = height_unknown = id_unknown = silent = 0
    details = []
    for key in keys:
        true, read = expected.get(key), observed.get(key)
        true_boxes = true.get('boxes', []) if true else []
        read_boxes = read.get('boxes', []) if read else []
        read_boxes = read_boxes if isinstance(read_boxes, list) else []
        true_height, read_height = len(true_boxes), _height(read)
        if read is not None and true is not None and read_height == true_height:
            height_correct += 1
        if read_height is None:
            height_unknown += 1
        slots = max(true_height, read_height or 0, len(read_boxes))
        for tier in range(slots):
            target = true_boxes[tier] if tier < len(true_boxes) else None
            answer = read_boxes[tier] if tier < len(read_boxes) else None
            id_total += 1
            id_correct += int(target is not None and target == answer)
            id_unknown += int(answer is None)
        exact = bool(true is not None and read is not None and read_height == true_height and true_boxes == read_boxes
                     and true.get('length_ft') == read.get('length_ft'))
        wrong = key in accepted_keys and not exact
        silent += int(wrong)
        details.append({'key': key, 'true_height': true_height, 'observed_height': read_height,
                        'accepted': key in accepted_keys, 'exact_stack': exact,
                        'silent_wrong': wrong})
    height_score, id_score = _fraction(height_correct, len(keys)), _fraction(id_correct, id_total)
    if errors:
        if not height_score['total']:
            height_score['accuracy'] = 0.0
        if not id_score['total']:
            id_score['accuracy'] = 0.0
    return {'label': 'measured', 'malformed_evidence': bool(errors), 'malformed_reasons': errors,
            'box_count': {'true': true_count, 'observed': count,
            'absolute_error': error, 'exact': count == true_count if count is not None else False,
            'accuracy': count_accuracy, 'denominator': true_count},
            'stack_height': height_score,
            'id': id_score, 'silent_wrong_stacks': silent,
            'accepted_stacks': len(accepted_keys),
            'abstentions': {'stack_height': height_unknown, 'id': id_unknown,
                            'unaccepted_stacks': len(set(keys) - accepted_keys)},
            'wall_s': wall_s, 'cost_usd': cost_usd, 'stacks': details}


def aggregate_scores(scores) -> dict:
    """Micro-average heights and ids; count accuracy is mean per image."""
    scores = list(scores)
    output = {'label': 'measured', 'images': len(scores),
              'malformed_images': sum(bool(s.get('malformed_evidence')) for s in scores)}
    for name in ('stack_height', 'id'):
        output[name] = _fraction(sum(s[name]['correct'] for s in scores),
                                 sum(s[name]['total'] for s in scores))
        if not output[name]['total'] and output['malformed_images']:
            output[name]['accuracy'] = 0.0
    output['box_count'] = {'mean_accuracy': sum(s['box_count']['accuracy'] for s in scores) / len(scores) if scores else None,
                           'exact_images': sum(s['box_count']['exact'] for s in scores),
                           'total_images': len(scores)}
    for name in ('silent_wrong_stacks', 'accepted_stacks', 'wall_s', 'cost_usd'):
        output[name] = sum(s[name] for s in scores)
    output['abstentions'] = {name: sum(s['abstentions'][name] for s in scores)
                            for name in ('stack_height', 'id', 'unaccepted_stacks')}
    return output
