"""Dataset publication regressions use synthetic tokens, never physical gold."""
import json
from dataclasses import replace

import pytest

from llm.stowlm.sft_data import SFTExample, read_dataset, write_dataset


def example(split="train", *, tainted=False, n_boxes=1):
    return SFTExample("synthetic", split, [1, 2, 3], 1, 1, n_boxes, "synthetic-plan",
                      stubs={"synthetic": "diagnostic"} if tainted else {})


@pytest.mark.parametrize("split", ["../../leak", "../leak", "/absolute", "train/other", "", "validation"])
def test_writer_rejects_split_paths_before_writing(tmp_path, split):
    out = tmp_path / "tiny-chain" / "out"
    with pytest.raises(ValueError, match="split"):
        write_dataset([example(split, tainted=True)], out)
    assert not out.exists()
    assert not (tmp_path / "leak.jsonl").exists()


def test_rewrite_removes_absent_managed_splits(tmp_path):
    write_dataset([example(), example("val"), example("test")], tmp_path)
    write_dataset([example()], tmp_path)
    assert not (tmp_path / "val.jsonl").exists()
    assert not (tmp_path / "test.jsonl").exists()
    assert set(json.loads((tmp_path / "stats.json").read_text())["splits"]) == {"train"}


def test_writer_refuses_outbound_split_symlink(tmp_path):
    out = tmp_path / "tiny-chain"
    out.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("original")
    (out / "train.jsonl").symlink_to(outside)
    with pytest.raises(ValueError, match="target|symlink|directory"):
        write_dataset([example(tainted=True)], out)
    assert outside.read_text() == "original"


def test_writer_refuses_active_stub_context(tmp_path):
    import stowage.interfaces  # noqa: F401
    from stowage.frozen import stub_override
    with stub_override("feasibility.make_engine", lambda *a, **kw: None, reason="writer diagnostic"):
        with pytest.raises(RuntimeError, match="tiny-chain"):
            write_dataset([example()], tmp_path / "ordinary")
        stats = write_dataset([example()], tmp_path / "tiny-chain")
    assert stats["tainted_examples"] == 1
    assert read_dataset(tmp_path / "tiny-chain" / "train.jsonl")[0].stubs["feasibility.make_engine"]


def test_required_splits_refuse_partial_rewrite(tmp_path):
    write_dataset([example(), example("val")], tmp_path)
    previous = (tmp_path / "stats.json").read_bytes()
    with pytest.raises(ValueError, match="required|missing"):
        write_dataset([example()], tmp_path, required_splits=("train", "val"))
    assert (tmp_path / "stats.json").read_bytes() == previous
    assert (tmp_path / "val.jsonl").exists()


def test_production_writer_refuses_unadmitted_examples(tmp_path):
    with pytest.raises(ValueError, match="gold|published|admission"):
        write_dataset([example()], tmp_path, production=True)


def test_clean_serializer_is_explicitly_not_gold(tmp_path):
    stats = write_dataset([example(n_boxes=2), replace(example(), instance_id="smaller")], tmp_path)
    assert stats["dataset_kind"] == "unverified"
    assert stats["curriculum"] == ["n_boxes", "total_tokens", "instance_id"]
    assert stats["splits"]["train"]["n_boxes"]["min"] == 1
    assert [e.n_boxes for e in read_dataset(tmp_path / "train.jsonl")] == [1, 2]


def production_metadata_example():
    """Only a writer-policy unit oracle, never evidence of checker/physics approval."""
    from eval.splits import load_inventory
    from llm.stowlm.format import FORMAT_VERSION
    from llm.stowlm.format_prompt import PROMPT_VERSION
    from llm.stowlm.mask import MASK_VERSION
    from llm.stowlm.sft_data import DATASET_VERSION, example_sha256
    inventory = load_inventory()
    row = example()
    digest = example_sha256(row)
    row.meta = {"format": FORMAT_VERSION, "prompt": PROMPT_VERSION, "dataset": DATASET_VERSION,
        "gold": {"published": True, "split_version": inventory.version,
                 "split_entries_sha256": inventory.entries_sha256, "example_sha256": digest},
        "replay": {"ok": True, "production": True, "mask_version": MASK_VERSION,
                   "global_screen": "on", "stubs": {}, "example_sha256": digest}}
    return row


def test_production_writer_rejects_changed_tokens_after_admission(tmp_path):
    row = production_metadata_example()
    row.input_ids[-1] += 1
    with pytest.raises(ValueError, match="changed|digest|binding"):
        write_dataset([row], tmp_path, production=True)
    assert not tmp_path.joinpath("stats.json").exists()


@pytest.mark.parametrize("location", ["gold", "replay"])
def test_production_writer_requires_both_content_bindings(tmp_path, location):
    row = production_metadata_example()
    row.meta[location].pop("example_sha256")
    with pytest.raises(ValueError, match="digest|binding"):
        write_dataset([row], tmp_path, production=True)
