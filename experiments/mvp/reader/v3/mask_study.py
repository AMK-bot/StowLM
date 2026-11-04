"""How often does the schema mask change the top token on real logits? (X3, X4)

Rules are fixed in MASK-STUDY-RULE.md before any result. Sentences are public
validation rows, unedited, with the frozen prompt, decoder and greedy decoding.
Nothing here builds a sentence or prompt to force a block. Rows append to a JSONL
store, so a rerun resumes. Top-token probabilities are full-vocabulary and pre-mask.
Usage: python -m experiments.mvp.reader.v3.mask_study [--build-sample | --arm STORE [--adapter FOLDER]]
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from experiments.mvp.reader.evaluate import prompt_messages, read_jsonl, render_prompt, score_predictions
from experiments.mvp.reader.gpu_run import fixed_sample
from experiments.mvp.reader.v3.finish_run import RESULTS, log, wait_for_admission
from experiments.mvp.reader.v3.select_adapter import OUTPUT as SELECTION
from experiments.mvp.reader.v3.train import DATA, HERE, ROOT, sha256

SAMPLE = HERE / 'mask-sample.jsonl'
STUDY = RESULTS / 'mask-study'
REPORT = STUDY / 'report.json'
TRACES = ROOT / 'experiments/mvp/pipeline/xray/fixtures'
MODEL = ROOT / 'models/glimmer-30b-4bit'
SENTENCES, SEED, MIN_PROBABILITY, ALMOST_NONE = 200, 199, .01, 2
REASON_ORDER = ('bay_not_on_ship', 'tier_over_cap', 'not_in_grammar')
ATTEMPTS = 3
ERROR_RUN = 5  # this many failed rows in a row is a broken recorder, not bad rows
MIN_RECORDED = .95
ENV = {**os.environ, 'PYTHONPATH': 'src:experiments/mvp/frontier:experiments/mvp/frontier/lab'}


def expected_of(row):
    return json.loads(row['expected']) if isinstance(row['expected'], str) else row['expected']


def is_unsupported(row):
    """The 84 original unsupported rows; D224 domain rows have a domain_intent."""
    refusal = expected_of(row).get('refusal')
    return bool(refusal) and refusal.get('code') == 'unsupported' and row.get('domain_intent') is None


def is_absent_bay(row):
    """A refusal row naming a bay this ship doesn't have."""
    named = [int(n) for n in re.findall(r'(?i)\bbay\s+(\d+)', row['sentence'])]
    return bool(expected_of(row).get('refusal')) and any(n not in row['context']['bays'] for n in named)


def study_rows():
    sample = read_jsonl(SAMPLE)
    x4 = [row for row in read_jsonl(DATA / 'valid.jsonl') if is_unsupported(row) or is_absent_bay(row)]
    return sample, x4


