"""CPU-only publication/orchestration contracts; no physical gold claim."""
import importlib.util
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from eval.splits import load_inventory, spec_key


@pytest.fixture
def pipeline(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "experiments/sft-data/build.py"
    spec = importlib.util.spec_from_file_location("sft_pipeline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "current_gold_epoch", lambda: "synthetic-epoch")
    return module


@pytest.fixture
def published(tmp_path):
    inventory = load_inventory()
    member = next(e for e in inventory.manifest["entries"] if e["split"] == "train")
    pin = {"split_version": inventory.version, "split_entries_sha256": inventory.entries_sha256}
    record = {"input_relative_path": "input.json", "split_spec": member["spec"], "plan": {},
              "spec_key": spec_key(member["spec"]), "instance_id": "synthetic", "split": "train",
              "input_sha256": sha256(b"synthetic input").hexdigest(), **pin}
    gold = tmp_path / "gold"
    gold.mkdir()
    raw = json.dumps(record).encode()
    (gold / "record.json").write_bytes(raw)
    entry = {"path": "record.json", "sha256": sha256(raw).hexdigest(),
             **{k: record[k] for k in ("spec_key", "instance_id", "split", "input_sha256")}, **pin}
    manifest = {"format": "tiny-gold-manifest@1.0", "accepted": 1, "shards": [entry],
                "c2": "on/default", "relevant_source_sha256": "synthetic-epoch", **pin}
    path = gold / "manifest.json"
    path.write_text(json.dumps(manifest))
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "input.json").write_bytes(b"synthetic input")
    return path, inputs, manifest, record


def test_pipeline_refuses_empty_campaign_without_publishing(pipeline, published, tmp_path):
    path, inputs, manifest, _ = published
    manifest.update(accepted=0, shards=[])
    path.write_text(json.dumps(manifest))
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="no published gold"):
        pipeline.build_dataset(path, inputs, out)
    assert not out.exists()


@pytest.mark.parametrize("field,value", [("spec_key", "nonmember"), ("split", "val")])
def test_pipeline_checks_membership_before_record_read(pipeline, published, tmp_path, field, value):
    path, inputs, manifest, _ = published
    manifest["shards"][0][field] = value
    path.write_text(json.dumps(manifest))
    (path.parent / "record.json").unlink()
    with pytest.raises(ValueError, match="inventory"):
        pipeline.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",))


def test_pipeline_refuses_record_hash_mismatch(pipeline, published, tmp_path):
    path, inputs, _, _ = published
    (path.parent / "record.json").write_text("{}")
    with pytest.raises(ValueError, match="SHA256"):
        pipeline.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",))


def test_pipeline_refuses_stale_source_epoch(pipeline, published, tmp_path):
    path, inputs, manifest, _ = published
    manifest["relevant_source_sha256"] = "stale"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="source epoch"):
        pipeline.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",))


def test_pipeline_refuses_input_hash_mismatch(pipeline, published, tmp_path):
    path, inputs, _, _ = published
    (inputs / "input.json").write_text("changed")
    with pytest.raises(ValueError, match="input SHA256"):
        pipeline.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",))


@pytest.mark.parametrize("relative", ["../outside.json", "/tmp/outside.json"])
def test_pipeline_refuses_publication_path_escape(pipeline, published, tmp_path, relative):
    path, inputs, manifest, _ = published
    manifest["shards"][0]["path"] = relative
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="path"):
        pipeline.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",))


