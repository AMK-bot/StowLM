"""Frozen row scoring; local inference is imported only at explicit execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from .schema import compact_json, prompt_messages, validate_answer
from experiments.mvp.reader.evaluate import render_prompt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MAX_TOKENS = 256


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _development_bytes(root, name):
    """Open below the trusted root without following replaced directory links."""
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        data_fd = os.open('data', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=data_fd)
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('development input must be a regular file')
                return stream.read()
        finally:
            os.close(data_fd)
    finally:
        os.close(root_fd)


def read_development(path):
    """Only registered train/valid bytes may reach development evaluation."""
    root = HERE.resolve()
    allowed = {root / 'data' / name for name in ('train.jsonl', 'valid.jsonl')}
    resolved = Path(path).resolve()
    if resolved not in allowed:
        raise ValueError('only registered development inputs are allowed; sealed uses score_final')
    manifest_path = root / 'data/manifest.json'
    if manifest_path.is_symlink() or manifest_path.resolve() != manifest_path:
        raise ValueError('development manifest must not use symlinks')
    # The registered paths themselves cannot be redirected outside the root.
    if resolved.resolve() != resolved:
        raise ValueError('registered development input was redirected')
    manifest = json.loads(_development_bytes(root, 'manifest.json'))
    entry = manifest['files'][resolved.relative_to(root).as_posix()]
    payload = _development_bytes(root, resolved.name)
    checksum = hashlib.sha256(payload).hexdigest()
    if checksum != entry['sha256']:
        raise ValueError('registered development input hash mismatch')
    # Parse exactly the buffer whose hash was checked, never reopen the path.
    rows = [json.loads(line) for line in payload.decode('utf-8').splitlines()]
    if len(rows) != entry['count']:
        raise ValueError('registered development input count mismatch')
    return rows, checksum


def score_predictions(rows, predictions):
    ids = [r['id'] for r in rows]
    predicted_ids = [p['id'] for p in predictions]
    if len(set(ids)) != len(ids) or len(set(predicted_ids)) != len(predicted_ids):
        raise ValueError('duplicate case or prediction id')
    if set(predicted_ids) - set(ids):
        raise ValueError('unknown prediction id')
    by_id = {p['id']: p for p in predictions}
    exact = malformed = missing = silent = dg_count = 0
    for row in rows:
        expected = row['expected']
        is_dg = expected.get('dg') is not None and 'refusal' not in expected
        dg_count += is_dg
        pred = by_id.get(row['id'])
        if pred is None:
            missing += 1
            continue
        raw = pred['prediction']
        # Classify dangerous accepted proposals even when another field is invalid.
        try:
            raw_obj = json.loads(raw) if isinstance(raw, str) else raw
            if is_dg and isinstance(raw_obj, dict) and set(raw_obj) != {'refusal'}:
                dg = raw_obj.get('dg')
                if not isinstance(dg, dict) or any(
                    dg.get(key) != expected['dg'][key] for key in ('un_number', 'imdg_class')
                ):
                    silent += 1
        except (ValueError, TypeError):
            pass
        try:
            answer = validate_answer(raw, row['context'])
            if pred.get('decoding_error'):
                raise ValueError('mask failure')
        except (ValueError, TypeError, KeyError):
            malformed += 1
            continue
        exact += answer == expected
    return {'count': len(rows), 'exact_count': exact,
            'row_exact': exact / len(rows) if rows else 0,
            'silent_dg_errors': silent, 'dg_count': dg_count,
            'malformed_count': malformed, 'missing_count': missing,
            'recorded_prediction_count': len(predictions)}


def prompt_tokens(tokenizer, text, context):
    prompt = render_prompt(tokenizer, prompt_messages(text, context))
    return tokenizer.encode(prompt, add_special_tokens=False)


def run_glimmer(rows, *, model_path=None, adapter=None, max_tokens=MAX_TOKENS,
                on_prediction=None):
    from .decoder import make_logits_processor
    import mlx.core as mx
    from mlx_lm import load, generate
    from mlx_lm.sample_utils import make_sampler
    model, tokenizer = load(str(model_path or ROOT / 'models/glimmer-30b-4bit'),
                            adapter_path=str(adapter) if adapter else None)
    mx.random.seed(1404)
    stops = tokenizer.eos_token_ids
    if not stops:
        raise ValueError('tokenizer must declare EOS')
    predictions = []
    for row in rows:
        tokens = prompt_tokens(tokenizer, row['text'], row['context'])
        processor = make_logits_processor(tokenizer, row['context'], len(tokens), min(stops))
        answer = generate(model, tokenizer, prompt=tokens, max_tokens=max_tokens,
                          sampler=make_sampler(temp=0), logits_processors=[processor], verbose=False)
        prediction = {'id': row['id'], 'prediction': answer}
        try:
            processor.validate_output(answer)
        except (ValueError, TypeError) as error:
            prediction['decoding_error'] = str(error)
        predictions.append(prediction)
        if on_prediction:
            on_prediction(prediction)
    return predictions


def arm_report(rows, predictions, *, arm, data_sha256, adapter=None):
    from .decoder import DECODING_POLICY
    report = score_predictions(rows, predictions)
    report.update(arm=arm, data_sha256=data_sha256, schema_constrained=True,
                  decoding_policy=DECODING_POLICY, max_output_tokens=MAX_TOKENS,
                  decoder_sha256=digest(HERE / 'decoder.py'),
                  schema_sha256=digest(HERE / 'schema.py'),
                  prompt_sha256=digest(HERE / 'schema.py'),
                  evaluator_sha256=digest(HERE / 'evaluate.py'),
                  model='models/glimmer-30b-4bit' if arm != 'spark' else 'muse-spark-1.3',
                  adapter_sha256=digest(Path(adapter) / 'adapters.safetensors') if adapter else None,
                  label='measured')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=HERE / 'data/valid.jsonl')
    parser.add_argument('--backend', choices=['glimmer', 'spark'], required=True)
    parser.add_argument('--adapter', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gpu-lease', type=Path)
    args = parser.parse_args()
    try:
        rows, data_sha256 = read_development(args.data)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.error(str(error))
    if not args.output.resolve().is_relative_to(HERE):
        parser.error('output must stay inside intake_lora')
    from .admission import wait_admit
    wait_admit()
    if args.backend == 'glimmer':
        if not args.gpu_lease:
            parser.error('local baseline requires --gpu-lease from the GPU executive')
        from .gpu_run import wait_admission
        wait_admission(args.gpu_lease, seconds=0)
    if args.output.exists() or args.output.with_suffix('.predictions.jsonl').exists():
        parser.error('output already exists')
    if args.backend == 'spark':
        from .spark_baseline import run_spark
        predictions = run_spark(rows)
    else:
        predictions = run_glimmer(rows, adapter=args.adapter)
    report = arm_report(rows, predictions, arm=('spark' if args.backend == 'spark' else
                        'tuned' if args.adapter else 'untrained'),
                        data_sha256=data_sha256, adapter=args.adapter)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.with_suffix('.predictions.jsonl').open('x') as out:
        out.write(''.join(json.dumps(p, ensure_ascii=False) + '\n' for p in predictions))
    with args.output.open('x') as out:
        out.write(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
