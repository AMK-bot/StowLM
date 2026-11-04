"""Pin a complete switch-OFF Python package for the D79 tiny-only campaign.

APPROXIMATION: tiny generation uses this measured source epoch while the opt-in
booking-slack and constructor experiments edit the live tree. All existing D58
and producer screens run, and every emitted audit binds the actual pinned bytes.
This is an input-corpus epoch, not approval of any later loading plan.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil

from build import ROOT, atomic_json, sha256

# APPROXIMATION: tiny inputs use this pinned D79 source epoch while opt-in live
# code evolves; later gold plans still require the then-current full checker.


def files(package):
    return {str(p.relative_to(package)): sha256(p) for p in sorted(package.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json", ".toml"}}


def link_acceleration(target):
    """Preserve the normal qualified-cache lookup paths of relocated modules."""
    created = 0
    for destination, source in ((target / "params", ROOT / "params"),
                                (target / "experiments/linear-model", ROOT / "experiments/linear-model")):
        if source.exists() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source, target_is_directory=True)
            created += 1
    source = ROOT / "experiments/cache/stability"
    cache = target / "experiments/cache/stability"
    for path in source.rglob("*"):
        if path.is_file() and path.suffix in {".npz", ".json", ".toml"} and not path.name.startswith("."):
            destination = cache / path.relative_to(source)
            if not destination.exists() and not destination.is_symlink():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(path)
                created += 1
    # These are existing acceleration artifacts, not alternate checker rules;
    # the public checker still validates each table before dispatching it.
    atomic_json(target / "acceleration-links.json", dict(created_links=created, source_cache=str(source),
        note="Existing qualified artifacts; local tables retained; checker qualification guards unchanged"))


def main():
    source = ROOT / "src/stowage"
    target = Path(__file__).with_name("tiny_epoch")
    pin = target / "source-pin.json"
    if pin.exists():
        manifest = json.loads(pin.read_text())
        assert files(target / "src/stowage") == manifest["source_sha256"]
        link_acceleration(target)
        print(str(target.resolve()))
        return
    partial = target.with_name(f".tiny_epoch.{os.getpid()}.partial")
    for _ in range(10):
        shutil.rmtree(partial, ignore_errors=True)
        before = files(source)
        shutil.copytree(source, partial / "src/stowage", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if before == files(source) == files(partial / "src/stowage"):
            atomic_json(partial / "source-pin.json", dict(format="mvp-tiny-source-epoch-v1",
                source_sha256=before, rule_freeze_v1_enabled=False, global_screen=0,
                purpose="D79 tiny-only corpus; frozen against concurrent opt-in edits"))
            partial.rename(target)
            link_acceleration(target)
            print(str(target.resolve()))
            return
    raise RuntimeError("live source did not stay stable long enough to pin; retry later")


if __name__ == "__main__":
    main()
