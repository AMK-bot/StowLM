import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
from safetensors.numpy import load_file, save_file

from experiments.mvp.reader.v3 import gpu_run, record_traces, score_selected, select_adapter, swa, train


def weights(path, value, key='a.lora_a'):
    path.parent.mkdir(parents=True, exist_ok=True)
    save_file({key: np.full((2, 3), value, dtype=np.float32)}, str(path))


def trajectory(root, values, losses):
    checks, final = root / 'checks-final', root / 'adapter-v3-final'
    for iteration, value in values.items():
        weights(checks / f'{iteration:06d}' / swa.WEIGHTS, value)
        (checks / f'{iteration:06d}' / 'adapter_config.json').write_text('{}')
    weights(final / swa.WEIGHTS, list(values.values())[-1])
    (final / 'adapter_config.json').write_text('{}')
    (final / 'STOPPED.json').write_text(json.dumps({'reason': 'one full epoch finished', 'rows_completed': 4121}))
    log = root / 'gpu-run.jsonl'
    log.write_text(''.join(json.dumps({'event': 'val-loss', 'iteration': i, 'val_loss': v}) + '\n'
                           for i, v in losses.items()))
    return checks, final, log


def test_average_is_the_plain_mean_and_rejects_mismatch(tmp_path):
    weights(tmp_path / 'a.safetensors', 1.0)
    weights(tmp_path / 'b.safetensors', 2.0)
    weights(tmp_path / 'c.safetensors', 6.0)
    result = swa.average([tmp_path / name for name in ('a.safetensors', 'b.safetensors', 'c.safetensors')])
    assert result['a.lora_a'].dtype == np.float32 and (result['a.lora_a'] == 3.0).all()
    weights(tmp_path / 'other.safetensors', 1.0, key='b.lora_b')
    with pytest.raises(ValueError):
        swa.average([tmp_path / 'a.safetensors', tmp_path / 'other.safetensors'])
    weights(tmp_path / 'nan.safetensors', float('nan'))
    with pytest.raises(ValueError):
        swa.average([tmp_path / 'a.safetensors', tmp_path / 'nan.safetensors'])


def test_build_makes_four_hashed_scoreable_folders_and_keeps_order(tmp_path):
    values = {1200: 1., 1600: 2., 2000: 3., 2400: 4., 2800: 5., 3200: 6., 3600: 7., 4000: 8., 4121: 9.}
    losses = {1200: .03, 1600: .05, 2000: .03, 2400: .02, 2800: .008, 3200: .03, 3600: .02, 4000: .02, 4121: .02}
    checks, final, log = trajectory(tmp_path, values, losses)
    root = tmp_path / 'candidates'
    manifest = swa.build(root, checks, final, log)
    entries = {entry['name']: entry for entry in manifest['candidates']}
    assert list(entries) == list(select_adapter.SIMPLICITY)
    mean = lambda name: float(load_file(str(root / name / swa.WEIGHTS))['a.lora_a'][0, 0])
    assert (mean('final'), mean('best-val-loss'), mean('swa-last-3'), mean('swa-last-5')) == (9, 5, 8, 7)
    assert entries['best-val-loss']['iteration'] == 2800
    assert [m['iteration'] for m in entries['swa-last-5']['members']] == [2800, 3200, 3600, 4000, 4121]
    for name, entry in entries.items():
        assert train.sha256(root / name / swa.WEIGHTS) == entry['adapter_sha256']
        assert train.require_stopped_source(root / name)['adapter_sha256'] == entry['adapter_sha256']
    assert swa.build(root, checks, final, log) == manifest  # resume reads, never rebuilds


def test_final_falls_back_to_last_check_if_training_crashed(tmp_path):
    checks, final, log = trajectory(tmp_path, {400: 1., 800: 2., 1200: 3., 1600: 4., 2000: 5.},
                                    {400: .1, 800: .1, 1200: .1, 1600: .1, 2000: .1})
    (final / 'STOPPED.json').unlink()
    entries = swa.build(tmp_path / 'candidates', checks, final, log)['candidates']
    assert entries[0]['training_crashed'] and entries[0]['source'].endswith('002000')
    assert entries[1]['iteration'] == 2000  # val_loss tie goes to the later check


def row(identifier, expected, family):
    return {'id': identifier, 'family': family, 'expected': expected}


