"""Current-tokenizer sampled budgets; observations do not certify stowage safety."""
import importlib.util
import hashlib
import json
import sys

import pytest

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.format_prompt import encode_prompt, parse_prompt, instance_to_prompt
from stowage.actions.space import BoxSlotSpace, Relocate
from stowage.conventions import canonical_vessel_id
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1


@pytest.fixture(autouse=True)
def archived_sizing_regime():
    """Archived move-replay evidence is legacy; v3 has separate length tests."""
    with rule_freeze_v1(enabled=False):
        yield


@pytest.fixture(scope="module")
def sizing(repo_root):
    directory = repo_root / "experiments/format"
    # These scripts are also executable directly from their own directory.
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location("format_current", directory / "current.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(directory))


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


def test_current_sizing_evidence_covers_ladder_dense_and_overrides(repo_root):
    # Keep regression evidence beside its prompt tests. The experiment's
    # published copy is refreshed independently by its owner after a version bump.
    path = repo_root / "tests/llm/test_format_measurements.json"
    assert path.exists(), "Current prompt/output evidence must cover every ladder rung and overrides"
    report = json.loads(path.read_text())
    assert report["prompt_source_sha256"] == hashlib.sha256(
        (repo_root / "src/llm/stowlm/format_prompt.py").read_bytes()).hexdigest()
    assert report["sizing_source_sha256"] == hashlib.sha256(
        (repo_root / "experiments/format/current.py").read_bytes()).hexdigest()
    assert report["tokenizer_sha256"] == hashlib.sha256(
        (repo_root / "models/glimmer-30b-4bit/tokenizer.json").read_bytes()).hexdigest()
    assert report["cpu_cores"] == 1 and report["gpu"] is False
    assert report["sampling_limits"]
    rows = report["rows"]
    assert {canonical_vessel_id(r["profile"]) for r in rows if r.get("generator") == "random_yard"} >= {
        "tiny", "small", "medium", "full", "realistic_500", "realistic_800"}
    for generator in ("random_yard", "simulated_yard"):
        assert {canonical_vessel_id(r["profile"]) for r in rows if r.get("generator") == generator} >= {
            "realistic_500", "realistic_800"}
    for failure in report["failed_measurements"]:
        assert failure["output_tokens"] is None
        assert failure["total_sft_tokens"] is None
        assert failure["error"]
    assert any(r.get("noise") == 0.8 for r in rows)
    assert any(r["explicit_relocations"] > 0 and r["stage"] == "B" for r in rows)
    for row in rows:
        assert row["context_fit"]
        assert row["total_sft_tokens"] == row["prompt_tokens"] + row["output_tokens"]
        assert row["full_checker_certified"] is False
        assert row["move_engine_legal"]


@pytest.mark.parametrize("generator", ["random_yard", "simulated_yard"])
@pytest.mark.parametrize("profile", ["realistic_500", "realistic_800"])
def test_nonzero_noise_dense_prompt_output_and_total_budget(sizing, tokenizer, generator, profile):
    # This source-pinned historical sizing recipe reads its original manifest
    # path; canonicalize only the selection, preserving archived bytes and pins.
    manifest = json.loads((sizing.ROOT / "experiments/instances-v2/manifest.json").read_text())
    stored_profile = next(key.split("/", 1)[1] for key in manifest["groups"]
                          if key.split("/", 1)[0] == generator
                          and canonical_vessel_id(key.split("/", 1)[1]) == profile)
    instance, metadata = sizing.load_profile(stored_profile, generator, noise=0.8)
    assert metadata["noise"] == 0.8
    row = sizing.measure_instance(instance, tokenizer, metadata)
    assert row["prompt_budget_fit"]
    assert row["output_budget_fit"]
    assert row["context_fit"]
    assert row["move_engine_legal"]


def test_heterogeneous_layover_and_relocation_sequence_budget(sizing, tokenizer):
    instance, actions = sizing.heterogeneous_layover()
    facts = parse_prompt(instance_to_prompt(instance))
    assert facts["stage"] == "B"
    assert facts["constraints"]["instructions"]
    flags = [row[-1] for row in facts["cargo"]]
    assert any(f.get("dg") for f in flags)
    assert any(f.get("reefer") for f in flags)
    assert any(f.get("oog") for f in flags)
    assert any(isinstance(a, Relocate) for a in actions)
    codec = ActionCodec(tokenizer, [c.id for c in instance.containers])
    output = codec.encode_plan(actions)
    assert codec.decode_plan(output) == actions
    plan = BoxSlotSpace().expand(instance, actions)
    replay = apply_plan(instance, plan)
    assert replay.legal, replay.violations
    assert any(m.kind == "relocate" for m in plan.moves)
    assert any(m.kind == "discharge" for m in plan.moves)
    assert any(m.kind == "restow_on" for m in plan.moves)
    prompt = encode_prompt(instance, tokenizer)
    assert len(prompt) <= 2000 + 12 * len(instance.load_list)
    # D18's 5% override allowance is an expectation, not a bound on tiny layovers.
    # Account for the actual discharge/restow actions and exact explicit override.
    regular = [a for a in actions if not isinstance(a, Relocate)]
    explicit = sum(len(codec.encode_action(a)) for a in actions if isinstance(a, Relocate))
    assert len(output) <= 8 * len(regular) + explicit + len(codec.end_plan_tokens)
    assert len(prompt) + len(output) <= 131072
