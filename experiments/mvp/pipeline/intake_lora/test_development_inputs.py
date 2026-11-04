"""Use synthetic temporary files only; never inspect any real sealed file."""
import hashlib
import json
import sys

import pytest

from . import evaluate


@pytest.fixture
def development(tmp_path, monkeypatch):
    root = tmp_path / 'feature'
    data = root / 'data'
    data.mkdir(parents=True)
    files = {}
    for name in ('train.jsonl', 'valid.jsonl'):
        payload = (json.dumps({'id': name}) + '\n').encode()
        (data / name).write_bytes(payload)
        files[f'data/{name}'] = {'sha256': hashlib.sha256(payload).hexdigest(), 'count': 1}
    (data / 'manifest.json').write_text(json.dumps({'files': files}))
    monkeypatch.setattr(evaluate, 'HERE', root)
    return root


@pytest.mark.parametrize('name', ['train.jsonl', 'valid.jsonl'])
def test_registered_inputs_and_safe_aliases(development, tmp_path, name):
    path = development / 'data' / name
    rows, checksum = evaluate.read_development(path)
    assert rows == [{'id': name}]
    assert checksum == hashlib.sha256(path.read_bytes()).hexdigest()
    alias = tmp_path / 'public-alias.jsonl'
    alias.symlink_to(path)
    assert evaluate.read_development(alias) == (rows, checksum)
    parent_alias = tmp_path / 'public-parent'
    parent_alias.symlink_to(path.parent, target_is_directory=True)
    assert evaluate.read_development(parent_alias / name) == (rows, checksum)


@pytest.mark.parametrize('kind', ['own_sealed', 'sibling_sealed', 'arbitrary',
                                  'file_link', 'parent_link', 'registered_file_link',
                                  'registered_parent_link'])
def test_every_other_resolved_input_refused_before_read(development, tmp_path, monkeypatch, kind):
    target = tmp_path / 'sibling' / 'sealed' / 'synthetic.jsonl'
    target.parent.mkdir(parents=True)
    target.write_text('synthetic bytes must not be opened')
    path = {
        'own_sealed': development / 'sealed/sealed.jsonl',
        'sibling_sealed': target,
        'arbitrary': tmp_path / 'unregistered.jsonl',
    }.get(kind)
    if kind == 'file_link':
        path = tmp_path / 'alias.jsonl'
        path.symlink_to(target)
    elif kind == 'parent_link':
        parent = tmp_path / 'alias'
        parent.symlink_to(target.parent, target_is_directory=True)
        path = parent / target.name
    elif kind == 'registered_file_link':
        path = development / 'data/valid.jsonl'
        path.unlink()
        path.symlink_to(target)
    elif kind == 'registered_parent_link':
        (development / 'data').rename(development / 'old-data')
        (development / 'data').symlink_to(target.parent, target_is_directory=True)
        path = development / 'data/valid.jsonl'
    monkeypatch.setattr(evaluate, '_development_bytes',
                        lambda *args: pytest.fail('unregistered path reached byte reader'))
    with pytest.raises(ValueError, match='registered development'):
        evaluate.read_development(path)


def test_manifest_symlink_refused_before_read(development, tmp_path, monkeypatch):
    manifest = development / 'data/manifest.json'
    target = tmp_path / 'manifest.json'
    manifest.rename(target)
    manifest.symlink_to(target)
    monkeypatch.setattr(evaluate, '_development_bytes',
                        lambda *args: pytest.fail('linked manifest reached byte reader'))
    with pytest.raises(ValueError, match='manifest.*symlinks'):
        evaluate.read_development(development / 'data/valid.jsonl')


def test_unregistered_manifest_entry_cannot_expand_allowlist(development, tmp_path, monkeypatch):
    manifest = development / 'data/manifest.json'
    registration = json.loads(manifest.read_text())
    registration['files']['sealed/sealed.jsonl'] = {'sha256': '0' * 64, 'count': 1}
    manifest.write_text(json.dumps(registration))
    monkeypatch.setattr(evaluate, '_development_bytes',
                        lambda *args: pytest.fail('sealed registration reached byte reader'))
    with pytest.raises(ValueError, match='registered development'):
        evaluate.read_development(development / 'sealed/sealed.jsonl')


def test_hash_checked_before_parsing(development):
    path = development / 'data/valid.jsonl'
    path.write_text('invalid JSON and changed bytes')
    with pytest.raises(ValueError, match='hash mismatch'):
        evaluate.read_development(path)


def test_unregistered_development_file_refused(development):
    manifest = development / 'data/manifest.json'
    registration = json.loads(manifest.read_text())
    del registration['files']['data/valid.jsonl']
    manifest.write_text(json.dumps(registration))
    with pytest.raises(KeyError):
        evaluate.read_development(development / 'data/valid.jsonl')


def test_hash_and_rows_use_same_snapshot(development, monkeypatch):
    read_bytes = evaluate._development_bytes
    calls = []
    def replace_after_read(root, name):
        calls.append(name)
        payload = read_bytes(root, name)
        if name == 'valid.jsonl':
            (root / 'data' / name).write_text('replacement invalid JSON')
        return payload
    monkeypatch.setattr(evaluate, '_development_bytes', replace_after_read)
    rows, checksum = evaluate.read_development(development / 'data/valid.jsonl')
    assert rows == [{'id': 'valid.jsonl'}]
    assert checksum == hashlib.sha256(b'{"id": "valid.jsonl"}\n').hexdigest()
    assert calls == ['manifest.json', 'valid.jsonl']


@pytest.mark.parametrize('replace_parent', [False, True])
def test_symlink_replacement_race_fails_before_target_bytes(development, tmp_path, monkeypatch, replace_parent):
    read_bytes = evaluate._development_bytes
    target = tmp_path / 'synthetic-target'
    target.mkdir()
    (target / 'valid.jsonl').write_text('target must not be opened')
    def replace_after_manifest(root, name):
        payload = read_bytes(root, name)
        if name == 'manifest.json':
            if replace_parent:
                (root / 'data').rename(root / 'old-data')
                (root / 'data').symlink_to(target, target_is_directory=True)
            else:
                path = root / 'data/valid.jsonl'
                path.unlink()
                path.symlink_to(target / 'valid.jsonl')
        return payload
    monkeypatch.setattr(evaluate, '_development_bytes', replace_after_manifest)
    with pytest.raises(OSError):
        evaluate.read_development(development / 'data/valid.jsonl')


@pytest.mark.parametrize('backend', ['glimmer', 'spark'])
def test_cli_rejects_input_before_admission_model_or_network(development, tmp_path, monkeypatch, backend):
    from . import admission, spark_baseline
    def forbidden(*args, **kwargs):
        pytest.fail('invalid input reached admission, model, or network callback')
    monkeypatch.setattr(admission, 'wait_admit', forbidden)
    monkeypatch.setattr(evaluate, 'run_glimmer', forbidden)
    monkeypatch.setattr(spark_baseline, 'run_spark', forbidden)
    monkeypatch.setattr(evaluate, '_development_bytes', forbidden)
    monkeypatch.setattr(sys, 'argv', ['evaluate', '--backend', backend,
                        '--data', str(tmp_path / 'sibling/sealed/synthetic.jsonl'),
                        '--output', str(development / 'report.json')])
    with pytest.raises(SystemExit) as error:
        evaluate.main()
    assert error.value.code == 2
