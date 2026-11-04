"""Offline preparation tests: mock tokenizer and subprocess, never MLX or weights."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from . import admission, gpu_run, train


def config():
    return json.loads((train.HERE / 'config.json').read_text())


def row(identifier, family='train'):
    text = f'Booking {identifier}; synthetic only'
    expected = {'refusal': {'code': 'missing_fields'}}
    return {'id': identifier, 'family': family, 'text': text, 'context': {'rotation': ['THLCH']},
            'expected': expected,
            'messages': [{'role': 'system', 'content': 'Strict JSON or refusal.'},
                         {'role': 'user', 'content': text},
                         {'role': 'assistant', 'content': json.dumps(expected)}]}


def write_state(path, **updates):
    state = {'t': 1000, 'mask': 255, 'load1': 45}
    path.write_text(json.dumps({**state, **updates}))


def test_admission_waits_for_missing_stale_denied_and_malformed(tmp_path):
    path = tmp_path / 'state.json'
    assert admission.admit(path, now=1000) is None
    path.write_text('{')
    assert admission.admit(path, now=1000) is None
    for updates in ({'t': 880}, {'t': 1001}, {'load1': 46}, {'mask': 127},
                    {'mask': -255}, {'load1': None}, {'t': float('nan')}):
        write_state(path, **updates)
        assert admission.admit(path, now=1000) is None
    write_state(path, mask='0xff')
    assert admission.admit(path, now=1000)['mask'] == 255
    write_state(path, load1=50)
    sleeps = []

    def free_slot(seconds):
        sleeps.append(seconds)
        write_state(path)

    assert admission.wait_admit(path, sleep=free_slot, clock=lambda: 1000)['workers'] == 1
    assert sleeps == [5]


def test_seeded_selection_once_no_padding_or_repeat():
    cfg = config()
    rows = [row(str(i)) for i in range(3000)]
    selected = train.selected_rows(rows, cfg)
    assert len(selected) == 900
    assert len({r['id'] for r in selected}) == 900
    assert selected == train.selected_rows(rows, cfg)
    assert selected != rows[:900]
    assert train.selected_rows(rows[:3], cfg) != []
    assert len(train.selected_rows(rows[:3], cfg)) == 3


def test_recipe_forbids_adapter_source_and_oversized_wall():
    cfg = config()
    train.validate_recipe(cfg)
    for change in ({'source_adapter': 'reader/adapter-v3'}, {'resume': True},
                   {'model': 'models/other'}, {'hard_stop_seconds': 2701},
                   {'epochs': 2}, {'batch_size': 2}, {'max_train_examples': 0}):
        with pytest.raises(ValueError):
            train.validate_recipe({**cfg, **change})


def test_training_validation_checks_and_no_reader_answer_normalizer():
    good, held = row('t'), row('v', 'heldout')
    assert train.validate_splits([good], [held])['train_count'] == 1
    with pytest.raises(ValueError, match='families'):
        train.validate_splits([good], [row('v')])
    bad = row('other', 'heldout')
    bad['messages'][-1]['content'] = '{}'
    with pytest.raises(ValueError, match='completion'):
        train.validate_splits([good], [bad])


class MockTokenizer:
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False,
                            return_dict=False, **kwargs):
        # Match Glimmer's direct-answer header, including render_prompt's suffix.
        text = ''.join(m['role'] + (': to=user<|message|>' if m['role'] == 'assistant' else ':')
                       + m['content'] + '\n' for m in messages)
        if add_generation_prompt:
            text += 'assistant:'
        return list(text.encode()) if tokenize else text

    def encode(self, text, add_special_tokens=False):
        return list(text.encode())


def test_cpu_token_preflight_mock_tokenizer_never_loads_model(tmp_path):
    cfg = config()
    report = train.token_preflight(cfg, tmp_path, [row('t')], [row('v', 'valid')],
                                   {'device_loaded': False, 'sealed_read': False},
                                   tokenizer=MockTokenizer(), output=tmp_path / 'report.json')
    assert report['token_lengths']['train']['count'] == 1
    assert report['prompt_prefix_verified']
    assert report['device_loaded'] is False
    assert report['sealed_read'] is False
    assert report['truncation_allowed'] is False
    assert 'mlx.core' not in sys.modules
    with pytest.raises(ValueError, match='exceeds max_seq_length'):
        train.token_preflight({**cfg, 'max_seq_length': 3}, tmp_path,
                             [row('t')], [row('v', 'valid')], {},
                             tokenizer=MockTokenizer(), output=None)


def test_preflight_reads_only_train_valid_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(train, 'check_model', lambda path: None)
    monkeypatch.setattr(train, 'sha256', lambda path: Path(path).name)
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps(config()))
    for split, data in [('train', [row('t')]), ('valid', [row('v', 'heldout')])]:
        (tmp_path / f'{split}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in data))
    (tmp_path / 'manifest.json').write_text(json.dumps({'files': {
        'data/train.jsonl': {'sha256': 'train.jsonl', 'count': 1},
        'data/valid.jsonl': {'sha256': 'valid.jsonl', 'count': 1},
        'sealed/sealed.jsonl': {'sha256': 'unopened', 'count': 200}}}))
    # No sealed file exists, so any attempt to consume it would fail.
    cfg, model, rows, valid, report = train.data_preflight(tmp_path, cfg_path)
    assert report['selected_train_count'] == 1
    assert report['source_adapter'] is None
    assert report['sealed_read'] is False
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    manifest['files']['data/train.jsonl']['sha256'] = 'tampered'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='hash/count'):
        train.data_preflight(tmp_path, cfg_path)


def test_sealed_paths_and_symlinks_are_rejected(tmp_path):
    hidden = tmp_path / 'sealed' / 'cases.jsonl'
    hidden.parent.mkdir()
    hidden.write_text('DO NOT OPEN')
    link = tmp_path / 'train.jsonl'
    link.symlink_to(hidden)
    for path in (hidden, link):
        with pytest.raises(ValueError, match='sealed'):
            train._rows(path)


def test_gpu_lease_and_simultaneous_admission(tmp_path):
    lease, state = tmp_path / 'lease.json', tmp_path / 'state.json'
    assert gpu_run.gpu_lease(lease, now=1000) is None
    lease.write_text(json.dumps({'task': 'intake_lora', 'approved': True, 'authorized_by': 'claude',
                                 'issued_at': 900, 'expires_at': 4000}))
    assert gpu_run.gpu_lease(lease, now=1000)['approved'] is True
    assert gpu_run.gpu_lease(lease, now=1400) is None
    calls = []

    def release(seconds):
        calls.append(seconds)
        write_state(state)

    cpu, _ = gpu_run.wait_admission(lease, state_path=state, sleep=release, clock=lambda: 1000)
    assert cpu['mask'] == 255
    assert calls == [5]


def test_unsupervised_worker_rejected_before_model_import(tmp_path, monkeypatch):
    monkeypatch.delenv('INTAKE_SUPERVISOR_PID', raising=False)
    monkeypatch.setattr(train, 'run_train', lambda *a, **kw: pytest.fail('model invoked'))
    with pytest.raises(ValueError, match='supervisor'):
        gpu_run.worker(tmp_path / 'lease.json')


def test_supervisor_timeout_exact_child_and_checkpoint_report(tmp_path, monkeypatch):
    cfg = config()
    (tmp_path / 'config.json').write_text(json.dumps(cfg))
    lease = tmp_path / 'lease.json'
    lease.write_text('{}')
    adapter = tmp_path / 'adapter-intake-v1'
    adapter.mkdir()
    weights = adapter / 'adapters.safetensors'
    weights.write_bytes(b'mocked model checkpoint')
    (adapter / 'progress.json').write_text(json.dumps({
        'adapter_sha256': train.sha256(weights), 'checkpoint_rows_completed': 200,
        'optimizer_updates': 50}))
    (tmp_path / 'recipe-freeze.json').write_text('{}')
    monkeypatch.setattr(gpu_run, 'HERE', tmp_path)
    monkeypatch.setattr(gpu_run, 'ADAPTER', adapter)
    monkeypatch.setattr(gpu_run, 'prelaunch', lambda: cfg)
    monkeypatch.setattr(gpu_run, 'wait_admission', lambda *a, **kw: ({'mask': 255}, {}))
    monkeypatch.setattr(gpu_run.train, 'run_train', lambda *a, **kw: pytest.fail('model invoked'))
    commands = []

    def timeout(command, **kwargs):
        commands.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(gpu_run.subprocess, 'run', timeout)
    assert gpu_run.supervise(lease) == 124
    command, options = commands[0]
    assert command[:3] == [sys.executable, '-m', 'experiments.mvp.pipeline.intake_lora.gpu_run']
    assert options['timeout'] == 2700
    assert options['check'] is False
    report = json.loads((adapter / 'STOPPED.json').read_text())
    assert report['hard_wall_enforced'] is True
    assert report['checkpoint_usable'] is True
    assert report['rows_completed'] == 200
    assert report['selected_budget_completed'] is False


def test_recipe_freeze_rejects_mutation(tmp_path, monkeypatch):
    source = tmp_path / 'source.py'
    source.write_text('one')
    monkeypatch.setattr(train, 'ROOT', tmp_path)
    monkeypatch.setattr(train, 'recipe_files', lambda: [source])
    freeze = tmp_path / 'freeze.json'
    first = train.freeze_recipe(freeze)
    assert train.freeze_recipe(freeze) == first
    assert train.check_recipe_freeze(freeze)['source_adapter'] is None
    source.write_text('two')
    with pytest.raises(ValueError, match='recipe changed'):
        train.check_recipe_freeze(freeze)
