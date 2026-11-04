"""Reproduce D70 amendment 2 allocation; never generate, screen or solve."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.splits import InstanceSpec, canonical_spec, entries_sha256, summarize_entries, summarize_families

POLICY = {
    "hash_salt": "stowlm-dg-stress-v3:20261027",
    "split": "test", "test_family": "dg_stress",
    "selection": "fixed rung, vessel, cell order; no outcome screening",
    "seed_assignment": "2**62 + rung_index*2**32 + candidate_index",
    "small_pair_cohorts": {"flammable_water_reactive": 8, "flammable_solid_flammable_liquid": 8},
    "pair_definitions": {
        "flammable_water_reactive": {"classes": ["3", "4.3"], "un_numbers": ["1263", "1402"]},
        "flammable_solid_flammable_liquid": {"classes": ["4.1", "3"], "un_numbers": ["1333", "1263"]},
    },
}
RUNGS = ("tiny", "small")
VESSELS = ("feeder_500", "feeder_800", "feeder_1700")
TINY_SETTINGS = (
    ("laem_chabang_like", 0.2), ("singapore_like", 0.5),
    ("laem_chabang_like", 0.0), ("singapore_like", 0.2), ("laem_chabang_like", 0.5),
)
SMALL_SETTINGS = tuple(("laem_chabang_like" if index % 2 == 0 else "singapore_like", blocking)
                       for index, blocking in enumerate((0.0, 0.2, 0.5, 0.2, 0.5, 0.0, 0.5, 0.0, 0.2, 0.5)))
# Zero-based cell indices keep each pair on every ship and four specs per port.
PAIR_CELLS = (
    ((0, 3, 6), (1, 4, 7)),
    ((1, 4, 7), (0, 3)),
    ((0, 3), (1, 4, 6)),
)
PAIR_IDS = ("flammable_water_reactive", "flammable_solid_flammable_liquid")


def build_manifest() -> dict:
    entries = []
    for rung_index, rung in enumerate(RUNGS):
        candidate_index = 0
        for vessel_index, vessel in enumerate(VESSELS):
            settings = TINY_SETTINGS if rung == "tiny" else SMALL_SETTINGS
            for cell_index, (port, blocking_ratio) in enumerate(settings):
                candidate_index += 1
                pair = None
                if rung == "small":
                    pair = next((pair_id for pair_id, indices in zip(PAIR_IDS, PAIR_CELLS[vessel_index])
                                 if cell_index in indices), None)
                spec = InstanceSpec(
                    generator="random_yard", rung=rung, vessel=vessel, slice=rung,
                    port=port, noise=None, blocking_ratio=blocking_ratio,
                    seed=2**62 + rung_index * 2**32 + candidate_index,
                    cargo_profile="dg_stress", identity_version="v3", dg_stress_pair=pair,
                    n_boxes=None, dense_20ft=False, tiny_dg_enabled=True,
                    without_dg=False, equipment_profile="dry20_dg_stress",
                )
                entries.append({"split": "test", "test_family": "dg_stress", "spec": canonical_spec(spec)})
    return {
        "version": "dg-stress-v3", "identity_version": "v3", "split_seed": 20261027,
        "policy": POLICY, "entries_sha256": entries_sha256(entries),
        "counts": summarize_entries(entries), "family_counts": summarize_families(entries), "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).with_name("manifest.json")
    payload = json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if output.read_text() != payload:
            raise SystemExit("frozen manifest differs from deterministic reproduction")
        print("verified 45 frozen dg-stress-v3 specifications")
    else:
        output.write_text(payload)
        print(f"wrote 45 dg-stress-v3 specifications to {output}")


if __name__ == "__main__":
    main()
