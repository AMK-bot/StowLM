"""Read-only binding of the final TOML-owner handoff to the D132 composite."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
OWNER = ROOT / "experiments/mvp/tomlcache/patches"
CANDIDATE = Path("/private/tmp/stowlm-d132-assembly-20261030/candidate")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    manifest = json.loads((OWNER / "MANIFEST.json").read_text())
    evidence = json.loads((OWNER / "GUARD-EVIDENCE.json").read_text())
    assert manifest["epoch"] == 4
    assert evidence["candidate_guards_complete"] is True
    assert evidence["production_installed"] is False
    assert evidence["checker_guard"]["qualified"] is True
    assert evidence["checker_guard"]["exact_reports"] == 100
    assert evidence["decode_guard"]["traces"] == 214
    assert evidence["decode_guard"]["prefixes"] == 25454
    assert evidence["decode_guard"]["differences"] == 0
    checks = {}
    for name, record in manifest["patches"].items():
        assert sha(OWNER / name) == record["sha256"]
        checks[name] = record["sha256"]
    for name, record in manifest["files"].items():
        if name == "src/stowage/vessel/fleet_cache.py":
            # The independently resolved dependency-list union is the sole
            # noncanonical TOML target in the combined source tree.
            assert sha(CANDIDATE / name) == "cfb71d326127fd6607fe6619715d64b58e210256e67d38db87daa6af11c30da0"
        else:
            assert sha(CANDIDATE / name) == record["candidate_sha256"]
        checks[name] = sha(CANDIDATE / name)
    records = []
    for record in (evidence["checker_guard"]["summary"], evidence["decode_guard"]["guard"]):
        assert sha(ROOT / record["path"]) == record["sha256"]
        records.append(record)
    output = {"status": "PASS", "toml_owner_final": True,
              "owner_manifest_sha256": sha(OWNER / "MANIFEST.json"),
              "owner_evidence_sha256": sha(OWNER / "GUARD-EVIDENCE.json"),
              "owner_guard_records": records, "checks": checks,
              "production_installation_performed": False,
              "combined_guard_required_separately": True}
    destination = OUT / "toml-handoff-verified.json"
    destination.write_text(json.dumps(output, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "output": str(destination), "sha256": sha(destination)}))


if __name__ == "__main__":
    main()
