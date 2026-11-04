"""Prepare or execute the adopted-slack v2 corpus regeneration.

The default is a read-only dry run. --execute is for the leader's GO: stage
and validate evaluation/presentation replacements before publishing, then run
the resumable small/medium fitting campaign. Tiny test and the hero stay pinned.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter

import build
import freeze
import showcase


def read(path):
    return json.loads(Path(path).read_text())


def seed_inventory(root):
    """Reserve every requested train/val seed, including unfinished shards."""
    reserved = set()
    for split in ("train", "val"):
        path = root / f"{split}.requested.json"
        if path.exists():
            reserved.update(spec["seed"] for spec in read(path)["specs"])
    showcase_root = root / "archive/v1/showcase"
    if not showcase_root.exists():
        showcase_root = root / "showcase"
    for path in showcase_root.glob("*.audit.json"):
        row = read(path)
        reserved.add(row["config"]["seed"])
        reserved.update(item["seed"] for item in row.get("seed_selection", []))
    return reserved


QUALIFIED = {"feeder_500": {"booking_slack": .25, "booking_mass_fraction": .55},
             "feeder_800": {"booking_slack": .25, "booking_mass_fraction": .45}}


def pending_rows():
    return [{"split": "test", "ship": "feeder_1700", "rung": "small", "family": family, "count": count}
            for family, count in zip(build.FAMILIES, (3, 2))] + [
            {"split": "showcase", "ship": "feeder_1700", "rung": "small", "family": "random_yard", "count": 1}]


def normalize_settings(setting=None, mass_fraction=None, ship_settings=None):
    if ship_settings is not None:
        result = build.booking_settings(setting, mass_fraction, ship_settings)
    elif mass_fraction is None and setting == .25:
        result = deepcopy(QUALIFIED)
    else:
        result = {ship: {"booking_slack": setting, "booking_mass_fraction": mass_fraction}
                  for ship in QUALIFIED}
    assert set(result) == set(QUALIFIED), "D84 only qualifies feeder_500 and feeder_800; feeder_1700 is pending"
    assert result == QUALIFIED, "D84 settings must match the measured qualified settings"
    return result


def plan(root, setting=None, max_seeds=10, *, ship_settings=None, mass_fraction=None):
    settings = normalize_settings(setting, mass_fraction, ship_settings)
    original = root / "archive/v1/test.requested.json"
    inventory = read(original if original.exists() else root / "test.requested.json")
    original_specs = inventory["specs"]
    tiny = deepcopy([s for s in original_specs if s["rung"] == "tiny"])
    assert len(tiny) == 15
    small = deepcopy([s for s in original_specs if s["rung"] == "small" and s["vessel"] in settings])
    assert Counter(s["vessel"] for s in small) == Counter({ship: 5 for ship in settings})
    # APPROXIMATION: D84 excludes unqualified feeder_1700 and all medium draws;
    # pending rows preserve the deferred five-ship slots without inventing data.
    for index, spec in enumerate(small):
        spec.update(seed=28_100_000 + index, **settings[spec["vessel"]])
    specs = tiny + small
    assert Counter(s["generator"] for s in small) == Counter(dict.fromkeys(build.FAMILIES, 5))
    all_seeds = [s["seed"] for s in specs]
    assert max_seeds <= 100, "showcase row seed ranges must not overlap"
    showcase_seeds = {29_000_000 + row * 100 + offset for row in range(2) for offset in range(max_seeds)}
    assert len(set(all_seeds)) == len(all_seeds), "duplicate test seeds"
    assert not set(all_seeds) & seed_inventory(root), "test seeds overlap fitting/showcase"
    assert not showcase_seeds & (seed_inventory(root) | set(all_seeds)), "showcase seed collision"
    inventory.update(specs=specs, counts={"tiny": 15, "small": 10, "medium": 0},
                     revision="v2", ship_settings=settings, pending=pending_rows(),
                     previous_revision="v1", preserved_tiny_count=15,
                     medium_status="excluded_D84", status="partial_qualified")
    return inventory


def api_status(settings):
    from stowage.generate.instances import generate_simulated_instance
    from stowage.generate.instances_v2_random import generate_random_v2
    if not isinstance(settings, dict):
        settings = normalize_settings(settings)
    result = {}
    for producer in (generate_random_v2, generate_simulated_instance):
        try:
            result[producer.__name__] = {ship: build.booking_slack_kwargs(producer,
                row["booking_slack"], row["booking_mass_fraction"]) for ship, row in settings.items()}
        except (ValueError, RuntimeError, TypeError) as error:
            result[producer.__name__] = {"unavailable": str(error)}
    return result


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        assert build.sha256(source) == build.sha256(target), f"immutable copy mismatch: {target}"
    else:
        shutil.copy2(source, target)


def archive_v1(root):
    archive = root / "archive/v1"
    archive.mkdir(parents=True, exist_ok=True)
    # A marker makes interrupted archive creation resumable without overwriting
    # a frozen v1 file with a later v2 replacement.
    if not (archive / "evaluation-archive.complete.json").exists():
        for directory in ("test", "showcase"):
            for path in (root / directory).rglob("*"):
                if path.is_file():
                    copy_file(path, archive / path.relative_to(root))
        for name in ("test.requested.json", "manifest.json"):
            copy_file(root / name, archive / name)
        build.atomic_json(archive / "evaluation-archive.complete.json", {
            "format": "stowlm-mvp-v1-evaluation-archive", "hashes": {
                str(p.relative_to(archive)): build.sha256(p) for p in sorted(archive.rglob("*")) if p.is_file()}})
    return archive


def assert_preserved(root, stage, inventory):
    for spec in inventory["specs"]:
        if spec["rung"] == "tiny":
            for suffix in (".json", ".audit.json"):
                old = build.shard_path(spec, root).with_suffix(suffix)
                new = build.shard_path(spec, stage).with_suffix(suffix)
                assert build.sha256(old) == build.sha256(new), f"tiny changed: {new}"
    for suffix in (".json", ".audit.json", ".selection.json"):
        name = f"feeder_1700-full{suffix}"
        assert build.sha256(root / "showcase" / name) == build.sha256(stage / "showcase" / name)


def stage_evaluation(root, inventory, setting, workers, max_seeds):
    archive = archive_v1(root)
    stage = root / ".regenerate-v2-d84"
    stage.mkdir(exist_ok=True)
    build.atomic_json(stage / "test.requested.json", inventory)
    for spec in inventory["specs"]:
        if spec["rung"] == "tiny":
            for suffix in (".json", ".audit.json"):
                copy_file(build.shard_path(spec, archive).with_suffix(suffix),
                          build.shard_path(spec, stage).with_suffix(suffix))
    for suffix in (".json", ".audit.json", ".selection.json"):
        name = f"feeder_1700-full{suffix}"
        copy_file(archive / "showcase" / name, stage / "showcase" / name)
    build.run([s for s in inventory["specs"] if s["rung"] != "tiny"], workers=workers, root=stage)
    records = []
    started = perf_counter()
    for index, (vessel, size) in enumerate([("feeder_500", "small"), ("feeder_800", "small"), ("feeder_1700", "full")]):
        records.append(showcase.generate_row(stage / "showcase", vessel, size,
            29_000_000 + index * 100, max_seeds=max_seeds, booking_slack=inventory["ship_settings"].get(vessel, {}).get("booking_slack"),
            booking_mass_fraction=inventory["ship_settings"].get(vessel, {}).get("booking_mass_fraction"),
            preserve_existing=size == "full"))
    showcase.write_manifest(stage / "showcase", records, generation_wall_s=perf_counter()-started,
                            ship_settings=inventory["ship_settings"], pending=pending_rows(), revision=2)
    assert_preserved(archive, stage, inventory)
    registry_path = root / "releases.json"
    if registry_path.exists():
        registry = read(registry_path)
        registry.update(current_release="v2", status="partial_qualified",
                        ship_settings=inventory["ship_settings"], pending=pending_rows(), medium_status="excluded_D84")
        build.atomic_json(stage / "releases.json", registry)
    # Validate producer/D58 evidence, loadability, balance and all seed roots.
    freeze.assemble_manifest(stage, partial=True)
    return stage


def publish(root, stage, setting, inventory):
    """Each directory publication is atomic; a journal enables crash rollback."""
    journal = root / ".v2-publish.json"
    rollback = root / ".v2-rollback"
    if journal.exists():
        raise RuntimeError("unfinished publish journal: rerun without generation after recover_publish")
    # A prior publication may have completed before cleanup was interrupted.
    shutil.rmtree(rollback, ignore_errors=True)
    for directory in ("test", "showcase"):
        for prefix in (".incoming-v2-", ".old-v2-"):
            shutil.rmtree(root / f"{prefix}{directory}", ignore_errors=True)
    rollback.mkdir(exist_ok=True)
    release_path = root / "releases.json"
    release = read(release_path) if release_path.exists() else {"format": "stowlm-mvp-releases-v1"}
    replacement_hashes = {str(p.relative_to(stage)): build.sha256(p)
                          for directory in ("test", "showcase") for p in (stage / directory).rglob("*") if p.is_file()}
    release.update(current_release="v2", status="partial_qualified", ship_settings=inventory["ship_settings"],
                   pending=pending_rows(), medium_status="excluded_D84",
                   replacements={"test": {"from": "v1", "to": "v2", "seed_base": 28_000_000},
                                 "showcase": {"from": "v1", "to": "v2", "seed_base": 29_000_000}},
                   replacement_hashes=replacement_hashes, archive="archive/v1")
    release.setdefault("change_log", []).append({
        "from": "v1", "to": "v2", "status": "partial_qualified", "ship_settings": inventory["ship_settings"],
        "reason": "D84: replaced ten qualified small test and two small port-call showcases; preserved 15 tiny test and full hero bytes. Five feeder_1700 small test and one showcase pending; medium excluded.",
        "previous_hashes": "archive/v1/evaluation-archive.complete.json",
        "replacement_hashes": "replacement_hashes"})
    targets = ("test", "showcase", "test.requested.json", "releases.json", "manifest.json")
    state = {"targets": list(targets), "original_exists": {n: (root/n).exists() for n in targets},
             "backup_complete": False}
    build.atomic_json(journal, state)
    try:
        for name in targets:
            source = root / name
            if source.exists():
                if source.is_dir():
                    shutil.copytree(source, rollback / name)
                else:
                    shutil.copy2(source, rollback / name)
        state["backup_complete"] = True
        build.atomic_json(journal, state)
        for directory in ("test", "showcase"):
            incoming = root / f".incoming-v2-{directory}"
            shutil.copytree(stage / directory, incoming)
            (root / directory).rename(root / f".old-v2-{directory}")
            incoming.rename(root / directory)
        build.atomic_json(root / "test.requested.json", inventory)
        build.atomic_json(release_path, release)
        freeze.write_freeze(root, partial=True)
        freeze.verify_freeze(root)
    except BaseException:
        recover_publish(root)
        raise
    journal.unlink()
    shutil.rmtree(rollback)
    for directory in ("test", "showcase"):
        shutil.rmtree(root / f".old-v2-{directory}")


def recover_publish(root):
    journal = root / ".v2-publish.json"
    if not journal.exists():
        return
    rollback = root / ".v2-rollback"
    state = read(journal)
    if not state["backup_complete"]:
        shutil.rmtree(rollback, ignore_errors=True)
        journal.unlink()
        return
    for name in state["targets"]:
        backup = rollback / name
        if backup.exists():
            target = root / name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            backup.rename(target)
        elif not state["original_exists"][name] and (root / name).exists():
            (root / name).unlink()
    for directory in ("test", "showcase"):
        for prefix in (".incoming-v2-", ".old-v2-"):
            path = root / f"{prefix}{directory}"
            if path.exists():
                shutil.rmtree(path)
    shutil.rmtree(rollback, ignore_errors=True)
    journal.unlink()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--root", type=Path, default=build.CORPUS)
    parser.add_argument("--booking-slack", type=float, help="scalar slack (0.25 defaults to qualified D84 per-ship masses)")
    parser.add_argument("--booking-mass-fraction", type=float)
    parser.add_argument("--ship-settings", help="JSON object or path to JSON file mapping ships to producer settings")
    parser.add_argument("--small-train", type=int, default=2700)
    parser.add_argument("--small-val", type=int, default=70)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-showcase-seeds", type=int, default=100)
    parser.add_argument("--allocation", type=Path, default=Path(__file__).with_name("token_measurements.json"))
    parser.add_argument("--dry-run", action="store_true", help="read-only inventory/API check (default)")
    parser.add_argument("--execute", action="store_true", help="run only after leader GO and ledger admission")
    parser.add_argument("--evaluation-only", action="store_true", help="publish test/showcase; omit fitting generation")
    args = parser.parse_args()
    registry = args.root / 'releases.json'
    if registry.exists() and read(registry).get('current_release') == 'v2.1':
        parser.error('D90 v2.1 is frozen; use amend_v21.py / run_d90.py; the D84 rebuild cannot replace it')
    assert 1 <= args.workers <= 4
    assert 1 <= args.max_showcase_seeds <= 100
    raw_settings = None
    if args.ship_settings:
        candidate = Path(args.ship_settings)
        raw_settings = read(candidate) if not args.ship_settings.lstrip().startswith("{") else json.loads(args.ship_settings)
    settings = normalize_settings(args.booking_slack, args.booking_mass_fraction, raw_settings)
    if args.execute and args.dry_run:
        parser.error("--execute and --dry-run are mutually exclusive")
    if args.execute:
        recover_publish(args.root)
    inventory = plan(args.root, args.booking_slack, args.max_showcase_seeds, ship_settings=settings)
    apis = api_status(settings)
    print(json.dumps({"execute": args.execute, "ship_settings": settings, "api": apis,
                      "test_counts": dict(Counter(s["rung"] for s in inventory["specs"])),
                      "test_new_seeds": [s["seed"] for s in inventory["specs"] if s["rung"] != "tiny"],
                      "preserved": "15 tiny test instances + feeder_1700-full hero",
                      "showcase_seed_starts": [29_000_000, 29_000_100], "pending": pending_rows()}, sort_keys=True), flush=True)
    if not args.execute:
        return
    assert not any("unavailable" in entry for entry in apis.values()), "adopted producer API is not available"
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    assert rule_freeze_v1_enabled() is False
    import campaign
    # Fail before generation if the migration tooling has not landed yet.
    assert "ship_settings" in inspect.signature(campaign.prepare).parameters, "campaign slack migration API pending"
    stage = stage_evaluation(args.root, inventory, args.booking_slack, args.workers, args.max_showcase_seeds)
    # An exhausted test/showcase draw must leave active fitting inventories and
    # their v1 data intact. Migrate fitting only after staged evaluation passes.
    campaign.prepare(args.allocation, args.root, ship_settings=settings,
                     small_train=args.small_train, small_val=args.small_val, migrate=True)
    publish(args.root, stage, args.booking_slack, inventory)
    if not args.evaluation_only:
        subprocess.run([sys.executable, str(Path(__file__).with_name("small_batch.py")),
                        "--root", str(args.root), "--workers", str(args.workers)], check=True)
        freeze.write_freeze(args.root, partial=True)
        freeze.verify_freeze(args.root)


if __name__ == "__main__":
    main()
