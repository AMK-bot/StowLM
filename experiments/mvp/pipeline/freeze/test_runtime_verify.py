"""D284 runtime writes are additions, never permission to alter frozen bytes."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from experiments.mvp.pipeline.freeze import build, evidence, runtime_outputs
from experiments.mvp.pipeline.freeze import verify as verifier

SCRATCH = 'experiments/mvp/frontier/engine/peel_ladder/assignment-ballast'


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) if isinstance(value, dict) else value)
    return path


def _manifest(root, scope, *, pipeline=None):
    policy = runtime_outputs.load(root, import_closure=[])
    selection = evidence.inventory(root, scope=scope)
    names = build.inventory_paths(root, no_weights=False, import_closure=[],
                                   evidence_selection=selection, scope=scope,
                                   runtime_dirs=policy['directories'])
    # Existing pins deliberately overlap scratch. They must still be checked,
    # although the normal builder would omit newly generated scratch data.
    names = sorted(set(names) | {SCRATCH + '/pinned.json', SCRATCH + '/pinned.safetensors'})
    files = {name: build.sha256_file(root / name) for name in names}
    sources = {name: files[name] for name in build.source_paths(root, import_closure=[], scope=scope,
                                                              runtime_dirs=policy['directories'])}
    weight = SCRATCH + '/pinned.safetensors'
    stat = (root / weight).stat()
    value = {'schema_version': verifier.SCHEMA, 'task': '2.1', 'status': 'frozen', 'scope': scope,
             'dry_run': False, 'readiness': {'ready': True}, 'files': files,
             'source_files': sources, 'tree_sha256': build.tree_digest(sources),
             'model_weight_files': [weight], 'base_model': {'weight_files': {}},
             'weight_stats': {weight: {'exists': True, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}},
             'selection': {'runtime_outputs': policy, 'import_closure': {'paths': []}},
             'evidence': selection}
    if pipeline is not None:
        reference = json.loads(pipeline.read_text())
        value['pipeline_layer'] = {'path': pipeline.relative_to(root).as_posix(),
                                   'manifest_sha256': build.sha256_file(pipeline),
                                   'tree_digest': reference['tree_sha256']}
    return value


@pytest.fixture(params=['pipeline', 'film'])
def runtime_tree(tmp_path, monkeypatch, request):
    root = tmp_path / 'repo'
    put(root, 'src/runtime.py', '# stable runtime\n')
    put(root, SCRATCH + '/pinned.json', {'stable': True})
    put(root, SCRATCH + '/pinned.safetensors', 'synthetic weight bytes')
    put(root, runtime_outputs.CONFIG, {'schema_version': runtime_outputs.SCHEMA, 'directories': [SCRATCH]})
    # No real product fixtures, signatures or models belong to this policy test.
    monkeypatch.setattr(evidence, 'product_output_bindings', lambda *args, **kwargs:
                        {'errors': [], 'lattice_provenance': {}})
    pipeline = put(root, build.FREEZE + '/runtime-pipeline.json', _manifest(root, 'pipeline'))
    if request.param == 'pipeline':
        return root, pipeline
    film = put(root, build.FREEZE + '/runtime-film.json', _manifest(root, 'film', pipeline=pipeline))
    return root, film


def test_new_runtime_data_is_ignored_in_both_scopes(runtime_tree):
    root, path = runtime_tree
    put(root, SCRATCH + '/54442/L1.json', {'generated': True})
    put(root, SCRATCH + '/54442/cache/nested/trace.txt', 'runtime trace')
    assert verifier.verify(path, root=root) == []
    verifier.require_frozen(path, root=root)


@pytest.mark.parametrize('change', ['change', 'remove'])
def test_existing_data_pin_inside_runtime_folder_still_drifts(runtime_tree, change):
    root, path = runtime_tree
    name = SCRATCH + '/pinned.json'
    if change == 'change':
        put(root, name, {'changed': True})
    else:
        (root / name).unlink()
    drifts = verifier.verify(path, root=root)
    assert any(row['path'] == name and row['reason'] == ('changed' if change == 'change' else 'missing')
               for row in drifts)


def test_pinned_weight_stat_inside_runtime_folder_still_drifts(runtime_tree):
    root, path = runtime_tree
    name = SCRATCH + '/pinned.safetensors'
    put(root, name, 'new weight bytes')
    assert any(row['path'] == name and row['reason'] == 'changed'
               for row in verifier.verify(path, root=root))


def test_full_weight_hash_inside_runtime_folder_detects_restored_stat(runtime_tree):
    root, path = runtime_tree
    name = SCRATCH + '/pinned.safetensors'
    weight = root / name
    stat = weight.stat()
    weight.write_bytes(b'x' * stat.st_size)
    os.utime(weight, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert verifier.verify(path, root=root) == []
    assert any(row['path'] == name and row['reason'] == 'changed'
               for row in verifier.verify(path, root=root, check_weights=True))


@pytest.mark.parametrize('name', [SCRATCH + '/added.py', SCRATCH + '/cache/new.py',
                                  SCRATCH + '/nested/__pycache__/new.py'])
def test_new_source_is_never_ignored_even_in_runtime_cache(runtime_tree, name):
    root, path = runtime_tree
    put(root, name, '# unapproved source addition\n')
    assert any(row['path'] == name and row['reason'] == 'runtime_source'
               for row in verifier.verify(path, root=root))


def test_source_in_runtime_folder_is_rejected_even_if_independently_pinned(runtime_tree):
    root, path = runtime_tree
    name = SCRATCH + '/pinned.py'
    put(root, name, '# pinned code still cannot live in allowed scratch\n')
    value = json.loads(path.read_text())
    value['files'][name] = value['source_files'][name] = build.sha256_file(root / name)
    value['tree_sha256'] = build.tree_digest(value['source_files'])
    put(root, path.relative_to(root).as_posix(), value)
    assert any(row['path'] == name and row['reason'] == 'runtime_source'
               for row in verifier.verify(path, root=root))


def test_new_engine_data_outside_runtime_folder_still_drifts(runtime_tree):
    root, path = runtime_tree
    name = build.ENGINE + '/unapproved-results/new.json'
    put(root, name, {'added': True})
    assert any(row['path'] == name and row['reason'] == 'added'
               for row in verifier.verify(path, root=root))


def test_new_runtime_symlink_is_not_ignored(runtime_tree):
    root, path = runtime_tree
    alias = root / SCRATCH / 'runtime-link.json'
    alias.symlink_to(root / 'src/runtime.py')
    drifts = verifier.verify(path, root=root)
    assert drifts and any('symlink' in str(row) for row in drifts)


def test_live_config_cannot_expand_a_frozen_policy(runtime_tree):
    root, path = runtime_tree
    extra = build.ENGINE + '/unapproved-scratch/nested'
    put(root, runtime_outputs.CONFIG, {'schema_version': runtime_outputs.SCHEMA,
                                       'directories': [SCRATCH, extra]})
    name = extra + '/new.json'
    put(root, name, {'added': True})
    drifts = verifier.verify(path, root=root)
    assert any(row['path'] == runtime_outputs.CONFIG and row['reason'] == 'changed' for row in drifts)
    assert drifts
    with pytest.raises(verifier.FreezeDrift):
        verifier.require_frozen(path, root=root)


def test_manifest_policy_expansion_must_match_its_independent_config_pin(runtime_tree):
    root, path = runtime_tree
    value = json.loads(path.read_text())
    value['selection']['runtime_outputs']['directories'].append(build.ENGINE + '/unapproved-scratch/nested')
    put(root, path.relative_to(root).as_posix(), value)
    assert any(row['reason'] == 'invalid_runtime_policy' for row in verifier.verify(path, root=root))


def test_legacy_manifest_does_not_inherit_new_config_exclusions(runtime_tree):
    root, path = runtime_tree
    value = json.loads(path.read_text())
    value['selection'].pop('runtime_outputs')
    put(root, path.relative_to(root).as_posix(), value)
    name = SCRATCH + '/54442/new.json'
    put(root, name, {'added': True})
    assert any(row['path'] == name and row['reason'] == 'added'
               for row in verifier.verify(path, root=root))


@pytest.mark.parametrize('name', ['../outside', '/private/tmp/outside', 'src',
                                  'experiments/mvp/frontier/engine'])
def test_unsafe_snapshot_directory_is_rejected(runtime_tree, name):
    root, path = runtime_tree
    value = json.loads(path.read_text())
    value['selection']['runtime_outputs']['directories'] = [name]
    put(root, path.relative_to(root).as_posix(), value)
    assert any(row['reason'] == 'invalid_runtime_policy' for row in verifier.verify(path, root=root))


def test_imported_module_cannot_be_covered_by_runtime_policy(runtime_tree):
    root, path = runtime_tree
    value = json.loads(path.read_text())
    value['selection']['import_closure']['paths'] = [SCRATCH + '/imported.py']
    put(root, path.relative_to(root).as_posix(), value)
    assert any(row['reason'] == 'invalid_runtime_policy' for row in verifier.verify(path, root=root))


def test_runtime_directory_replaced_with_symlink_is_rejected(runtime_tree):
    root, path = runtime_tree
    directory = root / SCRATCH
    moved = directory.with_name('moved-data')
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    assert any(row['reason'] == 'invalid_runtime_policy' for row in verifier.verify(path, root=root))
