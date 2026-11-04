"""D224 v3 always runs; frozen source then sealed scoring and v2 reference.

Default prints commands without model/data access. No tuning after final freeze.
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
from experiments.mvp.reader.v3 import train
from experiments.mvp.reader.v3.train import HERE, READER, ROOT
from experiments.mvp.reader.v3.evaluate import DECODING_POLICY

RESULTS = HERE / 'results'
ADAPTER = HERE / 'adapter-v3'
SAMPLE = HERE / 'valid-sample.jsonl'
ENV = {**os.environ, 'TOKENIZERS_PARALLELISM': 'false', 'UV_CACHE_DIR': str(ROOT / '.uv-cache'),
       'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
       'VECLIB_MAXIMUM_THREADS': '1', 'NUMEXPR_NUM_THREADS': '1'}


def log(**fields):
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / 'gpu-run.jsonl').open('a') as stream:
        stream.write(json.dumps(fields) + '\n')


def freeze(stage, check=False):
    command = [sys.executable, '-m', 'experiments.mvp.reader.v3.freeze', '--stage', stage]
    if check:
        command.append('--check')
    subprocess.run(command, cwd=ROOT, env=ENV, check=True, stdout=subprocess.DEVNULL)


class Checker:
    def __init__(self):
        self.history = []

    def on_progress(self, **fields):
        log(event='train', **fields)

    def __call__(self, *, model, iteration, val_loss, deadline):
        if not iteration:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 'hard stop: three elapsed hours including validation'
        folder = HERE / 'checks' / f'{iteration:06d}'
        folder.mkdir(parents=True, exist_ok=False)
        shutil.copy(ADAPTER / 'adapter_config.json', folder / 'adapter_config.json')
        train.save_weights(model, folder / 'adapters.safetensors')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 'hard stop: three elapsed hours including validation'
        output = folder / 'valid-sample.json'
        command = [sys.executable, '-m', 'experiments.mvp.reader.v3.evaluate', '--data', str(SAMPLE),
                   '--backend', 'glimmer', '--adapter', str(folder), '--output', str(output)]
        try:
            subprocess.run(command, cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL,
                           check=True, timeout=remaining)
        except subprocess.TimeoutExpired:
            return 'hard stop: three elapsed hours during held-out check'
        result = json.loads(output.read_text())
        if result.get('decoding_policy') != DECODING_POLICY or not result.get('schema_constrained'):
            raise ValueError('validation did not use the frozen schema mask')
        self.history.append(result['exact_match'])
        log(event='valid-check', iteration=iteration, exact=result['exact_match'], val_loss=val_loss)
        if plateaued(self.history):
            return 'passed 95 percent and no improvement across two checks'
        return None


def train_only():
    train.check_admission()
    freeze('recipe', check=True)
    if (RESULTS / 'final-freeze.json').exists():
        raise ValueError('sealed phase started; retraining is forbidden')
    if ADAPTER.exists() and any(ADAPTER.iterdir()):
        raise ValueError('adapter-v3 is nonempty; no second run or silent optimizer resume')
    cfg, model, rows, valid, manifest = train.preflight()
    expected = ''.join(json.dumps(row, ensure_ascii=False) + '\n'
                       for row in fixed_sample(valid, n=80, seed=199))
    if SAMPLE.exists() and SAMPLE.read_text() != expected:
        raise ValueError('fixed validation sample changed')
    SAMPLE.write_text(expected)
    started = time.monotonic()
    source = Path(manifest['source_adapter'])
    report = train.run_train(rows, valid, model, source, ADAPTER,
                             cfg, manifest, callback=Checker(), started=started)
    freeze('recipe', check=True)
    log(event='stopped', **report)
    print(json.dumps(report, indent=2))


def evaluate_final(data, name, adapter=None):
    from experiments.mvp.reader.evaluate import read_jsonl, score_predictions
    output = RESULTS / name
    if output.exists():
        report = json.loads(output.read_text())
        if (report.get('data_sha256') != train.sha256(data)
                or report.get('backend') != 'glimmer'
                or report.get('adapter') != (str(adapter) if adapter else None)
                or report.get('model') != str(ROOT / 'models/glimmer-30b-4bit')
                or report.get('decoding_policy') != DECODING_POLICY
                or report.get('decoder_sha256') != train.sha256(HERE / 'decoder.py')
                or report.get('domain_words_sha256') != train.sha256(HERE / 'domain_words.py')
                or report.get('adapter_sha256') != (train.sha256(adapter / 'adapters.safetensors') if adapter else None)
                or not report.get('schema_constrained')):
            raise ValueError('existing final arm has incompatible provenance')
        scored = score_predictions(read_jsonl(data), read_jsonl(output.with_suffix('.predictions.jsonl')))
        if any(report.get(key) != value for key, value in scored.items()):
            raise ValueError('existing final metrics differ from original predictions')
        return
    command = [sys.executable, '-m', 'experiments.mvp.reader.v3.evaluate', '--data', str(data),
               '--backend', 'glimmer', '--output', str(output), '--sealed-final']
    if adapter:
        command += ['--adapter', str(adapter)]
    subprocess.run(command, cwd=ROOT, env=ENV, check=True, stdout=subprocess.DEVNULL)


def validate_spark(spark, data):
    ids = [row['id'] for row in spark.get('details', [])]
    if (spark.get('backend') != 'spark' or spark.get('count', 0) < 120
            or spark.get('loosening_count', 0) < 40 or spark.get('missing_count') != 0
            or spark.get('recorded_prediction_count') != spark.get('count')
            or len(ids) != spark.get('count') or len(set(ids)) != len(ids)
            or spark.get('data_sha256') != train.sha256(data)
            or not spark.get('schema_constrained')
            or spark.get('answer_language') != 'reader-schema-v3'):
        raise ValueError('complete historical schema-constrained Spark reference required before final GPU inference')


def freeze_v2_reference(source):
    """Bind stopped v2 bytes before its first same-seal inference, never reroute.

    This separate immutable freeze permits v3 scoring to finish while v2 is
    still running. A later score-final invocation can add the required reference
    without changing the final v3 freeze or any existing prediction artifact.
    """
    from experiments.mvp.reader.freeze_v2 import record, verify
    source = Path(source).resolve()
    if source.name != 'adapter-v2':
        raise ValueError('same-seal reference must be the stopped adapter-v2')
    train.require_stopped_source(source)
    files = [source / name for name in ('adapters.safetensors', 'adapter_config.json', 'STOPPED.json')]
    files += [RESULTS / 'final-freeze.json', HERE / 'decoder.py', HERE / 'domain_words.py', HERE / 'evaluate.py']
    frozen = {'version': 'reader-v3-v2-reference-freeze/1',
              'source_adapter': str(source),
              'source_adapter_sha256': train.sha256(source / 'adapters.safetensors'),
              'files_sha256': record(files, ROOT),
              'policy': 'Stopped v2, same sealed-v3 population and current local mask; no adoption until reference available.'}
    path = RESULTS / 'v2-reference-freeze.json'
    if path.exists():
        previous = json.loads(path.read_text())
        verify(previous, ROOT)
        if previous != frozen:
            raise ValueError('v2 reference selection cannot change')
    else:
        with path.open('x') as stream:
            stream.write(json.dumps(frozen, indent=2) + '\n')
    return frozen


def stopped_v2_reference(source):
    """Read-only readiness probe; never delay v3 or substitute another adapter."""
    source = Path(source)
    try:
        train.require_stopped_source(source)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return source


def score_final(spark_summary, adapter_v2_ref=None):
    from experiments.mvp.reader.v3.gates import gate_report
    from experiments.mvp.reader.v3.adoption import adoption_report
    train.check_admission()
    freeze('recipe', check=True)
    if not (ADAPTER / 'STOPPED.json').exists():
        raise ValueError('training must stop and record its final adapter before sealing')
    train.require_stopped_source(ADAPTER)
    data = HERE / 'sealed/cases.jsonl'
    spark = json.loads(spark_summary.read_text())
    validate_spark(spark, data)
    # Freeze binds the canonical Spark artifact, never an alternate summary.
    if spark_summary.resolve() != (HERE / 'sealed/spark-summary.json').resolve():
        raise ValueError('only the frozen canonical Spark summary may be used')
    freeze('final', check=(RESULTS / 'final-freeze.json').exists())
    requested_reference = Path(adapter_v2_ref or READER / 'adapter-v2')
    reference_freeze = RESULTS / 'v2-reference-freeze.json'
    if reference_freeze.exists():
        bound = json.loads(reference_freeze.read_text())
        if Path(bound['source_adapter']) != requested_reference.resolve():
            raise ValueError('v2 reference override differs from immutable selection')
        reference = requested_reference
        # A previously pinned reference can never silently become "pending".
        train.require_stopped_source(reference)
    else:
        reference = stopped_v2_reference(requested_reference)
    if reference is not None:
        # Bind all reference bytes before evaluate_final opens any sealed rows.
        freeze_v2_reference(reference)
    evaluate_final(data, 'sealed-glimmer-tuned.json', ADAPTER)
    evaluate_final(data, 'sealed-glimmer-untrained.json')
    if reference is not None:
        evaluate_final(data, 'sealed-glimmer-v2-reference.json', reference)
    read = lambda path: json.loads(path.read_text())
    report = gate_report(read(RESULTS / 'sealed-glimmer-tuned.json'),
                         read(RESULTS / 'sealed-glimmer-untrained.json'), spark)
    output = RESULTS / 'gates.json'
    if output.exists():
        if json.loads(output.read_text()) != report:
            raise ValueError('original gate report changed')
    else:
        with output.open('x') as stream:
            stream.write(json.dumps(report, indent=2) + '\n')
    v2_report = read(RESULTS / 'sealed-glimmer-v2-reference.json') if reference else None
    adoption = adoption_report(read(RESULTS / 'sealed-glimmer-tuned.json'),
                               read(RESULTS / 'sealed-glimmer-untrained.json'), spark,
                               v2_reference=v2_report)
    # Pending and ready states have distinct immutable artifacts. Completing v2
    # later adds the decision; it never overwrites a recorded pending assessment.
    adoption_path = RESULTS / ('adoption-ready.json' if reference else 'adoption-pending.json')
    if adoption_path.exists():
        if read(adoption_path) != adoption:
            raise ValueError('original adoption assessment changed')
    else:
        with adoption_path.open('x') as stream:
            stream.write(json.dumps(adoption, indent=2) + '\n')
    freeze('final', check=True)
    print(json.dumps({'gates': report, 'adoption': adoption}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--train-only', action='store_true')
    mode.add_argument('--score-final', action='store_true')
    parser.add_argument('--spark-summary', type=Path, default=HERE / 'sealed/spark-summary.json')
    parser.add_argument('--adapter-v2-ref', type=Path, default=READER / 'adapter-v2',
                        help='stopped v2 for same-sealed-v3 comparison; unfinished means adoption pending')
    args = parser.parse_args()
    if args.train_only:
        train_only()
    elif args.score_final:
        score_final(args.spark_summary, args.adapter_v2_ref)
    else:
        print(json.dumps({'device_loaded': False, 'sealed_read': False, 'hard_stop_seconds': 10800,
                          'run_policy': 'D224: v3 always runs regardless of v2 gates',
                          'commands': ['python -m experiments.mvp.reader.v3.train --data-only --tokenize-check',
                                       'python -m experiments.mvp.reader.v3.freeze --stage recipe',
                                       'python -m experiments.mvp.reader.v3.gpu_run --train-only',
                                       'python -m experiments.mvp.reader.v3.gpu_run --score-final']}, indent=2))


if __name__ == '__main__':
    main()
