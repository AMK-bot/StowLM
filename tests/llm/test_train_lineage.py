"""8.1 review fixes, CPU only (no MLX import): data admission, weight-bound adapter lineage, checkpoint discovery,
resume compatibility and the deterministic batch order. GPU trajectory tests are in test_train_resume.py."""

import itertools
import json
from dataclasses import replace
from pathlib import Path

import pytest

from llm.stowlm import train as T
from llm.stowlm.format import FORMAT_VERSION
from llm.stowlm.format_prompt import PROMPT_VERSION
from llm.stowlm.mask import MASK_VERSION
from llm.stowlm.planner import StowLMPlanner
from llm.stowlm.sft_data import DATASET_VERSION, SFTExample, example_sha256, write_dataset
from stowage.frozen import StubTaintError, require_clean
from stowage.model import Provenance


@pytest.fixture(autouse=True)
def isolate_future_migration_inventory(monkeypatch):
    # Forged unit rows exercise trainer lineage/content checks. The independent
    # migration gate is covered without this mock in test_migration_lineage.
    from llm.stowlm import sft_data
    original=sft_data.gold_rule_policy
    def policy():
        value=original()
        monkeypatch.setattr(value,'validate_migration_approval',lambda *a:None)
        return value
    monkeypatch.setattr(sft_data,'gold_rule_policy',policy)


def ex(n_prompt, n_completion, split="train", stubs=None, i=0):
    return SFTExample(instance_id=f"i{i}-{n_prompt}-{n_completion}-{split}", split=split,
                      input_ids=list(range(1, n_prompt + n_completion + 1)), prompt_len=n_prompt,
                      n_actions=1, n_boxes=1, plan_id="p", stubs=stubs or {})


def gold_rows(prompt=None, action_space="default"):
    """Rows that satisfy 7.1's production-gold admission fields (a forged fixture: tests the trainer's re-check).
    ``prompt`` defaults to the active prompt version (2.2 with the rule-freeze switch off, 3.0 with it on)."""
    from llm.stowlm.format_prompt import active_prompt_version
    from stowage.actions import ACTION_SPACE_VERSION

    from eval.splits import load_inventory

    inv = load_inventory()
    pins = {"split_version": inv.version, "split_entries_sha256": inv.entries_sha256}
    from llm.stowlm.sft_data import gold_rule_policy
    policy=gold_rule_policy()
    required_rules = policy.enforce_rule_policy(Path(__file__).resolve().parents[2])
    components=policy._module('migration_evidence').current_component_epoch()
    approval = dict(epoch_id='synthetic-lineage-epoch', g1_met=True,
                    min_yield={'tiny':0.5}, solve_yield={'tiny':1.0})
    rows = []
    for i, split in enumerate(["train", "train", "val"]):
        e = ex(5, 3 + i, split, i=i)
        e.meta = {"format": FORMAT_VERSION, "prompt": prompt or active_prompt_version(), "dataset": DATASET_VERSION,
                  **({} if action_space is None else
                     {"action_space": ACTION_SPACE_VERSION if action_space == "default" else action_space})}
        digest = example_sha256(e)
        e.meta["gold"] = {"published": True, "example_sha256": digest, 'data_mode':'production',
            'epoch_id':approval['epoch_id'], 'rung':'tiny', 'required_rules':required_rules,
            'g1_approval':approval, 'component_epoch':components, **pins}
        e.meta["replay"] = {"ok": True, "production": True, "mask_version": MASK_VERSION, "stubs": {},
                            "example_sha256": digest}
        rows.append(e)
    return rows, pins


def write_rows(rows, d: Path, stats_extra):
    d.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val"):
        (d / f"{split}.jsonl").write_text("".join(
            json.dumps(r.__dict__) + "\n" for r in rows if r.split == split))
    (d / "stats.json").write_text(json.dumps({"dataset": DATASET_VERSION, **stats_extra}))


def cfg(tmp_path, data, adapter, **kw):
    return T.TrainConfig(data_dir=str(data), adapter_dir=str(adapter), **kw)


def test_verified_production_gold_is_admitted_clean(tmp_path):
    rows, pins = gold_rows()
    write_rows(rows, tmp_path / "gold", {"dataset_kind": "production_gold", **pins})
    data = T.admit_dataset(cfg(tmp_path, tmp_path / "gold", tmp_path / "out"))
    assert data.identity["dataset_kind"] == "production_gold" and data.stubs == {}
    assert data.identity["dataset_version"] == DATASET_VERSION and data.identity["split_entries_sha256"] == pins[
        "split_entries_sha256"]
    assert len(data.identity["entries_sha256"]) == 64 and all(data.identity["files_sha256"].values())


