"""D203 reader run on the GPU, alone: waits for the one-placement test, trains with train.py's recipe at one epoch at most, scores exact match on a fixed held-out validation
sample at each checkpoint, stops early once it passes 95 % and hasn't improved across two checks, hard stop 20:15 EDT, the latest checkpoint is final. Then sealed tuned, sealed
untrained, the family test and the gates. Each check runs evaluate.py's own inference in a child process while training waits, so training and scoring never share a model.
Usage: python -m experiments.mvp.reader.gpu_run [--now]
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADAPTER = HERE/'adapter-v1'
RESULTS = HERE/'results'
CONFIG = HERE/'config-d203.json'
CHECKS = HERE/'checks'
LOG = HERE/'gpu-run.log'
SAMPLE_PATH = HERE/'data-valid-sample.jsonl'
SAMPLE_N, TARGET = 80, .95
HARD_STOP = datetime.fromisoformat('2026-10-31T20:15:00-04:00')
WAIT_FOR = ROOT/'experiments/mvp/strategy/k1b/one-place/DONE'
ENV = {**os.environ, 'TOKENIZERS_PARALLELISM': 'false', 'UV_CACHE_DIR': str(ROOT/'.uv-cache')}


class Stop(Exception):
    pass


def cores():
    return subprocess.run(['sysctl', '-n', 'kern.sched_recommended_cores'], capture_output=True, text=True).stdout.strip()


def log(**fields):
    with open(LOG, 'a') as stream:
        stream.write(json.dumps(dict(at=datetime.now().astimezone().isoformat(timespec='seconds'), cores=cores(), **fields))+'\n')


def fixed_sample(rows, n=SAMPLE_N, seed=199):
    """A fixed sample of valid rows, spread evenly across paraphrase families, so every check sees the same sentences."""
    rng, families = random.Random(seed), {}
    for row in sorted(rows, key=lambda r: r['id']):
        families.setdefault(row['family'], []).append(row)
    for group in families.values():
        rng.shuffle(group)
    chosen, turn = [], 0
    while len(chosen) < min(n, len(rows)):
        for name in sorted(families):
            if turn < len(families[name]) and len(chosen) < n:
                chosen.append(families[name][turn])
        turn += 1
    return chosen


def plateaued(history, target=TARGET):
    """It has passed the target, and neither of the last two checks beats the best before them."""
    return len(history) >= 3 and max(history[:-2]) >= target and max(history[-2:]) <= max(history[:-2])


def past_hard_stop(now=None):
    return (now or datetime.now().astimezone()) >= HARD_STOP


def save_weights(model, path):
    import mlx.core as mx
    from mlx.utils import tree_flatten
    mx.save_safetensors(str(path), dict(tree_flatten(model.trainable_parameters())))


def score(iteration):
    """One checkpoint on the held-out sample, through evaluate.py in a child process."""
    folder = CHECKS/f'{iteration:06d}'
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(ADAPTER/'adapter_config.json', folder/'adapter_config.json')
    return folder


class Checker:
    """mlx_lm training callback: scores at each validation report, enforces the stop rules at each train report."""

    def __init__(self, model):
        self.model, self.history, self.stop = model, [], None

    def on_val_loss_report(self, info):
        iteration = info['iteration']+1
        if iteration <= 1:
            return
        folder = score(iteration)
        save_weights(self.model, folder/'adapters.safetensors')
        output = folder/'valid-sample.json'
        started = time.monotonic()
        subprocess.run([sys.executable, '-m', 'experiments.mvp.reader.evaluate', '--data', str(SAMPLE_PATH), '--backend', 'glimmer', '--adapter', str(folder), '--output', str(output)],
                       cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, check=True)
        result = json.loads(output.read_text())
        self.history.append(result['exact_match'])
        log(event='check', iteration=iteration, exact=result['exact_match'], n=result.get('count'), val_loss=info['val_loss'], score_s=round(time.monotonic()-started))
        if plateaued(self.history):
            self.stop = 'passed 95 % and stopped improving across two checks'

    def on_train_loss_report(self, info):
        if info['iteration'] % 100 == 0:
            log(event='train', iteration=info['iteration'], loss=info['train_loss'], tokens_per_second=info.get('tokens_per_second'))
        if self.stop is None and past_hard_stop():
            self.stop = 'hard stop 20:15 EDT'
        if self.stop:
            raise Stop(self.stop)


def train():
    import mlx.core as mx
    import mlx_lm.tuner.trainer as trainer
    from experiments.mvp.reader import train as recipe

    mx.set_cache_limit(8*2**30)
    original, held = trainer.train, {}

    def with_checks(**kw):
        held['model'] = kw['model']
        return original(**kw, training_callback=Checker(kw['model']))
    trainer.train = with_checks
    sys.argv = ['train', '--data-dir', str(HERE/'data'), '--adapter-dir', str(ADAPTER), '--config', str(CONFIG), '--execute']
    try:
        recipe.main()
        reason = 'one epoch finished'
    except Stop as stop:
        reason = str(stop)
        save_weights(held['model'], ADAPTER/'adapters.safetensors')
    (ADAPTER/'STOPPED.json').write_text(json.dumps(dict(reason=reason, at=datetime.now().astimezone().isoformat(timespec='seconds')), indent=2)+'\n')
    log(event='stopped', reason=reason)


def evaluate(data, output, adapter=None, sealed=False):
    if (RESULTS/output).exists():
        return
    command = [sys.executable, '-m', 'experiments.mvp.reader.evaluate', '--data', str(data), '--backend', 'glimmer', '--output', str(RESULTS/output)]
    command += ['--adapter', str(adapter)] if adapter else []
    command += ['--sealed-final'] if sealed else []
    subprocess.run(command, cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, check=True)
    log(event='scored', output=output)


def gates():
    from experiments.mvp.reader.evaluate import gate_report
    output = RESULTS/'gates.json'
    if output.exists():
        return
    read = lambda name: json.loads((RESULTS/name).read_text())
    result = gate_report(read('sealed-glimmer-tuned.json'), read('sealed-glimmer-untrained.json'), read('spark-summary.json'))
    output.write_text(json.dumps(result, indent=2)+'\n')
    log(event='gates', result={k: v for k, v in result.items() if not isinstance(v, (dict, list))})


def main():
    from experiments.mvp.reader.evaluate import read_jsonl
    while '--now' not in sys.argv and not WAIT_FOR.exists():
        time.sleep(30)
    log(event='start')
    if not SAMPLE_PATH.exists():
        SAMPLE_PATH.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in fixed_sample(read_jsonl(HERE/'data/valid.jsonl'))))
    if not (ADAPTER/'STOPPED.json').exists():
        train()
    evaluate(HERE/'data/sealed.jsonl', 'sealed-glimmer-tuned.json', ADAPTER, sealed=True)
    evaluate(HERE/'data/sealed.jsonl', 'sealed-glimmer-untrained.json', sealed=True)
    gates()
    evaluate(HERE/'data/test.jsonl', 'family-test-tuned.json', ADAPTER)
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| reader/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- reader/gpu (fix-t8-sft, 31 Oct): D203 reader training and scoring; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (HERE/'GPU.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
