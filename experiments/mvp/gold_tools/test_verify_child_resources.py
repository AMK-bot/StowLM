import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from verify_child_resources import verify


def fixture(tmp_path):
    live, epoch = tmp_path / "live", tmp_path / "epoch"
    names = ["experiments/evidence.json", "src/stowage/linear/primitive.py"]
    pins = {}
    for name in names:
        for root in (live, epoch):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        pins[name] = hashlib.sha256(name.encode()).hexdigest()
    bank = epoch / "src/stowage/linear/margin_policy_data.json"
    bank.write_text(json.dumps(dict(evidence=[dict(path=str(live / name)) for name in names])))
    (epoch / "RESOURCE-MANIFEST.json").write_text(json.dumps(dict(files={
        names[0]: dict(sha256=pins[names[0]])})))
    (epoch / "MANIFEST.json").write_text(json.dumps(dict(files={names[1]: pins[names[1]]})))
    return live, epoch


def test_absolute_resource_and_core_pins_both_match(tmp_path):
    live, epoch = fixture(tmp_path)
    row = verify(epoch, live)
    assert row["absolute_evidence_paths"] == row["matching_paths"] == 2
    assert row["all_equal"] is True


def test_live_change_or_missing_resource_fails_equivalence(tmp_path):
    live, epoch = fixture(tmp_path)
    (live / "experiments/evidence.json").write_text("changed")
    (live / "src/stowage/linear/primitive.py").unlink()
    row = verify(epoch, live)
    assert row["matching_paths"] == 0
    assert row["all_equal"] is False
    assert any("FileNotFoundError" in record.get("error", "") for record in row["records"])
