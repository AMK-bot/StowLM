"""Active gold identity scans exclude historical quarantine and runtime trees."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("gold_scope_test_helpers", Path(__file__).with_name("test_gold.py"))
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)


@pytest.mark.parametrize("cohort", ["train", "val"])
@pytest.mark.parametrize("duplicate", ["instance_id", "input_sha256"])
@pytest.mark.parametrize("passed", [True, False])
def test_active_fit_duplicate_rejected_including_failures(tmp_path, cohort, duplicate, passed):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = helpers.commit_case(corpus)
    value = "synthetic-1" if duplicate == "instance_id" else helpers.driver.sha256(path)
    helpers.driver.atomic_json(output / cohort / "small/other/family/seed-2/result.json",
                               {duplicate: value, "completed": True, "passed": passed})
    with pytest.raises(ValueError, match="duplicate existing-gold"):
        helpers.verify_split(path, corpus, output)


@pytest.mark.parametrize("excluded", ["quarantine-pre-epoch", "runtime-epoch"])
def test_historical_and_runtime_results_never_read(tmp_path, monkeypatch, excluded):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = helpers.commit_case(corpus)
    ignored = output / excluded / "train/tiny/ship/family/seed-1/result.json"
    helpers.driver.atomic_json(ignored, {"instance_id": "synthetic-1", "input_sha256": helpers.driver.sha256(path),
                                        "completed": True, "passed": False})
    module = helpers.load_module("split_admission")
    original_read = module._read
    original_rglob = Path.rglob

    def guard_read(candidate):
        assert excluded not in Path(candidate).parts, "excluded result was read"
        return original_read(candidate)

    def guard_walk(directory, pattern):
        if Path(directory).is_relative_to(output):
            assert Path(directory) in {output / "train", output / "val"}, "walk entered unscoped output root"
        return original_rglob(directory, pattern)

    monkeypatch.setattr(module, "_read", guard_read)
    monkeypatch.setattr(Path, "rglob", guard_walk)
    assert module.verify_candidate(path, corpus, output)["instance_id"] == "synthetic-1"
