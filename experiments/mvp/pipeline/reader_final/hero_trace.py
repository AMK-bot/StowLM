"""Record the hero sentence, typed and from its radio transcript, through the adopted reader.

Same recorder, prompt, mask and greedy decoding as mask_study and real-block-trace.json. Every step
keeps the raw top-8 and the chosen token; an override is any step whose raw top token the mask refused.
Writes xray/fixtures/hero-trace.json once; an existing file is never overwritten.
Usage: python -m experiments.mvp.pipeline.reader_final.hero_trace
"""
from __future__ import annotations

import json
from time import perf_counter

from experiments.mvp.reader.v3 import mask_study
from .read_all import LATTICE, instance_of
from .reader import ROOT, identity

OUTPUT = ROOT / 'experiments/mvp/pipeline/xray/fixtures/hero-trace.json'
SELECTION = ROOT / 'experiments/mvp/pipeline/radio/hero-selection.json'
LABEL = 'Recorded: adopted reader swa-last-5 on the laptop'


def overrides(trace):
    return [{'step': step['step'], 'wanted': step['top_k'][0]['token'], 'probability': step['top_k'][0]['probability'],
             'reason': step['top_k'][0]['reason'], 'chosen': step['chosen']['token'],
             'end_token_swap': bool(mask_study.end_token_swap(step))}
            for step in trace['steps'] if step['top_k'][0]['blocked']]


def main():
    if OUTPUT.exists():
        raise SystemExit(f'{OUTPUT} exists; not overwritten')
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    known = identity()
    if known['name'] != 'swa-last-5':
        raise SystemExit('adopted reader is not swa-last-5')
    selection = json.loads(SELECTION.read_text())
    hero = json.loads((LATTICE / 'hero.json').read_text())['hero']
    context = instance_of(hero['path'], {})
    inputs = [('typed', selection['hero_sentence']), ('radio', selection['clips']['radio']['transcript'])]
    began = perf_counter()
    model, tokenizer = load(str(mask_study.MODEL), adapter_path=known['adapter_path'])
    loaded = perf_counter() - began
    mx.random.seed(mask_study.SEED)
    traces = []
    for source, sentence in inputs:
        started = perf_counter()
        raw = mask_study.record_row({'sentence': sentence, 'context': context}, model, tokenizer,
                                    known['adapter_sha256'], min(set(tokenizer.eos_token_ids)), stream_generate)
        seconds = perf_counter() - started
        trace = mask_study.labelled(raw, LABEL, 'Observed local inference; no API spend. Hero sentence, not selected from a sample.', 1)
        trace.pop('selection', None)
        from experiments.mvp.pipeline.xray.trace import seal_trace, validate_trace
        trace = validate_trace(seal_trace(trace))
        changed = overrides(trace)
        traces.append({'input': source, 'sentence': sentence, 'reading': json.loads(trace['output']) if trace['complete'] else None,
                       'reads_as_stored_rule': trace['complete'] and json.loads(trace['output']) == selection['canonical_rule'],
                       'wall_s': seconds, 'overrides': changed,
                       'overrides_excluding_end_token_swap': sum(not row['end_token_swap'] for row in changed), 'trace': trace})
    record = {'format': 'xray.hero-traces/1', 'label': LABEL, 'adapter': known['name'],
              'adapter_sha256': known['adapter_sha256'], 'decoder_sha256': known['decoder_sha256'],
              'domain_words_sha256': known['domain_words_sha256'], 'decoding_policy': known['decoding_policy'],
              'context_source': hero['path'], 'model_load_s': loaded, 'wall_s': perf_counter() - began,
              'stored_rule': selection['canonical_rule'], 'traces': traces}
    with OUTPUT.open('x') as stream:
        stream.write(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'wall_s': record['wall_s'], 'load_s': loaded,
                      'per_trace': [(t['input'], t['wall_s'], len(t['overrides']), t['overrides_excluding_end_token_swap'],
                                     t['reads_as_stored_rule'], [(o['wanted'], o['chosen'], o['reason']) for o in t['overrides']])
                                    for t in traces]}))


if __name__ == '__main__':
    main()
