"""Pin local bytes and prepare a reviewable patch without live mutations."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LIVE = "src/stowage/checker/rules_special.py"
TEST_TARGET = "tests/checker/test_rules_heat_ids.py"
OLD = "                heat_ids = {h.id for h in instance.vessel.heat_sources}\n"
NEW = """                # Tank IDs and source IDs are separate namespaces. Legacy
                # same-ID records have no link; never ignore an explicit link.
                heat_ids = {h.tank_id if h.tank_id is not None else h.id
                            for h in instance.vessel.heat_sources
                            if h.kind == "heated_tank"}
"""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    raw = (ROOT / LIVE).read_bytes()
    text = raw.decode()
    assert text.count(OLD) == 1, "Live anchor changed; review instead of guessing"
    assert not (ROOT / TEST_TARGET).exists(), "Do not overwrite another owner's test"
    updated = text.replace(OLD, NEW)
    ast.parse(updated)
    target = HERE / LIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(updated)
    baseline = HERE / "baseline" / LIVE
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_bytes(raw)
    test = (HERE / "test_rules_heat_ids.py").read_text()
    ast.parse(test)
    patch = "".join(difflib.unified_diff(text.splitlines(keepends=True), updated.splitlines(keepends=True),
                                      fromfile="a/" + LIVE, tofile="b/" + LIVE))
    patch += "".join(difflib.unified_diff([], test.splitlines(keepends=True),
                                       fromfile="/dev/null", tofile="b/" + TEST_TARGET))
    (HERE.parent / "heat-ids.patch").write_text(patch)
    pinned = {name: sha((ROOT / name).read_bytes()) for name in [LIVE,
        "src/stowage/model/vessel.py", "src/stowage/model/fixtures.py",
        "src/stowage/vessel/fleet.py", "tests/checker/test_rules_special.py"]}
    fixture_path = "experiments/mvp/corpus/showcase/feeder_800-small.json"
    frozen_raw = (ROOT / fixture_path).read_bytes()
    frozen = json.loads(frozen_raw)["vessel"]
    heated = {tank["id"] for tank in frozen["tanks"] if tank["heated"]}
    sources = frozen["heat_sources"]
    legacy = {source["id"] for source in sources}
    linked = {source["tank_id"] if source.get("tank_id") is not None else source["id"]
              for source in sources if source["kind"] == "heated_tank"}
    evidence = {"input": fixture_path, "sha256": sha(frozen_raw),
                "heated_tank_ids": sorted(heated),
                "heated_source_links": [{key: source.get(key) for key in ("id", "kind", "tank_id")}
                                        for source in sources if source["kind"] == "heated_tank"],
                "legacy_missing_ids": sorted(heated - legacy),
                "proposed_missing_ids": sorted(heated - linked),
                "kind": "Read-only serialized ID inventory, not a checker or plan-acceptance result"}
    (HERE / "frozen-id-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    record = {"prepared_utc": datetime.now(timezone.utc).isoformat(), "live_source": LIVE,
              "live_before_sha256": sha(raw), "scratch_source": str(target.relative_to(ROOT)),
              "scratch_sha256": sha(updated.encode()), "intended_test_target": TEST_TARGET,
              "standalone_test": str((HERE / "test_rules_heat_ids.py").relative_to(ROOT)),
              "patch": "experiments/mvp/fixwindow/heat-ids.patch", "patch_sha256": sha(patch.encode()),
              "pinned_live_files": pinned, "live_files_unchanged": all(
                  sha((ROOT / name).read_bytes()) == digest for name, digest in pinned.items()),
              "approximation": "None: explicit existing tank_id contract plus legacy exact-ID compatibility; numerical geometry/distance/limits unchanged",
              "runtime_verification": "pending cheap static synthetic regressions; no physics/solver measurements"}
    assert record["live_files_unchanged"]
    (HERE / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ("live_before_sha256", "scratch_sha256", "live_files_unchanged")}))
    print(json.dumps({key: evidence[key] for key in ("sha256", "legacy_missing_ids", "proposed_missing_ids")}))


if __name__ == "__main__":
    main()
