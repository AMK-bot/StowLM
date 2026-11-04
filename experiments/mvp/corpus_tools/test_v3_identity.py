"""Sealed-v3 specification and identity guards; reads metadata, never maps or plans."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

SHIPS = ("feeder_500", "feeder_800", "feeder_1700")
FAMILIES = ("random_yard", "simulated_yard")
SETTINGS = {
    "feeder_500": {"booking_slack": 0.25, "booking_mass_fraction": 0.55},
    "feeder_800": {"booking_slack": 0.25, "booking_mass_fraction": 0.45},
    "feeder_1700": {"booking_slack": 0.35, "booking_mass_fraction": 0.45},
}


def identity(spec):
    return (spec["vessel"], spec.get("rung", spec.get("size")),
            spec["generator"], int(spec["seed"]))


def make_specs(corpus_root, seed_base=187_000_000):
    """Copy v2.1 strata; D187 explicitly expands small from 15 to 30."""
    old = json.loads((Path(corpus_root) / "test.requested.json").read_text())
    tiny = [s for s in old["specs"] if s["rung"] == "tiny"]
    small = [s for s in old["specs"] if s["rung"] == "small"]
    if len(tiny) != 15 or len(small) != 15:
        raise ValueError("Expected frozen v2.1 inventory with 15 tiny and 15 small")
    result = []
    for rung, source in (("tiny", tiny), ("small", small + small)):
        for index, original in enumerate(source):
            spec = deepcopy(original)
            spec.update(split="test-v3", seed=seed_base + (100_000 if rung == "small" else 0) + index)
            if rung == "small":
                spec.update(SETTINGS[spec["vessel"]])
                # Balance each ship's odd five-row family split by reversing its repeat.
                if index >= 15:
                    spec["generator"] = FAMILIES[1 - FAMILIES.index(spec["generator"])]
            result.append(spec)
    counts = Counter((s["rung"], s["vessel"], s["generator"]) for s in result)
    for ship in SHIPS:
        if sum(counts["tiny", ship, family] for family in FAMILIES) != 5:
            raise ValueError(f"Tiny ship imbalance: {ship}")
        if any(counts["small", ship, family] != 5 for family in FAMILIES):
            raise ValueError(f"Small ship/family imbalance: {ship}")
    if len({s["seed"] for s in result}) != 45:
        raise ValueError("Duplicate v3 seed")
    return result


def _metadata(path):
    name = path.name
    return (name.endswith((".requested.json", ".audit.json", ".selection.json", ".failure.json"))
            or "manifest" in name or name == "releases.json")


def _collect(value, seeds, identities, hashes):
    if isinstance(value, list):
        for child in value:
            _collect(child, seeds, identities, hashes)
    elif isinstance(value, dict):
        if isinstance(value.get("seed"), int):
            seeds.add(value["seed"])
            if {"vessel", "generator"} <= value.keys() and ("rung" in value or "size" in value):
                identities.add(identity(value))
        digest = value.get("instance_sha256")
        if isinstance(digest, str):
            hashes.add(digest)
        for key, child in value.items():
            # These are physics/source evidence, never identities; avoid expensive traversal.
            if key not in {"source_files", "physics", "background_last", "producer_report",
                           "source_snapshot", "source_before", "source_after"}:
                if isinstance(child, (dict, list)):
                    _collect(child, seeds, identities, hashes)


def exclusion_inventory(corpus_root, output_root=None):
    """Include requested-but-unemitted seeds and archived/rejected showcase draws."""
    root = Path(corpus_root).resolve()
    excluded = Path(output_root).resolve() if output_root else root / "test-v3"
    seeds, identities, hashes, files = set(), set(), set(), []
    for path in sorted(root.rglob("*.json")):
        if path.is_relative_to(excluded) or not _metadata(path):
            continue
        raw = path.read_bytes()
        payload = json.loads(raw)
        _collect(payload, seeds, identities, hashes)
        files.append({"path": path.relative_to(root).as_posix(),
                      "sha256": hashlib.sha256(raw).hexdigest()})
    return {"seeds": seeds, "identities": identities, "instance_sha256": hashes,
            "proof": {"policy": "metadata_only_all_current_and_archived_inventories_audits_selections",
                      "metadata_file_count": len(files), "unique_excluded_seeds": len(seeds),
                      "unique_excluded_identities": len(identities),
                      "unique_excluded_instance_sha256": len(hashes), "files": files}}


def assert_disjoint(specs, inventory, emitted_hashes=None):
    seeds = [s["seed"] for s in specs]
    ids = [identity(s) for s in specs]
    if len(set(seeds)) != len(seeds) or len(set(ids)) != len(ids):
        raise ValueError("Duplicate fresh identity or seed")
    if set(seeds) & inventory["seeds"]:
        raise ValueError("Fresh seed collides with historical or reserved inventory")
    if set(ids) & inventory["identities"]:
        raise ValueError("Fresh identity collides with historical inventory")
    if emitted_hashes is not None:
        hashes = list(emitted_hashes)
        if len(set(hashes)) != len(hashes):
            raise ValueError("Duplicate fresh map SHA256")
        if set(hashes) & inventory["instance_sha256"]:
            raise ValueError("Fresh map SHA256 collides with historical artifact")
    return True
