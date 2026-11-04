"""Pin CURRENT producer/checker inputs for sealed test v3, without running them.

Code, parameters and mandatory hull bytes are copied. Existing qualified caches
remain individual symlinks; their observed bytes are hashed and never modified
by this helper. Runtime D58 certificates bind any subsequent cache population.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tomllib

from build import ROOT, atomic_json, sha256

SUFFIXES = {".py", ".json", ".toml"}


def _inputs(root):
    paths = [root / "pyproject.toml"]
    for folder in (root / "src/stowage", root / "params"):
        paths.extend(p for p in folder.rglob("*") if p.is_file() and p.suffix in SUFFIXES)
    # Hull references are explicit physical input pins, not optional caches.
    for path in (root / "params/fleet").glob("*.toml"):
        form = tomllib.loads(path.read_text()).get("hull_form", {})
        if form.get("family") == "stowlm_offsets_v1":
            asset = Path(form["offsets_path"])
            if asset.is_absolute() or ".." in asset.parts:
                raise ValueError("epoch requires repository-relative pinned hull paths")
            asset = root / asset
            if sha256(asset) != form["offsets_sha256"]:
                raise ValueError(f"invalid current hull pin: {asset}")
            paths.append(asset)
    return {str(p.relative_to(root)): sha256(p) for p in sorted(set(paths))}


def _references(root, copied):
    result = set()
    def visit(item):
        if isinstance(item, dict):
            if isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
                path = Path(item["path"])
                result.add(path if path.is_absolute() else root / path)
            for value in item.values():
                visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)
    for relative in copied:
        path = root / relative
        if path.suffix == ".json":
            try:
                visit(json.loads(path.read_bytes()))
            except (ValueError, UnicodeError):
                pass
    return {str(p): sha256(p) if p.is_file() else None for p in sorted(result)}


def _links(target, root):
    links = {}
    # Preserve resolved qualification-script identities. Never execute them here.
    linear = root / "experiments/linear-model"
    if linear.exists():
        destination = target / "experiments/linear-model"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(linear, target_is_directory=True)
        links[str(destination.relative_to(target))] = {"target": str(linear), "kind": "qualification-directory"}
    cache = root / "experiments/cache/stability"
    if cache.exists():
        for path in sorted(cache.rglob("*")):
            if not path.is_file() or path.suffix not in {".npz", ".json", ".toml"}:
                continue
            if path.name.startswith("."):
                continue
            destination = target / path.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(path)
            links[str(destination.relative_to(target))] = {"target": str(path), "sha256": sha256(path), "kind": "qualified-cache"}
    return links


def verify(outdir: Path) -> dict:
    """Check immutable copied bytes and qualification evidence, without imports."""
    outdir = Path(outdir).resolve()
    pin = json.loads((outdir / "source-pin.json").read_text())
    for relative, digest in pin["source_sha256"].items():
        if sha256(outdir / relative) != digest:
            raise ValueError(f"epoch source changed: {relative}")
    for name, digest in pin["qualification_sha256"].items():
        path = Path(name)
        if (sha256(path) if path.is_file() else None) != digest:
            raise ValueError(f"qualification evidence changed: {name}")
    for relative, entry in pin["links"].items():
        path = outdir / relative
        if not path.is_symlink() or str(path.readlink()) != entry["target"]:
            raise ValueError(f"epoch acceleration link changed: {relative}")
        if "sha256" in entry and sha256(path) != entry["sha256"]:
            raise ValueError(f"qualified cache input changed: {relative}")
    return pin


def snapshot(outdir: Path, root: Path = ROOT) -> dict:
    """Atomically create an immutable CURRENT-code epoch, or validate its pin.

    No old tiny snapshot is reused. Optional caches may acquire new local derived
    files at runtime; only copied physical/code bytes are immutable. Existing
    linked cache bytes and qualification evidence are checked by verify().
    """
    root, outdir = Path(root).resolve(), Path(outdir).resolve()
    if (outdir / "source-pin.json").exists():
        return verify(outdir)
    if outdir.exists():
        raise ValueError(f"refusing to overwrite unpinned epoch: {outdir}")
    partial = outdir.with_name(f".{outdir.name}.{os.getpid()}.partial")
    if partial.exists():
        raise ValueError(f"previous incomplete epoch requires inspection: {partial}")
    for _ in range(5):
        partial.mkdir(parents=True)
        before = _inputs(root)
        for relative in before:
            destination = partial / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, destination)
        if before != _inputs(root) or any(sha256(partial / p) != h for p, h in before.items()):
            shutil.rmtree(partial)
            continue
        qualification = _references(root, before)
        links = _links(partial, root)
        # Links must be described relative to the final, renamed epoch.
        pin = dict(format="stowlm-test-v3-current-epoch-v1", created_utc=datetime.now(timezone.utc).isoformat(),
                   live_root=str(root), source_sha256=before, qualification_sha256=qualification, links=links,
                   rule_freeze_v1_enabled=False, global_screen=0,
                   purpose="D187 sealed test v3; own generator validity checks only; no plan evaluation")
        atomic_json(partial / "source-pin.json", pin)
        verify(partial)
        if before != _inputs(root):
            shutil.rmtree(partial)
            continue
        partial.rename(outdir)
        return verify(outdir)
    raise RuntimeError("CURRENT source changed repeatedly while pinning; no epoch published")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outdir", type=Path)
    args = parser.parse_args()
    pin = snapshot(args.outdir)
    print(json.dumps({"epoch": str(args.outdir.resolve()), "source_files": len(pin["source_sha256"]),
                      "pin_sha256": sha256(args.outdir / "source-pin.json")}, sort_keys=True))
