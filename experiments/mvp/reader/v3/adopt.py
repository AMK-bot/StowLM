"""Decide the adopted reader by D263, then finish its x-ray. No sealed row is read or rerun.

--decide scores adapter-v2 through select_adapter's exact path on the same 400 rows
and writes results-final/final-adoption.json from the stored sealed reports.
--xray runs the mask study on the adopted adapter if it isn't the one measured, and
records the real traces through it. A rerun resumes from what exists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from experiments.mvp.reader.v3 import mask_study, record_traces, select_adapter
from experiments.mvp.reader.v3.finish_run import RESULTS, log
from experiments.mvp.reader.v3.train import HERE, READER, ROOT, sha256

ADOPTION = RESULTS / 'final-adoption.json'
XRAY = RESULTS / 'final-xray.json'
SEALED = HERE / 'results'
V2 = READER / 'adapter-v2'
MARGIN_ROWS = 12  # 3 points of 400
RULE = ('D263, fixed by the leader before any result: after the mask study ends, score adapter-v2 through '
        "select_adapter's exact path on the same 400-row selection sample. Retain v2 as the adopted reader, per "
        "the pre-registered result, unless v2 scores more than 3 points below swa-last-5's 93.25 % there; in that "
        'case adopt swa-last-5 for its domain coverage.')


def decide(v2_count, swa_count):
    """Name of the adopted adapter. Exactly 3 points below does not switch. No score retains v2."""
    if v2_count is not None and swa_count - v2_count > MARGIN_ROWS:
        return 'swa-last-5'
    return 'adapter-v2'


def sealed_numbers(report):
    return {key: report[key] for key in ('count', 'exact_match_count', 'exact_match_pct', 'loosening_count',
                                         'false_acceptance_count')}


def v2_entry():
    return {'name': 'adapter-v2', 'path': str(V2), 'adapter_sha256': sha256(V2 / 'adapters.safetensors')}


def write_decision(selection, v2_score, read=None):
    read = read or (lambda name: json.loads((SEALED / name).read_text()))
    swa = selection['scores'][selection['selected']['name']]
    name = decide(v2_score.get('exact_match_count'), swa['exact_match_count'])
    adopted = selection['selected'] if name == selection['selected']['name'] else v2_entry()
    gates = read('gates.json')
    record = {'rule': RULE, 'outcome': name, 'adopted': adopted,
              'selection_400': {'swa-last-5': swa, 'adapter-v2': v2_score},
              'sealed_128': {'swa-last-5': sealed_numbers(read('sealed-glimmer-tuned.json')),
                             'adapter-v2': sealed_numbers(read('sealed-glimmer-v2-reference.json')),
                             'untrained': sealed_numbers(read('sealed-glimmer-untrained.json'))},
              'gates_swa_last_5': {key: gates[key] for key in ('R1', 'R2', 'R3', 'all_pass')},
              'preregistered_adoption_status': read('adoption-ready.json')['status'],
              'sealed_note': 'Sealed v3 is spent. Both sealed numbers stand whichever adapter wins.',
              'caveat': select_adapter.CAVEAT}
    ADOPTION.write_text(json.dumps(record, indent=2) + '\n')
    log(event='final-adoption', outcome=name, v2_400=v2_score.get('exact_match_count'))
    return record


def run_decide():
    if ADOPTION.exists():
        return
    selection = json.loads(select_adapter.OUTPUT.read_text())
    rows = len(select_adapter.read_jsonl(select_adapter.SAMPLE))
    report = select_adapter.evaluate_candidate(v2_entry(), rows)
    score = select_adapter.summary(report) if report else {'exact_match_count': None, 'failed': True}
    write_decision(selection, score)


def run_xray():
    adoption = json.loads(ADOPTION.read_text())
    adopted = adoption['adopted']
    measured = json.loads(mask_study.REPORT.read_text())
    if adopted['adapter_sha256'] == measured['adapter']['adapter_sha256']:
        report_path, report = mask_study.REPORT, measured
    else:
        report_path = mask_study.STUDY / 'report-adopted.json'
        report = mask_study.study(adopted, 'adopted', report_path, 'real-block-trace-adopted.json')
    failed = 0
    for sentence, name, context in record_traces.jobs():
        output = record_traces.TRACES / name
        if output.exists():
            continue
        command = [sys.executable, '-m', 'experiments.mvp.pipeline.xray.record', '--adapter', adopted['path'],
                   '--sentence', sentence, '--output', str(output)]
        if context:
            record_traces.HERO_CONTEXT.write_text(json.dumps(context) + '\n')
            command += ['--context', str(record_traces.HERO_CONTEXT)]
        code = subprocess.run(command, cwd=ROOT, env=record_traces.ENV).returncode
        log(event='xray-trace', sentence=sentence, output=str(output), exit_code=code)
        failed += bool(code)
    XRAY.write_text(json.dumps({'adopted': adopted, 'mask_report': str(report_path), 'outcome': report['outcome'],
                                'block_trace': report.get('trace'), 'block_shown': report.get('block_shown'),
                                'top_token_changes': report['adapter_arm'], 'x4_absent_bay': report['x4_absent_bay'],
                                'x4_unsupported': report['x4_unsupported']}, indent=2, ensure_ascii=False) + '\n')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=('decide', 'xray'))
    run_decide() if parser.parse_args().step == 'decide' else run_xray()
