"""Reproduce specification-only releases; never generate or solve instances.

Use --version v2 for the replacement inventory (the default). Historical v1 is
retained byte-for-byte and can be checked using --version v1 --check.
"""
from __future__ import annotations

import argparse
from itertools import product
import json
from pathlib import Path

from eval.splits import (
    InstanceSpec, canonical_spec, entries_sha256, split_of, summarize_entries,
    summarize_families, test_family,
)

RUNGS = ("tiny", "small", "medium", "full")
TRAIN_COUNTS = dict(zip(RUNGS, (1500, 1500, 2500, 3500), strict=True))
VESSELS = ("feeder_500", "feeder_1000", "feeder_1700")
PORTS = ("laem_chabang_like", "singapore_like", "small_feeder")
FAMILIES = ("in_distribution", "heldout_port", "heldout_noise", "heldout_slice")
OUTPUT = Path(__file__).parent / "v2" / "manifest.json"
LEGACY_POLICY = {
    "hash_salt": "stowlm-splits-v1:20261027", "hash_buckets": 10000,
    "train_below": 9000, "val_below": 9500,
    "heldout_slice": {"vessel": "feeder_1700", "slice": "medium"},
    "heldout_port": "us_west_coast", "heldout_noise": 0.8,
    "selection": "seed ascending; vessel, port, generator-setting product order",
}


def candidates(rung: str, family: str, *, version: str = "v2"):
    """Yield a deterministic stream with unique seeds for every v2 candidate.

    v1 deliberately retains its historical overlapping streams for reproduction.
    v2 separates every family/rung/configuration, including simulated and random
    producers: even older generators that seed cargo before scenario controls
    cannot reuse a draw across selected v2 specs. Train/val/test select from the
    same in-distribution stream, and a seed has one hash-policy partition.
    """
    if version not in ("v1", "v2"):
        raise ValueError("unsupported split release")
    rung_index, family_index = RUNGS.index(rung), FAMILIES.index(family)
    vessels = ("feeder_1700",) if family == "heldout_slice" else VESSELS
    if rung == "medium" and family != "heldout_slice":
        vessels = VESSELS[:2]
    ports = ("us_west_coast",) if family == "heldout_port" else PORTS
    noise = (0.8,) if family == "heldout_noise" else (0.0, 0.2, 0.5)
    settings = [("simulated", n, None) for n in noise]
    if family != "heldout_noise":
        settings += [("random_yard", None, b) for b in (0.0, 0.3, 0.6)]
    combinations = tuple(product(vessels, ports, settings))
    candidate_index = 0
    while True:
        for vessel, port, (generator, noise_value, blocking_ratio) in combinations:
            if version == "v1":
                seed = candidate_index // len(combinations)
                controls = {"identity_version": "v1"}
            else:
                if candidate_index >= 2**32:
                    raise ValueError("exhausted reserved candidate seed range")
                seed = 2**48 + family_index * 2**40 + rung_index * 2**32 + candidate_index
                # None explicitly requests the current D36 regional demand policy;
                # it never means the old persisted ladder's diagnostic box count.
                controls = {"identity_version": "v2", "n_boxes": None,
                            "dense_20ft": False, "tiny_dg_enabled": True,
                            "without_dg": False,
                            "equipment_profile": "dry20_with_dg" if rung == "tiny" else "full_mix"}
            yield InstanceSpec(generator=generator, rung=rung, vessel=vessel,
                               slice=rung, port=port, noise=noise_value,
                               blocking_ratio=blocking_ratio, seed=seed, **controls)
            candidate_index += 1


def build_manifest(version: str = "v2") -> dict:
    if version not in ("v1", "v2"):
        raise ValueError("unsupported split release")
    entries = []
    for rung in RUNGS:
        allocations = [("train", "in_distribution", TRAIN_COUNTS[rung]),
                       ("val", "in_distribution", 25),
                       ("test", "in_distribution", 30 if rung == "medium" else 45),
                       ("test", "heldout_port", 15),
                       ("test", "heldout_noise", 15)]
        if rung == "medium":
            allocations.append(("test", "heldout_slice", 15))
        for split, family, count in allocations:
            selected = 0
            for spec in candidates(rung, family, version=version):
                if split_of(spec) != split:
                    continue
                if split == "test" and test_family(spec) != family:
                    continue
                entries.append({"split": split,
                                "test_family": family if split == "test" else None,
                                "spec": canonical_spec(spec)})
                selected += 1
                if selected == count:
                    break
    if version == "v1":
        policy = LEGACY_POLICY
        identity = {}
    else:
        from eval.splits_inventory import CANONICAL_POLICY_V2
        policy = CANONICAL_POLICY_V2
        identity = {"identity_version": "v2"}
    return {"version": version, **identity, "split_seed": 20261027,
            "policy": policy, "entries_sha256": entries_sha256(entries),
            "counts": summarize_entries(entries),
            "family_counts": summarize_families(entries), "entries": entries}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--version", choices=("v1", "v2"), default="v2")
    args = parser.parse_args()
    manifest = build_manifest(args.version)
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    output = Path(__file__).parent / args.version / "manifest.json"
    if args.check:
        if output.read_text() != payload:
            raise SystemExit("frozen manifest differs from deterministic reproduction")
        print(f"verified {len(manifest['entries'])} frozen {args.version} specifications")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload)
        print(f"wrote {len(manifest['entries'])} {args.version} specifications to {output}")


if __name__ == "__main__":
    main()