def test_historical_builder_cannot_claim_clean_gold(tmp_path):
    script = Path(__file__).resolve().parents[2] / "experiments/tiny-chain/build_sft.py"
    result = subprocess.run([sys.executable, str(script), "--no-stub", "--out", str(tmp_path / "out")],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "diagnostic only" in result.stderr and "experiments/sft-data/build.py" in result.stderr
    assert not (tmp_path / "out").exists()


def test_pipeline_requires_all_requested_splits_before_admission(pipeline, published, tmp_path):
    path, inputs, _, _ = published
    with pytest.raises(ValueError, match="missing required splits"):
        pipeline.build_dataset(path, inputs, tmp_path / "out")


def test_pipeline_binds_publication_and_uses_production_writer(pipeline, published, tmp_path, monkeypatch):
    path, inputs, manifest, record = published
    seen = {}
    sentinel = object()
    monkeypatch.setattr(pipeline, "load_instance", lambda path: "synthetic instance")
    monkeypatch.setattr(pipeline, "parse_plan", lambda raw: "synthetic plan")
    def admit(instance, plan, **kw):
        seen.update(kw)
        return sentinel
    def write(examples, out, **kw):
        assert examples == [sentinel]
        assert kw == {"production": False, "required_splits": ("train",)}
        return {"synthetic_orchestration_only": True}
    monkeypatch.setattr(pipeline, "admit_gold", admit)
    monkeypatch.setattr(pipeline, "write_dataset", write)
    result = pipeline.build_dataset(path, inputs, tmp_path / "out", required_splits=("train",), tokenizer=object())
    assert result["synthetic_orchestration_only"]
    assert seen["publication"]["entry"] == manifest["shards"][0]
    assert seen["publication"]["record_sha256"] == manifest["shards"][0]["sha256"]
    assert json.loads(seen["publication"]["record_bytes"]) == record
    assert seen["gold_record"] == record


def test_tiny_exception_dataset_is_pipeline_test_without_epoch_g1(pipeline, published, tmp_path, monkeypatch):
    path, inputs, manifest, _ = published
    manifest.update(data_mode='pipeline_test', tiny_g1_exception=True, epoch_id='tiny-epoch')
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(pipeline, 'load_instance', lambda _: 'synthetic instance')
    monkeypatch.setattr(pipeline, 'parse_plan', lambda _: 'synthetic plan')
    monkeypatch.setattr(pipeline, 'admit_gold', lambda *a, **kw: object())
    seen = {}
    def write(examples, out, **kwargs):
        seen.update(kwargs)
        return {}
    monkeypatch.setattr(pipeline, 'write_dataset', write)
    pipeline.build_dataset(path, inputs, tmp_path/'out', required_splits=('train',))
    assert seen['production'] is False, 'tiny G1 exception cannot become production_gold'


@pytest.mark.parametrize('damage', ['foreign_epoch', 'missing_yield', 'missing_rules', None])
def test_tiny_promotion_requires_same_epoch_approved_yield_and_rule_pin(
        pipeline, published, tmp_path, monkeypatch, damage):
    path, inputs, manifest, record = published
    policy = pipeline.sft_data.gold_rule_policy()
    # This unit tests tiny promotion/yield, not the separate migration inventory.
    monkeypatch.setattr(policy,'validate_migration_approval',lambda *a:None)
    monkeypatch.setattr(pipeline.sft_data,'gold_rule_policy',lambda:policy)
    components=policy._module('migration_evidence').current_component_epoch()
    manifest['component_epoch']=record['component_epoch']=components
    pin = policy.enforce_rule_policy(Path(__file__).resolve().parents[2])
    rung = record['split_spec']['rung']
    epoch_id = 'synthetic-approved-epoch'
    approval = dict(epoch_id=epoch_id, g1_met=True, min_yield={rung:0.5}, solve_yield={rung:1.0})
    if damage=='foreign_epoch':
        approval['epoch_id'] = 'other-epoch'
    elif damage=='missing_yield':
        approval.pop('min_yield')
    manifest.update(data_mode='pipeline_test', tiny_g1_exception=True, epoch_id=epoch_id,
                    required_rules=pin if damage!='missing_rules' else None)
    record['epoch_id'] = epoch_id
    raw = json.dumps(record).encode()
    (path.parent/'record.json').write_bytes(raw)
    manifest['shards'][0]['sha256'] = sha256(raw).hexdigest()
    path.write_text(json.dumps(manifest))
    epoch = dict(epoch_id=epoch_id, freeze_manifest={'required_rules':pin},
        bindings={record['spec_key']:dict(instance='input.json', split='train', sha256=record['input_sha256'])})
    (inputs/'manifest.json').write_text(json.dumps(epoch))
    monkeypatch.setattr(pipeline, '_epoch', lambda *a, **kw: epoch)
    monkeypatch.setattr(pipeline, 'validate_epoch_instance', lambda *a: 'synthetic instance')
    monkeypatch.setattr(pipeline, 'parse_plan', lambda _: 'synthetic plan')
    monkeypatch.setattr(pipeline, 'admit_gold', lambda *a, **kw: object())
    calls=[]
    monkeypatch.setattr(pipeline, 'write_dataset', lambda *a, **kw: calls.append(kw) or {})
    g1=tmp_path/'leader-g1.json'
    g1.write_text(json.dumps(approval))
    if damage:
        with pytest.raises(ValueError, match='epoch|min_yield|required_rules'):
            pipeline.build_dataset(path, inputs, tmp_path/'out', required_splits=('train',),
                                   g1_summary=g1, production=True)
        assert not calls
    else:
        pipeline.build_dataset(path, inputs, tmp_path/'out', required_splits=('train',),
                               g1_summary=g1, production=True)
        assert calls==[dict(production=True, required_splits=('train',))]


def test_synthetic_all_split_pipeline_statistics_and_curriculum(pipeline, tmp_path, monkeypatch):
    """Real token-mask replay over a synthetic action engine, never physical gold."""
    from tests.llm.test_sft_data_admission import synthetic_publication
    from llm.stowlm import sft_data
    import stowage.feasibility
    monkeypatch.setattr(pipeline, "current_gold_epoch", lambda: "a" * 64)
    monkeypatch.setattr(sft_data, "current_gold_source_sha256", lambda: "a" * 64)
    permitted = {}
    class SyntheticActionEngine:
        # Unit-only engine: action legality and C2 label are authored test data,
        # not physical qualification. The real verify_replay/MaskedDecoder runs.
        diagnostics = {"global_screen": "on"}
        def __init__(self, actions, depth=0):
            self.permitted = actions
            self.depth = depth
        def is_complete(self):
            return self.depth == len(self.permitted)
        def legal_actions(self):
            return self.permitted[self.depth:self.depth + 1]
        def copy(self):
            return SyntheticActionEngine(self.permitted, self.depth)
        def push(self, action):
            assert action in self.legal_actions()
            self.depth += 1
        def actions(self):
            return self.permitted[:self.depth]
    monkeypatch.setattr(stowage.feasibility, "make_feasibility_engine",
                        lambda instance, space: SyntheticActionEngine(permitted[instance.id]))
    gold = tmp_path / "synthetic-gold"
    inputs = tmp_path / "synthetic-inputs"
    out = tmp_path / "synthetic-output"
    gold.mkdir()
    inputs.mkdir()
    manifest = None
    entries = []
    for split, n_boxes, member_index in (("train", 3, 0), ("val", 2, 0), ("test", 1, 0), ("train", 1, 1)):
        instance, plan, record, publish = synthetic_publication(split, n_boxes, member_index=member_index)
        permitted[instance.id] = sft_data.plan_actions(instance, plan)
        record["input_relative_path"] = f"{split}{member_index}.json"
        publication = publish()
        entry = publication["entry"]
        target = gold / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(publication["record_bytes"])
        (inputs / record["input_relative_path"]).write_bytes(publication["input_bytes"])
        entries.append(entry)
        manifest = {k: v for k, v in publication.items() if k not in (
            "entry", "record_bytes", "input_bytes", "record_sha256")}
    manifest.update(shards=entries, accepted=len(entries), c2="on/default")
    path = gold / "manifest.json"
    path.write_text(json.dumps(manifest))
    stats = pipeline.build_dataset(path, inputs, out)
    assert set(stats["splits"]) == {"train", "val", "test"}
    assert [stats["splits"][split]["examples"] for split in ("train", "val", "test")] == [2, 1, 1]
    assert stats["replay"]["production_verified"] == 4
    assert stats["replay"]["verified"] == stats["replay"]["examples"] == 4
    assert stats["splits"]["train"]["n_actions"]["max"] == 3
    assert stats["splits"]["test"]["n_actions"]["max"] == 1
    assert [row.n_boxes for row in sft_data.read_dataset(out / "train.jsonl")] == [1, 3]
