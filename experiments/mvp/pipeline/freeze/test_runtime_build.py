"""D284 synthetic policy and builder checks; no real engine, suites or GPU."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.freeze import build, runtime_outputs as outputs
from experiments.mvp.pipeline.freeze.test_freeze import tree, put

SCRATCH = outputs.DEFAULT_DIRECTORIES[0]


def policy_file(root, directories):
    return put(root, outputs.CONFIG, {'schema_version': outputs.SCHEMA, 'directories': directories})


@pytest.mark.parametrize('scope', ['pipeline', 'film'])
def test_existing_runtime_output_bytes_are_omitted_from_all_inventories(tree, scope):
    policy_file(tree, [SCRATCH])
    names = [SCRATCH + '/123/trace.json', SCRATCH + '/123/cache/plot.html',
             SCRATCH + '/123/.cache/metadata.bin']
    for name in names:
        put(tree, name, 'synthetic scratch')
    selection = {'paths': names, 'symlink_paths': names, 'errors': []}
    assert not set(names) & set(build.source_paths(tree, import_closure=[], scope=scope))
    assert not set(names) & set(build.inventory_paths(tree, import_closure=[], evidence_selection=selection, scope=scope))
    assert not build.inventory_symlinks(tree, import_closure=[], evidence_selection=selection, scope=scope)


def test_build_pins_exact_config_and_snapshots_policy(tree):
    path = policy_file(tree, [SCRATCH])
    put(tree, SCRATCH + '/123/trace.json', '{}')
    value = build.build_manifest(tree, no_weights=True)
    policy = value['selection']['runtime_outputs']
    assert policy['directories'] == [SCRATCH]
    assert policy['config_present'] is True
    assert policy['config_sha256'] == build.sha256_file(path)
    assert value['files'][outputs.CONFIG] == policy['config_sha256']
    assert value['source_files'][outputs.CONFIG] == policy['config_sha256']
    assert not any(name.startswith(SCRATCH + '/') for name in value['files'])
    assert outputs.from_manifest(tree, value)['directories'] == [SCRATCH]
    assert value['readiness']['ready'], value['readiness']


@pytest.mark.parametrize('name', ['', '.', '..', '/tmp/output', 'experiments/cache',
                                 'experiments/mvp/frontier/engine',
                                 'experiments/mvp/frontier/engine/peel_ladder',
                                 'experiments/mvp/frontier/engine/results',
                                 'models/glimmer-30b-4bit/weights/shards',
                                 'params/fleet/one/cache', 'experiments/mvp/maps/sealed/cache',
                                 'experiments/mvp/testv3/output/rows',
                                 'experiments/mvp/pipeline/.keys/cache',
                                 SCRATCH + '/..', SCRATCH + '/', SCRATCH + '/*'])
def test_invalid_unsafe_and_broad_runtime_directories_are_rejected(tree, name):
    policy_file(tree, [name])
    with pytest.raises(ValueError):
        outputs.load(tree)


@pytest.mark.parametrize('name', ['experiments/cache/vessels', 'experiments/cache/stability',
                                 'experiments/cache/linear',
                                 'experiments/mvp/pipeline/sealed/runtime-cache'])
def test_narrow_known_cache_and_public_harness_output_directories_are_allowed(tree, name):
    policy_file(tree, [name])
    assert outputs.load(tree)['directories'] == [name]


@pytest.mark.parametrize('suffix', ['.py', '.pyi', '.so', '.sh', '.ts'])
def test_source_files_in_runtime_directory_are_never_excluded_silently(tree, suffix):
    policy_file(tree, [SCRATCH])
    name = SCRATCH + '/123/cache/nested/source' + suffix
    put(tree, name, '# synthetic source')
    with pytest.raises(ValueError, match='source file under runtime'):
        outputs.load(tree)
    with pytest.raises(ValueError, match='source file under runtime'):
        build.build_manifest(tree, no_weights=True)
    policy = outputs.validate(tree, {'directories': [SCRATCH]}, check_contents=False)
    assert name in outputs.enumerate(tree, policy)
    assert not outputs.is_ignored_addition(name, policy)


def test_imported_module_rejects_directory_even_without_existing_file(tree):
    policy_file(tree, [SCRATCH])
    name = SCRATCH + '/runtime_module.py'
    with pytest.raises(ValueError, match='imported module'):
        outputs.load(tree, import_closure=[name])
    with pytest.raises(ValueError, match='imported module'):
        build.source_paths(tree, import_closure=[name], runtime_dirs=[SCRATCH])


def test_runtime_directory_cannot_be_a_file_or_symlink(tree):
    path = put(tree, SCRATCH, '{}')
    policy_file(tree, [SCRATCH])
    with pytest.raises(ValueError, match='not a directory'):
        outputs.load(tree)
    path.unlink()
    path.symlink_to(tree / build.PIPELINE, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        outputs.load(tree)


def test_explicit_empty_runtime_dirs_do_not_inherit_mutable_config(tree):
    policy_file(tree, [SCRATCH])
    name = SCRATCH + '/123/trace.json'
    put(tree, name, '{}')
    assert name not in build.inventory_paths(tree, import_closure=[])
    assert name in build.inventory_paths(tree, import_closure=[], runtime_dirs=())
    assert outputs.from_manifest(tree, {})['directories'] == []


def test_frozen_metadata_cannot_expand_the_pinned_config(tree):
    policy_file(tree, [SCRATCH])
    value = build.build_manifest(tree, no_weights=True)
    forged = copy.deepcopy(value)
    forged['selection']['runtime_outputs']['directories'].append('experiments/cache/vessels')
    with pytest.raises(ValueError, match='differ from pinned config'):
        outputs.from_manifest(tree, forged)
    policy_file(tree, [SCRATCH, 'experiments/cache/vessels'])
    with pytest.raises(ValueError, match='differs from frozen pin'):
        outputs.from_manifest(tree, value)


def test_fallback_metadata_has_no_mutable_expansion(tree):
    value = build.build_manifest(tree, no_weights=True)
    assert outputs.from_manifest(tree, value)['directories'] == [SCRATCH]
    value['selection']['runtime_outputs']['directories'].append('experiments/cache/vessels')
    with pytest.raises(ValueError, match='fallback'):
        outputs.from_manifest(tree, value)


@pytest.mark.parametrize('directories', [[SCRATCH, SCRATCH], [SCRATCH, SCRATCH + '/nested']])
def test_duplicate_or_overlapping_runtime_output_directories_are_rejected(tree, directories):
    policy_file(tree, directories)
    with pytest.raises(ValueError):
        outputs.load(tree)
