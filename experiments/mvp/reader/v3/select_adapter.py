"""Pick one adapter by the rule in SELECTION-RULE.md. Never reads sealed data.

Named select_adapter because a v3/select.py would shadow the standard library's
`select` whenever this folder lands on sys.path, as pytest's default import does.

Every candidate goes through evaluate.main: the frozen decoder, the same schema
mask, the same prompt, no repair. Highest exact match wins; ties go to the first
name in SIMPLICITY. A candidate with identical weights shares one evaluation.
Usage: python -m experiments.mvp.reader.v3.select_adapter [--build-sample]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from experiments.mvp.reader.freeze_v2 import verify
from experiments.mvp.reader.gpu_run import fixed_sample
from experiments.mvp.reader.evaluate import read_jsonl
from experiments.mvp.reader.v3 import evaluate
from experiments.mvp.reader.v3.finish_run import RESULTS, RETRY_EXIT, log, wait_for_admission
from experiments.mvp.reader.v3.gpu_run import ENV, SAMPLE as CHECK_SAMPLE
from experiments.mvp.reader.v3.swa import CANDIDATES
from experiments.mvp.reader.v3.train import DATA, HERE, ROOT, sha256

SIMPLICITY = ('final', 'best-val-loss', 'swa-last-3', 'swa-last-5')
SAMPLE = HERE / 'selection-sample.jsonl'
FREEZE = HERE / 'selection-freeze.json'
RULE = HERE / 'SELECTION-RULE.md'
SCORES = RESULTS / 'selection'
OUTPUT = RESULTS / 'selection.json'
PER_STRATUM, SEED = 50, 199
CAVEAT = ('Validation overstates sealed: v2 read 91.25 % on validation and 80.47 % sealed. '
          'These scores choose an adapter; they are not an accuracy claim.')


def stratum(row):
    expected = row['expected']
    expected = json.loads(expected) if isinstance(expected, str) else expected
    if expected.get('constraint'):
        return f"rule:{expected['constraint']['type']}"
    refusal = expected['refusal']
    return f"refusal:{refusal.get('code') if isinstance(refusal, dict) else refusal}"


def build_sample(valid, excluded, per_stratum=PER_STRATUM, seed=SEED):
    """Equal rows per rule type and refusal code, spread across families, disjoint."""
    strata = {}
    for row in valid:
        if row['id'] not in excluded:
            strata.setdefault(stratum(row), []).append(row)
    chosen = []
    for name in sorted(strata):
        if len(strata[name]) < per_stratum:
            raise ValueError(f'stratum {name} is too small')
        chosen += fixed_sample(strata[name], n=per_stratum, seed=seed)
    return sorted(chosen, key=lambda row: row['id'])


def sample_text(rows):
    return ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)


def expected_sample():
    return build_sample(read_jsonl(DATA / 'valid.jsonl'), {row['id'] for row in read_jsonl(CHECK_SAMPLE)})


def choose(scores):
    """scores: name -> exact match count, or None if evaluation failed. Pure rule."""
    counted = {name: count for name, count in scores.items() if count is not None}
    if not counted:
        raise ValueError('no candidate was evaluated')
    best = max(counted.values())
    return next(name for name in SIMPLICITY if counted.get(name) == best)


def validated(report, entry, rows):
    if (report.get('data_sha256') != sha256(SAMPLE) or report.get('count') != rows
            or report.get('missing_count') != 0 or not report.get('schema_constrained')
            or report.get('decoding_policy') != evaluate.DECODING_POLICY
            or report.get('decoder_sha256') != sha256(HERE / 'decoder.py')
            or report.get('domain_words_sha256') != sha256(HERE / 'domain_words.py')
            or report.get('adapter_sha256') != entry['adapter_sha256']):
        raise ValueError(f"{entry['name']}: evaluation does not match the frozen recipe")
    return report


def evaluate_candidate(entry, rows, run=subprocess.run, wait=None):
    """The report, or None if the child failed. A denied child is retried."""
    output = SCORES / f"{entry['name']}.json"
    if output.exists():
        return validated(json.loads(output.read_text()), entry, rows)
    output.with_suffix('.predictions.jsonl').unlink(missing_ok=True)  # only a crash leaves this alone
    command = [sys.executable, '-m', 'experiments.mvp.reader.v3.finish_run', '--check-child',
               '--data', str(SAMPLE), '--backend', 'glimmer', '--adapter', entry['path'],
               '--output', str(output)]
    while True:
        (wait or (lambda: wait_for_admission(note=log)))()
        code = run(command, cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL).returncode
        if code != RETRY_EXIT:
            break
    if code:
        log(event='selection-failed', candidate=entry['name'], exit_code=code)
        return None
    return validated(json.loads(output.read_text()), entry, rows)


def summary(report):
    return {key: report.get(key) for key in ('count', 'exact_match_count', 'exact_match', 'malformed_count',
                                             'loosening_count', 'false_acceptance_count')}


def score_all(candidates, rows, evaluate_one=evaluate_candidate):
    entries = sorted(candidates, key=lambda entry: SIMPLICITY.index(entry['name']))
    by_hash, results = {}, {}
    for entry in entries:
        first = by_hash.get(entry['adapter_sha256'])
        if first:
            results[entry['name']] = {**results[first], 'same_weights_as': first}
            continue
        by_hash[entry['adapter_sha256']] = entry['name']
        report = evaluate_one(entry, rows)
        results[entry['name']] = summary(report) if report else {'exact_match_count': None}
        log(event='selection-score', candidate=entry['name'], **results[entry['name']])
    return results


def main():
    SCORES.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        print(OUTPUT.read_text())
        return
    verify(json.loads(FREEZE.read_text()))
    rows = read_jsonl(SAMPLE)
    if SAMPLE.read_text() != sample_text(expected_sample()):
        raise ValueError('selection sample changed')
    candidates = json.loads((CANDIDATES / 'manifest.json').read_text())['candidates']
    for entry in candidates:
        if sha256(CANDIDATES / entry['name'] / 'adapters.safetensors') != entry['adapter_sha256']:
            raise ValueError(f"{entry['name']}: weights changed")
    results = score_all(candidates, len(rows))
    name = choose({key: value['exact_match_count'] for key, value in results.items()})
    selected = next(entry for entry in candidates if entry['name'] == name)
    record = {'version': 'reader-v3-selection/1', 'rule_sha256': sha256(RULE), 'sample_sha256': sha256(SAMPLE),
              'rule': 'highest exact match on the 400-row sample; ties to the simplest: ' + ' < '.join(SIMPLICITY),
              'scores': results, 'selected': {'name': name, 'path': selected['path'],
                                              'adapter_sha256': selected['adapter_sha256']},
              'caveat': CAVEAT}
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2) + '\n')
    temporary.replace(OUTPUT)
    log(event='selected', **record['selected'])
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-sample', action='store_true', help='write the 400-row sample once; no scoring')
    if parser.parse_args().build_sample:
        with SAMPLE.open('x') as stream:
            stream.write(sample_text(expected_sample()))
        print(sha256(SAMPLE))
    else:
        main()
