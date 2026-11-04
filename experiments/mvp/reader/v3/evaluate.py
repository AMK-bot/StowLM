"""Greedy Glimmer scoring with identical v3 schema masking in every local arm.

No output repair: decoder/schema failure is recorded; truncated output scores wrong.
The unchanged Spark baseline is a historical reference; no current-mask parity
is claimed for it. Tuned, untrained and stopped-v2 local arms share this mask.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.mvp.reader.evaluate import read_jsonl, score_predictions, prompt_messages, render_prompt
from experiments.mvp.reader.train import check_model
from experiments.mvp.reader.v3.train import HERE, ROOT, sha256, check_admission

from experiments.mvp.reader.v3.decoder import DECODING_POLICY


def run_glimmer(rows, model_path, adapter=None, max_tokens=256):
    from experiments.mvp.reader.v3.decoder import make_logits_processor
    check_model(model_path)
    import mlx.core as mx
    from mlx_lm import load, generate
    from mlx_lm.sample_utils import make_sampler
    model, tokenizer = load(str(model_path), adapter_path=str(adapter) if adapter else None)
    mx.random.seed(199)
    stops = set(tokenizer.eos_token_ids)
    if not stops:
        raise ValueError('tokenizer has no EOS token for schema completion')
    stop = min(stops)
    predictions = []
    for row in rows:
        prompt = render_prompt(tokenizer, prompt_messages(row))
        # Pass this exact token list to MLX, avoiding implicit BOS changes.
        tokens = tokenizer.encode(prompt, add_special_tokens=False)
        processor = make_logits_processor(tokenizer, row['context'], len(tokens), stop)
        answer = generate(model, tokenizer, prompt=tokens, max_tokens=max_tokens,
                          sampler=make_sampler(temp=0), logits_processors=[processor], verbose=False)
        prediction = {'id': row['id'], 'prediction': answer}
        try:
            processor.validate_output(answer)
        except (ValueError, TypeError) as error:
            # Preserve raw output: cap/truncation must be scored as malformed,
            # never repaired, silently dropped, or mapped to refusal.
            prediction['decoding_error'] = str(error)
        predictions.append(prediction)
    return predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--backend', choices=('glimmer', 'predictions'), default='predictions')
    parser.add_argument('--predictions', type=Path)
    parser.add_argument('--model', type=Path, default=ROOT / 'models/glimmer-30b-4bit')
    parser.add_argument('--adapter', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-tokens', type=int, default=256)
    parser.add_argument('--sealed-final', action='store_true')
    args = parser.parse_args()
    if 'sealed' in str(args.data).lower() and not args.sealed_final:
        parser.error('sealed evaluation requires --sealed-final after recipe/adapter freeze')
    if args.output.exists() or args.output.with_suffix('.predictions.jsonl').exists():
        parser.error('original evaluation exists; refusing overwrite')
    if args.backend == 'predictions' and not args.predictions:
        parser.error('--predictions required')
    check_admission()
    rows = read_jsonl(args.data)
    predictions = (run_glimmer(rows, args.model, args.adapter, args.max_tokens)
                   if args.backend == 'glimmer' else read_jsonl(args.predictions))
    report = score_predictions(rows, predictions)
    report.update(data_sha256=sha256(args.data), backend=args.backend,
                  model=str(args.model) if args.backend == 'glimmer' else None,
                  adapter=str(args.adapter) if args.adapter else None,
                  schema_constrained=args.backend == 'glimmer',
                  answer_language='reader-schema-v3' if args.backend == 'glimmer' else None,
                  decoding_policy=DECODING_POLICY if args.backend == 'glimmer' else 'recorded-predictions',
                  decoder_sha256=sha256(HERE / 'decoder.py') if args.backend == 'glimmer' else None,
                  domain_words_sha256=sha256(HERE / 'domain_words.py') if args.backend == 'glimmer' else None,
                  recorded_prediction_count=len(predictions),
                  adapter_sha256=sha256(args.adapter / 'adapters.safetensors') if args.adapter else None,
                  schema_mask_scope='context bays, physical tiers and explicitly available IMDG classes'
                  if args.backend == 'glimmer' else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.backend == 'glimmer':
        with args.output.with_suffix('.predictions.jsonl').open('x') as stream:
            stream.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in predictions))
    with args.output.open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {'details', 'by_family'}}, indent=2))


if __name__ == '__main__':
    main()
