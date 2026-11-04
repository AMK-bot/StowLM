"""CPU regressions for content identities and adapter training lineage; no MLX/Metal."""

import hashlib
import json
import os
import sys
from types import SimpleNamespace

import pytest

from llm.stowlm.planner import StowLMPlanner
from stowage.actions.samples import tiny_instance
from stowage.frozen import StubTaintError, require_clean
from stowage.model import MovePlan, Provenance


@pytest.fixture(autouse=True)
def cpu_mlx_memory(monkeypatch):
    """Keep provenance tests on CPU while preserving the real identity checks."""
    from llm.stowlm import mlx_memory

    monkeypatch.setattr(mlx_memory, "apply_cache_limit", lambda default_gb=2.0: 2.0)
    monkeypatch.setattr(mlx_memory, "memory_gb", lambda: {})


def artifacts(tmp_path, *, stubs=None):
    model, adapter = tmp_path / "model", tmp_path / "adapter"
    model.mkdir()
    adapter.mkdir()
    (model / "model.safetensors").write_bytes(b"base weights")
    (adapter / "adapters.safetensors").write_bytes(b"adapter weights")
    (adapter / "adapter_config.json").write_text('{}')
    provenance = Provenance(stubs=stubs or {}, created_by="stowlm.train", parents=["training-data-sha256:fixture"])
    metadata = {"schema_version": 1,
                "weights_sha256": hashlib.sha256(b"adapter weights").hexdigest(),
                "provenance": provenance.model_dump(mode="json")}
    (adapter / "adapters.safetensors.provenance.json").write_text(json.dumps(metadata))
    return model, adapter


