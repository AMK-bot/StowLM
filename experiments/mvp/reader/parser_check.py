"""D224 owed check: load the adopted reader through brief's GlimmerParser and report seconds per sentence and memory on the GPU. Adopted means v3 if adoption-ready.json says so,
else v2 if its gates pass; with neither, the newest stopped adapter is checked and labelled unadopted. 30 fixed validation sentences, v3's masked decode; no sealed input.
Usage: python -m experiments.mvp.reader.parser_check
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from experiments.mvp.reader.pipeline import GlimmerParser
from experiments.mvp.reader.v3.evaluate import prompt_messages, render_prompt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SENTENCES = 30


def read(path):
    return json.loads(Path(path).read_text()) if Path(path).exists() else None


def choose():
    """(adapter folder, adopted?, reason)."""
    ready = read(HERE/'v3/results/adoption-ready.json')
    if ready and ready.get('adopt_v3') is True:
        return HERE/'v3/adapter-v3', True, 'v3 adoption-ready'
    gates = read(HERE/'results-v2/gates.json')
    if gates and all(gates.get(k) is True for k in ('R1', 'R2', 'R3')):
        return HERE/'adapter-v2', True, 'v2 passed its gates'
    for name, folder in (('v3', HERE/'v3/adapter-v3'), ('v2', HERE/'adapter-v2')):
        if (folder/'adapters.safetensors').exists():
            return folder, False, f'nothing adopted; newest stopped adapter is {name}'
    raise SystemExit('no stopped adapter')


def make_generate(model, tokenizer):
    import mlx.core as mx
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler
    from experiments.mvp.reader.v3.decoder import make_logits_processor
    from experiments.mvp.reader.schema import messages

    mx.random.seed(199)
    stop = min(set(tokenizer.eos_token_ids))

    def run(sentence, context):
        prompt = render_prompt(tokenizer, messages(sentence, context))
        tokens = tokenizer.encode(prompt, add_special_tokens=False)
        processor = make_logits_processor(tokenizer, context, len(tokens), stop)
        return generate(model, tokenizer, prompt=tokens, max_tokens=256, sampler=make_sampler(temp=0), logits_processors=[processor], verbose=False)
    return run


def main():
    import mlx.core as mx
    from mlx_lm import load
    folder, adopted, reason = choose()
    started = time.monotonic()
    mx.reset_peak_memory()
    model, tokenizer = load(str(ROOT/'models/glimmer-30b-4bit'), adapter_path=str(folder))
    load_s = time.monotonic()-started
    sha = hashlib.sha256((folder/'adapters.safetensors').read_bytes()).hexdigest()
    parser = GlimmerParser(make_generate(model, tokenizer), adapter_sha256=sha)
    rows = [json.loads(line) for line in (HERE/'v3/data-domain/valid.jsonl').read_text().splitlines()][::49][:SENTENCES]
    times, results = [], []
    for row in rows:
        began = time.monotonic()
        try:
            parser.parse(row['sentence'], row['context'])
            results.append('parsed')
        except Exception as error:
            results.append(type(error).__name__)
        times.append(time.monotonic()-began)
    report = dict(adapter=str(folder.relative_to(ROOT)), adapter_sha256=sha, adopted=adopted, reason=reason, sentences=len(rows), load_s=round(load_s, 1),
                  seconds_per_sentence=dict(mean=round(sum(times)/len(times), 2), first=round(times[0], 2), max=round(max(times), 2)),
                  peak_memory_gb=round(mx.get_peak_memory()/1e9, 2), outcomes={k: results.count(k) for k in sorted(set(results))})
    (HERE/'parser-check.json').write_text(json.dumps(report, indent=1)+'\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