def test_pipeline_test_rows_cannot_be_relabelled_as_production_gold(tmp_path):
    rows, pins = gold_rows()
    for row in rows:
        row.meta['gold'].update(data_mode='pipeline_test', tiny_g1_exception=True, epoch_id='old-epoch')
    write_rows(rows, tmp_path/'gold', {'dataset_kind':'production_gold', **pins})
    config = cfg(tmp_path, tmp_path/'gold', tmp_path/'sft-pilot/out')
    with pytest.raises(RuntimeError, match='pipeline_test|G1'):
        T.admit_dataset(config)
    admitted = T.admit_dataset(replace(config, diagnostic=True))
    assert admitted.stubs and admitted.identity['dataset_kind'] == 'diagnostic'


def test_admission_hashes_the_same_split_bytes_it_reads(tmp_path, monkeypatch):
    rows, pins = gold_rows()
    folder=tmp_path/'gold'
    write_rows(rows, folder, {'dataset_kind':'production_gold', **pins})
    path=folder/'train.jsonl'
    original=T._sha256_file
    def replace_after_hash(file):
        digest=original(file)
        if file==path:
            # An equally valid replacement still has a different byte identity.
            path.write_text(path.read_text()+'\n')
        return digest
    monkeypatch.setattr(T,'_sha256_file',replace_after_hash)
    data=T.admit_dataset(cfg(tmp_path,folder,tmp_path/'out'))
    assert data.identity['files_sha256']['train.jsonl']==original(path)


@pytest.mark.parametrize("damage", ["edited_after_binding", "unpublished", "stale_mask", "label_only", "stub"])
def test_non_gold_or_damaged_data_is_refused_without_diagnostic(tmp_path, damage):
    """Training refuses tainted, stub or unverified data for a shippable adapter, whatever stats.json claims."""
    rows, pins = gold_rows()
    claimed = "production_gold"
    if damage == "edited_after_binding":
        rows[0].input_ids[-1] += 1
    elif damage == "unpublished":
        rows[1].meta["gold"]["published"] = False
    elif damage == "stale_mask":
        rows[2].meta["replay"]["mask_version"] = "stowlm-mask@1.1"
    elif damage == "label_only":
        claimed = "unverified"
    else:
        rows[0].stubs = {"checker": "tiny"}
    write_rows(rows, tmp_path / "d", {"dataset_kind": claimed, **pins})
    with pytest.raises(RuntimeError, match="not verified production gold"):
        T.admit_dataset(cfg(tmp_path, tmp_path / "d", tmp_path / "sft-pilot" / "a"))


def test_diagnostic_fixture_is_labelled_and_confined(tmp_path):
    """A clean synthetic fixture trains only as a diagnostic, under sft-pilot/tiny-chain, with a lineage stub."""
    d = tmp_path / "fixture"
    write_dataset([ex(5, 3), ex(6, 3), ex(5, 3, "val")], d)
    with pytest.raises(RuntimeError, match="tiny-chain"):
        T.admit_dataset(cfg(tmp_path, d, tmp_path / "out", diagnostic=True))
    data = T.admit_dataset(cfg(tmp_path, d, tmp_path / "sft-pilot" / "a", diagnostic=True))
    assert data.identity["dataset_kind"] == "diagnostic" and "stowlm.train.dataset" in data.stubs
    prov = T.training_provenance(cfg(tmp_path, d, tmp_path / "a"), data, "b" * 64, [])
    with pytest.raises(StubTaintError):
        require_clean(prov, what="shipping")


def test_resume_inherits_checkpoint_taint_even_with_clean_data(tmp_path):
    """Review row 2: a clean dataset cannot wash the stubs out of weights trained on stubs."""
    rows, pins = gold_rows()
    write_rows(rows, tmp_path / "gold", {"dataset_kind": "production_gold", **pins})
    c = cfg(tmp_path, tmp_path / "gold", tmp_path / "out")
    data = T.admit_dataset(c)
    inherited = Provenance(stubs={"checker": "tiny-chain stub"}, parents=["sft-data-sha256:old"])
    prov = T.training_provenance(c, data, "b" * 64, [inherited])
    assert prov.stubs["checker"] == "tiny-chain stub" and "sft-data-sha256:old" in prov.parents
    assert f"sft-entries-sha256:{data.identity['entries_sha256']}" in prov.parents


