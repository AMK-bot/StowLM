import json
import os
import subprocess

import pytest

from experiments.mvp.reader.v3 import adopt, select_adapter, train


@pytest.mark.parametrize('v2, adopted', [(380, 'adapter-v2'), (373, 'adapter-v2'), (361, 'adapter-v2'),
                                         (360, 'swa-last-5'), (300, 'swa-last-5'), (None, 'adapter-v2')])
def test_rule_switches_only_past_three_points(v2, adopted):
    assert adopt.decide(v2, 373) == adopted


def sealed(count, loose):
    return {'count': 128, 'exact_match_count': count, 'exact_match_pct': count / 1.28, 'loosening_count': 48,
            'false_acceptance_count': loose, 'details': ['never copied']}


def test_record_holds_both_sealed_and_both_400_row_numbers(monkeypatch, tmp_path):
    monkeypatch.setattr(adopt, 'ADOPTION', tmp_path / 'final-adoption.json')
    monkeypatch.setattr(adopt, 'log', lambda **_: None)
    monkeypatch.setattr(adopt, 'v2_entry', lambda: {'name': 'adapter-v2', 'path': 'v2', 'adapter_sha256': 'b'})
    files = {'gates.json': {'R1': True, 'R2': False, 'R3': False, 'all_pass': False},
             'sealed-glimmer-tuned.json': sealed(118, 1), 'sealed-glimmer-v2-reference.json': sealed(120, 2),
             'sealed-glimmer-untrained.json': sealed(60, 0), 'adoption-ready.json': {'status': 'retain_v2'}}
    selection = {'selected': {'name': 'swa-last-5', 'path': 's', 'adapter_sha256': 'a'},
                 'scores': {'swa-last-5': {'exact_match_count': 373}}}
    record = adopt.write_decision(selection, {'exact_match_count': 370}, files.__getitem__)
    assert record['outcome'] == 'adapter-v2' and record['adopted']['adapter_sha256'] == 'b'
    assert record['sealed_128']['swa-last-5']['exact_match_count'] == 118
    assert record['sealed_128']['adapter-v2']['false_acceptance_count'] == 2
    assert record['selection_400']['adapter-v2']['exact_match_count'] == 370 and 'D263' in record['rule']
    assert 'details' not in record['sealed_128']['untrained']
    switched = adopt.write_decision(selection, {'exact_match_count': 350}, files.__getitem__)
    assert switched['outcome'] == 'swa-last-5' and switched['adopted']['adapter_sha256'] == 'a'
    assert json.loads(adopt.ADOPTION.read_text())['outcome'] == 'swa-last-5'


def test_chain_order_and_a_failed_step_does_not_stop_it(tmp_path):
    fake = tmp_path / 'fake'
    fake.write_text('#!/bin/sh\necho "$1 $2" >> "$READER_CHAIN_DIR/steps"\ncase "$2" in xray) exit 4;; esac\n')
    fake.chmod(0o755)
    intake = 'experiments/mvp/reader/v3/fake-intake-absent'
    env = {**os.environ, 'READER_CHAIN_FOREGROUND': '1', 'READER_CHAIN_DIR': str(tmp_path),
           'READER_CHAIN_RUN': str(fake), 'READER_CHAIN_INTAKE': intake}
    done = subprocess.run(['sh', str(train.HERE / 'adoption_chain.sh')], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    steps = [line.split() for line in (tmp_path / 'steps').read_text().splitlines()]
    assert [step[0].rsplit('.', 1)[1] for step in steps] == ['mask_study', 'adopt', 'adopt', 'intake_lease']
    status = [json.loads(line) for line in (tmp_path / 'chain-status.jsonl').read_text().splitlines()]
    assert [row['step'] for row in status][-2:] == ['intake_lease', 'intake_launch']
    assert status[2]['exit_code'] == 4 and status[-1]['exit_code'] != 0
