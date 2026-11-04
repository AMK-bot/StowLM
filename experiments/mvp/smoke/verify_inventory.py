"""Recheck the judge rule inventory against the loaded OFF catalogue."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re

from stowage.rules.catalogue import catalogue_snapshot, rule_freeze_v1_enabled
from stowage.versions import component_versions

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
EXCLUDED = {
    "port.dg.refused_class", "port.dg.direct_delivery", "port.e3.quay_crossing",
    "port.e3.yard_crossing", "port.stability.cargo_operations", "port.ballast.transfer_rate",
    "port.call.profile_resolution", "iacs.rec48.validation", "iacs.l5.validation",
    "iscode.stability.margin", "iacs.strength.margin",
}
FAMILIES = {"IMDG", "IS Code stability", "IACS strength", "lashing", "SOLAS visibility", "expert", "operational"}


def main():
    snapshot = catalogue_snapshot()
    assert not rule_freeze_v1_enabled(), "Inventory verification requires the actual OFF switch"
    rules_path = ROOT / "experiments/mvp/rules.json"
    rows = json.loads(rules_path.read_text())
    catalogue = {rule.id: rule for rule in snapshot.catalogue.rules}
    assert len(rows) == len({row["id"] for row in rows})
    assert {row["id"] for row in rows} == set(catalogue) - EXCLUDED
    for row in rows:
        assert set(row) == {"id", "family", "source", "description", "hardness"}
        assert row["family"] in FAMILIES
        assert row["hardness"] == catalogue[row["id"]].hardness
        assert row["source"].strip() and row["description"].strip()
    versions = component_versions()
    assert "resequencer" not in versions
    text = (ROOT / "experiments/mvp/versions.md").read_text()
    assert snapshot.content_sha256 in text
    registry = importlib.import_module("stowage.versions")
    constants = {name: value for name, value in vars(registry).items()
                 if name.endswith("_VERSION") and isinstance(value, str)}
    generator_versions = {}
    for key, module, attribute in (
        ("instances_campaign_v2", "instances_v2", "CAMPAIGN_VERSION"),
        ("instances_campaign_v3", "instances_v3", "CAMPAIGN_VERSION"),
        ("instances_simulated", "instances", "INSTANCES_VERSION"),
        ("random_yard_adapter", "instances_v2_random", "effective_random_v2_version"),
        ("random_yard", "random_yard", "effective_random_yard_version"),
        ("cargo_generator", "cargo", "effective_cargo_generator_version"),
        ("instances_voyage", "instances_voyage", "VOYAGE_DECLARATION_VERSION"),
    ):
        value = getattr(importlib.import_module("stowage.generate." + module), attribute)
        generator_versions[key] = value() if callable(value) else value
    from stowage.yard.simulator import effective_yard_simulator_version
    generator_versions["yard_simulator"] = effective_yard_simulator_version()
    documented = dict(re.findall(r"\| `([^`]+)` \| `([^`]+)` \|", text))
    for name, value in {**versions, **constants, **generator_versions,
                        "bounds": registry.BOUNDS_VERSION}.items():
        assert documented.get(name) == value, f"Version snapshot drift: {name}"
    hashes = {}
    drift = []
    for name, old in re.findall(r"\| `([^`]+)` \| `([a-f0-9]{64})` \|", text):
        path = ROOT / name
        if not path.exists():
            drift.append(name + " (missing)")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[name] = actual
        if actual != old:
            drift.append(name)
    report = dict(verified_utc=datetime.now(timezone.utc).isoformat(), rule_freeze_v1=False,
                  c2_enabled=False, decision="D78/D79", catalogue_digest=snapshot.content_sha256,
                  active_rule_count=len(rows), catalogue_declarations=len(catalogue),
                  component_versions=versions, inventory_schema_passed=True,
                  registry_constants=constants, generator_versions=generator_versions,
                  documented_versions_passed=True,
                  canonical_metadata_contract_sha256=hashlib.sha256(
                      (ROOT / "src/stowage/checker/stability_policy_contract.json").read_bytes()).hexdigest(),
                  pinned_source_drift=drift, current_pinned_source_sha256=hashes,
                  rules_sha256=hashlib.sha256(rules_path.read_bytes()).hexdigest())
    path = HERE / "v54-inventory-verification.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("verified_utc", "catalogue_digest", "active_rule_count", "pinned_source_drift")}))


if __name__ == "__main__":
    main()
