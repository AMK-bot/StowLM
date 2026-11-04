"""Stock TTS hero clips and Meta ASR, with durable per-order cost holds."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re

from experiments.mvp.pipeline.contract.schema import sha256_file
from experiments.mvp.voice.audio import radio, synthesize, wav_info
from experiments.mvp.voice.asr import MuseTranscriber, RATE

HERE = Path(__file__).resolve().parent
WORDINGS = HERE.parent / 'lattice/hero-wordings.json'
CAP = Decimal('0.05')
FALLBACK = 'Do not load any containers in bay 09.'


class ClipError(RuntimeError):
    pass


class CostCapExceeded(ClipError):
    pass


def candidates():
    source = json.loads(WORDINGS.read_text())
    rows = source['candidates']
    return [*rows, {'sentence': FALLBACK, 'rule': rows[0]['rule']}]


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def spent(manifest):
    return sum((Decimal(row['cost']) for row in manifest['attempts']), Decimal(0))


@contextmanager
def exclusive(output):
    with (output / '.generate.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def local_transcriber(output, cap):
    # The default ledger edits docs/compute.md, outside this order's fence.
    from llm.spark.spend import SpendLedger
    summary = output / 'spend.md'
    if not summary.exists():
        summary.write_text('')
    ledger = SpendLedger(path=output / 'spend.jsonl', compute_path=summary, cap_usd=cap)
    return MuseTranscriber(ledger=ledger)


def generate(output_dir=HERE, candidates=None, transcriber=None, synthesize_fn=None,
             radio_fn=None, cap_usd='0.05'):
    """Return the recorded queue; dependencies may be injected for offline tests."""
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with exclusive(output):
        return _generate(output, candidates, transcriber, synthesize_fn or synthesize,
                         radio_fn or radio, cap_usd)


def _generate(output, selected, transcriber, synthesize_fn, radio_fn, cap_usd):
    cap = Decimal(str(cap_usd))
    if not cap.is_finite() or not 0 <= cap <= CAP:
        raise CostCapExceeded('Order cap must be between zero and $0.05')
    selected = candidates() if selected is None else selected
    path = output / 'manifest.json'
    manifest = json.loads(path.read_text()) if path.exists() else {
        'schema_version': 'stowlm.radio-clips/1', 'synthetic': True,
        'mode': 'recorded', 'asr_model': 'muse-voice-transcribe-1.0',
        'cap_usd': str(cap), 'spend_usd': '0', 'attempts': [], 'clips': [],
        'candidates': selected,
        'disclosure': 'Stock public TTS with a simulated radio channel; no recording or real radio.',
    }
    if manifest['candidates'] != selected:
        raise ClipError('Candidate queue changed; refusing to reuse its clips')
    if spent(manifest) > cap:
        raise CostCapExceeded('Recorded ASR spend already exceeds the requested cap')
    manifest['cap_usd'] = str(cap)
    audio = output / 'audio'
    audio.mkdir(exist_ok=True)
    transcriber = transcriber if transcriber is not None else local_transcriber(output, cap)

    def checkpoint():
        manifest['spend_usd'] = str(spent(manifest))
        save(path, manifest)

    checkpoint()
    for index, item in enumerate(selected, 1):
        sentence = item['sentence']
        digest = hashlib.sha256(sentence.encode()).hexdigest()
        seed = int(digest[:8], 16)
        words = re.sub(r'[^a-z0-9]+', '-', sentence.lower()).strip('-')[:65].rstrip('-')
        slug = f'{index:02d}-{words}-{digest[:8]}'
        clean = audio / f'{slug}-clean.wav'
        for condition in ('clean', 'radio'):
            clip_id = f'{slug}-{condition}'
            wav = audio / f'{clip_id}.wav'
            prior = next((row for row in manifest['clips'] if row['id'] == clip_id), None)
            if prior and prior.get('transcript'):
                if not wav.exists() or sha256_file(wav) != prior['wav_sha256']:
                    raise ClipError('Transcribed WAV is missing or changed')
                continue
            if not wav.exists():
                if condition == 'clean':
                    synthesize_fn(sentence, 'en', wav)
                else:
                    proof = radio_fn(clean, wav, seed=seed)
                    save(wav.with_suffix('.audio.json'), proof)
            info = wav_info(wav)
            row = prior or {
                'id': clip_id, 'sentence': sentence, 'expected_rule': item['rule'],
                'condition': condition, 'wav_path': str(wav.relative_to(output)),
                'wav_sha256': info['sha256'], 'duration_s': info['duration_s'], 'seed': seed,
            }
            if row['wav_sha256'] != info['sha256']:
                raise ClipError('Prepared WAV changed after checkpoint')
            if prior is None:
                manifest['clips'].append(row)
                checkpoint()
            reserve = Decimal(math.ceil(info['duration_s'] + 1)) * RATE
            attempts = [attempt for attempt in manifest['attempts'] if attempt['clip_id'] == clip_id]
            while len(attempts) < 2:
                if spent(manifest) + reserve > cap:
                    raise CostCapExceeded('Order ASR cap would be exceeded')
                attempt = {'id': len(manifest['attempts']) + 1, 'clip_id': clip_id,
                           'status': 'started', 'cost': str(reserve)}
                manifest['attempts'].append(attempt)
                attempts.append(attempt)
                checkpoint()  # An interrupted request keeps its conservative hold.
                try:
                    result = transcriber.transcribe(wav)
                    cost = Decimal(result['evidence']['cost_usd'])
                    if not cost.is_finite() or not 0 <= cost <= reserve:
                        raise ClipError('ASR cost exceeds reservation')
                    transcript = result['transcript']
                    if not isinstance(transcript, str) or not transcript.strip():
                        raise ClipError('ASR transcript missing')
                except Exception:
                    attempt['status'] = 'failed'
                    checkpoint()
                    if len(attempts) == 2:
                        raise ClipError('ASR failed twice; stopped. See manifest attempts.') from None
                    continue
                attempt.update(status='completed', cost=str(cost))
                row.update(transcript=transcript, asr_seconds=result['latency_s'],
                           cost=str(cost), asr_evidence=result['evidence'])
                checkpoint()
                break
            else:
                raise ClipError('ASR already attempted twice; stopped.')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=HERE)
    parser.add_argument('--cap-usd', default='0.05')
    args = parser.parse_args()
    from .capacity import wait_for_capacity
    wait_for_capacity()
    try:
        manifest = generate(args.output_dir, cap_usd=args.cap_usd)
    except ClipError as error:
        raise SystemExit(str(error)) from None
    print(json.dumps({'clips': len(manifest['clips']), 'spend_usd': manifest['spend_usd']}))


if __name__ == '__main__':
    main()
