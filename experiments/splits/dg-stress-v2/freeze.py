"""Reproduce amended D70 allocation; never generate, screen or solve instances."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.splits import InstanceSpec, canonical_spec, entries_sha256, summarize_entries, summarize_families

POLICY = {
    "hash_salt": "stowlm-dg-stress-v2:20261027",
    "split": "test",
    "test_family": "dg_stress",
    "selection": "fixed rung, vessel, generator-setting order; no outcome screening",
    "seed_assignment": "2**61 + rung_index*2**32 + candidate_index",
    "small_pair_cohorts": {"flammable_water_reactive": 3, "water_reactive_acid": 3},
}
RUNGS = ("tiny", "small")
VESSELS = ("feeder_500", "feeder_800", "feeder_1700")
SETTINGS = (
    ("laem_chabang_like", 0.2, "flammable_water_reactive"),
    ("singapore_like", 0.5, "water_reactive_acid"),
    ("laem_chabang_like", 0.0, None),
    ("singapore_like", 0.2, None),
    ("laem_chabang_like", 0.5, None),
)


def build_manifest() -> dict:
    entries = []
    for rung_index, rung in enumerate(RUNGS):
        candidate_index = 0
        for vessel in VESSELS:
            for port, blocking_ratio, small_pair in SETTINGS:
                candidate_index += 1
                spec = InstanceSpec(
                    generator="random_yard", rung=rung, vessel=vessel, slice=rung,
                    port=port, noise=None, blocking_ratio=blocking_ratio,
                    seed=2**61 + rung_index * 2**32 + candidate_index,
                    cargo_profile="dg_stress", identity_version="v3",
                    dg_stress_pair=small_pair if rung == "small" else None,
                    n_boxes=None, dense_20ft=False, tiny_dg_enabled=True,
                    without_dg=False, equipment_profile="dry20_dg_stress",
                )
                entries.append({"split": "test", "test_family": "dg_stress", "spec": canonical_spec(spec)})
    return {
        "version": "dg-stress-v2", "identity_version": "v3", "split_seed": 20261027,
        "policy": POLICY, "entries_sha256": entries_sha256(entries),
        "counts": summarize_entries(entries), "family_counts": summarize_families(entries),
        "entries": entries,
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
        print("verified 30 frozen dg-stress-v2 specifications")
    else:
        output.write_text(payload)
        print(f"wrote 30 dg-stress-v2 specifications to {output}")


if __name__ == "__main__":
    main()
