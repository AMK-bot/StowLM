"""Picture evidence is a proposal; only fully validated readings become yard states."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import (
    Mode, Question, StageRecord, YardRead, sha256_file, sha256_json,
)
from stowage.conventions import parse_yard
from stowage.model.yard import Yard, YardState

FIXTURE_PATH = Path(__file__).with_name('fixtures') / 'recorded.json'


def _base(base_instance):
    if isinstance(base_instance, (str, Path)):
        return json.loads(Path(base_instance).read_text())
    if hasattr(base_instance, 'model_dump'):
        return base_instance.model_dump(mode='json')
    if isinstance(base_instance, dict):
        return copy.deepcopy(base_instance)
    raise ValueError('base_instance must be an Instance, dictionary or JSON path')


def reading_context(base_instance):
    """Static layout and ID/length registry only. Never include known yard positions."""
    base = _base(base_instance)
    layout = Yard.model_validate(base['port']['yard']).model_dump(mode='json')
    port_limit = base['port'].get('yard_max_tiers')
    if port_limit is not None and (type(port_limit) is not int or port_limit < 1):
        raise ValueError('invalid port yard tier limit')
    for block in layout['blocks']:
        if port_limit is not None:
            block['max_tiers'] = min(block['max_tiers'], port_limit)
    inventory = []
    for box in base['containers']:
        if not isinstance(box.get('id'), str) or box.get('length_ft') not in (20, 40, 45):
            raise ValueError('invalid container registry')
        inventory.append({'id': box['id'], 'length_ft': box['length_ft']})
    if len({box['id'] for box in inventory}) != len(inventory):
        raise ValueError('duplicate container registry ids')
    return layout, sorted(inventory, key=lambda box: box['id'])


def context_sha256(base_instance):
    layout, inventory = reading_context(base_instance)
    return sha256_json({'layout': layout, 'inventory': inventory})


class RecordedClient:
    """Replay only an exact image and static-context match; never call a model."""
    mode = Mode.recorded

    def __init__(self, path=None):
        self.path = Path(path) if path else FIXTURE_PATH
        self.records = []

    def read(self, image_path, *, layout, inventory):
        self.records = []
        if not self.path.is_file():
            raise LookupError('No recorded reading for this generated picture.')
        entries = json.loads(self.path.read_text())
        if not isinstance(entries, list):
            raise ValueError('recorded fixture must be a list')
        image_hash = sha256_file(image_path)
        context_hash = sha256_json({'layout': layout, 'inventory': inventory})
        matches = [row for row in entries if row.get('image_sha256') == image_hash
                   and row.get('context_sha256') == context_hash]
        if len(matches) != 1:
            raise LookupError('No unique recorded reading matches this picture and yard context.')
        entry = matches[0]
        if entry.get('mode') != 'recorded':
            raise ValueError('fixture must be labelled recorded')
        self.records = entry.get('records', [])
        return copy.deepcopy(entry['evidence'])


def _validate(evidence, base, layout, inventory):
    questions = []

    def ask(field, reason, prompt):
        questions.append(Question(id=f'yard:{len(questions) + 1}:{field}', field=field,
                                  reason=reason, prompt=prompt))

    if not isinstance(evidence, dict):
        ask('picture', 'invalid_evidence', 'Provide a structured reading of the yard picture.')
        return None, questions
    if evidence.get('complete_view') is not True:
        ask('complete_view', 'incomplete_view', 'Confirm all yard stacks are visible, including hidden lower tiers.')
    if evidence.get('quay_clear') is not True:
        ask('quay_buffer', 'quay_not_confirmed_clear', 'Confirm the quay buffer is empty or provide its contents.')
    if evidence.get('questions'):
        ask('picture', 'reader_uncertainty', 'Resolve the picture reader\'s reported uncertainty.')
    stacks = evidence.get('stacks')
    if not isinstance(stacks, list):
        ask('stacks', 'missing_stacks', 'Provide the observed stacks, including their bottom-to-top order.')
        return None, questions
    registry = {box['id']: box for box in inventory}
    blocks = {block['id']: block for block in layout['blocks']}
    seen_keys, seen_ids, footprints = set(), set(), set()
    parsed = []
    count = 0
    forbidden = {box['id'] for box in base['containers'] if box.get('direct_delivery')}
    forbidden.update(p['container_id'] for p in base.get('onboard', {}).get('placements', []))
    for index, stack in enumerate(stacks):
        field = f'stacks.{index}'
        before = len(questions)
        if not isinstance(stack, dict):
            ask(field, 'invalid_stack', 'Provide a stack key, length, height and ordered box identities.')
            continue
        key = stack.get('key')
        try:
            if not isinstance(key, str):
                raise ValueError('missing key')
            address = parse_yard(key + '-01')
            block = blocks[address.block]
        except (ValueError, KeyError, TypeError):
            ask(field + '.key', 'invalid_geometry', 'Read a valid stack address in the supplied yard layout.')
            block = None
        if isinstance(key, str):
            if key in seen_keys:
                ask(field + '.key', 'duplicate_stack', 'Resolve the duplicate stack address.')
            seen_keys.add(key)
        length = stack.get('length_ft')
        if type(length) is not int or length not in (20, 40, 45):
            ask(field + '.length_ft', 'unreadable_length', 'Confirm the stack length: 20, 40 or 45 feet.')
        elif block:
            if length not in block['allowed_lengths_ft']:
                ask(field + '.length_ft', 'disallowed_length', 'Resolve a stack length the block does not allow.')
            last_bay = address.bay + (length != 20)
            if last_bay > block['n_bays'] or address.row > block['n_rows']:
                ask(field + '.key', 'outside_block', 'Resolve the stack address outside the block boundary.')
            occupied = {(address.block, bay, address.row) for bay in range(address.bay, last_bay + 1)}
            if occupied & footprints:
                ask(field + '.key', 'overlapping_stacks', 'Resolve stacks with overlapping ground footprints.')
            footprints.update(occupied)
        height = stack.get('height')
        if type(height) is not int or height < 1:
            ask(field + '.height', 'unreadable_height', 'Confirm the positive number of tiers in this stack.')
        elif block and height > block['max_tiers']:
            ask(field + '.height', 'tier_limit', 'Resolve the stack height above the effective yard tier limit.')
        boxes = stack.get('boxes')
        if not isinstance(boxes, list):
            ask(field + '.boxes', 'missing_ids', 'Read each container identity in bottom-to-top order.')
            continue
        count += len(boxes)
        if height != len(boxes):
            ask(field + '.boxes', 'height_id_count_mismatch', 'Provide one identity or unreadable marker per tier.')
        # An optional explicit refusal overrides any filled-in guesses.
        if stack.get('order_readable') is False or stack.get('ids_readable') is False:
            ask(field + '.boxes', 'unreadable_order_or_ids', 'Confirm the identities and bottom-to-top order.')
        for tier, box in enumerate(boxes):
            box_field = field + f'.boxes.{tier}'
            if not isinstance(box, str) or not box:
                ask(box_field, 'unreadable_id', 'Read or confirm this container identity; it cannot be inferred from position.')
                continue
            if box not in registry:
                ask(box_field, 'unknown_id', 'Resolve a container identity absent from the supplied yard registry.')
            elif registry[box]['length_ft'] != length:
                ask(box_field, 'container_length_mismatch', 'Resolve the identity or stack length disagreement.')
            if box in forbidden:
                ask(box_field, 'container_not_in_yard', 'Resolve a container registered as onboard or direct delivery.')
            if box in seen_ids:
                ask(box_field, 'duplicate_id', 'Resolve the same container identity read in two places.')
            seen_ids.add(box)
        if len(questions) == before:
            parsed.append({'key': key, 'length_ft': length, 'boxes': boxes})
    segments = evidence.get('segments')
    segment_count = (len(segments) if isinstance(segments, list) else
                     segments.get('count') if isinstance(segments, dict) else segments)
    if type(segment_count) is not int or segment_count < 0:
        ask('segments', 'missing_segmentation', 'Provide the SAM container segment count for this image.')
    elif segment_count != count:
        ask('segments', 'segment_count_mismatch', 'Resolve the disagreement between segmented containers and read tiers.')
    if isinstance(segments, list):
        seen_segments = set()
        image_sizes = set()
        for index, segment in enumerate(segments):
            field = f'segments.{index}'
            if not isinstance(segment, dict):
                ask(field, 'invalid_segment', 'Provide a valid SAM container bounding box.')
                continue
            box = segment.get('box', segment.get('bbox'))
            if (not isinstance(box, (list, tuple)) or len(box) != 4
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in box)):
                ask(field, 'invalid_segment', 'Provide four finite numeric SAM bounding-box coordinates.')
                continue
            x1, y1, x2, y2 = box
            if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
                ask(field, 'invalid_segment', 'Resolve the SAM bounding box with invalid position or area.')
            size = segment.get('image_size')
            if 'image_size' in segment:
                if (not isinstance(size, (list, tuple)) or len(size) != 2
                        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                               for value in size)):
                    ask(field, 'invalid_segment', 'Provide a valid positive image width and height for this segment.')
                else:
                    image_sizes.add(tuple(size))
                    if x2 > size[0] or y2 > size[1]:
                        ask(field, 'invalid_segment', 'Resolve the SAM bounding box outside the image boundary.')
            coordinates = tuple(box)
            if coordinates in seen_segments:
                ask(field, 'duplicate_segment', 'Resolve duplicate SAM bounding boxes before counting containers.')
            seen_segments.add(coordinates)
        if len(image_sizes) > 1:
            ask('segments', 'invalid_segment', 'Resolve inconsistent source image sizes in the SAM segments.')
    required = set(registry) - forbidden
    missing = required - seen_ids
    if missing:
        ask('stacks', 'missing_required_ids', 'Locate these required yard containers: ' + ', '.join(sorted(missing)) + '.')
    if questions:
        return None, questions
    try:
        state = YardState.model_validate({'yard_id': layout['id'], 'time_s': 0.0,
                                         'stacks': parsed, 'quay_buffer': []})
    except ValueError:
        ask('stacks', 'invalid_yard_state', 'Resolve the invalid stack geometry or container identities.')
        return None, questions
    return state.model_dump(mode='json'), []


def read_yard_picture(image_path, *, base_instance, client=None) -> YardRead:
    """Return a complete YardState only when the picture evidence resolves every field.

    ``client.read(path, *, layout, inventory)`` returns ``stacks`` (key, length_ft,
    height, boxes bottom→top with null for unreadable IDs), ``complete_view``,
    ``quay_clear`` and ``segments`` (SAM segment list). Integer or {count: integer}
    counts are a recorded/testing seam; paid clients supply validated boxes. The default
    client replays hash-bound fixtures and never makes a network call.
    """
    started = time.perf_counter()
    client = client if client is not None else RecordedClient()
    mode = Mode.recorded if isinstance(client, RecordedClient) else Mode.live
    image_hash = context_hash = None
    client_wall = 0.0
    state, questions, inherited = None, [], []
    try:
        mode = Mode(getattr(client, 'mode', mode))
        base = _base(base_instance)
        layout, inventory = reading_context(base)
        context_hash = sha256_json({'layout': layout, 'inventory': inventory})
        image_hash = sha256_file(image_path)
        read_started = time.perf_counter()
        try:
            evidence = client.read(str(image_path), layout=copy.deepcopy(layout), inventory=copy.deepcopy(inventory))
        finally:
            client_wall = time.perf_counter() - read_started
        # A client can mutate its arguments, never the validator's trusted context.
        state, questions = _validate(evidence, base, layout, inventory)
        if not getattr(client, 'records', []) and isinstance(evidence, dict) and 'model' in evidence:
            cost = evidence.get('cost_usd', 0.0)
            wall = evidence.get('wall_s', 0.0)
            if (not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0
                    or not isinstance(wall, (int, float)) or not math.isfinite(wall) or wall < 0):
                raise ValueError('invalid model measurements')
            inherited.append(StageRecord(stage='yard_vision.models', mode=mode,
                input_sha256=sha256_json({'image_sha256': image_hash, 'context_sha256': context_hash}),
                output_sha256=sha256_json(evidence), model=evidence['model'],
                prompt_sha256=evidence.get('prompt_sha256'),
                cost_usd=0.0 if mode == Mode.recorded else cost,
                wall_s=0.0 if mode == Mode.recorded else wall,
                notes='Recorded answer replayed.' if mode == Mode.recorded else 'SAM segmentation and Spark picture reading.'))
    except Exception as error:
        state = None
        questions = [Question(id='yard:unavailable', field='picture', reason='reading_unavailable',
                              prompt=f'Provide a readable yard picture or a recorded reading ({type(error).__name__}).')]
    # Paid calls can finish before the response fails JSON parsing. Their
    # receipts still belong in this result, including an abstention.
    try:
        for row in getattr(client, 'records', []):
            record = StageRecord.model_validate(row)
            updates = {'mode': mode}
            if mode == Mode.recorded:
                updates.update(cost_usd=0.0, wall_s=0.0)
            inherited.append(record.model_copy(update=updates))
    except Exception:
        state = None
        questions.append(Question(id='yard:invalid_records', field='picture', reason='invalid_records',
                                  prompt='Resolve the invalid picture-reading receipts.'))
    result = YardRead(status='complete' if state is not None else 'needs_answers',
                      state=state, questions=questions)
    record = StageRecord(stage='yard_vision.validate', mode=mode,
                         input_sha256=sha256_json({'image_sha256': image_hash, 'context_sha256': context_hash}),
                         output_sha256=sha256_json(result.model_dump(mode='json')),
                         source_sha256=sha256_file(__file__), wall_s=max(0.0, time.perf_counter() - started - client_wall),
                         notes='Generated picture. Deterministic validation; snapshot time is local epoch 0. Planner confirms the reading.')
    return result.model_copy(update={'records': inherited + [record]})
