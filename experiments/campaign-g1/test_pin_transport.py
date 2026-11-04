"""Production-sized numerical pins cross process boundaries through verified files."""
from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def transport():
    spec = importlib.util.spec_from_file_location('test_campaign_pin_transport', HERE / 'pin_transport.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory_pin():
    # Transport-only synthetic data: portable even on an empty checkout. Pad
    # beyond this host's process limit without depending on numerical cache data.
    sources = {name: str(ROOT / 'experiments/cache' / name) for name in ('stability', 'vessels', 'linear')}
    count = max(1, os.sysconf('SC_ARG_MAX') // 192 + 128)
    files = {f'vessels/synthetic-{index:08d}-' + 'x'*192 + '.json': 'a'*64
             for index in range(count)}
    digest = hashlib.sha256(json.dumps(dict(sources=sources, files=files), sort_keys=True).encode()).hexdigest()
    return dict(schema_version=1, sources=sources, files=files, sha256=digest,
                diagnostic=True, evidence_eligible=False)


def real_inventory_pin():
    # Extra diagnostic only: actual pathnames plus digest placeholders isolate
    # transport size without reading gigabytes of numerical payloads.
    sources = {name: str(ROOT / 'experiments/cache' / name) for name in ('stability', 'vessels', 'linear')}
    files = {f'{name}/{path.relative_to(source).as_posix()}': 'a' * 64
             for name, source in sources.items() for path in Path(source).rglob('*') if path.is_file()}
    digest = hashlib.sha256(json.dumps(dict(sources=sources, files=files), sort_keys=True).encode()).hexdigest()
    return dict(schema_version=1, sources=sources, files=files, sha256=digest)


def check_child_transport(pin, tmp_path):
    raw = json.dumps(pin, sort_keys=True)
    assert len(raw) > os.sysconf('SC_ARG_MAX')
    legacy = dict(os.environ, CAMPAIGN_NUMERICAL_CACHE_PIN=raw)
    # Reproduce the original OS failure, with a real process launch, before
    # exercising the replacement protocol on exactly the same inventory.
    with pytest.raises(OSError) as error:
        subprocess.run([sys.executable, '-c', 'pass'], env=legacy, check=True)
    assert error.value.errno == errno.E2BIG
    api = transport()
    environment = api.pin_environment(pin, tmp_path, legacy)
    assert 'CAMPAIGN_NUMERICAL_CACHE_PIN' not in environment
    assert len(json.dumps(environment)) < os.sysconf('SC_ARG_MAX')
    child = "import json,runpy; p=runpy.run_path(%r)['read_pin'](); print(json.dumps({'files':len(p['files']),'sha256':p['sha256']}))" % str(HERE / 'pin_transport.py')
    result = subprocess.run([sys.executable, '-c', child], env=environment, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == dict(files=len(pin['files']), sha256=pin['sha256'])


def test_synthetic_inventory_real_child_uses_small_verified_environment(tmp_path):
    """Default regression uses a large synthetic inventory, no local caches."""
    check_child_transport(inventory_pin(), tmp_path)


@pytest.mark.slow
def test_real_current_cache_inventory_transport_diagnostic(tmp_path):
    pin = real_inventory_pin()
    if len(json.dumps(pin, sort_keys=True)) <= os.sysconf('SC_ARG_MAX'):
        pytest.skip('optional real-cache diagnostic requires a large local inventory')
    check_child_transport(pin, tmp_path)


def test_changed_pin_file_is_rejected_before_use(tmp_path):
    api = transport()
    environment = api.pin_environment({'files': {'vessels/a': 'a'*64}}, tmp_path, {})
    path = Path(environment['CAMPAIGN_NUMERICAL_CACHE_PIN_PATH'])
    path.chmod(0o644)
    path.write_text('{"files":{}}')
    with pytest.raises(ValueError, match='hash|digest|SHA'):
        api.read_pin(environment)


def test_existing_content_addressed_file_is_verified_not_replaced(tmp_path):
    api = transport()
    pin = {'files': {'vessels/a': 'a'*64}}
    environment = api.pin_environment(pin, tmp_path, {})
    path = Path(environment['CAMPAIGN_NUMERICAL_CACHE_PIN_PATH'])
    path.chmod(0o644)
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash|digest|SHA'):
        api.pin_environment(pin, tmp_path, {})
    assert path.read_bytes() == b'corrupt'


def test_legacy_inventory_environment_is_not_accepted():
    with pytest.raises(ValueError, match='legacy|path|file'):
        transport().read_pin({'CAMPAIGN_NUMERICAL_CACHE_PIN': '{}'})


def test_default_transport_regression_runs_on_clean_checkout(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('clean_checkout_pin_test', Path(__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    empty = tmp_path/'empty-checkout'
    empty.mkdir()
    monkeypatch.setattr(module,'ROOT',empty)
    module.test_synthetic_inventory_real_child_uses_small_verified_environment(tmp_path/'run')
