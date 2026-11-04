"""Synthetic inventories and canned JUnit only; no suites, GPU or model loads."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.mvp.pipeline.freeze import build
from experiments.mvp.pipeline.freeze import verify as verifier
from experiments.mvp.pipeline.freeze.admission import admission
from experiments.mvp.pipeline.freeze.suite_counts import parse_junit


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) if isinstance(value, dict) else value)
    return path


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / 'synthetic-repo'
    monkeypatch.setattr(build, '_git', lambda root: {'head': 'fake-head', 'modified_files': []})
    put(root, build.ENGINE + '/planner.py', '# synthetic engine\n')
    put(root, 'src/pinned.py', '# synthetic pinned native source\n')
    pins = {'planner.py': build.sha256_file(root / build.ENGINE / 'planner.py'),
            'src/pinned.py': build.sha256_file(root / 'src/pinned.py')}
    review = {'version': '0.13.0', 'source_files': pins}
    put(root, build.REVIEW, review)
    put(root, build.FINAL, review)
    put(root, build.NATIVE, {'src/pinned.py': pins['src/pinned.py']})
    put(root, build.ENGINE + '/__init__.py', 'VERSION = "0.13.0"\n')
    put(root, build.MODEL + '/config.json', {'synthetic': True})
    put(root, build.MODEL + '/model-00001.safetensors', 'synthetic base bytes')
    put(root, build.MODEL + '/model.safetensors.index.json',
        {'weight_map': {'fake': 'model-00001.safetensors'}})
    reader = 'experiments/mvp/reader/v3/candidates/swa-last-5'
    put(root, reader + '/adapters.safetensors', 'synthetic reader adapter bytes')
    put(root, reader + '/adapter_config.json', {'synthetic': True})
    decoder = put(root, 'experiments/mvp/reader/v3/decoder.py', '# synthetic decoder\n')
    domain = put(root, 'experiments/mvp/reader/v3/domain_words.py', '# synthetic words\n')
    identity = {'adapter_path': reader, 'name': 'swa-last-5',
                'adapter_sha256': build.sha256_file(root / reader / 'adapters.safetensors'),
                'decoder_sha256': build.sha256_file(decoder), 'domain_words_sha256': build.sha256_file(domain)}
    put(root, build.TIMING, {'identity': identity})
    put(root, build.INTAKE + '/results/sealed-final/gates.json',
        {'decision': 'cut', 'all_pass': False, 'reason': 'synthetic failed gate'})
    put(root, build.PIPELINE + '/runner/fixtures/profile-selection.json', {'profile': 'live'})
    put(root, build.PIPELINE + '/lattice/hero.json', {'synthetic': True})
    put(root, build.PIPELINE + '/lattice/store/plan.json', {'synthetic': True})
    put(root, build.PIPELINE + '/copilot/fixtures/replay.json', {'synthetic': True})
    put(root, build.PIPELINE + '/xray/fixtures/trace.json', {'synthetic': True})
    put(root, build.PIPELINE + '/package/fixtures/package.json', {'synthetic': True})
    for stage, names in build.PROMPT_SOURCES.items():
        for name in names:
            put(root, name, f'PROMPT = "Synthetic {stage} prompt. Read the input and return strictly validated JSON."\n')
    touches = ['src/pinned.py', 'src/free.py', 'app/view.py', build.REVIEW, 'src/pinned.py']
    for number, name in enumerate(touches, 1):
        text = f'diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-old\n+new\n'
        if number == 5:
            text = 'diff --git a/src/pinned.py b/src/free.py\nrename from src/pinned.py\nrename to src/free.py\n'
        put(root, f'experiments/mvp/postfreeze/{number:02d}-synthetic.diff', text)
    return root


def snapshot(root, *, no_weights=True):
    value = build.build_manifest(root, no_weights=no_weights)
    path = put(root, build.FREEZE + '/unit-manifest.json', value)
    return path, value


def test_deterministic_manifest_except_timestamp(tree):
    first = build.build_manifest(tree, no_weights=True)
    second = build.build_manifest(tree, no_weights=True)
    assert first.pop('created_utc')
    assert second.pop('created_utc')
    assert json.dumps(first, sort_keys=True, indent=2) == json.dumps(second, sort_keys=True, indent=2)
    assert first['tree_sha256'] == build.tree_digest(first['source_files'])
    assert first['readiness']['ready'], first['readiness']


def test_new_manifest_verifies_without_drift(tree):
    path, manifest = snapshot(tree)
    assert manifest['files']
    assert verifier.verify(path, root=tree) == []


def test_lists_every_changed_deleted_and_added_file(tree):
    path, manifest = snapshot(tree)
    changes = ['src/pinned.py', build.PIPELINE + '/lattice/hero.json',
               'experiments/mvp/reader/v3/decoder.py', build.MODEL + '/config.json']
    deleted = [build.FINAL, build.PIPELINE + '/copilot/fixtures/replay.json']
    added = ['src/new.py', build.PIPELINE + '/package/fixtures/new.json', build.MODEL + '/new-config.json']
    for name in changes:
        put(tree, name, 'changed synthetic bytes')
    for name in deleted:
        (tree / name).unlink()
    for name in added:
        put(tree, name, 'new synthetic bytes')
    drifts = verifier.verify(path, root=tree)
    assert {(row['path'], row['reason']) for row in drifts} == {
        *((name, 'changed') for name in changes),
        *((name, 'missing') for name in deleted),
        *((name, 'added') for name in added),
    }


def test_reader_adapter_drift_is_checked_by_default(tree):
    path, manifest = snapshot(tree)
    reader = manifest['adapters']['reader']['identity']['adapter_path'] + '/adapters.safetensors'
    assert reader in manifest['files']
    put(tree, reader, 'changed reader bytes')
    assert [(d['path'], d['reason']) for d in verifier.verify(path, root=tree)] == [(reader, 'changed')]


def test_base_weight_stat_drift_is_checked_by_default(tree):
    path, manifest = snapshot(tree, no_weights=False)
    name = build.MODEL + '/model-00001.safetensors'
    put(tree, name, 'changed base bytes')
    assert [(d['path'], d['reason']) for d in verifier.verify(path, root=tree)] == [(name, 'changed')]
    assert [(d['path'], d['reason']) for d in verifier.verify(path, check_weights=True, root=tree)] == [(name, 'changed')]


def test_dry_manifest_cannot_claim_base_weights_verified(tree):
    path, manifest = snapshot(tree)
    name = build.MODEL + '/model-00001.safetensors'
    assert manifest['base_model']['weight_files'][name] is None
    assert [(d['path'], d['reason']) for d in verifier.verify(path, check_weights=True, root=tree)] == [(name, 'weight_not_hashed')]


def test_detects_added_weight_by_default(tree):
    path, _ = snapshot(tree, no_weights=False)
    name = build.MODEL + '/model-00002.safetensors'
    put(tree, name, 'new synthetic shard')
    assert [(d['path'], d['reason']) for d in verifier.verify(path, root=tree)] == [(name, 'added')]
    assert [(d['path'], d['reason']) for d in verifier.verify(path, check_weights=True, root=tree)] == [(name, 'added')]


def test_missing_identity_files_are_all_reported(tree):
    path, manifest = snapshot(tree)
    names = [build.TIMING, build.REVIEW, 'experiments/mvp/reader/v3/decoder.py']
    for name in names:
        (tree / name).unlink()
    assert {(d['path'], d['reason']) for d in verifier.verify(path, root=tree)} == {(name, 'missing') for name in names}


def test_excluded_runtime_outputs_do_not_drift(tree):
    path, _ = snapshot(tree)
    for directory in ['runs', 'measurements', '__pycache__', 'caches', 'unit-tmp-example', '.pytest-tmp', 'pytest-final-tmp']:
        put(tree, build.PIPELINE + '/reader_final/' + directory + '/runtime.json', {'synthetic': True})
    put(tree, build.FREEZE + '/other-dry-manifest.json', {'synthetic': True})
    put(tree, build.PIPELINE + '/repo/metadata.jsonl', 'synthetic scan cache')
    put(tree, build.PIPELINE + '/repo/parallel-progress.json', {'synthetic': True})
    put(tree, build.INTAKE + '/results/sealed-final/untrained.predictions.jsonl', 'synthetic measurement')
    assert verifier.verify(path, root=tree) == []


def test_invalid_manifest_digest_is_reported(tree):
    path, manifest = snapshot(tree)
    manifest['tree_sha256'] = '0' * 64
    put(tree, build.FREEZE + '/unit-manifest.json', manifest)
    assert verifier.verify(path, root=tree)[0]['reason'] == 'invalid_digest'


def test_invalid_manifest_returns_drift(tmp_path):
    path = put(tmp_path, 'bad.json', '{')
    assert verifier.verify(path, root=tmp_path)[0]['reason'] == 'invalid_manifest'


def test_diff_verdict_checks_fixed_engine_and_every_touched_side(tree):
    manifest = build.build_manifest(tree, no_weights=True)
    assert manifest['engine']['review_check']['unchanged']
    assert [Path(row['path']).name[:2] for row in manifest['postfreeze_diffs'] if row['safe']] == ['02', '03']
    assert manifest['postfreeze_diffs'][0]['pinned_touches'] == ['src/pinned.py']
    assert manifest['postfreeze_diffs'][3]['pinned_touches'] == [build.REVIEW]
    assert manifest['postfreeze_diffs'][4]['pinned_touches'] == ['src/pinned.py']


@pytest.mark.parametrize('name', ['src/pinned.py', build.ENGINE + '/planner.py'])
def test_engine_drift_makes_every_diff_unsafe(tree, name):
    put(tree, name, 'changed pinned bytes')
    manifest = build.build_manifest(tree, no_weights=True)
    assert manifest['engine']['review_check']['drifts'] == [name]
    assert not any(row['safe'] for row in manifest['postfreeze_diffs'])


def test_explicit_cut_removes_intake_adapter_requirement(tree):
    manifest = build.build_manifest(tree, no_weights=False)
    assert manifest['adapters']['intake']['status'] == 'cut'
    assert not (tree / build.INTAKE / 'adapter-intake-v1').exists()
    assert manifest['readiness']['ready'], manifest['readiness']


def test_pending_intake_is_not_ready_to_freeze(tree):
    (tree / build.INTAKE / 'results/sealed-final/gates.json').unlink()
    manifest = build.build_manifest(tree, no_weights=True)
    assert manifest['adapters']['intake']['status'] == 'pending'
    assert not manifest['readiness']['ready']


def test_adopted_intake_adapter_is_pinned_and_verified(tree):
    weight = build.INTAKE + '/adapter-intake-v1/adapters.safetensors'
    put(tree, weight, 'synthetic adopted intake bytes')
    put(tree, build.INTAKE + '/results/sealed-final/gates.json',
        {'decision': 'eligible', 'all_pass': True, 'I1': True, 'I2': True, 'I3': True, 'I4': True})
    put(tree, build.INTAKE + '/results/sealed-final/adoption.json',
        {'adapter_sha256': build.sha256_file(tree / weight)})
    path, manifest = snapshot(tree)
    assert manifest['adapters']['intake']['status'] == 'adopted'
    assert manifest['readiness']['ready'], manifest['readiness']
    put(tree, weight, 'changed intake bytes')
    assert [(d['path'], d['reason']) for d in verifier.verify(path, root=tree)] == [(weight, 'changed')]


def test_prompt_profile_and_demo_assets_are_pinned(tree):
    manifest = build.build_manifest(tree, no_weights=True)
    assert all(row['sources'] for row in manifest['prompts'].values())
    assert build.PIPELINE + '/runner/fixtures/profile-selection.json' in manifest['profiles']
    for suffix in ['lattice/hero.json', 'copilot/fixtures/replay.json',
                   'xray/fixtures/trace.json', 'package/fixtures/package.json']:
        assert build.PIPELINE + '/' + suffix in manifest['fixtures']
    assert build.PIPELINE + '/lattice/store/plan.json' not in manifest['files']


def test_canned_junit_counts_nested_suites_once():
    counts = parse_junit(HERE / 'fixtures/suites.xml')
    assert counts == {'tests': 6, 'passed': 2, 'failures': 1, 'errors': 1,
                      'skipped': 1, 'xfailed': 1, 'time_s': 1.0}


def test_suites_shell_parses_canned_counts_without_running_suites(tmp_path):
    out = tmp_path / build.FREEZE / 'counts.json'
    result = subprocess.run(['bash', str(HERE / 'suites.sh'), '--parse-junit',
                             str(HERE / 'fixtures/suites.xml'), '--out', str(out),
                             '--root', str(tmp_path), '--suite', 'synthetic', '--exit-code', '1'],
                            capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr
    counts = json.loads(out.read_text())
    assert counts['tests'] == 6 and counts['passed'] == 2 and counts['xfailed'] == 1
    assert counts['suite'] == 'synthetic' and counts['exit_code'] == 1


@pytest.mark.parametrize('escape', ['outside', 'traversal', 'symlink'])
def test_suite_counts_root_override_still_rejects_output_escape(tmp_path, escape):
    fence = tmp_path / build.FREEZE
    fence.mkdir(parents=True)
    if escape == 'outside':
        out = tmp_path / 'counts.json'
    elif escape == 'traversal':
        out = fence / '..' / 'counts.json'
    else:
        target = tmp_path / 'outside'
        target.mkdir()
        (fence / 'escape').symlink_to(target, target_is_directory=True)
        out = fence / 'escape' / 'counts.json'
    result = subprocess.run([sys.executable, str(HERE / 'suite_counts.py'),
                             str(HERE / 'fixtures/suites.xml'), '--out', str(out),
                             '--root', str(tmp_path)], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'outside freeze directory' in result.stderr
    assert not out.exists()


def test_suite_counts_default_root_rejects_output_outside_real_freeze(tmp_path):
    out = HERE.parent / ('rejected-counts-' + tmp_path.name + '.json')
    result = subprocess.run([sys.executable, str(HERE / 'suite_counts.py'),
                             str(HERE / 'fixtures/suites.xml'), '--out', str(out)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'outside freeze directory' in result.stderr
    assert not out.exists()


@pytest.mark.parametrize(('mask', 'load', 'age', 'expected'), [
    (255, 45, 119.9, True), (127, 1, 0, False), (255, 45.1, 0, False),
    (255, 1, 120, False), (255, 1, -1, False), (4095, 1, 0, True),
])
def test_capacity_admission_boundaries(tmp_path, mask, load, age, expected):
    path = put(tmp_path, 'state.json', {'mask': mask, 'load1': load, 't': 1000 - age})
    assert admission(path, now=1000)['admitted'] is expected


@pytest.mark.parametrize('value', ['{', '{}', '{"mask":-1,"load1":1,"t":1000}',
                                 '{"mask":255,"load1":false,"t":1000}'])
def test_bad_capacity_reading_refuses_without_raising(tmp_path, value):
    path = put(tmp_path, 'state.json', value)
    assert admission(path, now=1000)['admitted'] is False


def test_suites_shell_capacity_check_never_fails(tmp_path):
    result = subprocess.run(['bash', str(HERE / 'suites.sh'), '--check-admission',
                             '--state', str(tmp_path / 'missing.json'), '--now', '1000'],
                            capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['admitted'] is False


def test_shell_scripts_parse():
    for name in ['suites.sh', 'freeze.sh']:
        subprocess.run(['bash', '-n', str(HERE / name)], capture_output=True, text=True, check=True, timeout=5)
