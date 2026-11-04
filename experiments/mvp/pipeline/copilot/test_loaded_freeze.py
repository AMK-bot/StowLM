"""Import/freeze checks only; no provider, native planner or checker calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.copilot.loaded_freeze import CRITICAL_MODULES, validate_loaded_freeze


AGENT = 'experiments.mvp.pipeline.copilot.agent'
STAMP = "IMPORT_SOURCE_SHA256 = __import__('hashlib').sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()\n"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_source(path):
    spec = importlib.util.spec_from_file_location('_offline_source_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_old_loaded_module_rejected_even_when_current_disk_matches_freeze(tmp_path):
    source = tmp_path / 'agent.py'
    source.write_text(STAMP + "VALUE = 'old'\n")
    loaded = load_source(source)
    old = loaded.IMPORT_SOURCE_SHA256
    source.write_text(STAMP + "VALUE = 'new implementation after warm import'\n")
    frozen = {'agent.py': sha(source)}
    assert old != frozen['agent.py'] and loaded.VALUE == 'old'
    with pytest.raises(RuntimeError, match='Loaded code differs'):
        validate_loaded_freeze(frozen, root=tmp_path, modules={AGENT: loaded})


def test_matching_import_source_stamp_returns_audit_receipt(tmp_path):
    source = tmp_path / 'agent.py'
    source.write_text(STAMP + "VALUE = 'current'\n")
    loaded = load_source(source)
    digest = sha(source)
    checked = validate_loaded_freeze({'agent.py': digest}, root=tmp_path, modules={AGENT: loaded})
    assert checked == [{'module': AGENT, 'source_file': 'agent.py',
                        'import_source_sha256': digest, 'frozen_source_sha256': digest}]


@pytest.mark.parametrize('stamp', [None, '', 'invalid', 'a' * 63, 123])
def test_loaded_critical_module_missing_valid_stamp_is_fatal(tmp_path, stamp):
    source = tmp_path / 'agent.py'
    source.write_text('original')
    module = SimpleNamespace(__file__=str(source))
    if stamp is not None:
        module.IMPORT_SOURCE_SHA256 = stamp
    with pytest.raises(RuntimeError, match='no import-time source stamp'):
        validate_loaded_freeze({'agent.py': sha(source)}, root=tmp_path, modules={AGENT: module})


def test_loaded_critical_module_must_be_listed_in_manifest(tmp_path):
    source = tmp_path / 'agent.py'
    source.write_text(STAMP)
    with pytest.raises(RuntimeError, match='missing from the freeze'):
        validate_loaded_freeze({}, root=tmp_path, modules={AGENT: load_source(source)})


def test_matching_digest_from_different_module_path_is_rejected(tmp_path):
    source, other = tmp_path / 'agent.py', tmp_path / 'other.py'
    source.write_text(STAMP)
    other.write_bytes(source.read_bytes())
    with pytest.raises(RuntimeError, match='another source path'):
        validate_loaded_freeze({'agent.py': sha(source)}, root=tmp_path, modules={AGENT: load_source(other)})


def test_disk_change_after_import_and_freeze_is_rejected(tmp_path):
    source = tmp_path / 'agent.py'
    source.write_text(STAMP)
    module = load_source(source)
    frozen = {'agent.py': sha(source)}
    source.write_text(STAMP + 'CHANGED = True\n')
    with pytest.raises(RuntimeError, match='source changed'):
        validate_loaded_freeze(frozen, root=tmp_path, modules={AGENT: module})


def test_unloaded_critical_modules_are_not_imported():
    assert validate_loaded_freeze({}, root=Path('/unused/offline'), modules={}) == []


def test_plain_package_and_warm_launcher_import_do_not_preload_agent_or_authorization():
    program = """
import importlib, sys
package = importlib.import_module('experiments.mvp.pipeline.copilot')
importlib.import_module('experiments.mvp.pipeline.copilot.hero_campaign')
for leaf in ('agent', 'authorization', 'tools', 'hero_run'):
    assert 'experiments.mvp.pipeline.copilot.' + leaf not in sys.modules, leaf
assert 'Copilot' in dir(package)
"""
    subprocess.run([sys.executable, '-c', program], check=True, capture_output=True, text=True)


def test_lazy_public_exports_retain_existing_api():
    program = """
from experiments.mvp.pipeline.copilot import Copilot, ActionGrant, TurnRequest
from experiments.mvp.pipeline.copilot.agent import Copilot as DirectCopilot
from experiments.mvp.pipeline.copilot.authorization import ActionGrant as DirectGrant, TurnRequest as DirectRequest
assert Copilot is DirectCopilot and ActionGrant is DirectGrant and TurnRequest is DirectRequest
"""
    subprocess.run([sys.executable, '-c', program], check=True, capture_output=True, text=True)


def test_hero_freeze_checks_explicit_loaded_registry_even_for_offline_fixture(tmp_path):
    from experiments.mvp.pipeline.copilot import hero_run
    (tmp_path / 'fixtures').mkdir()
    scenario = tmp_path / 'fixtures/hero-scenarios.json'
    scenario.write_text('{}')
    source = tmp_path / 'agent.py'
    source.write_text(STAMP + "VALUE = 'old'\n")
    module = load_source(source)
    source.write_text(STAMP + "VALUE = 'changed after importing'\n")
    marker = {'files': {'agent.py': sha(source)}, 'scenarios_sha256': sha(scenario)}
    # Historical temp-root disk-only tests remain possible without pretending
    # the real modules came from that synthetic directory.
    hero_run.validate_freeze(marker, root=tmp_path)
    with pytest.raises(RuntimeError, match='Loaded code differs'):
        hero_run.validate_freeze(marker, root=tmp_path, loaded_modules={AGENT: module})


def test_actual_critical_sources_define_stamp_at_import_time():
    base = Path(__file__).parent
    for filename in CRITICAL_MODULES.values():
        text = (base / filename).read_text()
        assert text.index('IMPORT_SOURCE_SHA256 =') > text.index('from __future__ import')