@pytest.mark.parametrize("component", ["model", "adapter"])
def test_evaluation_identity_changes_when_same_path_weights_replaced(tmp_path, component):
    model, adapter = artifacts(tmp_path)
    planner = StowLMPlanner(model_path=model, adapter_path=adapter)
    old = planner.evaluation_config
    weights = model / "model.safetensors" if component == "model" else adapter / "adapters.safetensors"
    stat = weights.stat()
    weights.write_bytes(b"x" * stat.st_size)
    os.utime(weights, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    # An adapter with stale provenance must fail closed, independently of the
    # evaluator's separate full-directory content identity.
    if component == "adapter":
        with pytest.raises(ValueError, match="provenance|digest"):
            _ = planner.evaluation_config
        sidecar = adapter / "adapters.safetensors.provenance.json"
        metadata = json.loads(sidecar.read_text())
        metadata["weights_sha256"] = hashlib.sha256(weights.read_bytes()).hexdigest()
        sidecar.write_text(json.dumps(metadata))
        assert old["adapter_content_sha256"] != planner.evaluation_config["adapter_content_sha256"]
    else:
        assert old["model_content_sha256"] != planner.evaluation_config["model_content_sha256"]


@pytest.mark.parametrize("damage", ["missing", "legacy_run_only", "stale_digest", "missing_stubs", "schema"])
def test_untrusted_adapter_metadata_fails_closed_before_mlx(tmp_path, monkeypatch, damage):
    model, adapter = artifacts(tmp_path)
    sidecar = adapter / "adapters.safetensors.provenance.json"
    metadata = json.loads(sidecar.read_text())
    if damage in ("missing", "legacy_run_only"):
        sidecar.unlink()
        if damage == "legacy_run_only":
            (adapter / "run.json").write_text(json.dumps({"stubs": {}}))
    else:
        if damage == "stale_digest":
            metadata["weights_sha256"] = "0" * 64
        elif damage == "missing_stubs":
            del metadata["provenance"]["stubs"]
        else:
            metadata["schema_version"] = 999
        sidecar.write_text(json.dumps(metadata))
    loaded = []
    monkeypatch.setitem(sys.modules, "mlx_lm", SimpleNamespace(load=lambda *a, **k: loaded.append(True)))
    planner = StowLMPlanner(model_path=model, adapter_path=adapter)
    with pytest.raises(ValueError, match="provenance"):
        planner._load()
    assert not loaded
    with pytest.raises(StubTaintError):
        require_clean(planner.provenance, what="official evaluation planner")


def mock_generation(monkeypatch, instance):
    import llm.stowlm.planner as module
    import llm.stowlm.mask as mask
    import stowage.feasibility as feasibility
    import stowage.moves as moves

    plan = MovePlan(id="candidate", instance_id=instance.id, call_index=instance.call_index)
    engine = SimpleNamespace(to_plan=lambda: plan)
    monkeypatch.setattr(feasibility, "make_feasibility_engine", lambda *a, **k: engine)
    monkeypatch.setattr(mask, "MaskedDecoder", lambda *a: SimpleNamespace(engine=engine, actions=[]))
    monkeypatch.setattr(module, "load_tokenizer", lambda: object())
    monkeypatch.setattr(module, "codec_for", lambda *a: object())
    monkeypatch.setattr(module, "encode_prompt", lambda *a: [1])
    monkeypatch.setattr(moves, "count_moves", lambda *a: SimpleNamespace(weighted_nonproductive=0))
    # A future candidate feasibility selector may invoke the pipeline. Preserve
    # this deliberately synthetic test oracle's candidate unchanged.
    import stowage.planner as pipeline
    monkeypatch.setattr(pipeline, "finalize", lambda instance, plan, **kw: plan)
    sequence = SimpleNamespace(status="complete", tokens=[1], logprob=0.0)
    stats = SimpleNamespace(summary=lambda: {"host_mask_s": 0.0, "wall_s": 1.0})
    monkeypatch.setitem(sys.modules, "llm.stowlm.mask_mlx", SimpleNamespace(
        generate_masked=lambda *a, **k: ([sequence], stats)))
    monkeypatch.setitem(sys.modules, "mlx_lm", SimpleNamespace(load=lambda *a, **k: (object(), None)))


def test_tainted_adapter_provenance_reaches_plan_result_and_eval_gate(tmp_path, monkeypatch):
    from eval.harness import run_evaluation

    model, adapter = artifacts(tmp_path, stubs={"checker.check": "tiny-chain training oracle"})
    instance = tiny_instance(0, n_load=0)
    mock_generation(monkeypatch, instance)
    planner = StowLMPlanner(model_path=model, adapter_path=adapter)
    result = planner.plan(instance)
    assert result.plan is not None, result.message
    for artifact in (planner, result, result.plan):
        assert artifact.provenance.stubs["checker.check"] == "tiny-chain training oracle"
        assert "training-data-sha256:fixture" in artifact.provenance.parents
        with pytest.raises(StubTaintError):
            require_clean(artifact, what="official evaluation")
    with pytest.raises(StubTaintError, match="official evaluation planner"):
        run_evaluation(planner, run_dir=tmp_path / "evaluation")


def test_clean_adapter_retains_training_lineage(tmp_path):
    model, adapter = artifacts(tmp_path)
    planner = StowLMPlanner(model_path=model, adapter_path=adapter)
    require_clean(planner.provenance, what="official evaluation")
    assert "training-data-sha256:fixture" in planner.provenance.parents


def test_loaded_model_cannot_be_relabeled_after_disk_replacement(tmp_path, monkeypatch):
    model, adapter = artifacts(tmp_path)
    monkeypatch.setitem(sys.modules, "mlx_lm", SimpleNamespace(load=lambda *a, **k: (object(), None)))
    planner = StowLMPlanner(model_path=model, adapter_path=adapter)
    planner._load()
    (model / "model.safetensors").write_bytes(b"new base weights")
    with pytest.raises(ValueError, match="reload|changed"):
        _ = planner.evaluation_config


def test_replacement_metadata_cannot_erase_loaded_adapter_taint(tmp_path, monkeypatch):
    model, adapter = artifacts(tmp_path, stubs={"checker.check": "tainted original adapter"})
    monkeypatch.setitem(sys.modules, "mlx_lm", SimpleNamespace(load=lambda *a, **k: (object(), None)))
    planner = StowLMPlanner(model_path=model, adapter_path=adapter)
    planner._load()
    sidecar = adapter / "adapters.safetensors.provenance.json"
    metadata = json.loads(sidecar.read_text())
    metadata["provenance"]["stubs"] = {}
    sidecar.write_text(json.dumps(metadata))
    assert planner.provenance.stubs["checker.check"] == "tainted original adapter"
    with pytest.raises(ValueError, match="changed|reload"):
        planner._load()


def test_adapter_runtime_error_result_retains_training_taint(tmp_path, monkeypatch):
    model, adapter = artifacts(tmp_path, stubs={"checker.check": "training stub"})
    instance = tiny_instance(0, n_load=0)
    mock_generation(monkeypatch, instance)
    monkeypatch.setitem(sys.modules, "mlx_lm", SimpleNamespace(
        load=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic loading failure"))))
    result = StowLMPlanner(model_path=model, adapter_path=adapter).plan(instance)
    assert result.status == "error" and "synthetic loading failure" in result.message
    assert result.provenance.stubs["checker.check"] == "training stub"


def test_result_pins_current_catalogue_content(tmp_path, monkeypatch):
    from stowage.frozen import current_provenance

    model, adapter = artifacts(tmp_path)
    instance = tiny_instance(0, n_load=0)
    mock_generation(monkeypatch, instance)
    result = StowLMPlanner(model_path=model, adapter_path=adapter).plan(instance)
    catalogue = current_provenance().versions["rule_catalogue"]
    assert catalogue.startswith("sha256:") and len(catalogue) == len("sha256:") + 64
    assert result.provenance.versions["rule_catalogue"] == catalogue
    assert result.plan.provenance.versions["rule_catalogue"] == catalogue


def test_missing_adapter_metadata_returns_tainted_error(tmp_path, monkeypatch):
    model, adapter = artifacts(tmp_path)
    (adapter / "adapters.safetensors.provenance.json").unlink()
    instance = tiny_instance(0, n_load=0)
    mock_generation(monkeypatch, instance)
    result = StowLMPlanner(model_path=model, adapter_path=adapter).plan(instance)
    assert result.status == "error" and "untrusted adapter provenance" in result.message
    with pytest.raises(StubTaintError):
        require_clean(result, what="official evaluation")
