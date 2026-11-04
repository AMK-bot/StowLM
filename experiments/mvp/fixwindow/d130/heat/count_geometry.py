"""Static optimistic E8 capacity enumeration on SHOWCASE only; no solver imports.

APPROXIMATION: count every possible supporting-height combination drawn from
declared cargo heights, ignoring inventory, weights, securing, visibility,
support ordering, DG, yard and hydrostatics. This overstates usable capacity.
No positive count certifies a complete plan or an individually mask-legal move.
"""
from collections import Counter
from hashlib import sha256
import itertools
import json
import math
from pathlib import Path
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
INSTANCE = ROOT / "experiments/mvp/corpus/showcase/feeder_800-small.json"
EXPECTED = "d2453d9d2055f306fd6cbcea461fdb3543dd754e36af54eb5b9f218bccaa7694"
assert sha256(INSTANCE.read_bytes()).hexdigest() == EXPECTED
world = json.loads(INSTANCE.read_bytes())
vessel = world["vessel"]
cells = {c["id"]: c for c in vessel["cells"]}
stacks = {s["id"]: s for s in vessel["stacks"]}
catalogue_path = ROOT / "src/stowage/rules/catalogue.toml"
catalogue = tomllib.loads(catalogue_path.read_text())
clearance = next(r for r in catalogue["rules"] if r["id"] == "expert.e8.away_from_heat")["parameters"]["minimum_clearance"]["value"]
assert not world["rule_overrides"]  # This fixed instance has no overrides.
heights = sorted({b["height_m"] for b in world["containers"]})
heat_boxes = [b for b in world["containers"] if b["id"] in world["load_list"]
              and "away_from_heat" in b["special_stowage"]]
assert len(heat_boxes) == 1
heat_box = heat_boxes[0]
sources = [tuple((h[f"{axis}_min_m"], h[f"{axis}_max_m"]) for axis in "xyz")
           for h in vessel["heat_sources"]]


def e8_clear(envelope):
    for source in sources:
        overlap = all(a[0] < b[1] - 1e-9 and b[0] < a[1] - 1e-9
                      for a, b in zip(envelope, source))
        distance = math.sqrt(sum(max(a[0] - b[1], b[0] - a[1], 0) ** 2
                                 for a, b in zip(envelope, source)))
        if overlap or distance + 1e-9 < clearance:
            return False
    return True


records = []
for length, metres in ((20, 6.058), (40, 12.192)):
    positions = [(cid, (cell,)) for cid, cell in cells.items()] if length == 20 else [
        (f"{int(cid[:3])+1:03d}{cid[3:]}", (cell, cells[f"{int(cid[:3])+2:03d}{cid[3:]}"]))
        for cid, cell in cells.items()
        if f"{int(cid[:3])+2:03d}{cid[3:]}" in cells]
    for slot, physical in positions:
        if not all(c[f"accepts_{length}"] for c in physical):
            continue
        columns = [stacks[c["stack_id"]] for c in physical]
        ordinals = [s["cell_ids"].index(c["id"]) for s, c in zip(columns, physical)]
        assert len(set(ordinals)) == 1
        assert len({s["base_z_m"] for s in columns}) == 1
        below = ordinals[0]
        x = sum(c["lcg_m"] for c in physical) / len(physical)
        y = sum(c["tcg_m"] for c in physical) / len(physical)
        legal_heights = []
        for support in itertools.product(heights, repeat=below):
            total = sum(support) + heat_box["height_m"]
            if total > min(s["max_height_m"] for s in columns) + 1e-9:
                continue
            floor = columns[0]["base_z_m"] + sum(support)
            envelope = ((x - metres / 2, x + metres / 2), (y - 2.438 / 2, y + 2.438 / 2),
                        (floor, floor + heat_box["height_m"]))
            if e8_clear(envelope):
                legal_heights.append({"floor_m": floor, "support_heights_m": list(support)})
        records.append({"slot": slot, "length_ft": length, "on_deck": columns[0]["on_deck"],
                        "ordinal": below + 1, "e8_optimistically_available": bool(legal_heights),
                        "witness": legal_heights[0] if legal_heights else None})

summary = []
for length in (20, 40):
    for deck in (False, True):
        rows = [r for r in records if r["length_ft"] == length and r["on_deck"] == deck]
        summary.append({"length_ft": length, "on_deck": deck, "physical_positions": len(rows),
                        "optimistic_e8_positions": sum(r["e8_optimistically_available"] for r in rows),
                        "available_by_ordinal": dict(Counter(r["ordinal"] for r in rows
                                                         if r["e8_optimistically_available"]))})

manifest_path = ROOT / "experiments/mvp/corpus/manifest.json"
manifest = json.loads(manifest_path.read_bytes())
metadata = manifest["files"]
heldout = [r for r in metadata if r.get("split") in {"test", "val"} or r["path"].startswith(("test/", "val/"))]
audit_path = INSTANCE.with_suffix(".audit.json")
audit = json.loads(audit_path.read_bytes())
result = {
    "instance_sha256": EXPECTED,
    "catalogue_sha256": sha256(catalogue_path.read_bytes()).hexdigest(),
    "manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
    "audit_sha256": sha256(audit_path.read_bytes()).hexdigest(),
    "heat_target": {k: heat_box[k] for k in ("id", "length_ft", "height_m", "gross_mass_kg", "dg")},
    "clearance_m": clearance, "cargo_height_types_m": heights,
    "counting_is_optimistic_not_a_complete_plan": True, "summary": summary, "positions": records,
    "heldout_metadata_only": {
        "rows_examined": len(heldout),
        "same_instance_sha256": [r["path"] for r in heldout if r.get("sha256") == EXPECTED],
        "same_seed": [r["path"] for r in heldout if r.get("seed") == audit["config"]["seed"]],
        "proposed_replacement_seed_range": [29100100, 29100109],
        "replacement_seed_collisions_all_manifest_splits": [r["path"] for r in metadata
            if 29100100 <= (r.get("seed") or -1) <= 29100109],
        "test_or_val_instance_bodies_opened": False,
        "caveat": "File SHA differs after metadata changes; seed disjointness and file hashes do not prove semantic cargo identity disjointness.",
    },
    "old_recipe": audit["config"],
}
(HERE / "geometry-counts.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({"summary": summary, "heldout_metadata_only": result["heldout_metadata_only"]}, indent=2))
