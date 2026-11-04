"""Installer transaction mocks: every simulated target is below --basetemp."""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('d126_install_under_test', HERE/'install_watchdog.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path/'synthetic-project'
    fence = root/'experiments/mvp/fixwindow'
    fence.mkdir(parents=True)
    rows = []
    for index, target in enumerate(('src/stowage/checker/stability.py', 'experiments/mvp/gold_tools/driver.py', 'tests/checker/test_external_watchdog.py')):
        old, new = f'value = {index}\n'.encode(), f'value = {index+10}\n'.encode()
        candidate = f'experiments/mvp/fixwindow/candidate-{index}.py'
        (root/candidate).write_bytes(new)
        if index < 2:
            (root/target).parent.mkdir(parents=True, exist_ok=True)
            (root/target).write_bytes(old)
        rows.append(dict(target=target, candidate=candidate, before=installer.digest(old) if index < 2 else None,
                         after=installer.digest(new)))
    patch = fence/'synthetic.patch'
    patch.write_bytes(b'inert mock patch bytes')
    monkeypatch.setattr(installer, 'ROOT', root)
    monkeypatch.setattr(installer, 'FENCE', fence)
    monkeypatch.setattr(installer, 'FILES', tuple(rows))
    monkeypatch.setattr(installer, 'PATCHES', ((str(patch.relative_to(root)), installer.digest(patch.read_bytes())),))
    monkeypatch.setattr(installer, 'boundary_receipt', lambda path: dict(producer_epoch_closed=True, path='synthetic'))
    calls = []
    def patch_check(reverse=False):
        calls.append(reverse)
        return dict(command=['mock-readonly-git', '--reverse' if reverse else '--forward'], exit_code=0)
    monkeypatch.setattr(installer, 'readonly_patch_check', patch_check)
    return root, fence, rows, calls


def test_check_never_promotes_sources_or_test(sandbox):
    root, fence, rows, calls = sandbox
    result = installer.run(apply=False, receipt_dir=fence/'check')
    assert result['checked_only'] and not result['applied']
    assert calls == [False]
    assert all(installer.current(root/row['target']) == row['before'] for row in rows)
    assert not (fence/'check/backups').exists()


def test_apply_backs_up_exact_bytes_and_promotes_test_only_after_validation(sandbox):
    root, fence, rows, calls = sandbox
    result = installer.run(apply=True, receipt_dir=fence/'apply', boundary=Path('synthetic'))
    assert result['applied'] and result['passed']
    assert calls == [False, True]
    for row in rows:
        assert installer.current(root/row['target']) == row['after']
        backup = fence/'apply/backups'/row['target']
        assert installer.current(backup) == row['before']
    assert (fence/'apply/CHECKER-NOTES.template.md').is_file()


def test_candidate_drift_cannot_write_any_target(sandbox):
    root, fence, rows, calls = sandbox
    (root/rows[-1]['candidate']).write_bytes(b'value = 999\n')
    with pytest.raises(ValueError, match='candidate drift'):
        installer.run(apply=True, receipt_dir=fence/'drift', boundary=Path('synthetic'))
    assert all(installer.current(root/row['target']) == row['before'] for row in rows)


def test_reverse_check_failure_restores_sources_and_removes_new_test(sandbox, monkeypatch):
    root, fence, rows, calls = sandbox
    def patch_check(reverse=False):
        if reverse:
            raise ValueError('synthetic reverse applicability failure')
        return dict(exit_code=0)
    monkeypatch.setattr(installer, 'readonly_patch_check', patch_check)
    with pytest.raises(ValueError, match='reverse'):
        installer.run(apply=True, receipt_dir=fence/'rollback', boundary=Path('synthetic'))
    assert all(installer.current(root/row['target']) == row['before'] for row in rows)
    record = json.loads((fence/'rollback/RECEIPT.json').read_text())
    assert not record['applied'] and all(row['restored'] for row in record['rollback'])


def test_cancellation_after_replace_uses_prepublication_journal(sandbox, monkeypatch):
    root, fence, rows, calls = sandbox
    original = installer.atomic_write
    changed = []
    def interrupted(path, raw, mode=0o644):
        original(path, raw, mode)
        if path == root/rows[0]['target'] and not changed:
            changed.append(True)
            raise KeyboardInterrupt()
    monkeypatch.setattr(installer, 'atomic_write', interrupted)
    with pytest.raises(KeyboardInterrupt):
        installer.run(apply=True, receipt_dir=fence/'interrupted', boundary=Path('synthetic'))
    assert all(installer.current(root/row['target']) == row['before'] for row in rows)


def test_foreign_edit_is_preserved_instead_of_blind_rollback(sandbox, monkeypatch):
    root, fence, rows, calls = sandbox
    original = installer.atomic_write
    changed = []
    def concurrent(path, raw, mode=0o644):
        original(path, raw, mode)
        if path == root/rows[0]['target'] and not changed:
            changed.append(True)
            path.write_bytes(b'foreign = 42\n')
            raise OSError('synthetic concurrent edit')
    monkeypatch.setattr(installer, 'atomic_write', concurrent)
    with pytest.raises(OSError, match='concurrent'):
        installer.run(apply=True, receipt_dir=fence/'foreign', boundary=Path('synthetic'))
    assert (root/rows[0]['target']).read_bytes() == b'foreign = 42\n'
    record = json.loads((fence/'foreign/RECEIPT.json').read_text())
    assert record['rollback'][0]['restored'] is False


def test_foreign_symlink_is_preserved_and_conflict_receipt_is_written(sandbox, monkeypatch):
    root, fence, rows, calls = sandbox
    original = installer.atomic_write
    foreign = fence/'foreign-content.py'
    foreign.write_bytes(b'foreign = 42\n')
    changed = []
    def concurrent(path, raw, mode=0o644):
        original(path, raw, mode)
        if path == root/rows[0]['target'] and not changed:
            changed.append(True)
            path.unlink()
            path.symlink_to(foreign)
            raise OSError('synthetic symlink edit')
    monkeypatch.setattr(installer, 'atomic_write', concurrent)
    with pytest.raises(OSError, match='symlink edit'):
        installer.run(apply=True, receipt_dir=fence/'foreign-symlink', boundary=Path('synthetic'))
    assert (root/rows[0]['target']).is_symlink()
    assert foreign.read_bytes() == b'foreign = 42\n'
    record = json.loads((fence/'foreign-symlink/RECEIPT.json').read_text())
    assert record['rollback'][0]['restored'] is False