def test_sample_is_stratified_disjoint_and_fixed():
    kinds = [{'constraint': {'type': 'close_bay'}}, {'refusal': {'code': 'ambiguous'}}, {'refusal': 'unsupported'}]
    rows = [row(f'{k}-{i}', kind, f'family-{i % 3}') for k, kind in enumerate(kinds) for i in range(12)]
    rows.append(row('as-text', json.dumps(kinds[0]), 'family-0'))
    excluded = {'0-0', '1-0'}
    sample = select_adapter.build_sample(rows, excluded, per_stratum=6, seed=199)
    assert len(sample) == 18 and not excluded & {r['id'] for r in sample}
    counts = {}
    for chosen in sample:
        counts[select_adapter.stratum(chosen)] = counts.get(select_adapter.stratum(chosen), 0) + 1
    assert sorted(counts) == ['refusal:ambiguous', 'refusal:unsupported', 'rule:close_bay'] and set(counts.values()) == {6}
    assert sample == select_adapter.build_sample(rows, excluded, per_stratum=6, seed=199)
    with pytest.raises(ValueError):
        select_adapter.build_sample(rows, excluded, per_stratum=13)


def test_real_sample_matches_the_rule():
    rows = select_adapter.read_jsonl(select_adapter.SAMPLE)
    checks = {r['id'] for r in select_adapter.read_jsonl(select_adapter.CHECK_SAMPLE)}
    assert len(rows) == 400 and len({r['id'] for r in rows}) == 400 and not checks & {r['id'] for r in rows}
    assert select_adapter.SAMPLE.read_text() == select_adapter.sample_text(select_adapter.expected_sample())
    assert 'sealed' not in str(select_adapter.SAMPLE).lower()


def test_highest_exact_match_wins_and_ties_go_to_the_simplest():
    assert select_adapter.choose({'final': 350, 'best-val-loss': 360, 'swa-last-3': 371, 'swa-last-5': 370}) == 'swa-last-3'
    assert select_adapter.choose({'swa-last-5': 370, 'swa-last-3': 370, 'best-val-loss': 370, 'final': 300}) == 'best-val-loss'
    assert select_adapter.choose({'final': 370, 'swa-last-5': 370}) == 'final'
    assert select_adapter.choose({'final': None, 'swa-last-3': 1}) == 'swa-last-3'
    with pytest.raises(ValueError):
        select_adapter.choose({'final': None})


def test_identical_weights_share_one_evaluation_and_a_failure_is_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(select_adapter, 'log', lambda **_: None)
    candidates = [{'name': 'swa-last-3', 'adapter_sha256': 'b'}, {'name': 'best-val-loss', 'adapter_sha256': 'a'},
                  {'name': 'final', 'adapter_sha256': 'a'}, {'name': 'swa-last-5', 'adapter_sha256': 'c'}]
    seen = []

    def evaluate_one(entry, _rows):
        seen.append(entry['name'])
        return None if entry['name'] == 'swa-last-5' else {'exact_match_count': 300, 'count': 400}

    results = select_adapter.score_all(candidates, 400, evaluate_one)
    assert seen == ['final', 'swa-last-3', 'swa-last-5']
    assert results['best-val-loss']['same_weights_as'] == 'final' and results['swa-last-5']['exact_match_count'] is None


def good_report(entry):
    return {'data_sha256': train.sha256(select_adapter.SAMPLE), 'count': 400, 'missing_count': 0,
            'schema_constrained': True, 'decoding_policy': select_adapter.evaluate.DECODING_POLICY,
            'decoder_sha256': train.sha256(train.HERE / 'decoder.py'),
            'domain_words_sha256': train.sha256(train.HERE / 'domain_words.py'),
            'adapter_sha256': entry['adapter_sha256'], 'exact_match_count': 350}


def test_candidate_retries_when_denied_and_rejects_a_different_mask(monkeypatch, tmp_path):
    monkeypatch.setattr(select_adapter, 'SCORES', tmp_path)
    entry = {'name': 'final', 'path': str(tmp_path), 'adapter_sha256': 'x'}
    codes, waits = [select_adapter.RETRY_EXIT, 0], []

    def run(command, **_):
        assert '--check-child' in command and str(select_adapter.SAMPLE) in command
        code = codes.pop(0)
        if not code:
            (tmp_path / 'final.json').write_text(json.dumps(good_report(entry)))
        return SimpleNamespace(returncode=code)

    report = select_adapter.evaluate_candidate(entry, 400, run, lambda: waits.append(1))
    assert report['exact_match_count'] == 350 and len(waits) == 2
    bad = {**good_report(entry), 'decoder_sha256': 'other'}
    (tmp_path / 'final.json').write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        select_adapter.evaluate_candidate(entry, 400, run)


