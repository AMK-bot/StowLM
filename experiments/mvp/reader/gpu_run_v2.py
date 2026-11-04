"""D219 GPU-owner runner. Default prints commands; no model or sealed rows read.

--train-only performs weight warm-start and held-out checks. --score-final is a
separate, irreversible sealed evaluation after the final freeze. No automatic
training follows a sealed score, and the v1 runner is never executed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from experiments.mvp.reader.gpu_run import fixed_sample, plateaued
from experiments.mvp.reader.train import HERE, ROOT
from experiments.mvp.reader import train_v2

RESULTS = HERE / 'results-v2'
ADAPTER = HERE / 'adapter-v2'
SAMPLE = HERE / 'data-v2-valid-sample.jsonl'
ENV = {**os.environ, 'TOKENIZERS_PARALLELISM': 'false', 'UV_CACHE_DIR': str(ROOT / '.uv-cache'),
       'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}


def log(**fields):
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / 'gpu-run.jsonl').open('a') as stream:
        stream.write(json.dumps(fields) + '\n')


def freeze(stage, check=False):
    command = [sys.executable, '-m', 'experiments.mvp.reader.freeze_v2', '--stage', stage]
    if check:
        command.append('--check')
    subprocess.run(command, cwd=ROOT, env=ENV, check=True, stdout=subprocess.DEVNULL)


class Checker:
    def __init__(self):
        self.history = []

    def on_progress(self, **fields):
        log(event='train', **fields)

    def __call__(self, *, model, iteration, val_loss, deadline):
        if not iteration:  # Preserve D203's initial validation-loss-only check.
            return None
        folder = HERE / 'checks-v2' / f'{iteration:06d}'
        folder.mkdir(parents=True, exist_ok=False)
        shutil.copy(ADAPTER / 'adapter_config.json', folder / 'adapter_config.json')
        train_v2.save_weights(model, folder / 'adapters.safetensors')
        output = folder / 'valid-sample.json'
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 'hard stop: three elapsed hours including validation'
        command = [sys.executable, '-m', 'experiments.mvp.reader.evaluate', '--data', str(SAMPLE),
                   '--backend', 'glimmer', '--adapter', str(folder), '--output', str(output)]
        try:
            subprocess.run(command, cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, check=True,
                           timeout=remaining)
        except subprocess.TimeoutExpired:
            # subprocess.run stops only the exact child it started.
            return 'hard stop: three elapsed hours during held-out check'
        result = json.loads(output.read_text())
        self.history.append(result['exact_match'])
        log(event='valid-check', iteration=iteration, exact=result['exact_match'], val_loss=val_loss)
        if plateaued(self.history):
            return 'passed 95 percent and no improvement across two checks'
        return None


def train_only():
    from experiments.mvp.reader.evaluate import read_jsonl
    freeze('recipe', check=True)
    if (ADAPTER / 'STOPPED.json').exists():
        raise ValueError('adapter-v2 is already final; refusing a second tuning run')
    cfg, model, train, valid, manifest = train_v2.preflight(
        HERE / 'data-v2', HERE / 'adapter-v1', ADAPTER, HERE / 'config-v2.json')
    expected_sample = ''.join(json.dumps(row, ensure_ascii=False) + '\n'
                              for row in fixed_sample(valid, n=80, seed=199))
    if SAMPLE.exists() and SAMPLE.read_text() != expected_sample:
        raise ValueError('held-out sample differs from fixed valid sample')
    SAMPLE.write_text(expected_sample)
    started = time.monotonic()
    report = train_v2.run_train(train, valid, model, HERE / 'adapter-v1', ADAPTER,
                                cfg, manifest, callback=Checker(), started=started)
    freeze('recipe', check=True)
    log(event='stopped', **report)
    print(json.dumps(report, indent=2))


def evaluate_final(data, name, adapter=None):
    output = RESULTS / name
    if output.exists():
        # An interrupted final-scoring command can finish its missing arm while
        # preserving each original artifact. Recompute metrics from original raw
        # predictions and exact frozen rows; never generate the existing arm twice.
        from experiments.mvp.reader.evaluate import read_jsonl, score_predictions
        report = json.loads(output.read_text())
        if (report.get('data_sha256') != train_v2.sha256(data)
                or report.get('backend') != 'glimmer'
                or report.get('adapter') != (str(adapter) if adapter else None)
                or report.get('model') != str(ROOT / 'models/glimmer-30b-4bit')):
            raise ValueError('existing final metrics have incompatible provenance')
        scored = score_predictions(read_jsonl(data), read_jsonl(output.with_suffix('.predictions.jsonl')))
        if any(report.get(key) != value for key, value in scored.items()):
            raise ValueError('existing final metrics differ from their raw predictions')
        return
    command = [sys.executable, '-m', 'experiments.mvp.reader.evaluate', '--data', str(data),
               '--backend', 'glimmer', '--output', str(output), '--sealed-final']
    if adapter:
        command += ['--adapter', str(adapter)]
    subprocess.run(command, cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, check=True)


def score_final(spark_summary: Path):
    from experiments.mvp.reader.gates_v2 import gate_report_v2
    freeze('recipe', check=True)
    final_freeze = RESULTS / 'final-freeze.json'
    freeze('final', check=final_freeze.exists())
    # All selection is now fixed; only here may evaluate open sealed-v2 rows.
    data = HERE / 'sealed-v2' / 'cases.jsonl'
    spark = json.loads(spark_summary.read_text())
    spark_ids = [row['id'] for row in spark.get('details', [])]
    if (spark.get('backend') != 'spark' or spark.get('count', 0) < 120
            or spark.get('loosening_count', 0) < 30 or spark.get('missing_count') != 0
            or spark.get('recorded_prediction_count') != spark.get('count')
            or len(spark_ids) != spark.get('count') or len(set(spark_ids)) != len(spark_ids)
            or spark.get('data_sha256') != train_v2.sha256(data)):
        raise ValueError('complete Spark zero-shot metrics on the frozen sealed-v2 are required before final inference')
    evaluate_final(data, 'sealed-glimmer-tuned.json', ADAPTER)
    evaluate_final(data, 'sealed-glimmer-untrained.json')
    read = lambda path: json.loads(path.read_text())
    report = gate_report_v2(read(RESULTS / 'sealed-glimmer-tuned.json'),
                            read(RESULTS / 'sealed-glimmer-untrained.json'), spark)
    gates = RESULTS / 'gates.json'
    if gates.exists():
        if json.loads(gates.read_text()) != report:
            raise ValueError('existing gates differ from original final metrics')
    else:
        gates.write_text(json.dumps(report, indent=2) + '\n')
    freeze('final', check=True)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--train-only', action='store_true')
    mode.add_argument('--score-final', action='store_true')
    parser.add_argument('--spark-summary', type=Path, default=HERE / 'sealed-v2' / 'spark-summary.json')
    args = parser.parse_args()
    if args.train_only:
        train_only()
    elif args.score_final:
        score_final(args.spark_summary)
    else:
        print(json.dumps({'device_loaded': False, 'sealed_read': False,
                          'commands': ['python -m experiments.mvp.reader.train_v2 --tokenize-check',
                                       'python -m experiments.mvp.reader.freeze_v2 --stage recipe',
                                       'python -m experiments.mvp.reader.gpu_run_v2 --train-only',
                                       'python -m experiments.mvp.reader.gpu_run_v2 --score-final'],
                          'hard_stop_seconds': 10800}, indent=2))


if __name__ == '__main__':
    main()