def record_row(row, model, tokenizer, adapter_sha256, stop, stream):
    """One row through the frozen prompt and mask, as evaluate.py and record.py do."""
    from experiments.mvp.pipeline.xray.recorder import RecordingLogitsProcessor
    from experiments.mvp.reader.v3.decoder import make_logits_processor
    from mlx_lm.sample_utils import make_sampler
    prompt = render_prompt(tokenizer, prompt_messages(row))
    tokens = tokenizer.encode(prompt, add_special_tokens=False)
    recorder = RecordingLogitsProcessor(make_logits_processor(tokenizer, row['context'], len(tokens), stop),
                                        sentence=row['sentence'], top_k=8, tokenizer=tokenizer,
                                        model='muse-glimmer-30b', adapter_sha256=adapter_sha256,
                                        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
    chunks, generated = [], []
    for response in stream(model, tokenizer, prompt=tokens, max_tokens=256, sampler=make_sampler(temp=0),
                           logits_processors=[recorder]):
        chunks.append(response.text)
        generated.append(int(response.token))
    return recorder.finish(''.join(chunks), final_token_ids=generated, require_complete=False)


def load_store(path):
    """id -> record; a torn last line from a crash is dropped and that row reruns."""
    records = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            records[record['id']] = record
    return records


def run_rows(rows, path, record, wait=None):
    """Skip recorded rows; a stored error reruns. A run of errors stops the arm."""
    done = load_store(path)
    failures = 0
    if path.exists() and path.read_bytes()[-1:] not in (b'', b'\n'):
        with path.open('a') as stream:  # end a torn line, or the next row glues onto it
            stream.write('\n')
    for row in rows:
        if 'trace' in done.get(row['id'], {}):
            continue
        (wait or (lambda: wait_for_admission(note=log)))()
        try:
            entry = {'id': row['id'], 'trace': record(row)}
        except (ValueError, TypeError, KeyError, IndexError) as error:
            entry = {'id': row['id'], 'error': repr(error)}
        with path.open('a') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        done[row['id']] = entry
        failures = failures + 1 if 'error' in entry else 0
        if failures >= ERROR_RUN:
            raise SystemExit(f"{ERROR_RUN} rows failed in a row; last: {entry['error']}")
    return done


def unique(rows):
    return list({row['id']: row for row in rows}.values())


def run_arm(store, adapter=None):
    """adapter: a folder for the 200 + X4 rows; None is base Glimmer on the 200."""
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    sample, x4 = study_rows()
    rows = unique(sample + x4) if adapter else sample
    path = STUDY / f'{store}.jsonl'
    stored = load_store(path)
    if all('trace' in stored.get(row['id'], {}) for row in rows):
        return
    digest = sha256(Path(adapter) / 'adapters.safetensors') if adapter else base_digest()
    wait_for_admission(note=log)
    model, tokenizer = load(str(MODEL), adapter_path=str(adapter) if adapter else None)
    stops = set(tokenizer.eos_token_ids)
    if not stops:
        raise ValueError('tokenizer has no EOS token')
    mx.random.seed(SEED)
    run_rows(rows, path, lambda row: record_row(row, model, tokenizer, digest, min(stops), stream_generate))


def base_digest():
    """Trace format demands a digest; an unadapted model has none, so the base config's stands in."""
    return sha256(MODEL / 'config.json')


def end_token_swap(step):
    """The answer is finished and the model wants a different end token: same meaning, no block.

    Glimmer has several end tokens and the mask admits one. Wanting to stop early
    isn't this: then the chosen token continues, so is_stop is false.
    """
    top = step['top_k'][0]
    return step['is_stop'] and top['blocked'] and re.fullmatch(r'<\|[^|]*\|>', top['token']) is not None


def top_blocks(trace):
    """Steps where the mask refused the model's raw top token."""
    return [{'step': step['step'], 'token': step['top_k'][0]['token'], 'reason': step['top_k'][0]['reason'],
             'probability': step['top_k'][0]['probability'], 'chosen': step['chosen']['token']}
            for step in trace['steps'] if step['top_k'][0]['blocked'] and not end_token_swap(step)]


def real_blocks(trace):
    return [block for block in top_blocks(trace) if block['probability'] >= MIN_PROBABILITY]


def summarize(rows, records):
    traces = {row['id']: records[row['id']].get('trace') for row in rows if row['id'] in records}
    good = {key: value for key, value in traces.items() if value}
    blocks = [block for trace in good.values() for block in top_blocks(trace)]
    reasons = {reason: sum(block['reason'] == reason for block in blocks) for reason in REASON_ORDER}
    scored_rows = [row for row in rows if row['id'] in good]
    scored = score_predictions(scored_rows, [{'id': key, 'prediction': value['output']} for key, value in good.items()])
    return {'sentences': len(rows), 'recorded': len(good), 'errors': len(traces) - len(good),
            'tokens': sum(len(trace['steps']) for trace in good.values()),
            'tokens_top_changed': len(blocks),
            'end_token_swaps_excluded': sum(end_token_swap(step) for trace in good.values() for step in trace['steps']),
            'sentences_top_changed': sum(bool(top_blocks(trace)) for trace in good.values()),
            'sentences_real_block': sum(bool(real_blocks(trace)) for trace in good.values()),
            'by_reason': reasons, 'exact_match_count': scored['exact_match_count'],
            'accepted_loosening': scored['false_acceptance_count'], 'loosening_rows': scored['loosening_count']}


def best_block(rows, records):
    """The rule's pick: reason order, then top probability, then lowest id. Complete traces only."""
    ranked = []
    for row in rows:
        trace = (records.get(row['id']) or {}).get('trace')
        if not trace or not trace['complete']:
            continue
        for block in real_blocks(trace):
            ranked.append((REASON_ORDER.index(block['reason']), -block['probability'], row['id'], block, trace))
    return min(ranked, key=lambda item: item[:3])[3:] if ranked else None


def labelled(trace, label, note, sentences):
    """Relabel, reseal and revalidate. The label must never read as a demo of everything."""
    from experiments.mvp.pipeline.xray.trace import seal_trace, validate_trace
    value = deepcopy(trace)
    value['source']['label'] = label
    value['selection'] = {'selected_from_sentences': sentences, 'rule': 'MASK-STUDY-RULE.md'}
    value['stage_record']['notes'] = note
    return validate_trace(seal_trace(value))


def save_trace(trace, name):
    path = TRACES / name
    if not path.exists():
        with path.open('x') as stream:
            stream.write(json.dumps(trace, indent=2, ensure_ascii=False) + '\n')
    return str(path)


def bays_named(value):
    if isinstance(value, dict):
        return [bay for key, item in value.items()
                for bay in ([item] if 'bay' in key and type(item) is int else bays_named(item))]
    if isinstance(value, list):
        return [bay for item in value for bay in bays_named(item)]
    return []


def x4_outcome(row, trace):
    """refused / model_real_bay / mask_real_bay / other_rule / unfinished for one row."""
    if not trace['complete']:
        return 'unfinished'
    answer = json.loads(trace['output'])
    if answer.get('refusal'):
        return 'refused'
    named = [bay for bay in bays_named(answer.get('constraint')) if bay in row['context']['bays']]
    if not named:
        return 'other_rule'
    return 'mask_real_bay' if any(b['reason'] == 'bay_not_on_ship' for b in top_blocks(trace)) else 'model_real_bay'


def x4_table(rows, records):
    table, examples = {}, []
    for row in rows:
        trace = (records.get(row['id']) or {}).get('trace')
        outcome = x4_outcome(row, trace) if trace else 'error'
        table[outcome] = table.get(outcome, 0) + 1
        if outcome == 'mask_real_bay' and len(examples) < 5:
            examples.append({'id': row['id'], 'sentence': row['sentence'], 'output': trace['output'],
                             'blocked': top_blocks(trace)})
    return {'rows': len(rows), 'outcomes': table, 'mask_real_bay_examples': examples}


def run_child(name, adapter=None):
    command = [sys.executable, '-m', 'experiments.mvp.reader.v3.mask_study', '--arm', name]
    command += ['--adapter', str(adapter)] if adapter else []
    for attempt in range(ATTEMPTS):
        code = subprocess.run(command, cwd=ROOT, env=ENV).returncode
        log(event='mask-study-arm', arm=name, attempt=attempt, exit_code=code)
        if code == 0:
            return
    raise SystemExit(f'{name} arm failed {ATTEMPTS} times; rerun resumes')


def study(entry, store, report_path, block_trace):
    """One adapter through the rule. A base trace is shared: same model, same 200 rows."""
    STUDY.mkdir(parents=True, exist_ok=True)
    sample, x4 = study_rows()
    run_child(store, entry['path'])
    adapter = load_store(STUDY / f'{store}.jsonl')
    report = {'rule_sha256': sha256(HERE / 'MASK-STUDY-RULE.md'), 'sample_sha256': sha256(SAMPLE),
              'adapter': entry, 'adapter_arm': summarize(sample, adapter),
              'x4_absent_bay': x4_table([row for row in x4 if is_absent_bay(row)], adapter),
              'x4_unsupported': x4_table([row for row in x4 if is_unsupported(row)], adapter),
              'wording': 'Say illegal bays, never wrong bays: the mask knows the ship, not what was meant.'}
    if report['adapter_arm']['recorded'] < MIN_RECORDED * len(sample):
        raise SystemExit(f"only {report['adapter_arm']['recorded']} of {len(sample)} rows recorded")
    picked = None
    if report['adapter_arm']['sentences_real_block'] >= ALMOST_NONE:
        picked = best_block(sample, adapter)
    if picked:
        block, trace = picked
        report['outcome'] = 'adapter has real blocks'
        report['trace'] = save_trace(labelled(trace, f'Recorded answer, selected from {len(sample)} sentences',
                                              'Observed local inference; no API spend. Picked by the fixed rule.',
                                              len(sample)), block_trace)
    else:
        run_child('base')
        base = load_store(STUDY / 'base.jsonl')
        report['base_arm'] = summarize(sample, base)
        report['outcome'] = 'adapter has almost none; base Glimmer recorded under the same mask'
        picked = best_block(sample, base)
        if picked:
            block, trace = picked
            report['trace'] = save_trace(labelled(
                trace, f'Glimmer before fine-tuning, selected from {len(sample)} sentences',
                'Untrained base Glimmer, no adapter; adapter_sha256 holds the base config hash. '
                'Observed local inference; no API spend. Picked by the fixed rule.',
                len(sample)), 'before-fine-tuning-trace.json')
    report['block_shown'] = picked[0] if picked else None
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    log(event='mask-study', store=store, outcome=report['outcome'], trace=report.get('trace'),
        adapter_real_blocks=report['adapter_arm']['sentences_real_block'])
    return report


def main():
    print(json.dumps(study(json.loads(SELECTION.read_text())['selected'], 'adapter', REPORT,
                           'real-block-trace.json'), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-sample', action='store_true')
    parser.add_argument('--arm')
    parser.add_argument('--adapter')
    args = parser.parse_args()
    if args.build_sample:
        rows = fixed_sample(read_jsonl(DATA / 'valid.jsonl'), n=SENTENCES, seed=SEED)
        with SAMPLE.open('x') as stream:
            stream.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
        print(sha256(SAMPLE))
    elif args.arm:
        run_arm(args.arm, args.adapter)
    else:
        main()
