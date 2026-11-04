"""Offline regressions for import closure, paths and manifest construction."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.mvp.pipeline.freeze import build, imports
from experiments.mvp.pipeline.freeze.test_freeze import tree, put


def test_import_hook_follows_lazy_relative_dynamic_and_external_local_modules(tmp_path):
    put(tmp_path, 'app/__init__.py', '')
    put(tmp_path, 'app/v6.py', '''import socket
raise RuntimeError('source must never execute')
from outside import parser

def lazy():
    import outside.prompt
    import importlib
    importlib.import_module('outside.adoption')
''')
    put(tmp_path, 'outside/__init__.py', 'from . import schema\n')
    for name in ('schema', 'parser', 'prompt', 'adoption'):
        put(tmp_path, f'outside/{name}.py', "raise RuntimeError('not executed')\n")
    result = imports.discover(tmp_path, ('app.v6',))
    assert set(result['paths']) == {'app/__init__.py', 'app/v6.py', 'outside/__init__.py',
                                   *(f'outside/{name}.py' for name in ('schema', 'parser', 'prompt', 'adoption'))}
    assert result['missing_entrypoints'] == []
    selected = set(build.source_paths(tmp_path, import_closure=result['paths']))
    assert selected >= {name for name in result['paths'] if not name.startswith('app/')}
    assert not any(name.startswith('app/') for name in selected)
    assert set(build.source_paths(tmp_path, import_closure=result['paths'], scope='film')) >= set(result['paths'])


def test_closure_does_not_read_sealed_data(tmp_path):
    put(tmp_path, 'app/v6.py', 'import experiments.mvp.reader.v3.sealed.secret\n')
    put(tmp_path, 'experiments/mvp/reader/v3/sealed/secret.py', 'SECRET=1\n')
    with pytest.raises(ValueError, match='sealed data excluded'):
        imports.discover(tmp_path, ('app.v6',))


def test_new_engine_reader_voice_and_root_configuration_are_sources(tree):
    names = [build.ENGINE + '/new-runtime.py', 'experiments/mvp/reader/schema.py',
             'experiments/mvp/reader/v3/new_prompt.py', 'experiments/mvp/voice/audio.py',
             'experiments/mvp/voice/asr.py', 'experiments/mvp/voice/flow.py',
             'params/fleet/example.toml', 'data/fixtures/example.json']
    for name in names:
        put(tree, name, '' if name.endswith('.py') else '{}')
    assert set(names) <= set(build.source_paths(tree))


@pytest.mark.parametrize('kind', ['file', 'directory', 'replacement'])
def test_rejects_new_or_replaced_import_closure_symlinks(tree, kind):
    target = put(tree, 'target.py', '')
    path = tree / 'app' / ('newdir' if kind == 'directory' else 'view.py')
    path.parent.mkdir(exist_ok=True)
    if kind == 'replacement':
        path.write_text('')
        path.unlink()
    path.symlink_to(target.parent if kind == 'directory' else target, target_is_directory=kind == 'directory')
    put(tree, 'app/v6.py', 'import app.newdir.target\n' if kind == 'directory' else 'import app.view\n')
    with pytest.raises(ValueError, match='symlink'):
        build.source_paths(tree)


def test_diff_git_octal_path_matches_engine_pin(tree):
    diff = put(tree, 'quoted.diff', 'diff --git "a/src/\\160inned.py" "b/src/\\160inned.py"\n--- "a/src/\\160inned.py"\n+++ "b/src/\\160inned.py"\n')
    assert build.diff_paths(diff) == ['src/pinned.py']
    existing = tree / 'experiments/mvp/postfreeze/02-synthetic.diff'
    existing.write_text(diff.read_text())
    manifest = build.build_manifest(tree, no_weights=True)
    assert manifest['postfreeze_diffs'][1]['safe'] is False
    assert manifest['postfreeze_diffs'][1]['pinned_touches'] == ['src/pinned.py']


@pytest.mark.parametrize('text', ['"a/\\q.py"', '"a/\\12.py"', 'a/b\\c.py', '"a/unterminated.py', '"a/file.py"suffix'])
def test_git_ambiguous_paths_fail_closed(text):
    with pytest.raises((ValueError, UnicodeError)):
        build.git_paths(text)


def test_git_utf8_octal_decode_and_rename():
    assert build.git_paths('"src/\\303\\251.py" -> "src/other.py"') == ['src/é.py', '->', 'src/other.py']


def test_full_build_hashes_weights_and_dry_build_pins_every_weight_stat(tree):
    full = build.build_manifest(tree)
    dry = build.build_manifest(tree, no_weights=True)
    assert full['base_model']['weights_hashed'] is True
    for name in full['model_weight_files']:
        assert full['files'][name] == build.sha256_file(tree / name)
        stat = (tree / name).stat()
        assert dry['weight_stats'][name] == {'exists': True, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def test_manifest_has_sealed_harness_fields_and_exact_profile(tree):
    profile = {'name': 'standard', 'pending_measurement': False, 'target_wall_s': 900.0, 'seed': 0}
    put(tree, build.PIPELINE + '/runner/profiles.py', f'def get_profile(name):\n    return {profile!r}\n')
    manifest = build.build_manifest(tree)
    assert manifest['task'] == '2.1' and manifest['status'] == 'frozen'
    assert manifest['standard_profile'] == profile
    assert all(manifest['artifacts'][kind] for kind in ('engine', 'adapter', 'prompt'))
    assert all(name in manifest['files'] for names in manifest['artifacts'].values() for name in names)


@pytest.mark.parametrize('escape', ['direct', 'symlink'])
def test_cli_output_is_resolved_inside_freeze_before_build(tree, tmp_path, escape):
    outside = tmp_path / 'outside'
    outside.mkdir()
    freeze = tree / build.FREEZE
    freeze.mkdir(parents=True, exist_ok=True)
    if escape == 'symlink':
        (freeze / 'escape').symlink_to(outside, target_is_directory=True)
        output = freeze / 'escape/manifest.json'
    else:
        output = outside / 'manifest.json'
    result = subprocess.run([sys.executable, str(Path(build.__file__)), '--root', str(tree),
                             '--no-weights', '--out', str(output)], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and 'output outside freeze' in result.stderr
    assert not (outside / 'manifest.json').exists()


def test_private_keys_never_enter_inventory(tree):
    for name in (build.PIPELINE + '/runner/.keys/signing.pem', build.PIPELINE + '/package/private-key.pem'):
        put(tree, name, 'private secret: do not read')
    public = build.PIPELINE + '/package/published-public-key.pem'
    put(tree, public, 'public')
    selected = build.inventory_paths(tree)
    assert public in selected
    assert not any('.keys/' in name or 'private-key' in name for name in selected)
