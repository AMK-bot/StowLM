"""Collate completed verification; never turn a failed attempt into a pass."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from stowage.generate.physics_identity import current_physics_identity

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
labels = ("generate-final-current", "vessel-complete", "slow-final-current",
          "identity-verified", "latest-source-retry", "latest-background-retry")
rows = {}
for label in labels:
    compute = json.loads((HERE / f"{label}-compute.json").read_text())
    assert "exit_code" in compute, (label, "still running")
    if label != "latest-background-retry":
        assert compute["exit_code"] == 0, (label, compute["exit_code"])
    counts = dict(passed=0, skipped=0, failed=0)
    for case in ET.parse(HERE / f"{label}.xml").iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            counts["failed"] += 1
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    if label != "latest-background-retry":
        assert not counts["failed"], label
    rows[label] = {**counts, "exit_code": compute["exit_code"], **{key: compute[key] for key in
        ("elapsed_s", "estimate_s", "peak_child_RSS_MiB", "source_changes", "command")}}

before = json.loads((HERE / "current-inputs-start.json").read_text())
after = {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
    for base in (ROOT / "src/stowage", ROOT / "params")
    for path in sorted(base.rglob("*"))
    if path.is_file() and path.suffix in {".py", ".json", ".toml"}}
changed = sorted(name for name in before["sources"].keys() | after.keys()
                 if before["sources"].get(name) != after.get(name))
physics = current_physics_identity()
(HERE / "final-physics-identity.json").write_text(json.dumps(physics, indent=2) + "\n")
summary = dict(completed_utc=datetime.now(timezone.utc).isoformat(), jobs=rows,
    default_passed=sum(rows[key]["passed"] for key in labels[:2]),
    default_skipped=sum(rows[key]["skipped"] for key in labels[:2]),
    full_input_sample_started_utc=before["sampled_utc"], full_input_changes=changed,
    current_physics_sha256=physics["sha256"], sources_end=after,
    reserved_peak=dict(cores=4, RAM_GB=12, GPU=False),
    measured_aggregate_RSS_GB=None)
summary["open_verification_issue"] = (
    "Latest extra background rerun rejected concurrent linear/dependencies.py edit; "
    "complete default suites passed, but re-review needs a quiet source epoch."
    if rows["latest-background-retry"]["exit_code"] else None)
(HERE / "verification-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({key: value for key, value in summary.items()
                  if key not in {"jobs", "sources_end"}}, sort_keys=True))
