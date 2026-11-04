"""Gold campaign retries retain invocation and publication integrity."""
import importlib.util
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def gold(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "experiments/gold-plans/campaign/gold.py"
    spec = importlib.util.spec_from_file_location("gold_p1_integrity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "freeze_check", lambda _: {})
    monkeypatch.setattr(module.machinery(), "runner_check", lambda *_: None)
    return module


def setup_case(tmp_path):
    row = dict(rung="tiny", sha256="input", group="random_yard/tiny",
        shard=dict(spec_key="key", split="train"))
    identity = dict(epoch_id="e", split_entries_sha256="v2", instance_manifest_sha256="instances", g1_summary=None)
    args = SimpleNamespace(timeout_s=2, scalable_budgets={"tiny": 1}, exact_budgets={"tiny": 1},
        ballast_s=1, check_s=1, split_manifest=tmp_path / "split.json", diagnostic=False,
        seed=0, max_nodes=10)
    return row, identity, args


def test_controller_checkpoint_cannot_repin_replacement_output(gold, tmp_path, monkeypatch):
    row, identity, args = setup_case(tmp_path)
    target = tmp_path / 'shards/key/result.json'
    atomic = gold.atomic_json
    class Child:
        pid = 12345
        def __init__(self, environment):
            atomic(target, dict(accepted=False, status='timeout', rung='tiny',
                attempt_id=environment['CAMPAIGN_ATTEMPT_ID']))
        def wait(self, timeout=None):
            return 0
    def replace_after_write(path, value):
        digest = atomic(path, value)
        if Path(path) == target and 'worker_returncode' in value:
            atomic(path, dict(value, accepted=True, diagnostic=True, evidence_eligible=False))
        return digest
    monkeypatch.setattr(gold, 'atomic_json', replace_after_write)
    monkeypatch.setattr(gold.subprocess, 'Popen', lambda *_, **kwargs: Child(kwargs['env']))
    with pytest.raises(ValueError, match='checksum'):
        gold.run_shard(args, identity, row, tmp_path)


def test_gold_retry_quarantines_stale_result_and_publication(gold, tmp_path, monkeypatch):
    row, identity, args = setup_case(tmp_path)
    artifact = tmp_path / "plans/key.json"
    gold.atomic_json(artifact, dict(accepted=True, plan="stale"))
    gold.atomic_json(tmp_path / "state/key.json", dict(accepted=True, gold_file=str(artifact)))
    gold.atomic_json(tmp_path / "shards/key/result.json", dict(accepted=True, status="feasible",
        gold_file=str(artifact), gold_sha256=gold.sha(artifact), rung="tiny"))
    class Child:
        pid = 12345
        def wait(self, timeout=None):
            return 0  # a child exiting without a result cannot adopt an older result
    monkeypatch.setattr(gold.subprocess, "Popen", lambda *_, **__: Child())
    result = gold.run_shard(args, identity, row, tmp_path)
    assert not result["accepted"]
    assert not result["evidence_eligible"]
    assert not artifact.exists()
    assert list((tmp_path / "shards/key/quarantine").rglob("*key.json"))


def test_production_gold_refuses_diagnostic_child_lineage(gold, tmp_path, monkeypatch):
    row, identity, args = setup_case(tmp_path)
    class Child:
        pid = 12345
        def __init__(self, environment):
            gold.atomic_json(tmp_path / "shards/key/result.json", dict(accepted=True,
                attempt_id=environment['CAMPAIGN_ATTEMPT_ID'],
                status="feasible", rung="tiny", diagnostic=True, evidence_eligible=False))
        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(gold.subprocess, "Popen", lambda *_, **kwargs: Child(kwargs['env']))
    result = gold.run_shard(args, identity, row, tmp_path)
    assert not result["accepted"]
    assert not result["evidence_eligible"]


@pytest.mark.parametrize("mutation", ["delete", "corrupt"])
def test_gold_checkpoint_inventory_pins_published_artifact(gold, tmp_path, monkeypatch, mutation):
    row, identity, args = setup_case(tmp_path)
    artifact = tmp_path / "plans/key.json"
    class Child:
        pid = 12345
        def __init__(self, environment):
            gold.atomic_json(artifact, dict(accepted=True, plan="synthetic"))
            gold.atomic_json(tmp_path / "shards/key/result.json", dict(accepted=True,
                attempt_id=environment['CAMPAIGN_ATTEMPT_ID'],
                status="feasible", rung="tiny", gold_file=str(artifact), gold_sha256=gold.sha(artifact)))
        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(gold.subprocess, "Popen", lambda *_, **kwargs: Child(kwargs['env']))
    gold.run_shard(args, identity, row, tmp_path)
    marker = tmp_path / "shards/key/done.json"
    shard_identity = dict(campaign=identity, input=row)
    assert gold.machinery().valid_done(marker, shard_identity)
    checkpoint = json.loads(marker.read_text())
    assert any(item["path"] == str(artifact) for item in checkpoint["artifacts"])
    if mutation == "delete":
        artifact.unlink()
    else:
        artifact.write_text("corrupt")
    assert not gold.machinery().valid_done(marker, shard_identity)
    with pytest.raises(ValueError, match="done marker|artifact"):
        gold.run_shard(args, identity, row, tmp_path)


def test_spawned_gold_worker_installs_pinned_cache_fence(monkeypatch, tmp_path):
    """Exercise spawn's module entry without starting a solver or a process."""
    path = Path(__file__).resolve().parents[2] / "experiments/gold-plans/campaign/gold.py"
    events = []
    adapter = SimpleNamespace(redirect_cache=lambda root: events.append(('redirect', root)),
        enforce_scope=lambda: events.append(('fence', None)))
    monkeypatch.setitem(sys.modules, '_campaign_gold_cache', adapter)
    cache = tmp_path / 'cache-work'
    monkeypatch.setenv('CAMPAIGN_CACHE_ROOT', str(cache))
    monkeypatch.setenv('CAMPAIGN_NUMERICAL_CACHE_PIN', '{"snapshot_sha256":"frozen"}')
    runpy.run_path(str(path), run_name='__mp_main__')
    assert events == [('redirect', cache), ('fence', None)]
    assert adapter.HERE == path.parent
    assert adapter.ROOT == path.parents[3]


def test_gold_parent_cache_initializer_exports_root(gold, tmp_path, monkeypatch):
    events = []
    adapter = SimpleNamespace(redirect_cache=lambda root: events.append(('redirect', root)),
        enforce_scope=lambda: events.append(('fence', None)))
    monkeypatch.setitem(sys.modules, '_campaign_gold_cache', adapter)
    monkeypatch.delenv('CAMPAIGN_CACHE_ROOT', raising=False)
    cache = tmp_path / 'cache-work'
    gold.cache_fence(cache)
    assert gold.os.environ['CAMPAIGN_CACHE_ROOT'] == str(cache)
    assert events == [('redirect', cache), ('fence', None)]
