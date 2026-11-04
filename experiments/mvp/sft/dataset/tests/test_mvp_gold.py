"""MVP gold (v4 7.1, D79): admission, replay binding, writer and trainer gates. Fixture: one frozen tiny test
reference plan (two-step, checker-passed); fine for tooling tests, never for training."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
REC = ROOT / "experiments/mvp/runs/test-twostep/instances/sim.tiny.8000011/record.json"
pytestmark = pytest.mark.skipif(not REC.exists(), reason="mvp-refs fixture missing")


@pytest.fixture(scope="module")
def triple():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from build import load_triple

    instance, plan, report, _, _ = load_triple(REC)
    return instance, plan, report


@pytest.fixture(scope="module")
def val_gold(triple):
    from llm.stowlm.sft_data import build_mvp_example

    instance, plan, report = triple
    return build_mvp_example(instance, plan, report, split="val", source="fixture")


@pytest.fixture(scope="module")
def gold(triple):
    from llm.stowlm.sft_data import build_mvp_example

    instance, plan, report = triple
    return build_mvp_example(instance, plan, report, split="train", source="fixture")


def test_admits_and_binds(gold):
    from llm.stowlm.sft_data import MVP_GOLD, mvp_row_failure

    assert gold.meta["gold"]["kind"] == MVP_GOLD and gold.meta["replay"]["d79"] is True
    assert gold.meta["replay"]["production"] is False  # D79 replay never claims the C2-on production label
    assert mvp_row_failure(gold) is None


def test_tampered_tokens_fail(gold):
    from llm.stowlm.sft_data import mvp_row_failure

    bad = replace(gold, input_ids=[*gold.input_ids[:-1], gold.input_ids[-1] + 1], meta=json.loads(json.dumps(gold.meta)))
    assert "binding" in mvp_row_failure(bad)


def test_failed_report_refused(triple):
    from llm.stowlm.sft_data import build_mvp_example

    instance, plan, report = triple
    with pytest.raises(ValueError, match="did not pass"):
        build_mvp_example(instance, plan, {**report, "passed": False}, split="train", source="fixture")


def test_test_split_refused(triple):
    from llm.stowlm.sft_data import build_mvp_example

    instance, plan, report = triple
    with pytest.raises(ValueError, match="train/val only"):
        build_mvp_example(instance, plan, report, split="test", source="fixture")


def test_writer_refuses_taint(gold, tmp_path):
    from llm.stowlm.sft_data import write_dataset

    tainted = replace(gold, stubs={"x": "y"}, meta=json.loads(json.dumps(gold.meta)))
    with pytest.raises((ValueError, RuntimeError)):
        write_dataset([tainted], tmp_path / "out", mvp=True)


def test_trainer_admits_mvp_gold_clean(gold, val_gold, tmp_path):
    from llm.stowlm.sft_data import write_dataset
    from llm.stowlm.train import TrainConfig, admit_dataset

    val = val_gold
    stats = write_dataset([gold, val], tmp_path / "data", mvp=True)
    assert stats["dataset_kind"] == "mvp_gold" and stats["replay"]["d79_verified"] == 2
    data = admit_dataset(TrainConfig(data_dir=str(tmp_path / "data"), adapter_dir=str(tmp_path / "adapter")))
    assert data.identity["dataset_kind"] == "mvp_gold" and not data.stubs


def test_trainer_refuses_tampered_mvp_gold(gold, val_gold, tmp_path):
    from llm.stowlm.sft_data import write_dataset
    from llm.stowlm.train import TrainConfig, admit_dataset

    val = val_gold
    write_dataset([gold, val], tmp_path / "data", mvp=True)
    path = tmp_path / "data" / "train.jsonl"
    row = json.loads(path.read_text())
    row["meta"]["replay"]["d79"] = False
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(RuntimeError, match="D79 mask replay"):
        admit_dataset(TrainConfig(data_dir=str(tmp_path / "data"), adapter_dir=str(tmp_path / "adapter")))


def test_replay_must_include_securing_layer(gold):  # D95
    from llm.stowlm.sft_data import SECURING_LAYER, mvp_row_failure

    assert gold.meta["replay"]["securing"] == SECURING_LAYER
    meta = json.loads(json.dumps(gold.meta))
    meta["replay"]["securing"] = None
    assert "securing" in mvp_row_failure(replace(gold, meta=meta))


def test_parallel_and_cached_builds_equal_the_serial_build(tmp_path):
    """--workers N and the row cache give byte-identical datasets and reports (except timing/cache fields)."""
    import subprocess

    recs = sorted(str(p) for p in REC.parents[1].glob("*/record.json"))[:4]
    outs = {}
    for name, extra in (("serial", ["--no-cache"]), ("pool", ["--no-cache", "--workers", "2"])):
        out = tmp_path / "sft-pilot" / name
        subprocess.run(["uv", "run", "python", str(Path(__file__).resolve().parents[1] / "build.py"), "--out",
                        str(out), "--test-fixture", *extra, "--train", *recs[:3], "--val", recs[3]],
                       cwd=ROOT, check=True, capture_output=True)
        outs[name] = out
    for f in ("train.jsonl", "val.jsonl", "stats.json"):
        assert (outs["serial"] / f).read_bytes() == (outs["pool"] / f).read_bytes()
    reports = [json.loads((o / "build_report.json").read_text()) for o in outs.values()]
    for r in reports:
        for k in ("wall_s", "workers", "row_cache", "out"):
            r.pop(k, None)
    assert reports[0] == reports[1]
