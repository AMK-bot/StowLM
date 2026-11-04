"""Cohort provenance and sizing invariants; no solver or model inference."""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.mvp.strategy.proposer import prepare


def frozen():
    return json.loads((prepare.HERE / "maps.json").read_text())


def test_frozen_train_cohort_is_balanced_unseen_small_and_exact_medium():
    manifest = frozen()
    rows = manifest["maps"]
    assert Counter(row["size"] for row in rows) == {"small": 20, "medium": 8}
    assert len({row["sha256"] for row in rows}) == len(rows)
    ships = Counter(row["ship"] for row in rows if row["size"] == "small")
    assert max(ships.values()) - min(ships.values()) == 1
    seen_paths, seen_hashes, _ = prepare.headroom_seen()
    for row in rows:
        assert row["split"] == "train"
        assert "train" in Path(row["input"]).parts
        assert not {"test", "val", "validation", "showcase"} & set(Path(row["input"]).parts)
        assert prepare.digest(prepare.ROOT / row["input"]) == row["sha256"]
        if row["size"] == "small":
            assert row["input"] not in seen_paths
            assert row["sha256"] not in seen_hashes
            assert row["headroom_overlap"] is False
        else:
            assert row["headroom_overlap"] is True
    assert manifest["headroom_overlap_counts"] == {"medium": 8}
    assert manifest["frozen_before_proposer_results"] is True
    assert manifest["selection"]["outcomes_read"] is False


def test_manifest_selection_is_reproducible_without_reading_results():
    current = prepare.build_manifest()
    expected = frozen()
    assert [row["map_id"] for row in current["maps"]] == [row["map_id"] for row in expected["maps"]]
    assert [row["sha256"] for row in current["maps"]] == [row["sha256"] for row in expected["maps"]]


def test_cli_refuses_to_replace_frozen_manifest(tmp_path):
    manifest = tmp_path / "maps.json"
    manifest.write_text("do not replace\n")
    result = subprocess.run([sys.executable, str(prepare.HERE / "prepare.py"), "--freeze", "--manifest", str(manifest)],
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "already frozen" in result.stderr
    assert manifest.read_text() == "do not replace\n"


def test_token_measurements_identify_proxy_and_synthetic_full():
    path = prepare.HERE / "token_sizes.json"
    if not path.exists():
        pytest.skip("run prepare.py --token-sizes to generate sizing evidence")
    measured = json.loads(path.read_text())
    assert measured["manifest_sha256"] == prepare.digest(prepare.HERE / "maps.json")
    for relative, sha256 in measured["implementation_sha256"].items():
        assert sha256 == prepare.digest(prepare.ROOT / relative)
    assert measured["tokenizer"]["chat_template_included"] is False
    assert "proxy" in measured["tokenizer"]["spark"]
    assert measured["tokenizer"]["sha256"] == prepare.digest(prepare.ROOT / measured["tokenizer"]["path"])
    assert set(measured["ranges"]) == {"small", "medium", "full"}
    full = [row for row in measured["measurements"] if row["size"] == "full"]
    assert len(full) == 1 and full[0]["provenance"]["synthetic"] is True
    assert full[0]["loads"] == 815 and full[0]["blocks"] >= 36
    for row in measured["measurements"]:
        assert row["prompt"]["glimmer_raw_tokens"] > row["summary"]["glimmer_raw_tokens"] > 0
        assert row["layout_one_block_per_category_syntax_only"]["glimmer_raw_tokens"] <= row["layout_all_blocks_per_category"]["glimmer_raw_tokens"]
        assert row["spark_proxy_usd_by_proposal_count"]["16"] == 16 * row["spark_proxy_usd_per_proposal"]
    assert "exceed k" in measured["pricing_assumption"]["budget_caveat"]
