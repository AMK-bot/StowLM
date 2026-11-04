"""Paid vision adapters. No retries; recorded observations replay without these.

SparkClient currently accepts text only. LedgerSparkClient extends that client
with image input, retaining its authoritative ledger, credit gate, pricing and
worst-case reservation. It does not replace any shared transport or source.
SAM masks are retained verbatim in the provider's special-token encoding.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from uuid import uuid4

from PIL import Image, ImageDraw
import requests

from llm.spark.client import (
    CONTEXT_TOKENS, ENDPOINT, MODEL, SparkClient, SparkCreditNotReady,
    SparkError, SparkHTTPError, SparkIncompleteError, SparkProtocolError,
    SparkResponse,
)
from llm.spark.spend import SpendError, Usage, calculate_cost
from experiments.mvp.pipeline.contract.schema import StageRecord, sha256_file, sha256_json
from stowage.model.yard import YardState

BASE = 'https://api.meta.ai/v1'
SAM_COST = Decimal('0.0025')
IMAGE_COST = Decimal('0.01')
PRICE_SOURCE = 'https://dev.meta.ai/docs/pricing-rate-limits'


class VisionError(RuntimeError):
    """Safe-to-display failure; provider error bodies are not exposed."""


def data_url(path):
    path = Path(path)
    with Image.open(path) as image:
        image.verify()
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in {'image/png', 'image/jpeg', 'image/webp'}:
        raise ValueError('Expected PNG, JPEG or WebP image')
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def output_text(data):
    return ''.join(part.get('text', '') for item in data.get('output', [])
                   if item.get('type') == 'message' for part in item.get('content', [])
                   if part.get('type') == 'output_text')


class LedgerSparkClient(SparkClient):
    """Multimodal extension using the inherited, shared spend ledger.

    Unlike the text client's bounded retries, this extension makes one attempt.
    Failed transport/protocol outcomes retain the conservative reservation.
    """

    def complete_image(self, prompt, images, *, purpose='yard-vision-read', max_output_tokens=4096):
        if not isinstance(prompt, str) or not prompt.strip() or not images:
            raise ValueError('Nonempty prompt and images required')
        if type(max_output_tokens) is not int or not 16 <= max_output_tokens <= 131072:
            raise ValueError('Invalid maximum output tokens')
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 200:
            raise ValueError('Invalid purpose tag')
        ready = (os.environ.get('SPARK_CREDIT_READY') == '1'
                 if self._credit_ready is None else self._credit_ready)
        if not ready:
            raise SparkCreditNotReady('Muse Spark credit is on hold; no request sent')
        content = [{'type': 'input_text', 'text': prompt}]
        content.extend({'type': 'input_image', 'image_url': value} for value in images)
        body = {'model': MODEL, 'input': [{'type': 'message', 'role': 'user', 'content': content}],
                'max_output_tokens': max_output_tokens, 'stream': False, 'store': False,
                'reasoning': {'effort': 'low'}}
        reservation = calculate_cost(Usage(CONTEXT_TOKENS - max_output_tokens, max_output_tokens))
        base = {'time': datetime.now(timezone.utc).isoformat(), 'model': MODEL,
                'purpose': self._safe(purpose), 'reservation_id': uuid4().hex,
                'request_id': None, 'attempt': 1, 'reasoning_effort': 'low',
                'input_tokens': None, 'output_tokens': None, 'cached_tokens': None}
        with self.ledger.locked():
            self.ledger.check_capacity(reservation)
            self.ledger.append({**base, 'phase': 'reservation', 'status': 'pending',
                                'cost_usd': str(reservation), 'cost_kind': 'conservative_hold'})
            request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method='POST',
                headers={'Authorization': f'Bearer {self._key}', 'Content-Type': 'application/json',
                         'Accept': 'application/json'})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    header_id = response.headers.get('x-request-id')
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                certain = 400 <= status < 500
                self.ledger.append({**base, 'phase': 'settlement', 'status': 'http_error',
                    'http_status': status, 'cost_usd': '0' if certain else str(reservation),
                    'cost_kind': 'reported_rejection' if certain else 'conservative_hold'})
                raise SparkHTTPError(status) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                self.ledger.append({**base, 'phase': 'settlement', 'status': 'transport_error',
                    'cost_usd': str(reservation), 'cost_kind': 'conservative_hold'})
                raise SparkError('Muse Spark transport failure; billing unknown') from None
            try:
                data = json.loads(raw)
                if data.get('model', MODEL) != MODEL:
                    raise ValueError('Unexpected model')
                u = data['usage']
                usage = Usage(u['input_tokens'], u['output_tokens'],
                              u.get('input_tokens_details', {}).get('cached_tokens', 0))
                if usage.input_tokens + usage.output_tokens > CONTEXT_TOKENS or usage.output_tokens > max_output_tokens:
                    raise ValueError('Usage exceeds bounds')
                request_id = header_id or data['id']
                if not isinstance(request_id, str) or not request_id:
                    raise ValueError('Missing request ID')
                text = output_text(data)
                cost = calculate_cost(usage)
            except (ValueError, KeyError, TypeError, AttributeError, SpendError):
                self.ledger.append({**base, 'phase': 'settlement', 'status': 'protocol_error',
                    'cost_usd': str(reservation), 'cost_kind': 'conservative_hold'})
                raise SparkProtocolError('Invalid Muse Spark response; billing unknown') from None
            status = data.get('status', 'completed')
            safe_id = self._safe(request_id)
            self.ledger.append({**base, 'phase': 'settlement',
                'status': status if status in {'completed', 'incomplete', 'failed', 'cancelled'} else 'response_error',
                'request_id': safe_id, 'input_tokens': usage.input_tokens,
                'output_tokens': usage.output_tokens, 'cached_tokens': usage.cached_tokens,
                'cost_usd': str(cost), 'cost_kind': 'measured'})
            if status == 'incomplete':
                details = data.get('incomplete_details') or {}
                raise SparkIncompleteError(details.get('reason'), usage, cost, safe_id)
            if status != 'completed':
                raise SparkProtocolError('Muse Spark returned unsuccessful response; usage logged')
            return SparkResponse(self._safe(text), usage, cost, safe_id)


class VisionBudget:
    """Durable SAM/Image campaign cap. Unknown calls retain their full hold."""

    def __init__(self, path, cap_usd=5):
        self.path = Path(path)
        self.cap = Decimal(str(cap_usd))
        if not self.cap.is_finite() or not 0 <= self.cap <= 5:
            raise ValueError('Vision campaign cap must be between zero and $5')

    @contextmanager
    def call(self, model, amount):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix + '.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            rows = [json.loads(line) for line in self.path.read_text().splitlines()] if self.path.exists() else []
            held = {row['call_id']: Decimal(row['cost_usd']) for row in rows}
            if sum(held.values(), Decimal(0)) + amount > self.cap:
                raise VisionError('SAM/Image campaign cap reached; no request sent')
            row = {'call_id': uuid4().hex, 'model': model, 'cost_usd': str(amount),
                   'cost_kind': 'conservative_hold', 'status': 'reserved',
                   'time': datetime.now(timezone.utc).isoformat(), 'automatic_retries': 0,
                   'pricing_source': PRICE_SOURCE}
            self._append(row)
            try:
                yield row
            except Exception:
                row.update(status='failed_or_unknown')
                raise
            finally:
                self._append(row)

    def _append(self, row):
        with self.path.open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())


def parse_segments(text):
    """Parse real provider boxes and preserve associated compressed masks."""
    segments = []
    token = r'<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>'
    matches = list(re.finditer(token, text))
    for index, match in enumerate(matches):
        x1, y1, x2, y2, width, height = map(int, match.groups())
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise VisionError('SAM returned invalid box geometry')
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        masks = re.findall(r'<\|mask;x=(-?\d+);y=(-?\d+);data=(.*?)\|>', text[match.end():end], re.DOTALL)
        mask = None
        if masks:
            x, y, data = masks[0]
            mask = {'x': int(x), 'y': int(y), 'data': data, 'encoding': 'sam-special-token'}
        segments.append({'box': [x1, y1, x2, y2], 'image_size': [width, height], 'mask': mask})
    if text.count('<|box;') != len(segments) or text.count('<|mask;') != sum(s['mask'] is not None for s in segments):
        raise VisionError('SAM special-token output could not be fully parsed')
    return segments


def _outlines_url(path, segments):
    with Image.open(path) as source:
        image = source.convert('RGB')
    draw = ImageDraw.Draw(image)
    for index, segment in enumerate(segments):
        sx, sy = image.width / segment['image_size'][0], image.height / segment['image_size'][1]
        box = [round(v * (sx if i % 2 == 0 else sy)) for i, v in enumerate(segment['box'])]
        draw.rectangle(box, outline='#ff00ff', width=3)
        draw.text((box[0] + 3, box[1] + 3), str(index + 1), fill='#ff00ff')
    data = io.BytesIO()
    image.save(data, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode('ascii')


def read_prompt(layout, inventory):
    return '''Read this generated yard picture. The second image has SAM candidate box outlines;
outlines can miss boxes or merge them and are not ground truth. Infer only visible facts.
Return JSON only: {"complete_view":false,"quay_clear":false,"stacks":[
{"key":"BLOCK-BBB-RR","length_ft":20,"height":1,"boxes":[null]}]}.
List nonempty stacks. boxes are bottom to top, one entry per tier. Use null for every
hidden, occluded, ambiguous or illegible container id, height, length or stack key.
Never fill an id by elimination from inventory. Never infer unseen tiers from inventory.
Set complete_view true only if the whole supplied yard layout is visibly covered and
all stack heights and occupied positions can be read; otherwise false. Set quay_clear
true only if a visible labelled quay area is clearly empty; otherwise false. Do not
interpret absence of a quay drawing as an empty quay. Painted bay/row labels identify
ground positions; use them, not ordering of inventory. Do not treat SAM candidates as
container counts. The supplied metadata contains static layout and unordered allowed
identities only, never true placements. If this image cannot establish a fact, use null.
LAYOUT: ''' + json.dumps(layout, sort_keys=True) + '\nINVENTORY: ' + json.dumps(inventory, sort_keys=True)


class YardVisionClient:
    def __init__(self, *, spark=None, api_key=None, budget_path=None, cap_usd=5, http_post=None):
        self.spark = spark
        self._key = api_key if api_key is not None else os.environ.get('MODEL_API_KEY', '')
        self.budget = VisionBudget(budget_path or Path(__file__).with_name('spend.jsonl'), cap_usd)
        self._http_post = http_post or requests.post
        self.records = []
        self.last_attempt = {}

    def _record(self, stage, *, input_value, output_value, model, prompt, wall_s, cost_usd):
        self.records.append(StageRecord(stage=stage, mode='live',
            input_sha256=sha256_json(input_value), output_sha256=sha256_json(output_value),
            model=model, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            source_sha256=sha256_file(__file__), wall_s=wall_s, cost_usd=float(cost_usd),
            notes='Synthetic generated image; paid model observation, deterministically validated downstream.'))

    def _post(self, endpoint, payload, price):
        if not self._key.strip() or any(c in self._key for c in '\r\n'):
            raise VisionError('Set MODEL_API_KEY before paid vision calls')
        with self.budget.call(payload['model'], price) as record:
            started = time.perf_counter()
            try:
                response = self._http_post(BASE + endpoint,
                    headers={'Authorization': 'Bearer ' + self._key, 'Content-Type': 'application/json'},
                    json=payload, timeout=(20, 180))
            except (requests.RequestException, TimeoutError, OSError):
                raise VisionError('Vision transport failed; billing unknown') from None
            request_id = response.headers.get('x-request-id')
            record.update(wall_s=time.perf_counter() - started, http_status=response.status_code,
                          request_id=request_id.replace(self._key, '[REDACTED]') if isinstance(request_id, str) else None)
            if not 200 <= response.status_code < 300:
                raise VisionError(f'Vision HTTP {response.status_code}; billing unverified')
            try:
                data = json.loads(response.text.replace(self._key, '[REDACTED]'))
                if not isinstance(data, dict):
                    raise ValueError('Not object')
            except (ValueError, TypeError):
                raise VisionError('Invalid vision response; billing unknown') from None
            record.update(status='response_received', cost_kind='published_rate')
            return data, dict(record)

    def segment(self, image_path):
        started = time.perf_counter()
        payload = {'model': 'sam-3.1', 'store': False, 'input': [
            {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'shipping container'},
                {'type': 'input_image', 'image_url': data_url(image_path)}]}]}
        data, record = self._post('/responses', payload, SAM_COST)
        self.last_attempt['sam'] = {'raw': data, 'receipt': record}
        self._record('yard_vision.sam', input_value={'image_sha256': sha256_file(image_path)},
            output_value=data, model='sam-3.1', prompt='shipping container',
            wall_s=time.perf_counter() - started, cost_usd=SAM_COST)
        if data.get('status') != 'completed' or data.get('usage', {}).get('images_processed') != 1:
            raise VisionError('SAM did not return one completed image segmentation')
        return {'segments': parse_segments(output_text(data)), 'raw': data, 'receipt': record}

    def read(self, image_path, *, layout, inventory):
        started = time.perf_counter()
        self.records = []
        self.last_attempt = {'image_sha256': sha256_file(image_path),
            'layout': layout, 'inventory': inventory}
        prompt = read_prompt(layout, inventory)
        spark = self.spark or LedgerSparkClient(max_retries=0, timeout=180)
        sam = self.segment(image_path)
        self.last_attempt['sam'] = sam
        spark_started = time.perf_counter()
        answer = spark.complete_image(prompt,
            [data_url(image_path), _outlines_url(image_path, sam['segments'])],
            purpose='yard-vision-read', max_output_tokens=4096)
        spark_wall_s = time.perf_counter() - spark_started
        spark_meta = {'text': answer.text, 'request_id': answer.request_id,
                      'cost_usd': float(answer.cost_usd), 'wall_s': spark_wall_s}
        usage = getattr(answer, 'usage', None)
        if usage is not None:
            spark_meta['usage'] = {'input_tokens': usage.input_tokens, 'output_tokens': usage.output_tokens,
                                   'cached_tokens': usage.cached_tokens}
        self.last_attempt['spark'] = spark_meta
        self._record('yard_vision.spark', input_value={
            'image_sha256': self.last_attempt['image_sha256'], 'layout': layout,
            'inventory': inventory, 'segments': sam['segments']},
            output_value=spark_meta, model=MODEL, prompt=prompt,
            wall_s=spark_wall_s, cost_usd=answer.cost_usd)
        text = answer.text.strip()
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
        try:
            observation = json.loads(text)
            if not isinstance(observation, dict):
                raise ValueError('Expected object')
        except (ValueError, TypeError):
            raise VisionError('Spark yard response is not a JSON object') from None
        # Deterministic state validation belongs to read_yard_picture; never
        # coerce absent flags into evidence of a complete camera view.
        observation.setdefault('complete_view', False)
        observation.setdefault('quay_clear', False)
        observation.setdefault('stacks', [])
        observation.update(segments=sam['segments'],
            raw={'sam_response': sam['raw'], 'sam_receipt': sam['receipt'],
                 'spark_text': answer.text, 'spark_request_id': answer.request_id},
            mode='live', model='sam-3.1+muse-spark-1.3', wall_s=time.perf_counter() - started,
            cost_usd=float(SAM_COST + answer.cost_usd),
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        self.last_attempt['evidence'] = observation
        return observation

    def repaint(self, image_path, output_path, *, source_state=None):
        """Generate a photo; ground truth is allowed only in this synthesis step.

        The optional manifest never enters ``read`` or ``read_prompt``. Returning
        it and the exact prompt lets the campaign freeze and audit generation.
        Scene preservation still requires a separate check before scoring.
        """
        output_path = Path(output_path)
        if output_path.exists():
            raise VisionError('Repaint output already exists; refusing duplicate paid call')
        prompt = ('Repaint this generated elevated yard view as a realistic aerial photograph. '
            'Keep EXACTLY the same shipping container count, stack heights, bottom-to-top order, '
            'positions, dimensions and colours. Keep all painted container ids and ground bay/row '
            'labels legible and unchanged. Do not add or remove any object or container. '
            'Keep the empty labelled quay area and the Generated picture label. No new stacks. '
            'Change only material appearance, light and texture. Return one image.')
        manifest = None
        if source_state is not None:
            source = YardState.model_validate(source_state)
            stacks = [{'key': stack.key, 'length_ft': stack.length_ft,
                       'height': len(stack.boxes), 'boxes_bottom_to_top': list(stack.boxes)}
                      for stack in source.stacks]
            stack_boxes = sum(stack['height'] for stack in stacks)
            manifest = {'schema': 'stowlm.yard-synthesis/1', 'yard_id': source.yard_id,
                'stack_count': len(stacks), 'stack_container_count': stack_boxes,
                'container_count': stack_boxes + len(source.quay_buffer),
                'quay_buffer': list(source.quay_buffer), 'stacks': stacks}
            prompt += (
                '\nThe following synthesis manifest is the exact scene to preserve, not a suggestion. '
                f'There must be EXACTLY {manifest["container_count"]} shipping containers in the whole image, '
                f'with EXACTLY {manifest["stack_container_count"]} containers in '
                f'EXACTLY {manifest["stack_count"]} nonempty yard stacks and '
                f'EXACTLY {len(source.quay_buffer)} containers on the quay. '
                'Do not add background containers, new stacks, hidden boxes, extra tiers, cranes or vehicles. '
                'Each stack key, length and tier count must exactly match the manifest. '
                'The boxes_bottom_to_top array gives the exact identity at each tier. '
                'Preserve the source container colours and all ground slot labels. '
                'Paint each exact listed ID on the same visible container face as in the source, '
                'large, sharply legible and with every character unchanged. '
                'Do not substitute plausible shipping codes, random letters, logos, gibberish or altered digits. '
                'Copy IDs character for character. Do not move an ID to another container or tier. '
                'Preserve the original camera angle, crop and visibility. '
                'Before finishing, check the total count, every stack height and every visible ID '
                'against this manifest. SYNTHESIS_MANIFEST: ' + json.dumps(manifest, sort_keys=True))
        payload = {'model': 'muse-image-1.0', 'prompt': prompt,
            'images': [{'image_url': data_url(image_path)}], 'n': 1, 'reasoning_strength': 'low',
            'tool_enablement': {'enable_image_search': False, 'enable_web_search': False, 'enable_shell': False}}
        data, record = self._post('/images/edits', payload, IMAGE_COST)
        try:
            if len(data['data']) != 1:
                raise ValueError('Expected exactly one image')
            blob = base64.b64decode(data['data'][0]['b64_json'], validate=True)
            with Image.open(io.BytesIO(blob)) as generated:
                generated.verify()
            # Save as PNG regardless of provider format. Always carry the label.
            with Image.open(io.BytesIO(blob)) as generated:
                image = generated.convert('RGB')
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 190, 22), fill='white')
            draw.text((6, 5), 'Generated picture', fill='black')
        except (ValueError, KeyError, TypeError, OSError):
            raise VisionError('Muse did not return one decodable image') from None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format='PNG')
        return {'path': str(output_path), 'generated': True, 'scene_preservation': 'unchecked',
                'receipt': record, 'usage': data.get('usage'), 'cost_usd': float(IMAGE_COST),
                'synthesis_manifest': manifest,
                'synthesis_manifest_sha256': sha256_json(manifest) if manifest is not None else None,
                'prompt': prompt,
                'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
