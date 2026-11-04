"""Launcher contract checks; no solver or numerical job is started."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


spec = importlib.util.spec_from_file_location('small_twostep_launcher', Path(__file__).with_name('run_small_twostep.py'))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def fixture_launcher(tmp_path, monkeypatch, version='4.4.0'):
    here = tmp_path / 'experiments/mvp/refs_tools'
    source = here / 'frozen-runtime-twostep-v44'
    sourcefile = source / 'src/stowage/solver/twostep.py'
    sourcefile.parent.mkdir(parents=True)
    sourcefile.write_text(f'VERSION = "{version}"\n')
    digest = hashlib.sha256(sourcefile.read_bytes()).hexdigest()
    (source / 'snapshot.json').write_text(json.dumps({'source_files': {'src/stowage/solver/twostep.py': digest}}))
    (here / 'run_small_v2.py').write_text('# test driver\n')
    monkeypatch.setattr(launcher, 'HERE', here)
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)
    captured = []
    monkeypatch.setattr(launcher.os, 'execv', lambda binary, command: captured.append(command))
    return here, source, captured


def test_default_release_seed_and_provenance(tmp_path, monkeypatch):
    here, source, captured = fixture_launcher(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, 'argv', ['launcher', '--workers', '2', '--limit', '1'])
    launcher.main()
    command = captured[0]
    assert command[command.index('--workers') + 1] == '2'
    assert command[command.index('--seed-policy') + 1] == 'instance'
    assert command[command.index('--release') + 1] == str(here / 'small-v21-release-frozen.json')
    assert command[command.index('--limit') + 1] == '1'
    output = tmp_path / 'experiments/mvp/runs/test-twostep-small-v21-v44'
    assert command[command.index('--output-dir') + 1] == str(output)
    assert not (tmp_path / 'experiments/mvp/runs/test-twostep-small-v21').exists()
    provenance = json.loads((output / 'twostep-source-provenance.json').read_text())
    assert provenance['version'] == '4.4.0'
    assert provenance['twostep_source_sha256']['src/stowage/solver/twostep.py']
    invocation = json.loads(next((output / 'launcher-invocations').glob('*.json')).read_text())
    assert invocation['command'] == command
    # The full resume command changes, while the pinned source identity does not.
    monkeypatch.setattr(sys, 'argv', ['launcher', '--workers', '2'])
    launcher.main()
    assert len(list((output / 'launcher-invocations').glob('*.json'))) == 2


def test_explicit_list_output_and_fixed_policy(tmp_path, monkeypatch):
    _, _, captured = fixture_launcher(tmp_path, monkeypatch)
    output = tmp_path / 'experiments/mvp/runs/test-pilot'
    instance_list = tmp_path / 'instances.txt'
    monkeypatch.setattr(sys, 'argv', ['launcher', '--workers', '1', '--seed-policy', 'fixed',
                                    '--output-dir', str(output), '--instance-list', str(instance_list)])
    launcher.main()
    command = captured[0]
    assert command[command.index('--output-dir') + 1] == str(output)
    assert command[command.index('--instance-list') + 1] == str(instance_list)
    assert command[command.index('--seed-policy') + 1] == 'fixed'


@pytest.mark.parametrize('kind', ['version', 'hash', 'outside'])
def test_reject_invalid_pins_before_launch(tmp_path, monkeypatch, kind):
    _, source, captured = fixture_launcher(tmp_path, monkeypatch, version='4.4.1' if kind == 'version' else '4.4.0')
    argv = ['launcher']
    if kind == 'hash':
        (source / 'src/stowage/solver/twostep.py').write_text('VERSION = "4.4.0"\n# drift\n')
    if kind == 'outside':
        argv += ['--output-dir', str(tmp_path / 'outside')]
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises((RuntimeError, AssertionError, ValueError)):
        launcher.main()
    assert captured == []


def test_pinned_provenance_cannot_be_overwritten(tmp_path):
    path = tmp_path / 'provenance.json'
    launcher.write_pinned(path, {'source': 'a'})
    launcher.write_pinned(path, {'source': 'a'})
    with pytest.raises(RuntimeError, match='provenance differs'):
        launcher.write_pinned(path, {'source': 'b'})
    assert json.loads(path.read_text()) == {'source': 'a'}


def test_reject_excess_worker_budget(tmp_path, monkeypatch):
    _, _, captured = fixture_launcher(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, 'argv', ['launcher', '--workers', '3'])
    with pytest.raises(SystemExit):
        launcher.main()
    assert captured == []


def test_explicit_old_output_rejected_without_modification(tmp_path, monkeypatch):
    _, _, captured = fixture_launcher(tmp_path, monkeypatch)
    old_output = tmp_path / 'experiments/mvp/runs/test-twostep-small-v21'
    old_output.mkdir(parents=True)
    provenance_path = old_output / 'twostep-source-provenance.json'
    old_bytes = b'{"version": "4.3.0", "source": "accepted-v43"}\n'
    provenance_path.write_bytes(old_bytes)
    record_path = old_output / 'instances/old/record.json'
    record_path.parent.mkdir(parents=True)
    record_path.write_bytes(b'{"accepted": true}\n')
    before = {p.relative_to(old_output): p.read_bytes() for p in old_output.rglob('*') if p.is_file()}
    monkeypatch.setattr(sys, 'argv', ['launcher', '--output-dir', str(old_output)])
    with pytest.raises(RuntimeError, match='provenance differs'):
        launcher.main()
    after = {p.relative_to(old_output): p.read_bytes() for p in old_output.rglob('*') if p.is_file()}
    assert after == before
    assert captured == []
