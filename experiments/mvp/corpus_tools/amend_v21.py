"""D90 additive evaluation amendment; dry run unless --execute is supplied.

Existing evaluation instance/audit bytes are immutable. New draws are staged and
resumable; metadata backups and an original-byte inventory precede publication.
No loading plan is certified here: the generator/D58 gates certify instances.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from time import perf_counter

import build
import freeze
import showcase

SETTING = {"booking_slack": .35, "booking_mass_fraction": .45}


def read(path):
    return json.loads(Path(path).read_text())


def plan(root, max_seeds=100):
    assert 1 <= max_seeds <= 100
    inventory = read(root / "test.requested.json")
    old = [s for s in inventory["specs"] if s["vessel"] != "feeder_1700" or s["rung"] != "small"]
    assert len(old) == 25 and Counter(s["rung"] for s in old) == {"tiny": 15, "small": 10}
    # Keep the original five-per-ship test design, with its alternating odd
    # family slot. D90 retains a 3/2 random/simulated split for this final ship.
    templates = [s for s in build.specs_for("test", {"small": 15}) if s["vessel"] == "feeder_1700"]
    assert len(templates) == 5
    for index, spec in enumerate(templates):
        spec.update(seed=28_100_010 + index, generator=build.FAMILIES[index % 2], **SETTING)
    reserved = {s["seed"] for s in old}
    for split in ("train", "val"):
        path = root / f"{split}.requested.json"
        if path.exists():
            reserved.update(s["seed"] for s in read(path)["specs"])
    for path in (root / "showcase").glob("*.audit.json"):
        audit = read(path)
        if audit["config"].get("vessel") == "feeder_1700" and audit["config"].get("size") == "small":
            continue  # This amendment's own resumed showcase.
        reserved.add(audit["config"]["seed"])
        reserved.update(s["seed"] for s in audit.get("seed_selection", []))
    proposed = {s["seed"] for s in templates}
    showcase_seeds = set(range(29_000_200, 29_000_200 + max_seeds))
    assert not proposed & reserved, "new test seeds overlap reserved seeds"
    assert not showcase_seeds & (reserved | proposed), "new showcase seeds overlap reserved seeds"
    existing = [s for s in inventory["specs"] if s not in old]
    assert not existing or existing == templates, "conflicting amendment test inventory"
    settings = deepcopy(inventory["ship_settings"])
    settings["feeder_1700"] = SETTING.copy()
    inventory.update(specs=old + templates, counts={"tiny": 15, "small": 15, "medium": 0},
        revision="v2.1", previous_revision="v2", status="complete", pending=[], ship_settings=settings)
    return inventory, templates


def preserve(root):
    archive = root / "archive/v2-before-D90"
    archive.mkdir(parents=True, exist_ok=True)
    pin_path = archive / "preserved-evaluation.json"
    if not pin_path.exists():
        pins = {}
        for split in ("test", "showcase"):
            for path in (root / split).rglob("*.json"):
                if path.name.endswith(".audit.json") or path.with_suffix(".audit.json").exists():
                    pins[str(path.relative_to(root))] = build.sha256(path)
        assert len(pins) == 56, "expected original 25 test + 3 showcase instance/audit pairs"
        build.atomic_json(pin_path, pins)
        for name in ("test.requested.json", "releases.json", "manifest.json", "showcase/showcase-manifest.json",
                     "test/SHA256SUMS", "showcase/SHA256SUMS"):
            source = root / name
            if source.exists():
                target = archive / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    pins = read(pin_path)
    assert all(build.sha256(root / name) == digest for name, digest in pins.items()), "original evaluation bytes changed"
    return pins


def copy_addition(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        assert build.sha256(source) == build.sha256(target), f"published addition differs: {target}"
    else:
        temporary = target.with_name('.' + target.name + '.D90.partial')
        shutil.copy2(source, temporary)
        temporary.replace(target)


def execute(root, *, workers=1, max_seeds=100, report_path=None):
    started = perf_counter()
    inventory, specs = plan(root, max_seeds)
    pins = preserve(root)
    stage = root / ".amend-v2.1"
    for spec in specs:
        path = build.shard_path(spec, root)
        if path.with_suffix(".audit.json").exists():
            build.validate_shard(path)
            for source in (path, path.with_suffix(".audit.json")):
                copy_addition(source, stage / source.relative_to(root))
    build.run(specs, root=stage, workers=workers)
    for spec in specs:
        build.validate_shard(build.shard_path(spec, stage))
    (stage / "showcase").mkdir(parents=True, exist_ok=True)
    showcase.generate_row(stage / "showcase", "feeder_1700", "small", 29_000_200,
        max_seeds=max_seeds, **SETTING)
    preserve(root)
    additions = {}
    for source in sorted(stage.rglob("*.json")):
        relative = source.relative_to(stage)
        if relative.parts[0] not in {"test", "showcase"} or source.name.endswith(".failure.json"):
            continue
        copy_addition(source, root / relative)
        additions[str(relative)] = build.sha256(source)
    release = read(root / "releases.json")
    release.update(current_release="v2.1", status="complete", pending=[], amendment_ship="feeder_1700",
                   ship_settings=inventory["ship_settings"])
    release.setdefault("replacement_hashes", {}).update(additions)
    if not any(row.get("to") == "v2.1" for row in release["change_log"]):
        release["change_log"].append({"from": "v2", "to": "v2.1", "status": "complete",
            "reason": "D90: add five qualified feeder_1700 small tests and one small Laem Chabang showcase; preserve original 25 test and three showcase instance/audit bytes.",
            "ship_settings": {"feeder_1700": SETTING}, "added_hashes": additions})
    build.atomic_json(root / "test.requested.json", inventory)
    build.atomic_json(root / "releases.json", release)
    records = [read(path) for path in sorted((root / "showcase").glob("*.audit.json"))]
    showcase.write_manifest(root / "showcase", records, generation_wall_s=sum(
        r.get("selection_total_seconds", r["elapsed_generation_s"]) for r in records),
        ship_settings=inventory["ship_settings"], pending=[], revision="v2.1")
    manifest = freeze.write_freeze(root, partial=True)
    freeze.verify_freeze(root)
    assert all(build.sha256(root / name) == digest for name, digest in pins.items())
    hashes = {r["path"]: r["sha256"] for r in manifest["files"]
              if r["split"] in {"test", "showcase"} and r["rung"] == "small"}
    report = {"current_release": "v2.1", "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_counts": manifest["active_counts"], "pending_release_slots": [], "hashes": hashes,
        "hash_lists": {split: {"path": str((root / split / "SHA256SUMS").resolve()),
                        "sha256": build.sha256(root / split / "SHA256SUMS")} for split in ("test", "showcase")},
        "ship_settings": inventory["ship_settings"], "amendment_elapsed_s": perf_counter() - started,
        "preserved_original_evaluation_pairs": len(pins) // 2, "added_hashes": additions}
    report_path = report_path or Path(__file__).with_name("EVAL-V2-REPORT.json")
    build.atomic_json(report_path, report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--root", type=Path, default=build.CORPUS)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 5))
    parser.add_argument("--max-showcase-seeds", type=int, default=100)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.execute:
        execute(args.root, workers=args.workers, max_seeds=args.max_showcase_seeds, report_path=args.report)
    else:
        inventory, specs = plan(args.root, args.max_showcase_seeds)
        print(json.dumps({"execute": False, "revision": "v2.1", "additions": specs,
                          "counts": inventory["counts"], "ship_settings": inventory["ship_settings"]}, indent=2))


if __name__ == "__main__":
    main()