def test_sidecar_round_trips_through_the_planner_consumer(tmp_path):
    """The trainer's sidecar is what the 6.2 planner accepts; taint and base-model identity survive."""
    from eval.harness_integrity import _path_digest

    model, adapter = tmp_path / "model", tmp_path / "adapter"
    model.mkdir()
    adapter.mkdir()
    (model / "model.safetensors").write_bytes(b"base")
    (adapter / "adapters.safetensors").write_bytes(b"lora")
    (adapter / "adapter_config.json").write_text("{}")
    base = _path_digest(model)
    T.write_adapter_sidecar(adapter, Provenance(stubs={"stowlm.train.dataset": "diagnostic"}),
                            {"base_model_content_sha256": base})
    planner = StowLMPlanner(model_path=model, adapter_path=adapter)
    assert planner.provenance.stubs == {"stowlm.train.dataset": "diagnostic"}
    with pytest.raises(StubTaintError):
        require_clean(planner.provenance, what="official evaluation planner")
    assert planner.evaluation_config["model_content_sha256"] == base
    T.write_adapter_sidecar(adapter, Provenance(), {"base_model_content_sha256": "0" * 64})
    with pytest.raises(ValueError, match="different base model"):
        _ = planner.evaluation_config
    (adapter / "adapters.safetensors").write_bytes(b"retrained")  # weights replaced, sidecar stale: fail closed
    with pytest.raises(ValueError, match="provenance"):
        _ = StowLMPlanner(model_path=model, adapter_path=adapter).evaluation_config


def fake_checkpoint(out: Path, step: int, *, state=True, **state_kw):
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{step:07d}_adapters.safetensors").write_bytes(f"a{step}".encode())
    (out / f"{step:07d}_optimizer.safetensors").write_bytes(f"o{step}".encode())
    if state:
        rec = {"schema_version": 1, "iteration": step,
               "adapter_sha256": T._sha256_file(out / f"{step:07d}_adapters.safetensors"),
               "optimizer_sha256": T._sha256_file(out / f"{step:07d}_optimizer.safetensors"), **state_kw}
        (out / f"{step:07d}_state.json").write_text(json.dumps(rec))


def test_checkpoint_discovery_skips_incomplete_and_refuses_legacy(tmp_path):
    """Review row 7: discovery reads the output root's complete pairs; a half-written newer step falls back."""
    legacy = tmp_path / "legacy"
    fake_checkpoint(legacy, 50, state=False)
    with pytest.raises(RuntimeError, match="legacy"):
        T.latest_complete_checkpoint(legacy)
    out = tmp_path / "out"
    out.mkdir()
    assert T.latest_complete_checkpoint(out) is None
    fake_checkpoint(out, 50)
    fake_checkpoint(out, 75)
    fake_checkpoint(out, 100, state=False)  # interrupted before its marker
    assert T.latest_complete_checkpoint(out)["iteration"] == 75
    (out / "0000075_optimizer.safetensors").write_bytes(b"torn")  # files no longer match the marker
    assert T.latest_complete_checkpoint(out)["iteration"] == 50


def test_resume_refuses_a_different_trajectory(tmp_path):
    base = cfg(tmp_path, "d", "a", iters=100, lr_schedule="cosine", warmup_frac=0.05)
    data_id = {"entries_sha256": "e" * 64}
    state = {"iteration": 50, "trajectory_config": T.trajectory_config(base), "data_identity": data_id,
             "base_model_content_sha256": "m" * 64}
    T.check_resume_compatible(state, replace(base, steps_per_report=5, steps_per_eval=7, resume=True), data_id, "m" * 64)
    for changed, match in ((replace(base, learning_rate=1e-5), "learning_rate"),
                           (replace(base, iters=200), "iters"),  # cosine schedule depends on iters
                           (replace(base, seed=1), "seed")):
        with pytest.raises(RuntimeError, match=match):
            T.check_resume_compatible(state, changed, data_id, "m" * 64)
    with pytest.raises(RuntimeError, match="dataset"):
        T.check_resume_compatible(state, base, {"entries_sha256": "x" * 64}, "m" * 64)
    with pytest.raises(RuntimeError, match="base model"):
        T.check_resume_compatible(state, base, data_id, "x" * 64)
    const = cfg(tmp_path, "d", "a", iters=75)
    state["trajectory_config"] = T.trajectory_config(const)
    T.check_resume_compatible(state, replace(const, iters=150), data_id, "m" * 64)  # constant lr: may extend


def test_train_refuses_mismatched_checkpoint_before_mlx(tmp_path, monkeypatch):
    import sys

    d = tmp_path / "sft-pilot" / "data"
    write_dataset([ex(5, 3), ex(5, 3, "val")], d)
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"base")
    out = tmp_path / "sft-pilot" / "a"
    c = cfg(tmp_path, d, out, diagnostic=True)
    fake_checkpoint(out, 10, trajectory_config={**T.trajectory_config(c), "rank": 99},
                    data_identity={"entries_sha256": "x"}, base_model_content_sha256="y", provenance={})
    monkeypatch.setitem(sys.modules, "mlx_lm", None)  # any MLX import would raise ImportError
    with pytest.raises(RuntimeError, match="different config"):
        T.train(replace(c, model=str(model)))
    with pytest.raises(RuntimeError, match="already holds checkpoints"):
        T.train(replace(c, model=str(model), resume=False))


