"""CPU publication regressions for the D150/D151 failure modes."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

SPEC = importlib.util.spec_from_file_location("_teacher9_export_test", Path(__file__).with_name("export.py"))
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)


def manifest_for(entries, sources=None):
    from llm.exit.outcome_epoch import epoch_id

    manifest = dict(schema="stowlm9-manifest-v1", outcome_epoch=epoch_id({"physics.py": "A"}),
                    entries=entries, sources=sources or {}, rule_deadline="2099-10-31T19:45:00-04:00")
    manifest["manifest_id"] = export._sha(json.dumps(manifest, sort_keys=True).encode())
    return manifest


@pytest.fixture
def case(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    run = repo / "experiments/mvp/stowlm9"
    inputs = repo / "experiments/mvp/corpus/train/small"
    inputs.mkdir(parents=True)
    monkeypatch.setattr(export, "REPO", repo)
    monkeypatch.setattr(export, "_assert_epoch", lambda expected: None)
    ipath = inputs / "one.json"
    ipath.write_text('{"id":"one"}')
    entry = dict(key="train__small__one", input=str(ipath.relative_to(repo)),
                 input_sha256=export._sha(ipath.read_bytes()), instance_id="one", size="small",
                 baseline_record=None, baseline_record_sha256=None)
    producer = repo / "producer.py"
    producer.write_text("producer A")
    manifest = manifest_for([entry], {"producer.py": export._sha(producer.read_bytes())})
    expected = {"physics.py": "A"}
    ack = dict(protocol="exit-check-outcome-ack-v1", request_id="owned-request",
               loaded_pre=expected, loaded_post=expected, imports={"physics.py": "A"}, resources={})
    actions = [{"op": "P", "box": "box", "slot": "0010182"}]
    row = dict(key=entry["key"], input=entry["input"], input_sha256=entry["input_sha256"],
               manifest_id=manifest["manifest_id"], outcome_epoch=manifest["outcome_epoch"],
               binding=dict(manifest_id=manifest["manifest_id"], input_sha256=entry["input_sha256"],
                            baseline_record_sha256=None, stream="rule"),
               finished_at=time.time() - 1, status="complete", passed=True, source="rule", moves=2,
               record=dict(instance_path=entry["input"], outcome_epoch=manifest["outcome_epoch"],
                           report={"passed": True}, mask={"passed": True}, weighted_moves=2,
                           canonical_version="stowlm9-final-placement-v1", plan={"actions": actions},
                           actions_sha256=export._json_sha(actions),
                           checker_watchdog={"available": True, "epoch_ack": ack}))
    path = run / "rule/rows" / f"{entry['key']}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(row))
    return SimpleNamespace(repo=repo, run=run, entry=entry, manifest=manifest, row=row, path=path)


def replay_bound_example():
    from llm.stowlm import sft_data
    from llm.stowlm.format import FORMAT_VERSION
    from llm.stowlm.mask import MASK_VERSION
    from stowage.actions import ACTION_SPACE_VERSION

    example = sft_data.SFTExample(instance_id="one", split="train", input_ids=[1, 2, 3], prompt_len=1,
        n_actions=1, n_boxes=1, plan_id="one.plan", meta=dict(
            format=FORMAT_VERSION, prompt="stowlm-prompt@9.0", action_space=ACTION_SPACE_VERSION,
            dataset=sft_data.DATASET_VERSION,
            gold=dict(published=True, kind=sft_data.MVP_GOLD, checker_passed=True),
            replay=dict(ok=True, d79=True, mask_version=MASK_VERSION, stubs={}, securing=sft_data.SECURING_LAYER)))
    digest = sft_data.example_sha256(example)
    example.meta["gold"]["example_sha256"] = digest
    example.meta["replay"]["example_sha256"] = digest
    return example


@pytest.mark.parametrize("change", ["epoch", "input", "binding", "checker_ack", "plan", "future"])
def test_rejects_stale_or_unbound_winner(case, change):
    row = copy.deepcopy(case.row)
    if change == "epoch":
        row["outcome_epoch"] = "stale"
    elif change == "input":
        (case.repo / case.entry["input"]).write_text("different bytes")
    elif change == "binding":
        row["binding"]["baseline_record_sha256"] = "prior candidate"
    elif change == "checker_ack":
        row["record"]["checker_watchdog"]["epoch_ack"]["imports"]["physics.py"] = "B"
    elif change == "plan":
        row["record"]["plan"]["actions"][0]["slot"] = "0020182"
    else:
        row["finished_at"] = time.time() + 3600
    case.path.write_text(json.dumps(row))
    with pytest.raises(ValueError):
        export.admitted_rows(case.manifest, "rule", case.run)


def test_only_current_complete_rows_discovered(case):
    (case.path.parent / "stale-map.json").write_text(json.dumps(case.row))
    winners, counts, snapshots = export.admitted_rows(case.manifest, "rule", case.run)
    assert len(winners) == len(snapshots) == 1
    assert counts["ignored_stale_files"] == 1
    case.row.update(status="error", passed=False)
    case.path.write_text(json.dumps(case.row))
    winners, counts, _ = export.admitted_rows(case.manifest, "rule", case.run)
    assert winners == [] and counts["errors"] == 1


def test_rule_deadline_excludes_late_finished_rows(case):
    case.manifest["rule_deadline"] = "2000-01-01T00:00:00-05:00"
    case.manifest.pop("manifest_id")
    case.manifest["manifest_id"] = export._sha(json.dumps(case.manifest, sort_keys=True).encode())
    case.row["manifest_id"] = case.manifest["manifest_id"]
    case.row["binding"]["manifest_id"] = case.manifest["manifest_id"]
    case.path.write_text(json.dumps(case.row))
    winners, counts, _ = export.admitted_rows(case.manifest, "rule", case.run)
    assert winners == [] and counts["after_deadline"] == 1


def test_manifest_tampering_is_refused(case):
    case.manifest["entries"] = []
    with pytest.raises(ValueError, match="manifest content binding"):
        export.admitted_rows(case.manifest, "rule", case.run)


def test_d209_allows_guarded_rows_after_snapshot_time(case):
    case.manifest['rule_deadline'] = '2000-01-01T00:00:00-05:00'
    case.manifest['config'] = dict(rule_stop_at_deadline=False, rule_entry_limit=None)
    case.manifest.pop('manifest_id')
    case.manifest['manifest_id'] = export._sha(json.dumps(case.manifest, sort_keys=True).encode())
    case.row['manifest_id'] = case.manifest['manifest_id']
    case.row['binding']['manifest_id'] = case.manifest['manifest_id']
    case.path.write_text(json.dumps(case.row))
    winners, counts, _ = export.admitted_rows(case.manifest, 'rule', case.run)
    assert len(winners) == 1
    assert counts['after_deadline'] == 0


def test_fresh_publication_removes_old_winner_and_keeps_val_bytes(case, monkeypatch):
    """Stub expensive physics only; exercise real publication, files and stats."""
    from llm.stowlm import sft_data
    from llm.stowlm.sft_data import SFTExample
    from stowage.actions.space import Place

    val = case.run / "frozen-val.jsonl"
    val.write_bytes(b"".join((json.dumps(dict(instance_id=f"val-{i}", split="val", input_ids=[1, 2],
                                             prompt_len=1, n_actions=1, n_boxes=1, plan_id=f"val-{i}.plan", stubs={},
                                             meta=dict(prompt="stowlm-prompt@2.2", gold={"input_sha256": f"val-sha-{i}"}))) + "  \n").encode()
                             for i in range(80)))
    val_bytes = val.read_bytes()
    input_sha = case.entry["input_sha256"]
    instance = SimpleNamespace(id="one", size_class="small", load_list=["box"], model_dump=lambda **_: {"id": "one"})
    builder = SimpleNamespace(MAX_LEN=10240, load_triple=lambda _: (instance, object(), {}, input_sha,
                                                                  case.repo / case.entry["input"]))
    prompt = SimpleNamespace(PROMPT_VERSION="stowlm-prompt@9.0", install=lambda: None)
    monkeypatch.setattr(export, "_module", lambda name, path: builder if "builder" in name else prompt)
    monkeypatch.setattr("llm.stowlm.format.load_tokenizer", lambda: object())
    monkeypatch.setattr("llm.exit.identity.IdentityGuard.tokens", lambda instance, raw_sha: {("instance_id", instance.id), ("sha256", raw_sha)})
    monkeypatch.setattr(sft_data, "plan_actions", lambda *args: [Place("box", "0010182")])
    monkeypatch.setattr(sft_data, "active_prompt_version", lambda: "stowlm-prompt@9.0")
    build_calls = []

    def build_example(*args, **kwargs):
        build_calls.append(True)
        return replay_bound_example()

    monkeypatch.setattr(sft_data, "build_mvp_example", build_example)

    def write_dataset(examples, stage, **kwargs):
        (stage / "train.jsonl").write_text("winner\n")
        return {"splits": {"train": {"examples": len(examples)}}}

    monkeypatch.setattr(sft_data, "write_dataset", write_dataset)
    manifest_path = case.run / "manifest.json"
    manifest_path.write_text(json.dumps(case.manifest))
    first = export.export_stream(manifest_path, "rule", root=case.run, val_path=val)
    assert first["counts"]["train"] == 1
    published = case.run / "data-85"
    assert published.is_symlink()
    first_generation = published.resolve()
    assert (published / "val.jsonl").read_bytes() == val_bytes
    reused = export.export_stream(manifest_path, "rule", root=case.run, val_path=val)
    assert reused["serialized_row_cache"]["hits"] == 1
    assert len(build_calls) == 1
    # Exact byte changes to an otherwise valid row cannot reuse the prior row.
    case.row["new_diagnostic"] = "new bytes"
    case.path.write_text(json.dumps(case.row))
    refreshed = export.export_stream(manifest_path, "rule", root=case.run, val_path=val)
    assert refreshed["serialized_row_cache"]["misses"] == 1
    assert len(build_calls) == 2
    # Current input admission runs even when a valid serialized cache exists.
    input_path = case.repo / case.entry["input"]
    original_input = input_path.read_bytes()
    input_path.write_text("changed bytes")
    with pytest.raises(ValueError, match="input bytes changed"):
        export.export_stream(manifest_path, "rule", root=case.run, val_path=val)
    input_path.write_bytes(original_input)
    producer = case.repo / "producer.py"
    producer.write_text("producer B")
    with pytest.raises(ValueError, match="export source changed"):
        export.export_stream(manifest_path, "rule", root=case.run, val_path=val)
    producer.write_text("producer A")
    case.row.update(passed=False, record=None)
    case.path.write_text(json.dumps(case.row))
    second = export.export_stream(manifest_path, "rule", root=case.run, val_path=val)
    assert second["counts"]["train"] == 0 and second["counts"]["no_teacher"] == 1
    assert published.resolve() != first_generation
    assert (published / "train.jsonl").read_bytes() == b""
    assert (published / "val.jsonl").read_bytes() == val_bytes
    assert not list((published / "records").rglob("record.json"))
    # A failure in the convenience sidecar after publication must not delete
    # the generation now referenced by the atomic dataset symlink.
    replace = export.os.replace

    def fail_metrics(source, target):
        if Path(target).name == "export-metrics.json":
            raise OSError("sidecar disk error")
        return replace(source, target)

    monkeypatch.setattr(export.os, "replace", fail_metrics)
    with pytest.raises(OSError, match="sidecar disk error"):
        export.export_stream(manifest_path, "rule", root=case.run, val_path=val)
    assert (published / "val.jsonl").read_bytes() == val_bytes
    assert (published / "train.jsonl").read_bytes() == b""
    monkeypatch.setattr(export.os, "replace", replace)
    # Invalid new input cannot disturb the last good publication.
    old_target = published.resolve()
    case.row.update(passed=True, record=copy.deepcopy(case.row))
    case.path.write_text(json.dumps(case.row))
    with pytest.raises(ValueError):
        export.export_stream(manifest_path, "rule", root=case.run, val_path=val)
    assert published.resolve() == old_target


def test_cache_binds_prompt_producers_input_and_full_metadata(case, monkeypatch):
    from llm.stowlm import sft_data

    monkeypatch.setattr(sft_data, "active_prompt_version", lambda: "stowlm-prompt@9.0")
    binding = export.cache_binding(case.manifest, "row-A", case.entry, "prompt-A")
    example = replay_bound_example()
    example.meta["gold"].update(input_sha256=case.entry["input_sha256"],
                                 manifest_id=case.manifest["manifest_id"], outcome_epoch=case.manifest["outcome_epoch"])
    tokens = {("instance_id", "one"), ("sha256", case.entry["input_sha256"]), ("seed", "1")}
    path = case.run / ".sft-cache/test.json"
    export.write_cached_example(path, binding, example, tokens)
    assert export.read_cached_example(path, binding) is not None
    for key in ("row_sha256", "input_sha256", "prompt_source_sha256", "producer_sources_sha256", "manifest_id", "outcome_epoch"):
        changed = {**binding, key: "new value"}
        assert export.read_cached_example(path, changed) is None
    saved = json.loads(path.read_bytes())
    saved["payload"]["example"]["meta"]["gold"]["teacher_source"] = "tampered metadata"
    path.write_text(json.dumps(saved))
    assert export.read_cached_example(path, binding) is None


def test_frozen_validation_identity_is_never_training(case, monkeypatch):
    source = case.repo / "historical-gold.json"
    source.write_text(json.dumps({"input_path": case.entry["input"]}))
    rows = [dict(instance_id=f"val-{i}", split="val", meta={"gold": {"input_sha256": f"hash-{i}"}})
            for i in range(80)]
    rows[0]["meta"]["gold"].update(source=str(source), input_sha256=case.entry["input_sha256"])
    path = case.run / "frozen-val.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr("stowage.io.load_instance", lambda _: {"id": "historical", "seed": 42})
    raw, frozen, tokens = export.validation_snapshot(path)
    assert raw == path.read_bytes() and len(frozen) == 80
    assert ("seed", "42") in tokens
    assert ("instance_id", "historical") in tokens
    assert ("sha256", case.entry["input_sha256"]) in tokens
    (case.repo / case.entry["input"]).write_text("changed source")
    with pytest.raises(ValueError, match="validation source input changed"):
        export.validation_snapshot(path)
