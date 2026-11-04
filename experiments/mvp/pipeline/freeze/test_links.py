"""Data-link pins never weaken the code and private-data fence."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from experiments.mvp.pipeline.freeze import build, links
from experiments.mvp.pipeline.freeze.test_freeze import tree, put


def link(root, name, target):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    return path


def test_relative_and_absolute_data_links_are_pinned_separately(tree):
    target_name = build.PIPELINE + '/package/fixtures/package.json'
    target = tree / target_name
    names = [build.PIPELINE + '/package/fixtures/replay.json', build.PIPELINE + '/xray/fixtures/trace-link.json']
    link(tree, names[0], os.path.relpath(target, (tree / names[0]).parent))
    link(tree, names[1], str(target))
    value = build.build_manifest(tree, no_weights=True)
    assert value['readiness']['ready'], value['readiness']
    assert set(names) <= set(value['symlinks'])
    assert not set(names) & set(value['files'])
    for name in names:
        assert value['symlinks'][name] == {
            'link_text': os.readlink(tree / name), 'resolved_target': target_name,
            'target_sha256': value['files'][target_name]}
    assert set(names) <= set(build.inventory_symlinks(tree, import_closure=[]))


def test_inspect_hashes_target_actual_bytes(tree):
    target_name = build.PIPELINE + '/package/fixtures/package.json'
    name = build.PIPELINE + '/package/fixtures/replay.json'
    path = link(tree, name, str(tree / target_name))
    hashes = {target_name: build.sha256_file(tree / target_name)}
    record = links.inspect(tree, name, hashes, hashes=hashes)
    put(tree, target_name, {'changed': True})
    assert links.inspect(tree, name, hashes)['target_sha256'] != record['target_sha256']
    with pytest.raises(ValueError, match='changed from independent pin'):
        links.inspect(tree, name, hashes, hashes=hashes)


def test_unpinned_target_is_not_automatically_added(tree):
    target = put(tree, 'not-selected/answer.json', {'answer': 1})
    name = build.PIPELINE + '/package/fixtures/replay.json'
    link(tree, name, str(target))
    value = build.build_manifest(tree, no_weights=True)
    assert not value['readiness']['ready']
    assert any('not independently pinned' in error for error in value['readiness']['errors'])
    assert 'not-selected/answer.json' not in value['files']
    assert name not in value['symlinks']


@pytest.mark.parametrize('target_name', [
    'experiments/mvp/reader/v3/sealed/hidden.json',
    'experiments/mvp/testv3/hidden.json',
    'experiments/mvp/corpus/test-v3/hidden.json',
    build.PIPELINE + '/package/private-key.pem',
])
def test_rejects_sealed_and_private_targets_without_reading(tree, target_name, monkeypatch):
    target = put(tree, target_name, 'unreadable sentinel')
    name = build.PIPELINE + '/package/fixtures/replay.json'
    link(tree, name, str(target))
    original = Path.open
    def guard(path, *args, **kwargs):
        if path == target:
            raise AssertionError('must not open forbidden target')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guard)
    with pytest.raises(ValueError, match='excluded'):
        links.inspect(tree, name, {target_name})


def test_rejects_outside_target(tree, tmp_path):
    target = put(tmp_path, 'outside.json', '{}')
    name = build.PIPELINE + '/package/fixtures/replay.json'
    link(tree, name, str(target))
    with pytest.raises(ValueError, match='outside repository'):
        links.inspect(tree, name, {'outside.json'})


def test_rejects_dangling_link_and_directory_target(tree):
    name = build.PIPELINE + '/package/fixtures/replay.json'
    path = link(tree, name, 'absent.json')
    with pytest.raises(FileNotFoundError):
        links.inspect(tree, name, {'absent.json'})
    path.unlink()
    path.symlink_to(tree / build.PIPELINE / 'package/fixtures', target_is_directory=True)
    with pytest.raises(ValueError, match='regular file'):
        links.inspect(tree, name, {build.PIPELINE + '/package/fixtures'})


def test_nonimported_code_leaf_link_is_allowed_when_target_pinned(tree):
    name = build.PIPELINE + '/package/fixtures/replay.py'
    target_name = 'src/pinned.py'
    link(tree, name, str(tree / target_name))
    assert links.inspect(tree, name, {target_name})['resolved_target'] == target_name
    assert name in build.inventory_symlinks(tree, import_closure=[])
    value = build.build_manifest(tree, no_weights=True)
    assert name in value['symlinks']
    assert value['readiness']['ready'], value['readiness']


def test_imported_leaf_link_is_rejected_even_when_target_pinned(tree):
    name = 'app/linked.py'
    target_name = 'src/pinned.py'
    link(tree, name, str(tree / target_name))
    with pytest.raises(ValueError, match='import symlink'):
        links.inspect(tree, name, {target_name}, import_closure=[name])
    put(tree, 'app/v6.py', 'import app.linked\n')
    with pytest.raises(ValueError, match='symlink'):
        build.source_paths(tree)


def test_target_chains_allowed_but_alias_parent_directory_links_rejected(tree):
    name = build.PIPELINE + '/package/fixtures/replay.json'
    target_name = build.PIPELINE + '/package/fixtures/package.json'
    intermediate = link(tree, 'chain.json', str(tree / target_name))
    path = link(tree, name, str(intermediate))
    assert links.inspect(tree, name, {target_name})['resolved_target'] == target_name
    path.unlink()
    alias = link(tree, build.PIPELINE + '/package/fixture-alias', str(path.parent))
    path.symlink_to(tree / target_name)
    with pytest.raises(ValueError, match='symlink excluded'):
        links.inspect(tree, alias.relative_to(tree).as_posix() + '/replay.json', {target_name})


def test_plain_regular_file_cannot_impersonate_symlink_record(tree):
    name = build.PIPELINE + '/package/fixtures/package.json'
    with pytest.raises(ValueError, match='expected data symlink'):
        links.inspect(tree, name, {name})