def test_sealed_freeze_binds_selected_folder_not_adapter_v3(tmp_path):
    selected = tmp_path / 'swa-last-3'
    original = lambda stage, chosen=None: [train.HERE / 'adapter-v3' / 'adapters.safetensors',
                                           train.HERE / 'adapter-v3' / 'STOPPED.json', train.HERE / 'config.json']
    files = score_selected.selected_inputs(selected, original)('final')
    assert files[:2] == [selected / 'adapters.safetensors', selected / 'STOPPED.json']
    assert select_adapter.OUTPUT in files and select_adapter.RULE in files
    assert score_selected.STOPPED_START / 'adapters.safetensors' in files  # the stopped start stays bound


def test_sealed_children_retry_and_never_raise_on_denial(monkeypatch):
    calls, admits = [], []
    codes = [score_selected.RETRY_EXIT, 0]
    monkeypatch.setattr(score_selected, 'admit', lambda: admits.append(1))

    def run(command, **_):
        calls.append(command)
        return SimpleNamespace(returncode=codes.pop(0))

    monkeypatch.setattr(score_selected.subprocess, 'run', run)
    done = score_selected.WaitingSubprocess().run(
        ['python', '-m', 'experiments.mvp.reader.v3.evaluate', '--sealed-final'], check=True)
    assert done.returncode == 0 and len(calls) == 2 and len(admits) == 2
    assert calls[0][3:] == ['--check-child', '--sealed-final']


def test_summary_reports_failed_gates_as_a_result():
    selection = {'selected': {'name': 'final'}, 'caveat': select_adapter.CAVEAT}
    gates = {'R1': False, 'R2': True, 'R3': False, 'all_pass': False}
    adoption = {'status': 'retain_v2', 'adopt_v3': False, 'no_worse_exact_match': True,
                'no_worse_accepted_loosening': True}
    summary = score_selected.sealed_summary(selection, gates, adoption)
    assert summary['R1'] is False and summary['adoption_status'] == 'retain_v2'
    assert summary['no_regression_exact'] is True and '80.47' in summary['caveat']


def test_trace_jobs_use_the_hero_when_ready_else_the_fallback(tmp_path):
    missing = record_traces.jobs(tmp_path / 'none.json', [])
    assert missing == [('Close bay 7.', 'real-trace.json', None),
                       ('Close bay 99.', 'real-trace-illegal-bay.json', None)]
    hero = tmp_path / 'hero.json'
    hero.write_text(json.dumps({'sentences': ['Keep class 3 away from bay 5.'], 'instance_path': 'maps/feeder500-x.json'}))
    context = {'id': 'feeder_500', 'allowed_imdg_classes': ['3']}
    rows = [{'context': {'id': 'feeder_800', 'allowed_imdg_classes': ['3']}}, {'context': context}]
    assert record_traces.jobs(hero, rows)[0] == ('Keep class 3 away from bay 5.', 'real-trace.json', context)


def test_chain_runs_steps_in_order_and_keeps_going_after_a_sealed_failure(tmp_path):
    fake = tmp_path / 'fake'
    fake.write_text('#!/bin/sh\necho "$1" >> "$READER_CHAIN_DIR/steps"\n'
                    'case "$1" in *score_selected) exit 3;; esac\n')
    fake.chmod(0o755)
    (tmp_path / 'run.pid').write_text('999999\n')
    env = {**os.environ, 'READER_CHAIN_FOREGROUND': '1', 'READER_CHAIN_DIR': str(tmp_path), 'READER_CHAIN_RUN': str(fake)}
    done = subprocess.run(['sh', str(train.HERE / 'finish_chain.sh')], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert [line.rsplit('.', 1)[1] for line in (tmp_path / 'steps').read_text().splitlines()] == [
        'swa', 'select_adapter', 'score_selected', 'record_traces']
    assert 'sealed exit 3' in done.stdout
