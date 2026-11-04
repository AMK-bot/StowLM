"""Launch refusal, metadata weights and small admission/count/path checks only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.mvp.pipeline.freeze import build, evidence, links
from experiments.mvp.pipeline.freeze import verify as verifier
from experiments.mvp.pipeline.freeze import admission as capacity
from experiments.mvp.pipeline.freeze.paths import contained_output
from experiments.mvp.pipeline.freeze.suite_counts import parse_junit

HERE = Path(__file__).resolve().parent


def test_verify_direct_cli_imports_without_running_checks():
    result = subprocess.run([sys.executable, str(HERE / 'verify.py'), '--help'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '--require-frozen' in result.stdout


def test_output_fence_itself_cannot_be_replaced_with_symlink(tmp_path):
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    fence = tmp_path / 'freeze'
    fence.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='fence'):
        contained_output(fence / 'manifest.json', fence)


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    source = root / 'src/pinned.py'
    source.parent.mkdir()
    source.write_text('synthetic source')
    weight = root / 'models/base.safetensors'
    weight.parent.mkdir()
    weight.write_bytes(b'synthetic weight')
    adapter = root / 'adapter/reader.safetensors'
    adapter.parent.mkdir()
    adapter.write_bytes(b'synthetic reader')
    files = {str(path.relative_to(root)): build.sha256_file(path) for path in (source, weight, adapter)}
    sources = {'src/pinned.py': files['src/pinned.py']}
    weights = ['models/base.safetensors', 'adapter/reader.safetensors']
    manifest = {'schema_version': verifier.SCHEMA, 'task': '2.1', 'status': 'frozen', 'scope': 'film',
                'dry_run': False, 'readiness': {'ready': True}, 'files': files,
                'source_files': sources, 'tree_sha256': build.tree_digest(sources),
                'base_model': {'weight_files': {'models/base.safetensors': files['models/base.safetensors']}},
                'model_weight_files': weights,
                'weight_stats': {name: {'exists': True, 'size': (root / name).stat().st_size,
                                       'mtime_ns': (root / name).stat().st_mtime_ns} for name in weights}}
    pipeline = {**manifest, 'scope': 'pipeline', 'files': {}, 'source_files': {},
                'tree_sha256': build.tree_digest({}), 'base_model': {'weight_files': {}},
                'model_weight_files': [], 'weight_stats': {}}
    pipeline_path = root / build.FREEZE / 'guard-pipeline.json'
    pipeline_path.parent.mkdir(parents=True)
    pipeline_path.write_text(json.dumps(pipeline))
    manifest['pipeline_layer'] = {'path': pipeline_path.relative_to(root).as_posix(),
                                  'manifest_sha256': build.sha256_file(pipeline_path),
                                  'tree_digest': pipeline['tree_sha256']}
    path = root / 'manifest.json'
    path.write_text(json.dumps(manifest))
    # Isolate the small dependency manifest's inventory; production scope logic
    # is covered separately with real inventories in test_scopes_verify.py.
    monkeypatch.setattr(build, 'inventory_paths', lambda *args, **kwargs:
                        [] if kwargs.get('scope') == 'pipeline' else sorted(files))
    return root, path, manifest


def test_guard_returns_digest_of_exact_manifest_bytes(guarded):
    root, path, manifest = guarded
    assert verifier.require_frozen(path, root=root) == {
        'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'tree_digest': manifest['tree_sha256']}
    assert verifier.require_frozen(path, root=root, full_weights=True)['tree_digest'] == manifest['tree_sha256']


@pytest.mark.parametrize(('field', 'value', 'reason'), [
    ('dry_run', True, 'dry_manifest'), ('readiness', {'ready': False}, 'not_ready'),
    ('schema_version', 'future/9', 'unsupported_schema'), ('status', 'draft', 'not_frozen'),
    ('task', '9.9', 'not_frozen'),
])
def test_guard_refuses_dry_unready_or_unknown_schema(guarded, field, value, reason):
    root, path, manifest = guarded
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    assert verifier.verify(path, root=root) == []
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(path, root=root)
    assert any(row['reason'] == reason for row in error.value.drifts)
    assert reason in str(error.value)


@pytest.mark.parametrize('name', ['models/base.safetensors', 'adapter/reader.safetensors'])
@pytest.mark.parametrize('change', ['remove', 'size', 'mtime'])
def test_default_check_stats_each_weight(guarded, name, change):
    root, path, _ = guarded
    weight = root / name
    if change == 'remove':
        weight.unlink()
    elif change == 'size':
        weight.write_bytes(b'changed size')
    else:
        stat = weight.stat()
        os.utime(weight, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000))
    drifts = verifier.verify(path, root=root)
    assert [(row['path'], row['reason']) for row in drifts] == [(name, 'missing' if change == 'remove' else 'changed')]
    with pytest.raises(verifier.FreezeDrift):
        verifier.require_frozen(path, root=root)


def test_full_check_hashes_weights_with_restored_stat(guarded):
    root, path, _ = guarded
    weight = root / 'models/base.safetensors'
    stat = weight.stat()
    weight.write_bytes(b'X' * stat.st_size)
    os.utime(weight, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert verifier.verify(path, root=root) == []
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(path, root=root, full_weights=True)
    assert error.value.drifts[0]['path'] == 'models/base.safetensors'
    assert error.value.drifts[0]['reason'] == 'changed'


def test_guard_detects_manifest_replacement_during_verification(guarded, monkeypatch):
    root, path, _ = guarded
    original = verifier._verify
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_bytes(path.read_bytes() + b'\n')
        return result
    monkeypatch.setattr(verifier, '_verify', changed)
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(path, root=root)
    assert error.value.drifts == [{'path': str(path), 'reason': 'manifest_changed_during_verification'}]


@pytest.mark.parametrize('name', ['src/pinned.py', 'models/base.safetensors'])
def test_replaced_pinned_symlink_is_rejected_without_opening_target(guarded, name):
    root, path, _ = guarded
    target = root / name
    target.unlink()
    target.symlink_to(root / 'missing-target')
    assert any(row['path'] == name and row['reason'] == 'symlink'
               for row in verifier.verify(path, root=root))


def test_default_check_enumerates_new_weight(guarded, monkeypatch):
    root, path, manifest = guarded
    monkeypatch.setattr(build, 'inventory_paths', lambda *args, **kwargs:
                        [] if kwargs.get('scope') == 'pipeline' else sorted(manifest['files']) + ['models/new.safetensors'])
    assert verifier.verify(path, root=root) == [{'path': 'models/new.safetensors', 'reason': 'added'}]


def test_guard_rejects_new_evidence_only_hero_cache_symlink(guarded):
    root, path, _ = guarded
    link = root / 'experiments/mvp/ui/cache/v6-hero/new-plan.json'
    link.parent.mkdir(parents=True)
    link.symlink_to(root / 'src/pinned.py')
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(path, root=root)
    assert any(row['path'] == link.relative_to(root).as_posix() and row['reason'] == 'added'
               for row in error.value.drifts)


def test_dry_verify_permits_only_unchanged_evidence_errors(guarded):
    root, path, manifest = guarded
    fill = root / 'app/fill-v6.json'
    fill.parent.mkdir()
    fill.write_text(json.dumps({'n': {'source': 'experiments/mvp/ui/missing-source.json'}}))
    manifest['dry_run'] = True
    manifest['evidence'] = evidence.inventory(root, scope='film')
    path.write_text(json.dumps(manifest))
    assert manifest['evidence']['errors']
    assert verifier.verify(path, root=root) == []
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(path, root=root)
    assert any(row['reason'] == 'missing_evidence' for row in error.value.drifts)
    fill.unlink()
    assert verifier.verify(path, root=root)[0]['reason'] == 'evidence_error_removed'


def test_final_guard_rejects_manifest_evidence_errors_even_if_manually_ready(guarded):
    root, path, manifest = guarded
    name = 'experiments/mvp/ui/cache/v6-hero/old-unsafe.json'
    manifest['evidence'] = {'errors': ['symlink excluded: ' + name]}
    manifest['readiness'] = {'ready': True}
    path.write_text(json.dumps(manifest))
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(path, root=root)
    assert any(row['path'] == name and row['reason'] == 'symlink' for row in error.value.drifts)


def _pin_leaf(guarded, *, target='src/pinned.py'):
    root, path, manifest = guarded
    name = 'experiments/mvp/ui/cache/v6-hero/plan.json'
    link = root / name
    link.parent.mkdir(parents=True)
    link.symlink_to(root / target)
    manifest['symlinks'] = {name: links.inspect(root, name, manifest['files'])}
    manifest['evidence'] = evidence.inventory(root, scope='film')
    path.write_text(json.dumps(manifest))
    return root, path, manifest, link


def test_valid_pinned_leaf_link_passes_and_exact_text_retarget_drifts(guarded):
    root, path, manifest, link = _pin_leaf(guarded)
    assert verifier.verify(path, root=root) == []
    assert verifier.require_frozen(path, root=root)['tree_digest'] == manifest['tree_sha256']
    link.unlink()
    link.symlink_to(os.path.relpath(root / 'src/pinned.py', link.parent))
    drifts = verifier.verify(path, root=root)
    assert any(row['reason'] == 'changed_symlink' and row['path'] == link.relative_to(root).as_posix()
               for row in drifts)


def test_pinned_leaf_target_bytes_are_rehashed_even_when_weight_stat_restored(guarded):
    root, path, _, link = _pin_leaf(guarded, target='models/base.safetensors')
    weight = root / 'models/base.safetensors'
    stat = weight.stat()
    weight.write_bytes(b'x' * stat.st_size)
    os.utime(weight, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    drifts = verifier.verify(path, root=root)
    assert any(row['reason'] == 'changed_symlink' and row['path'] == link.relative_to(root).as_posix()
               for row in drifts)


def test_pinned_leaf_retarget_to_another_pinned_file_drifts(guarded):
    root, path, _, link = _pin_leaf(guarded)
    link.unlink()
    link.symlink_to(root / 'models/base.safetensors')
    drifts = verifier.verify(path, root=root)
    changed = next(row for row in drifts if row['reason'] == 'changed_symlink')
    assert changed['actual']['resolved_target'] != changed['expected']['resolved_target']


@pytest.mark.parametrize('target_kind', ['outside', 'unpinned', 'directory'])
def test_guard_rejects_unsafe_leaf_link_target(guarded, target_kind):
    root, path, manifest = guarded
    name = 'experiments/mvp/ui/cache/v6-hero/plan.json'
    link = root / name
    link.parent.mkdir(parents=True)
    target = root.parent / 'outside.json' if target_kind == 'outside' else root / 'unpinned.json'
    if target_kind == 'directory':
        target = root / 'src'
    else:
        target.write_text('synthetic unpinned target')
    link.symlink_to(target)
    manifest['symlinks'] = {name: {'link_text': str(target), 'resolved_target': str(target), 'target_sha256': '0' * 64}}
    path.write_text(json.dumps(manifest))
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(path, root=root)
    assert any(row['path'] == name and row['reason'] == 'unsafe_symlink' for row in error.value.drifts)


def test_import_closure_symlink_is_rejected_even_when_target_is_pinned(guarded):
    root, path, manifest, link = _pin_leaf(guarded)
    name = link.relative_to(root).as_posix()
    manifest['selection'] = {'import_closure': {'paths': [name]}}
    path.write_text(json.dumps(manifest))
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(path, root=root)
    assert any(row['path'] == name and row['reason'] == 'import_symlink' for row in error.value.drifts)


def test_admission_overflow_refuses_and_retries(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps({'mask': 255, 'load1': 10**1000, 't': 1}))
    assert capacity.admission(path, now=1)['admitted'] is False
    sleeps = []
    def make_available(interval):
        sleeps.append(interval)
        path.write_text(json.dumps({'mask': 255, 'load1': 1, 't': 1}))
    monkeypatch.setattr(capacity.time, 'time', lambda: 1)
    monkeypatch.setattr(capacity.time, 'sleep', make_available)
    assert capacity.wait_for_admission(path, interval=0.1)['admitted'] is True
    assert sleeps == [0.1]


def test_counts_multiple_junit_error_elements(tmp_path):
    path = tmp_path / 'errors.xml'
    path.write_text('<testsuite><testcase time="1"><error/><error/></testcase><testcase/></testsuite>')
    counts = parse_junit(path)
    assert counts['tests'] == 2 and counts['errors'] == 2 and counts['passed'] == 1


def test_output_containment_rejects_traversal_and_symlink(tmp_path):
    fence = tmp_path / 'freeze'
    fence.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (fence / 'escape').symlink_to(outside, target_is_directory=True)
    for path in (outside / 'out.json', fence / '..' / 'out.json', fence / 'escape' / 'out.json'):
        with pytest.raises(ValueError, match='outside freeze'):
            contained_output(path, fence)
    assert contained_output(fence / 'new' / 'out.json', fence) == fence / 'new' / 'out.json'


def test_counts_cli_rejects_output_outside_fence(tmp_path):
    output = HERE.parents[0] / ('rejected-count-' + tmp_path.name + '.json')
    result = subprocess.run(['bash', str(HERE / 'suites.sh'), '--parse-junit',
                             str(HERE / 'fixtures/suites.xml'), '--out', str(output)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert 'outside freeze' in result.stderr
    assert not output.exists()


def test_suites_rejects_outside_output_before_admission(tmp_path):
    output = HERE.parents[0] / ('rejected-suites-' + tmp_path.name)
    shim = tmp_path / 'bin'
    shim.mkdir()
    uv = shim / 'uv'
    # Never let a regression in containment reach admission or a real suite.
    uv.write_text('#!/bin/bash\n'
                  '[[ "${5:-}" == */freeze/paths.py ]] || exit 77\n'
                  'exec "$FREEZE_TEST_PYTHON" "${@:5}"\n')
    uv.chmod(0o755)
    env = {**os.environ, 'PATH': str(shim) + os.pathsep + os.environ['PATH'],
           'FREEZE_TEST_PYTHON': sys.executable}
    result = subprocess.run(['bash', str(HERE / 'suites.sh'), '--out-dir', str(output)],
                            capture_output=True, text=True, timeout=20, env=env)
    assert result.returncode != 0
    assert 'outside freeze' in result.stderr
    assert not output.exists()
