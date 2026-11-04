"""Stat-bound output summaries survive dynamic helper imports, never stale edits."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

_spec = importlib.util.spec_from_file_location("gold_cache_test_helpers", Path(__file__).with_name("test_gold.py"))
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
CACHE_NAME = "_stowlm_gold_output_identity_cache_v1"


def setup_case(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    candidate = helpers.commit_case(corpus)
    other = output / "val/tiny/other/family/seed-2/result.json"
    helpers.driver.atomic_json(other, dict(instance_id="other", input_sha256="b"*64,
                                          completed=True, passed=False, huge_source_pins={"unused": "x"*1000}))
    return corpus, output, candidate, other


def test_reimport_reuses_only_identity_summary(tmp_path, monkeypatch):
    corpus, output, candidate, other = setup_case(tmp_path)
    first = helpers.load_module("split_admission")
    reads = []
    original = first._read
    monkeypatch.setattr(first, "_read", lambda p: reads.append(Path(p)) or original(p))
    first.verify_candidate(candidate, corpus, output)
    first.verify_candidate(candidate, corpus, output)
    assert reads.count(other) == 1
    # The helper module is freshly constructed each call by the real driver.
    second = helpers.load_module("split_admission")
    second_original = second._read
    monkeypatch.setattr(second, "_read", lambda p: reads.append(Path(p)) or second_original(p))
    second.verify_candidate(candidate, corpus, output)
    assert reads.count(other) == 1
    assert reads.count(candidate.with_suffix(".audit.json")) == 3
    summary = sys.modules[CACHE_NAME].entries[str(other.resolve())][1]
    assert set(summary) == {"instance_id", "input_sha256"}


@pytest.mark.parametrize("field", ["instance_id", "input_sha256"])
@pytest.mark.parametrize("atomic", [False, True])
def test_changed_identity_invalidates_in_place_and_atomic_replace(tmp_path, field, atomic):
    corpus, output, candidate, other = setup_case(tmp_path)
    module = helpers.load_module("split_admission")
    module.verify_candidate(candidate, corpus, output)
    row = json.loads(other.read_text())
    row[field] = "synthetic-1" if field == "instance_id" else helpers.driver.sha256(candidate)
    if atomic:
        replacement = other.with_name("replacement.json")
        replacement.write_text(json.dumps(row))
        replacement.replace(other)
    else:
        # Even restoring mtime cannot hide the ctime/size change.
        before = other.stat()
        other.write_text(json.dumps(row))
        os.utime(other, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="duplicate existing-gold"):
        helpers.load_module("split_admission").verify_candidate(candidate, corpus, output)


def test_output_cache_does_not_cache_candidate_bytes(tmp_path):
    corpus, output, candidate, other = setup_case(tmp_path)
    module = helpers.load_module("split_admission")
    module.verify_candidate(candidate, corpus, output)
    candidate.write_text(json.dumps(dict(id="tampered", seed=1)))
    with pytest.raises(ValueError, match="audit checksum"):
        module.verify_candidate(candidate, corpus, output)
