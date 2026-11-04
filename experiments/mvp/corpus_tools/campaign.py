"""Eight resumable single-core shards; admit each worker in the fleet ledger.

APPROXIMATION: counts target measured prompt + mandatory-placement token lower
bounds on the frozen test corpus. Actual checked gold completions may include
extra moves; the final SFT builder must remeasure full training token shares.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from build import CORPUS, POLICY, RUNGS, atomic_json, run, sha256, specs_for, booking_settings

# APPROXIMATION: rung counts use measured prompts plus mandatory-place token
# lower bounds; checked gold completion lengths must be remeasured later.


def prepare(allocation_path, root, *, booking_slack=None, booking_mass_fraction=None,
            ship_settings=None, small_train=None, small_val=None, migrate=False):
    allocation_path = Path(allocation_path)
    measurement = json.loads(allocation_path.read_text())
    allocation = dict(measurement["allocation"])
    root = Path(root)
    settings = booking_settings(booking_slack, booking_mass_fraction, ship_settings)
    resized = small_train is not None or small_val is not None
    if resized and (small_train is None or small_val is None or not settings):
        raise ValueError("D84 resize requires small_train, small_val and qualified ship settings")
    if resized and (small_train < 0 or small_val < 0):
        raise ValueError("small target counts must be nonnegative")
    inventories = {}
    for split in ("train", "val"):
        counts = dict(allocation[f"{split}_counts_by_rung"])
        if resized:
            counts.update(small=small_train if split == "train" else small_val, medium=0)
            existing_request = root / f"{split}.requested.json"
            if existing_request.exists():
                tiny = [s for s in json.loads(existing_request.read_text())["specs"] if s["rung"] == "tiny"]
            else:
                tiny = specs_for(split, {"tiny": counts.get("tiny", 0)})
            counts["tiny"] = len(tiny)
            ships = [v for v in ("feeder_500", "feeder_800", "feeder_1700") if v in settings]
            non_tiny = []
            base = {"train": 30_100_000, "val": 40_100_000}[split]
            for i in range(counts["small"]):
                ship_index = i % len(ships)
                # Alternating odd family slots yield equal global family counts.
                template = specs_for(split, {"small": 1})[0]
                non_tiny.append({**template, "vessel": ships[ship_index],
                    "generator": ("random_yard", "simulated_yard")[(i // len(ships) + ship_index) % 2],
                    "seed": base + i, "port": "laem_chabang_like" if i % 4 < 2 else "singapore_like",
                    "noise": (0.2, 0.5, 0.8)[(i // (2 * len(ships))) % 3], **settings[ships[ship_index]]})
            inventories[split] = tiny + non_tiny
        else:
            inventories[split] = specs_for(split, counts)
            if settings:
                base = {"train": 30_000_000, "val": 40_000_000}[split]
                inventories[split] = [s if s["rung"] == "tiny" else
                    {**s, "seed": base + (s["seed"] - {"train": 10_000_000, "val": 20_000_000}[split]),
                     **settings[s["vessel"]]} for s in inventories[split]
                    if s["rung"] == "tiny" or s["vessel"] in settings]
        if settings and not migrate:
            raise ValueError("new slack inventory requires explicit v2 migration")
        allocation[f"{split}_counts_by_rung"] = counts
        request_path = root / f"{split}.requested.json"
        if request_path.exists():
            existing = json.loads(request_path.read_text())
            if existing["specs"] != inventories[split]:
                if not migrate or not settings:
                    raise ValueError(f"refusing to replace an admitted inventory: {request_path}")
                old_tiny = [s for s in existing["specs"] if s["rung"] == "tiny"]
                new_tiny = [s for s in inventories[split] if s["rung"] == "tiny"]
                if old_tiny != new_tiny:
                    raise ValueError("v2 must preserve tiny inventory exactly")
                archive = root / "archive/v1"
                archive.mkdir(parents=True, exist_ok=True)
                saved_request = archive / request_path.name
                if not saved_request.exists():
                    shutil.copy2(request_path, saved_request)
                archive_inventory = archive / f"{split}-superseded-v1.hashes.json"
                if not archive_inventory.exists():
                    paths = [p for rung in ("small", "medium")
                             for p in (root / split / rung).rglob("*") if p.is_file()]
                    atomic_json(archive_inventory, dict(split=split, from_version="v1", to_version="v2",
                        booking_slack=booking_slack, ship_settings=settings,
                        hashes={str(p.relative_to(root)): sha256(p) for p in sorted(paths)}))
                for rung in ("small", "medium"):
                    old = root / split / rung
                    target = archive / split / rung
                    if old.exists():
                        if target.exists():
                            raise ValueError(f"archive collision: {target}")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        old.rename(target)
        atomic_json(request_path, dict(split_policy=POLICY,
            counts=counts, specs=inventories[split], token_allocation_source=str(allocation_path),
            token_allocation_sha256=sha256(allocation_path), booking_slack=booking_slack,
            booking_mass_fraction=booking_mass_fraction, ship_settings=settings,
            corpus_version="v2" if settings else "v1", resize_decision="D84" if resized else None))
    if resized:
        # APPROXIMATION: D84 explicit demand counts supersede v1 token sizing.
        # Lighter new bookings need new gold lengths; old token shares are only
        # historical measurements, never estimates of this resized corpus.
        historical = {key: allocation.pop(key) for key in
            ("count_granularity", "counts_by_rung", "estimated_token_shares", "estimated_tokens_by_rung",
             "target_token_shares", "method", "total_instances") if key in allocation}
        allocation.update(historical_v1_token_sizing=historical,
            counts_by_rung={r: sum(allocation[f"{s}_counts_by_rung"][r] for s in ("train", "val")) for r in RUNGS},
            total_instances=sum(len(specs) for specs in inventories.values()),
            method="D84 explicit qualified-small counts; preserved accepted tiny",
            estimated_token_shares=None, estimated_tokens_by_rung=None,
            limitation="new booking distribution and checked gold completion token lengths unmeasured")
    atomic_json(root / "token_allocation.json", dict(
        measurement_path=str(allocation_path), measurement_sha256=sha256(allocation_path),
        allocation=allocation,
        approximation="measured prompt plus mandatory-placement lower bound; gold completion lengths pending"))
    return inventories


def jobs(root):
    inventories = {s: json.loads((root / f"{s}.requested.json").read_text())["specs"]
                   for s in ("train", "val")}
    # D84: publish fitting shards first so checked-gold workers can stream them.
    # Every instance remains independently resumable.
    result = []
    for split in ("train", "val"):
        groups = {r: [s for s in inventories[split] if s["rung"] == r] for r in RUNGS}
        for i in range(max(map(len, groups.values()))):
            for rung in RUNGS:
                if i < len(groups[rung]):
                    result.append(groups[rung][i])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--allocation", type=Path, default=Path(__file__).with_name("token_measurements.json"))
    parser.add_argument("--root", type=Path, default=CORPUS)
    parser.add_argument("--worker-index", type=int)
    parser.add_argument("--worker-count", type=int, default=8)
    parser.add_argument("--tiny-only", action="store_true", help="D79 restart: generate only admitted tiny rows")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--booking-slack", type=float, help="placeholder; adopted API required before small/medium generation")
    parser.add_argument("--booking-mass-fraction", type=float)
    parser.add_argument("--ship-settings", help="JSON mapping or JSON file of per-ship booking controls")
    parser.add_argument("--small-train", type=int)
    parser.add_argument("--small-val", type=int)
    args = parser.parse_args()
    settings = booking_settings(args.booking_slack, args.booking_mass_fraction, args.ship_settings)
    assert 1 <= args.workers <= 4
    if args.prepare:
        counts = prepare(args.allocation, args.root, booking_slack=args.booking_slack,
                         booking_mass_fraction=args.booking_mass_fraction, ship_settings=settings or None,
                         small_train=args.small_train, small_val=args.small_val, migrate=bool(settings))
        print(json.dumps({split: len(specs) for split, specs in counts.items()}))
    if args.worker_index is not None:
        assert 0 <= args.worker_index < args.worker_count <= 8
        selected = jobs(args.root)
        if args.tiny_only:
            selected = [s for s in selected if s["rung"] == "tiny"]
        elif not settings:
            parser.error("small/medium generation is paused; use --tiny-only or the adopted --booking-slack setting")
        if settings and not args.tiny_only:
            if any(s["vessel"] not in settings or any(s.get(k) != value for k, value in settings[s["vessel"]].items())
                   for s in selected if s["rung"] != "tiny"):
                parser.error("inventory setting differs; prepare v2 with regenerate.py first")
        run(selected[args.worker_index::args.worker_count], workers=args.workers, root=args.root)
    elif args.tiny_only:
        selected = [s for s in jobs(args.root) if s["rung"] == "tiny"]
        run(selected, workers=args.workers, root=args.root)
    elif not args.prepare:
        parser.error("use --prepare or --worker-index")


if __name__ == "__main__":
    main()
