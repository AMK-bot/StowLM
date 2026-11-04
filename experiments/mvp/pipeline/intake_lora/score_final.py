"""One sealed score after CPU preparation and an explicitly completed GPU run.

An interrupted score stays consumed. Never remove the marker to rerun or tune.
This command is handed to the Claude executive; preparation never invokes it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .evaluate import HERE, ROOT, arm_report, digest, run_glimmer
from .gates import FREEZE_UTC, gate_report
from . import artifacts

SEALED = HERE / 'sealed/sealed.jsonl'


def sealed_registration(manifest):
    """Read only public registration metadata, never the sealed file."""
    candidates = manifest.get('files', {})
    for key, value in candidates.items():
        if str(key).endswith('sealed.jsonl'):
            if isinstance(value, dict):
                return value
            return {'sha256': value, 'count': 200}
    raise ValueError('sealed hash registration missing')


def freeze_final(adapter, output):
    from .train import check_recipe_freeze
    adapter, output = artifacts._paths(adapter, output)
    recipe = check_recipe_freeze()
    if output.exists():
        raise ValueError('one-shot output folder already exists')
    if not (adapter / 'STOPPED.json').exists() or not (adapter / 'adapters.safetensors').exists():
        raise ValueError('a stopped adapter with complete weights is required')
    stop = json.loads((adapter / 'STOPPED.json').read_text())
    if (stop.get('adapter_sha256') != digest(adapter / 'adapters.safetensors')
        or stop.get('checkpoint_usable') is False or stop.get('source_adapter', 'missing') is not None):
        raise ValueError('stopped adapter hash mismatch')
    run = json.loads((adapter / 'run.json').read_text())
    if (run.get('source_adapter', 'missing') is not None
        or run.get('config_sha256') != digest(HERE / 'config.json')):
        raise ValueError('adapter was not produced by the registered bare-base recipe')
    manifest = json.loads((HERE / 'data/manifest.json').read_text())
    registration = sealed_registration(manifest)
    if registration.get('count') != 200 or not registration.get('sha256'):
        raise ValueError('registered sealed population must be 200')
    # Bind the complete runtime inventory and every checked recipe dependency.
    # No model weights are loaded and no sealed bytes are opened by this step.
    files = artifacts.frozen_files(adapter, recipe)
    frozen = {'version': artifacts.FINAL_VERSION,
              'created_utc': datetime.now(timezone.utc).isoformat(),
              'sealed_registration': registration,
              'files_sha256': {str(p.relative_to(ROOT)): digest(p) for p in files},
              'policy': 'one score; report all gates; no repair or repeated attempts'}
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'freeze.json').open('x') as stream:
        stream.write(json.dumps(frozen, indent=2) + '\n')
    return frozen


def verify_freeze(frozen):
    artifacts.verify_freeze(frozen, HERE / 'adapter-intake-v1')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', type=Path, default=HERE / 'adapter-intake-v1')
    parser.add_argument('--output', type=Path, default=HERE / 'results/sealed-final')
    parser.add_argument('--execute-once', action='store_true')
    parser.add_argument('--gpu-lease', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute_once:
        parser.error('explicit --execute-once required; this opens sealed data once')
    from .gpu_run import wait_admission
    wait_admission(args.gpu_lease, seconds=0)
    frozen = freeze_final(args.adapter, args.output)
    freeze_sha = digest(args.output / 'freeze.json')
    marker = HERE / 'sealed/SCORE_CONSUMED.json'
    with marker.open('x') as stream:
        stream.write(json.dumps({'freeze_sha256': freeze_sha,
                    'started_utc': datetime.now(timezone.utc).isoformat()}) + '\n')
    # First and only opening of sealed bytes. Integrity and row decoding share it.
    import hashlib
    raw = SEALED.read_bytes()
    if hashlib.sha256(raw).hexdigest() != frozen['sealed_registration']['sha256']:
        raise ValueError('sealed bytes differ from registration')
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    if len(rows) != 200:
        raise ValueError('sealed count mismatch')
    from .spark_baseline import run_spark
    reports = []
    for name in ('spark', 'untrained', 'tuned'):
        wait_admission(args.gpu_lease, seconds=0)
        verify_freeze(frozen)
        adapter = args.adapter if name == 'tuned' else None
        predictions, error = [], None
        with (args.output / f'{name}.predictions.jsonl').open('x') as stream:
            def persist(prediction):
                stream.write(json.dumps(prediction, ensure_ascii=False) + '\n')
                stream.flush()
                predictions.append(prediction)
            try:
                if name == 'spark':
                    run_spark(rows, on_prediction=persist)
                else:
                    run_glimmer(rows, adapter=adapter, on_prediction=persist)
            except Exception as exc:
                # Preserve partial raw results and continue every remaining arm.
                # No retry, repair or billing error body is emitted.
                error = type(exc).__name__
        report = arm_report(rows, predictions, arm=name,
                            data_sha256=frozen['sealed_registration']['sha256'], adapter=adapter)
        report['freeze_sha256'] = freeze_sha
        if error:
            report['execution_error'] = error
        with (args.output / f'{name}.json').open('x') as stream:
            stream.write(json.dumps(report, indent=2) + '\n')
        reports.append(report)
    verify_freeze(frozen)
    spark, untrained, tuned = reports
    try:
        report = gate_report(tuned, untrained, spark)
    except ValueError as error:
        report = {'all_pass': False, 'decision': 'cut', 'fallback': 'Spark intake',
                  'reason': str(error), 'arms': {a['arm']: a for a in reports},
                  'I1': False, 'I2': False, 'I3': False, 'I4': False}
    report['completed_utc'] = datetime.now(timezone.utc).isoformat()
    report['freeze_utc'] = FREEZE_UTC
    report['freeze_sha256'] = freeze_sha
    if datetime.fromisoformat(report['completed_utc']) > datetime.fromisoformat(FREEZE_UTC):
        report.update(decision='cut', fallback='Spark intake', reason='result completed after pipeline freeze')
    with (args.output / 'gates.json').open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    if report.get('all_pass') is True and report.get('decision') == 'eligible':
        artifacts.write_adoption(args.adapter, args.output)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
