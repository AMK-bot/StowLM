"""Tiny CPU-only synthetic-image Meta access probes. No retries or secret CLI args.

Run with ``uv run python experiments/mvp/meta-tools/vision/probe_vision.py prep``.
Only ``sam`` and ``muse`` perform a paid HTTP request. Each mode is single-use:
an existing receipt refuses a second request, including after a transport failure.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import time

from PIL import Image, ImageDraw
import requests


ROOT = Path(__file__).resolve().parent
ENV = Path('/Users/anda/Projects/.env')
BASE = 'https://api.meta.ai/v1'
PRICE_SOURCE = 'https://dev.meta.ai/docs/pricing-rate-limits'
KEY_NAMES = ('MODEL_API_KEY', 'META_API_KEY', 'SPARK_API_KEY', 'MUSE_API_KEY')


def prepare():
    """An original drawing of one red shipping container, no external imagery."""
    image = Image.new('RGB', (256, 256), '#e8eff4')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 194, 255, 255), fill='#a8b0b8')
    draw.polygon([(39, 193), (190, 193), (225, 209), (64, 209)], fill='#7c858d')
    draw.rectangle((40, 93, 191, 193), fill='#c9332c', outline='#62211e', width=2)
    draw.polygon([(40, 93), (65, 73), (216, 73), (191, 93)], fill='#ec685b', outline='#62211e')
    draw.polygon([(191, 93), (216, 73), (216, 173), (191, 193)], fill='#93251f', outline='#62211e')
    for x in range(50, 190, 12):
        draw.line((x, 99, x, 187), fill='#9b2923', width=3)
    for y in range(91, 170, 12):
        draw.line((197, y+3, 211, y-9), fill='#78201d', width=2)
    path = ROOT / 'synthetic-container.png'
    image.save(path)
    return path


def load_key():
    # Parse locally rather than sourcing a shell file; never execute its contents.
    # Keep values only in the current Python process's environment.
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        if '=' not in line:
            continue
        name, value = line.split('=', 1)
        name = name.strip()
        if name not in KEY_NAMES:
            continue
        words = shlex.split(value, comments=True)
        os.environ[name] = ' '.join(words)
    for name in KEY_NAMES:
        if os.environ.get(name):
            return os.environ[name]
    raise SystemExit('No supported Meta key variable found; no request made.')


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def run(mode):
    image_path = prepare()
    blob = image_path.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    if mode == 'prep':
        print(json.dumps({'image': str(image_path), 'sha256': sha, 'bytes': len(blob)}))
        return
    receipt_path = ROOT / f'{mode}-receipt.json'
    if receipt_path.exists():
        raise SystemExit('Receipt already exists; refusing another paid call.')
    data_url = 'data:image/png;base64,' + base64.b64encode(blob).decode('ascii')
    if mode == 'sam':
        endpoint = BASE + '/responses'
        payload = {
            'model': 'sam-3.1',
            'input': [{'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'red shipping container'},
                {'type': 'input_image', 'image_url': data_url},
            ]}],
        }
        upper_bound = 0.0025
    else:
        endpoint = BASE + '/images/edits'
        payload = {
            'model': 'muse-image-1.0',
            'prompt': 'Change the red shipping container to blue. Keep everything else unchanged. Return one image.',
            'images': [{'image_url': data_url}],
            'n': 1,
            'reasoning_strength': 'low',
            'tool_enablement': {'enable_image_search': False, 'enable_web_search': False, 'enable_shell': False},
        }
        upper_bound = 0.01
    # Two fixed single-use probes <= $0.0125, far below this child's $1 cap.
    assert upper_bound <= 0.01
    key = load_key()
    receipt = {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'state': 'reserved', 'model': payload['model'], 'endpoint': endpoint,
        'input_sha256': sha, 'input_bytes': len(blob), 'input_dimensions': [256, 256],
        'reserved_usd': upper_bound, 'pricing_source': PRICE_SOURCE,
        'automatic_retries': 0,
    }
    write_json(receipt_path, receipt)
    write_json(ROOT / f'{mode}-request.json', payload)
    start = time.perf_counter()
    try:
        response = requests.post(
            endpoint, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
            json=payload, timeout=(20, 180),
        )
        receipt['latency_seconds'] = round(time.perf_counter() - start, 6)
        receipt['http_status'] = response.status_code
        receipt['response_bytes'] = len(response.content)
        receipt['request_id'] = response.headers.get('x-request-id')
        # Sanitize defensively even though the service should never echo credentials.
        raw = response.text.replace(key, '[REDACTED]')
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {'raw_body': raw}
        if 200 <= response.status_code < 300:
            if mode == 'sam':
                receipt['provider_status'] = result.get('status')
                receipt['usage'] = result.get('usage')
                text = ''.join(part.get('text', '') for item in result.get('output', []) for part in item.get('content', []) if part.get('type') == 'output_text')
                receipt['output_text'] = text
                receipt['box_count'] = text.count('<|box;')
                receipt['mask_count'] = text.count('<|mask;')
                receipt['cost_usd_from_published_rate'] = 0.0025
            else:
                receipt['usage'] = result.get('usage')
                receipt['returned_images'] = len(result.get('data', []))
                receipt['cost_usd_from_published_rate'] = receipt['returned_images'] * 0.01
                for index, item in enumerate(result.get('data', [])):
                    if item.get('b64_json'):
                        output = base64.b64decode(item['b64_json'])
                        ext = result.get('output_format', 'webp')
                        output_path = ROOT / f'muse-output-{index}.{ext}'
                        output_path.write_bytes(output)
                        with Image.open(io.BytesIO(output)) as generated:
                            receipt['output_dimensions'] = list(generated.size)
                        item['b64_json'] = {'saved_path': output_path.name, 'bytes': len(output), 'sha256': hashlib.sha256(output).hexdigest()}
            receipt['state'] = 'response_received'
        else:
            receipt['state'] = 'http_error'
            receipt['exact_error'] = result
            # Do not assert failed-call cost is zero without billing evidence.
            receipt['cost_usd_from_published_rate'] = None
        write_json(ROOT / f'{mode}-response.json', result)
    except requests.RequestException as error:
        receipt.update(state='transport_error', latency_seconds=round(time.perf_counter()-start, 6), exact_error=str(error).replace(key, '[REDACTED]'))
    finally:
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_json(receipt_path, receipt)
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prep', 'sam', 'muse'])
    run(parser.parse_args().mode)
