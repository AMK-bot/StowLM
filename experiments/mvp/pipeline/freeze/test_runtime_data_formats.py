"""Runtime directories admit an explicit set of non-executable data formats."""
from __future__ import annotations

import pytest

from experiments.mvp.pipeline.freeze import runtime_outputs
from experiments.mvp.pipeline.freeze import verify as verifier
from experiments.mvp.pipeline.freeze.test_runtime_verify import SCRATCH, put, runtime_tree


@pytest.mark.parametrize('suffix', sorted(runtime_outputs.DATA_SUFFIXES))
def test_allowlisted_non_executable_data_is_ignored(runtime_tree, suffix):
    root, manifest = runtime_tree
    name = SCRATCH + '/nested/cache/generated' + suffix
    put(root, name, 'synthetic runtime data\n')
    policy = runtime_outputs.load(root)
    assert not runtime_outputs.source_path(name, root=root)
    assert runtime_outputs.is_ignored_addition(name, policy, root=root)
    assert verifier.verify(manifest, root=root) == []


@pytest.mark.parametrize('name,contents,executable', [
    ('run.bash', 'echo unapproved\n', False),
    ('run.zsh', 'echo unapproved\n', False),
    ('run', '#!/bin/sh\necho unapproved\n', False),
    ('unknown.custom-format', 'unapproved format\n', False),
    ('script.json', '#!/bin/sh\necho unapproved\n', False),
    ('script.txt', '#!/usr/bin/env python\nprint(1)\n', False),
    ('executable.json', '{}\n', True),
    ('executable.txt', 'runtime-looking data\n', True),
])
def test_unknown_executable_and_shebang_files_are_runtime_source(
        runtime_tree, name, contents, executable):
    root, manifest = runtime_tree
    name = SCRATCH + '/nested/cache/' + name
    path = put(root, name, contents)
    if executable:
        path.chmod(path.stat().st_mode | 0o111)
    policy = runtime_outputs.validate(root, {'directories': [SCRATCH]}, check_contents=False)
    assert runtime_outputs.source_path(name, root=root)
    assert not runtime_outputs.is_ignored_addition(name, policy, root=root)
    with pytest.raises(ValueError, match='source file under runtime output directory: ' + name):
        runtime_outputs.load(root)
    assert any(row['path'] == name and row['reason'] == 'runtime_source'
               for row in verifier.verify(manifest, root=root))
    with pytest.raises(verifier.FreezeDrift):
        verifier.require_frozen(manifest, root=root)


@pytest.mark.parametrize('mode', [0o100, 0o010, 0o001])
def test_any_executable_bit_rejects_a_data_file(tmp_path, mode):
    name = 'cache.json'
    path = tmp_path / name
    path.write_text('{}\n')
    path.chmod(0o600 | mode)
    assert runtime_outputs.source_path(name, root=tmp_path)


@pytest.mark.parametrize('change', ['executable', 'shebang'])
def test_independently_pinned_data_is_still_audited_as_runtime_source(runtime_tree, change):
    root, manifest = runtime_tree
    name = SCRATCH + '/pinned.json'
    path = root / name
    if change == 'executable':
        path.chmod(path.stat().st_mode | 0o100)
    else:
        path.write_text('#!/bin/sh\n')
    drifts = verifier.verify(manifest, root=root)
    assert any(row['path'] == name and row['reason'] == 'runtime_source' for row in drifts)
    if change == 'shebang':
        assert any(row['path'] == name and row['reason'] == 'changed' for row in drifts)


def test_classifier_does_not_follow_symlinks_even_for_allowed_suffix(tmp_path):
    (tmp_path / 'target.json').write_text('{}\n')
    (tmp_path / 'alias.json').symlink_to(tmp_path / 'target.json')
    with pytest.raises(ValueError, match='symlink'):
        runtime_outputs.source_path('alias.json', root=tmp_path)
