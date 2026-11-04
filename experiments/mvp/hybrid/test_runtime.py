"""Snapshot integrity, parameter/assets resolution, and import isolation."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hybrid_runtime_tested', HERE / 'runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'checkout'
    write(root, 'pyproject.toml', '[project]\nname="fixture"\nversion="0"\n')
    write(root, 'src/stowage/__init__.py', '')
    write(root, 'src/stowage/params.py', '''from pathlib import Path
def repo_root():
    return next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
''')
    primitive = write(root, 'src/stowage/linear/boundary_calibration.py', 'VALUE = "sealed"\n')
    write(root, 'src/stowage/linear/margin_policy.py', '''from pathlib import Path
import hashlib
_ROOT = Path(__file__).resolve().parents[3]
_BANK_PATH = Path(__file__).with_name("margin_policy_data.json")
def policy_inputs():
    return {"bank_sha256": hashlib.sha256(_BANK_PATH.read_bytes()).hexdigest()}
''')
    write(root, 'src/stowage/generate/physics_identity.py', 'from pathlib import Path\n')
    evidence = write(root, 'experiments/qualification/result.json', '{"passed":true}\n')
    builder = write(root, 'experiments/qualification/builder.py', 'VALUE = "builder"\n')
    bank = dict(evidence=[dict(path=str(primitive), sha256=runtime._hash(primitive)),
        dict(path='experiments/qualification/builder.py', sha256=runtime._hash(builder)),
        dict(path=str(evidence), sha256=runtime._hash(evidence))])
    nested_evidence = write(root, 'experiments/cache/stability/liquid-qualified.json',
                            '{"qualified":true}\n')
    bank['evidence'].append(dict(path=str(nested_evidence), sha256=runtime._hash(nested_evidence)))
    write(root, 'src/stowage/linear/margin_policy_data.json', json.dumps(bank))
    write(root, 'src/stowage/rules/catalogue.toml', '[rules]\nactive=true\n')
    write(root, 'params/fleet/ship.toml', '[hull_form]\noffsets_path="data/raw/hull.fix"\n')
    write(root, 'data/raw/hull.fix', 'pinned geometry\n')
    write(root, 'models/glimmer-30b-4bit/tokenizer.json', '{"tokenizer":"cpu"}\n')
    write(root, 'experiments/mvp/hybrid/planner.py', 'VALUE = "hybrid"\n')
    write(root, 'experiments/mvp/hybrid/admission.py', 'VALUE = "admission"\n')
    write(root, 'experiments/mvp/hybrid/contribution.py', 'VALUE = "contribution"\n')
    write(root, 'experiments/mvp/frontier/planner.py', 'VALUE = "frontier"\n')
    write(root, 'experiments/mvp/strategy/compact.py', 'VALUE = "compact"\n')
    helper = HERE.parents[2] / 'experiments/mvp/gold_tools/driver.py'
    write(root, 'experiments/mvp/gold_tools/driver.py', helper.read_text())
    write(root, 'experiments/cache/vessels/numerical.json', '{"numeric":1}\n')
    write(root, 'experiments/cache/stability/geometry.npz', 'numeric fixture\n')
    write(root, 'experiments/cache/linear/model/model.npz', 'numeric fixture\n')
    return root


def seal(repository):
    sources = {'src/stowage/params.py': runtime._hash(repository / 'src/stowage/params.py')}
    return runtime.seal_runtime(sources, destination=repository / 'experiments/mvp/hybrid/runtime',
                                repo_root=repository)


def test_copies_code_resources_geometry_tokenizer_and_helpers(repository):
    binding = seal(repository)
    root = Path(binding['root'])
    for name in ('src/stowage/params.py', 'src/stowage/rules/catalogue.toml',
                 'params/fleet/ship.toml', 'data/raw/hull.fix',
                 'models/glimmer-30b-4bit/tokenizer.json',
                 'experiments/mvp/hybrid/admission.py', 'experiments/mvp/hybrid/contribution.py',
                 'experiments/qualification/builder.py', 'experiments/mvp/gold_tools/driver.py'):
        assert name in binding['files']
        assert not (root / name).is_symlink()
        assert (root / name).read_bytes() == (repository / name).read_bytes()
    assert not (root / 'experiments/cache').is_symlink()
    assert (root / 'experiments/cache/vessels').is_symlink()
    assert runtime.verify_runtime(binding)
    assert seal(repository) == binding


def test_original_source_edits_cannot_change_existing_runtime(repository):
    binding = seal(repository)
    write(repository, 'src/stowage/params.py', 'raise RuntimeError("unrelated live edit")\n')
    write(repository, 'src/new_training_module.py', 'raise RuntimeError("new live module")\n')
    assert runtime.verify_runtime(binding)
    assert 'unrelated live edit' not in (Path(binding['root']) / 'src/stowage/params.py').read_text()


def test_bank_bytes_and_identity_preserved_while_absolute_pins_relocate(repository):
    binding = seal(repository)
    root = Path(binding['root'])
    bank_path = root / 'src/stowage/linear/margin_policy_data.json'
    bank = json.loads(bank_path.read_text())
    pin = bank['evidence'][0]
    primitive = root / 'src/stowage/linear/boundary_calibration.py'
    assert Path(pin['path']).is_absolute()  # Original resource bytes are untouched.
    assert pin['sha256'] == runtime._hash(primitive)
    original_hash = runtime._hash(repository / 'src/stowage/linear/margin_policy_data.json')
    assert runtime._hash(bank_path) == original_hash
    assert binding['transforms'] == {}
    write(repository, 'src/stowage/linear/boundary_calibration.py', 'VALUE = "changed live source"\n')
    assert runtime.verify_runtime(binding)
    script = '''import importlib.util,json,sys,hashlib
from pathlib import Path
spec=importlib.util.spec_from_file_location("snapshot_helper",sys.argv[1])
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
binding=json.loads(sys.argv[2]);runtime.activate_runtime(binding)
from stowage.linear import margin_policy,boundary_calibration
from stowage.generate import physics_identity
root=Path(binding["root"]); original=Path(binding["original_root"])
bank=json.loads(margin_policy._BANK_PATH.read_text())
primitive=Path(boundary_calibration.__file__)
assert margin_policy.Path(bank["evidence"][0]["path"]).resolve()==primitive.resolve()
assert boundary_calibration.VALUE=="sealed"
assert margin_policy.policy_inputs()["bank_sha256"]==sys.argv[3]
assert margin_policy._ROOT==root
evidence=bank["evidence"][2]
relocated=margin_policy.Path(evidence["path"])
assert str(relocated)==str(root/"experiments/qualification/result.json")
assert hashlib.sha256(relocated.read_bytes()).hexdigest()==evidence["sha256"]
assert str(physics_identity.Path(str(original/"src/stowage/linear/boundary_calibration.py")))==str(primitive)
assert Path(bank["evidence"][0]["path"])==original/"src/stowage/linear/boundary_calibration.py"
runtime.assert_runtime_imports(binding)
print("scoped resource relocation verified")
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(HERE / 'runtime.py'),
                             json.dumps(binding), original_hash], cwd=repository,
                            env={**os.environ, **runtime.runtime_environment(binding)},
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'scoped resource relocation verified' in result.stdout


def test_numeric_evidence_is_hash_bound_read_link_and_python_is_copied(repository):
    binding = seal(repository)
    name = 'experiments/qualification/result.json'
    link = Path(binding['root']) / name
    assert link.is_symlink()
    assert binding['shared_reads'][name]['sha256'] == runtime._hash(repository / name)
    assert 'experiments/qualification/builder.py' in binding['files']
    assert 'experiments/qualification/builder.py' not in binding['shared_reads']
    write(repository, name, '{"passed":false}\n')
    with pytest.raises(ValueError, match='runtime evidence changed'):
        runtime.verify_runtime(binding)


def test_nested_cache_evidence_coexists_with_sparse_cache_links(repository):
    binding = seal(repository)
    root = Path(binding['root'])
    pinned = 'experiments/cache/stability/liquid-qualified.json'
    ordinary = 'experiments/cache/stability/geometry.npz'
    assert pinned in binding['shared_reads'] and pinned not in binding['links']
    assert ordinary in binding['links'] and ordinary not in binding['shared_reads']
    assert not set(binding['shared_reads']) & set(binding['links'])
    assert not (root / 'experiments/cache/stability').is_symlink()
    assert (root / pinned).read_bytes() == (repository / pinned).read_bytes()
    assert (root / ordinary).read_bytes() == (repository / ordinary).read_bytes()
    assert (root / 'experiments/cache/linear').is_symlink()
    assert runtime.verify_runtime(binding)


def test_cache_tree_with_code_is_filtered_without_python_links(repository):
    write(repository, 'experiments/cache/linear/unsafe.py', 'raise RuntimeError("live code")\n')
    binding = seal(repository)
    root = Path(binding['root'])
    assert not (root / 'experiments/cache/linear').is_symlink()
    assert not (root / 'experiments/cache/linear/unsafe.py').exists()
    assert (root / 'experiments/cache/linear/model').is_symlink()
    assert not any(Path(name).suffix == '.py' for name in binding['links'])
    assert runtime.verify_runtime(binding)


def test_later_code_injection_into_numeric_cache_cannot_import(repository):
    binding = seal(repository)
    write(repository, 'experiments/cache/vessels/later.py', 'raise RuntimeError("must not execute")\n')
    script = '''import importlib.util,json,sys
spec=importlib.util.spec_from_file_location("snapshot_helper",sys.argv[1])
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
binding=json.loads(sys.argv[2]);runtime.activate_runtime(binding)
try:
 import experiments.cache.vessels.later
except ImportError:
 print("external cache code blocked")
else:
 raise AssertionError("cache code imported")
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(HERE / 'runtime.py'), json.dumps(binding)],
                            cwd=repository, env={**os.environ, **runtime.runtime_environment(binding)},
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'external cache code blocked' in result.stdout


def test_stat_memo_reuses_only_previously_verified_unchanged_evidence(repository, monkeypatch):
    binding = seal(repository)
    cache = {}
    assert runtime.verify_runtime(binding, stat_cache=cache)
    original_hash = runtime._hash
    calls = []
    def hash_record(path):
        calls.append(str(path))
        return original_hash(path)
    monkeypatch.setattr(runtime, '_hash', hash_record)
    assert runtime.verify_runtime(binding, stat_cache=cache)
    name = 'experiments/qualification/result.json'
    assert str(Path(binding['root']) / name) not in calls
    write(repository, name, '{"passed":false}\n')
    with pytest.raises(ValueError, match='runtime evidence changed'):
        runtime.verify_runtime(binding, stat_cache=cache)


@pytest.mark.parametrize('mutation', ['modified', 'injected', 'symlink'])
def test_runtime_corruption_is_rejected(repository, mutation):
    binding = seal(repository)
    root = Path(binding['root'])
    if mutation == 'modified':
        path = root / 'src/stowage/params.py'
        path.chmod(0o644)
        path.write_text('changed\n')
    elif mutation == 'injected':
        write(root, 'src/stowage/injected.py', 'VALUE=1\n')
    else:
        path = root / 'src/stowage/params.py'
        path.unlink()
        path.symlink_to(repository / 'src/stowage/params.py')
    with pytest.raises(ValueError, match='runtime (file|membership) changed'):
        runtime.verify_runtime(binding)


def test_changed_source_binding_cannot_be_sealed(repository):
    source = repository / 'src/stowage/params.py'
    with pytest.raises(ValueError, match='source changed before runtime seal'):
        runtime.seal_runtime({'src/stowage/params.py': '0' * 64}, repo_root=repository,
            destination=repository / 'experiments/mvp/hybrid/runtime')


def test_output_scope_and_binding_identity_are_checked(repository):
    with pytest.raises(ValueError, match='inside hybrid'):
        runtime.seal_runtime({}, repo_root=repository, destination=repository / 'outside')
    binding = seal(repository)
    binding['files']['src/stowage/params.py'] = '0' * 64
    with pytest.raises(ValueError, match='binding identity'):
        runtime.verify_runtime(binding)


def test_worker_imports_and_parameter_root_stay_in_snapshot(repository):
    binding = seal(repository)
    root = Path(binding['root'])
    write(repository, 'src/stowage/params.py', 'raise RuntimeError("live src must not execute")\n')
    script = '''import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("snapshot_helper",sys.argv[1])
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
binding=json.loads(sys.argv[2]);runtime.activate_runtime(binding)
from stowage.params import repo_root
import planner,compact
assert repo_root()==Path(binding["root"])
assert planner.VALUE=="hybrid" and compact.VALUE=="compact"
runtime.assert_runtime_imports(binding)
print("snapshot imports verified")
'''
    env = {**os.environ, **runtime.runtime_environment(binding)}
    result = subprocess.run([sys.executable, '-B', '-c', script, str(HERE / 'runtime.py'), json.dumps(binding)],
                            cwd=repository, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'snapshot imports verified' in result.stdout


def test_live_editable_import_fallback_is_blocked(repository):
    binding = seal(repository)
    write(repository, 'src/stowage/live_only.py', 'raise RuntimeError("must not execute")\n')
    script = '''import importlib.util,json,sys
spec=importlib.util.spec_from_file_location("snapshot_helper",sys.argv[1])
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
binding=json.loads(sys.argv[2]);runtime.activate_runtime(binding)
sys.path.append(binding["original_root"]+"/src")
try:
 import stowage.live_only
except ImportError:
 print("blocked")
else:
 raise AssertionError("live source fallback was admitted")
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(HERE / 'runtime.py'), json.dumps(binding)],
                            cwd=repository, env={**os.environ, **runtime.runtime_environment(binding)},
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'blocked' in result.stdout