def test_batch_schedule_resumes_the_uninterrupted_sequence():
    """Review row 4: batch b depends only on (seed, b), so a resume at step k continues with batch k."""
    lengths = [5, 9, 3, 7, 8, 2, 6]
    full = list(itertools.islice(T.batch_schedule(lengths, 2, seed=3), 20))
    for k in (1, 3, 7, 13):
        assert list(itertools.islice(T.batch_schedule(lengths, 2, seed=3, start=k), 20 - k)) == full[k:]
    epoch = full[:3]  # 3 full batches of 2 (one example left over, as in mlx-lm)
    assert sorted(i for b in epoch for i in b) == sorted(i for b in full[3:6] for i in b)
    assert full[:3] != full[3:6] or full[3:6] != full[6:9]  # reshuffled per epoch
    assert list(itertools.islice(T.batch_schedule(lengths, 2, seed=4), 20)) != full
    assert T.step_seed(0, 5) == T.step_seed(0, 5) != T.step_seed(0, 6)


def test_grad_accumulation_must_align_with_checkpoints(tmp_path):
    with pytest.raises(ValueError, match="grad_accumulation"):
        T.train(cfg(tmp_path, "d", "a", iters=10, steps_per_save=5, grad_accumulation_steps=4))


def test_processed_tokens_follow_the_training_batch_schedule():
    """fix-t8-sft pipeline test: run.json reported mlx-lm's loss-token rate (11 tok/s) for ~258 processed tok/s.
    The processed count is every token fed, per global step, from the same deterministic batch order."""
    import itertools

    lengths = [600, 610, 590, 605, 620]
    fed = T.processed_tokens(lengths, 2, seed=3, start=4, n=6)
    groups = list(itertools.islice(T.batch_schedule(lengths, 2, seed=3, start=4), 6))
    assert fed == [sum(lengths[i] for i in g) for g in groups] and all(1180 <= f <= 1230 for f in fed)
    assert sorted(T.processed_tokens(lengths, 1, seed=0, start=0, n=5)) == sorted(lengths)  # one epoch, batch 1


def test_lineage_records_admitted_prompt_and_format_versions(tmp_path):
    """w2-eval-honesty handoff: the adapter lineage carries the prompt/format the training rows were built with;
    mixed or contradicting versions are not production gold."""
    rows, pins = gold_rows()
    write_rows(rows, tmp_path / "gold", {"dataset_kind": "production_gold", "prompt": PROMPT_VERSION,
                                         "format": FORMAT_VERSION, **pins})
    data = T.admit_dataset(cfg(tmp_path, tmp_path / "gold", tmp_path / "out"))
    assert data.identity["prompt_version"] == PROMPT_VERSION and data.identity["format_version"] == FORMAT_VERSION
    write_rows(rows, tmp_path / "stale", {"dataset_kind": "production_gold", "prompt": "stowlm-prompt@2.1",
                                          "format": FORMAT_VERSION, **pins})
    with pytest.raises(RuntimeError, match="prompt versions"):
        T.admit_dataset(cfg(tmp_path, tmp_path / "stale", tmp_path / "out"))



def test_prompt_3_0_rows_are_admitted_under_the_switch_with_their_action_space(tmp_path):
    """8.1 adoption of stowlm-prompt@3.0 (w2-l4-format-review): admission follows active_prompt_version(), and the
    row's recorded action-space version must be the current one; legacy-prompt rows are refused once 3.0 is active."""
    from llm.stowlm.format_prompt import EXPERT_PROMPT_VERSION
    from stowage.actions import ACTION_SPACE_VERSION
    from stowage.rules.catalogue import rule_freeze_v1

    with rule_freeze_v1(enabled=True):
        rows, pins = gold_rows()
        write_rows(rows, tmp_path / "v3", {"dataset_kind": "production_gold", **pins})
        data = T.admit_dataset(cfg(tmp_path, tmp_path / "v3", tmp_path / "out"))
        assert data.identity["prompt_version"] == EXPERT_PROMPT_VERSION and data.stubs == {}
        assert data.identity["action_space_version"] == ACTION_SPACE_VERSION
        for name, kw in (("legacy", {"prompt": PROMPT_VERSION}), ("no_space", {"action_space": None}),
                         ("old_space", {"action_space": "0.9"})):
            rows, pins = gold_rows(**kw)
            write_rows(rows, tmp_path / name, {"dataset_kind": "production_gold", **pins})
            with pytest.raises(RuntimeError, match="not verified production gold"):
                T.admit_dataset(cfg(tmp_path, tmp_path / name, tmp_path / "out"))


def test_default_sequence_cap_covers_the_measured_prompt_3_0_lengths():
    """Full ship ~9,160 and the dense 800 pilot 18,006 tokens (prompt 3.0 + completion); truncation is an error."""
    assert T.TrainConfig(data_dir="d", adapter_dir="a").max_seq_length >= 18006
