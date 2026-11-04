"""Transaction faults are injected only into temporary synthetic roots/packages."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('d132_batch_tool_test', Path(__file__).with_name('batch_tool.py'))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    package, root = tmp_path / 'package', tmp_path / 'root'
    monkeypatch.setattr(tool, 'PACKAGE', package)
    root.mkdir()
    package.mkdir()
    files = {'src/stowage/old.py': b'old source\n',
             'src/stowage/untouched.py': b'untouched\n', 'tests/test_old.py': b'old test\n',
             'params/test.toml': b'key=1\n',
             'src/llm/securing/mask_filter.py': b'qualified mask dependency\n',
             'models/tokenizer.json': b'{"qualified":true}\n'}
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (root / 'src/stowage/old.py').chmod(0o640)
    before = tool.source_info(root)
    targets = {}
    artifacts = {}
    for name, data in {'src/stowage/old.py': b'new source\n',
                       'src/stowage/new.py': b'new module\n',
                       'tests/test_old.py': b'new test\n'}.items():
        original = tool.file_info(root / name)
        target = package / 'after' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)
        targets[name] = {'before': original, 'after': tool.file_info(target)}
        artifacts['after/' + name] = hashlib.sha256(data).hexdigest()
    after = dict(before)
    for name, target in targets.items():
        if name.startswith('src/stowage/'):
            after[name] = target['after']
    manifest = {'qualification_complete': True, 'src_stowage_before': before,
                'src_stowage_after': after, 'targets': targets,
                'qualified_inputs': {name: tool.file_info(root / name) for name in
                    ('params/test.toml', 'src/llm/securing/mask_filter.py', 'models/tokenizer.json')},
                'artifacts': artifacts, 'fast_test_commands': []}

    def save():
        target = package / 'manifest.json'
        target.write_text(json.dumps(manifest))
        (package / 'manifest.sha256').write_text(hashlib.sha256(target.read_bytes()).hexdigest())

    save()
    return tool, root, package, manifest, save


def test_install_and_exact_rollback_are_idempotent(setup):
    tool, root, package, manifest, _ = setup
    assert tool.execute('install', root, check_only=True)['state'] == 'ready'
    assert tool.source_info(root) == manifest['src_stowage_before']
    assert tool.execute('install', root)['state'] == 'installed'
    assert tool.execute('install', root)['state'] == 'already_installed'
    assert tool.execute('rollback', root, check_only=True)['state'] == 'ready'
    assert tool.execute('rollback', root)['state'] == 'rolled_back'
    assert tool.source_info(root) == manifest['src_stowage_before']
    assert (root / 'src/stowage/old.py').stat().st_mode & 0o777 == 0o640
    assert not (root / 'src/stowage/new.py').exists()
    assert tool.execute('rollback', root)['state'] == 'already_rolled_back'
    assert tool.execute('install', root)['state'] == 'installed'


@pytest.mark.parametrize('relative', ['src/stowage/old.py', 'src/stowage/untouched.py',
                                     'src/stowage/addition.py', 'src/stowage/new.py',
                                     'tests/test_old.py', 'params/test.toml',
                                     'src/llm/securing/mask_filter.py', 'models/tokenizer.json'])
def test_all_preconditions_checked_before_source_write(setup, relative):
    tool, root, _, _, _ = setup
    (root / relative).write_bytes(b'foreign bytes')
    old = (root / 'src/stowage/old.py').read_bytes()
    with pytest.raises(RuntimeError):
        tool.execute('install', root)
    assert (root / 'src/stowage/old.py').read_bytes() == old


@pytest.mark.parametrize('relative', ['src/stowage/old.py', 'src/stowage/untouched.py',
                                     'src/stowage/addition.py', 'tests/test_old.py'])
def test_rollback_never_overwrites_foreign_edit(setup, relative):
    tool, root, _, _, _ = setup
    tool.execute('install', root)
    path = root / relative
    path.write_bytes(b'foreign post-install bytes')
    with pytest.raises(RuntimeError):
        tool.execute('rollback', root)
    assert path.read_bytes() == b'foreign post-install bytes'


def test_backup_corruption_refuses_rollback_before_write(setup):
    tool, root, _, manifest, _ = setup
    tool.execute('install', root)
    backup = root / 'experiments/mvp/batch-d132/assembly/install-state/backup/src/stowage/old.py'
    backup.write_bytes(b'corrupt backup')
    with pytest.raises(RuntimeError, match='Backup checksum'):
        tool.execute('rollback', root)
    assert tool.source_info(root) == manifest['src_stowage_after']


def test_payload_corruption_refuses_install(setup):
    tool, root, package, manifest, _ = setup
    (package / 'after/src/stowage/old.py').write_bytes(b'corrupt artifact')
    with pytest.raises(RuntimeError, match='artifact checksum'):
        tool.execute('install', root)
    assert tool.source_info(root) == manifest['src_stowage_before']


def test_unqualified_package_refuses_install(setup):
    tool, root, _, manifest, save = setup
    manifest['qualification_complete'] = False
    save()
    with pytest.raises(RuntimeError, match='not been marked qualified'):
        tool.execute('install', root)


def test_tooling_checksum_mismatch_refuses_install(setup, tmp_path, monkeypatch):
    tool, root, _, manifest, save = setup
    here = tmp_path / 'tooling/assembly'
    here.mkdir(parents=True)
    monkeypatch.setattr(tool, 'HERE', here)
    target = here.parent / 'INSTALL.sh'
    target.write_bytes(b'qualified tool')
    manifest['tooling_artifacts'] = {'INSTALL.sh': hashlib.sha256(target.read_bytes()).hexdigest()}
    save()
    target.write_bytes(b'changed tool')
    with pytest.raises(RuntimeError, match='tooling checksum'):
        tool.execute('install', root)


def test_symlink_target_refused(setup, tmp_path):
    tool, root, _, _, _ = setup
    path = root / 'src/stowage/old.py'
    path.unlink()
    external = tmp_path / 'foreign.py'
    external.write_bytes(b'do not overwrite')
    path.symlink_to(external)
    with pytest.raises(RuntimeError, match='symlink'):
        tool.execute('install', root)
    assert external.read_bytes() == b'do not overwrite'


def test_partial_install_rolls_back_only_known_epochs(setup, monkeypatch):
    tool, root, _, manifest, _ = setup
    original = tool.atomic

    def interrupted(path, data, mode):
        if path == root / 'src/stowage/new.py':
            raise OSError('synthetic interruption')
        original(path, data, mode)

    monkeypatch.setattr(tool, 'atomic', interrupted)
    with pytest.raises(OSError, match='synthetic interruption'):
        tool.execute('install', root)
    monkeypatch.setattr(tool, 'atomic', original)
    assert tool.execute('rollback', root)['state'] == 'rolled_back'
    assert tool.source_info(root) == manifest['src_stowage_before']


def test_missing_journal_refuses_claim_of_after_bytes(setup):
    tool, root, _, _, _ = setup
    tool.execute('install', root)
    (root / 'experiments/mvp/batch-d132/assembly/install-state/state.json').unlink()
    with pytest.raises(RuntimeError, match='no owned transaction'):
        tool.execute('install', root)
