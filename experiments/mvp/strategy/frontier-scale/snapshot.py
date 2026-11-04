"""Freeze and activate the registered CPU runtime; never invoke a planner.

The source snapshot is immutable and verified on resume. Derived physics
caches are independent copies, may grow during execution, and are excluded
from the immutable source digest.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUNTIME = HERE / "runtime"
CERTIFICATE = HERE / "runtime-snapshot.json"
MANIFEST = HERE / "manifest.json"
THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "HIGHS_THREADS",
               "BLIS_NUM_THREADS")
HASHED_FRONTIER = ("alns.py", "blocked.py", "blocks.py", "columns.py",
                   "master.py", "planner.py", "sequence.py", "sight.py")
HASHED_STRATEGY = ("compact.py", "blockrule/rule.py",
                   "blockrule/capacity_candidate.py", "headroom/layout.py")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def source_sha256(root: Path) -> str:
    result = hashlib.sha256()
    for name in HASHED_FRONTIER:
        result.update((root / "experiments/mvp/frontier" / name).read_bytes())
    for name in HASHED_STRATEGY:
        result.update((root / "experiments/mvp/strategy" / name).read_bytes())
    return result.hexdigest()


def relative_file(path: Path) -> str:
    rel = path.relative_to(ROOT)
    if any(part in {"test", "test-v3"} for part in rel.parts):
        raise ValueError(f"Forbidden input/source partition: {rel}")
    return str(rel)


def regular_files(directory: Path):
    return (p for p in sorted(directory.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
            and p.suffix not in {".pyc", ".pyo"})


def planned_sources(manifest: dict) -> dict[str, str]:
    files = {}
    for field, directory in (("src_sha256", ROOT / "src"),
                             ("params_sha256", ROOT / "params")):
        current = {relative_file(p): digest(p) for p in regular_files(directory)}
        if current != manifest[field]:
            changed = sorted(set(current) ^ set(manifest[field]) |
                             {p for p in current.keys() & manifest[field].keys()
                              if current[p] != manifest[field][p]})
            raise RuntimeError(f"Registered {field} drift: {changed}")
        files.update(current)
    for path in sorted((ROOT / "experiments/mvp/frontier").glob("*.py")):
        rel = relative_file(path)
        expected = manifest["frontier_python_sha256"][rel]
        if digest(path) != expected:
            raise RuntimeError(f"Registered Frontier drift: {rel}")
        files[rel] = expected
    strategy = ROOT / "experiments/mvp/strategy"
    helper_paths = [strategy / "compact.py"]
    for name in ("blockrule", "headroom"):
        helper_paths.extend(sorted((strategy / name).glob("*.py")))
    for path in helper_paths:
        files[relative_file(path)] = digest(path)
    for name in ("kcs", "dtc"):
        for path in regular_files(ROOT / "data/raw" / name):
            files[relative_file(path)] = digest(path)
    for required in ("data/raw/kcs/kcs.fix", "data/raw/dtc/DTC-scaled.stl.gz"):
        if required not in files:
            raise RuntimeError(f"Missing hull dependency: {required}")
    for name in ("pyproject.toml", "uv.lock"):
        files[name] = digest(ROOT / name)
    return dict(sorted(files.items()))


def registered_inputs(manifest: dict) -> dict[str, str]:
    files = {}
    for job in manifest["jobs"]:
        rel = job["input"]
        parts = Path(rel).parts
        if (Path(rel).is_absolute() or ".." in parts or "train" not in parts
                or any(p in {"test", "test-v3", "val", "validation", "archive"} for p in parts)
                or job["job"].get("split") != "train"):
            raise ValueError(f"Only registered TRAIN inputs admitted: {rel}")
        files[rel] = job["input_sha256"]
    if len(files) != 12:
        raise ValueError("Expected exactly twelve unique registered TRAIN inputs")
    return dict(sorted(files.items()))


def seed_physics_caches() -> dict:
    source = ROOT / "experiments/mvp/strategy/fillsweep/runs/main/runtime/experiments/cache"
    candidates = [p for name in ("stability", "vessels")
                  for p in regular_files(source / name)
                  if p.suffix in {".json", ".npz", ".npy"}]
    total = sum(p.stat().st_size for p in candidates)
    if total >= 1_000_000_000:
        return {"status": "not_copied_size_limit", "available_bytes": total}
    copied, skipped, byte_count = {}, [], 0
    for path in candidates:
        relative = path.relative_to(source)
        target = RUNTIME / "experiments/cache" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            skipped.append(str(relative))
            continue  # The donor cache is mutable and may be atomically refreshed.
        target.write_bytes(payload)
        copied[str(relative)] = hashlib.sha256(payload).hexdigest()
        byte_count += len(payload)
    return {"status": "copied", "source": str(source.relative_to(ROOT)),
            "files": len(copied), "bytes": byte_count, "initial_sha256": copied,
            "skipped_disappeared": skipped,
            "immutable_snapshot_member": False,
            "policy": "Independent bytes; ordinary content-addressed cache validation remains active. Mutable derived caches excluded from snapshot verification."}


def verify_snapshot() -> dict:
    record = json.loads(CERTIFICATE.read_text())
    manifest = json.loads(MANIFEST.read_text())
    if digest(MANIFEST) != record["manifest_sha256"]:
        raise RuntimeError("Registered manifest changed after runtime snapshot")
    if registered_inputs(manifest) != record["inputs"]:
        raise RuntimeError("Registered input membership changed")
    for relative, expected in {**record["sources"], **record["inputs"]}.items():
        if digest(RUNTIME / relative) != expected:
            raise RuntimeError(f"Frozen runtime changed: {relative}")
    if source_sha256(RUNTIME) != record["source_sha256"] or record["source_sha256"] != manifest["source_sha256"]:
        raise RuntimeError("Frozen Frontier source digest does not match registration")
    return record


def freeze_snapshot() -> dict:
    if CERTIFICATE.exists():
        return verify_snapshot()
    manifest = json.loads(MANIFEST.read_text())
    sources, inputs = planned_sources(manifest), registered_inputs(manifest)
    if source_sha256(ROOT) != manifest["source_sha256"]:
        raise RuntimeError("Current Frontier source digest does not match registration")
    for relative, expected in {**sources, **inputs}.items():
        source, target = ROOT / relative, RUNTIME / relative
        if digest(source) != expected:
            raise RuntimeError(f"Source/input changed before copy: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if digest(target) != expected or digest(source) != expected:
            raise RuntimeError(f"Source/input changed during copy: {relative}")
    if source_sha256(RUNTIME) != manifest["source_sha256"]:
        raise RuntimeError("Copied Frontier digest differs")
    record = {"schema": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "runtime": str(RUNTIME.relative_to(ROOT)), "sources": sources,
              "inputs": inputs, "manifest_sha256": digest(MANIFEST),
              "source_sha256": manifest["source_sha256"],
              "source_bytes": sum((RUNTIME / p).stat().st_size for p in sources),
              "python": sys.version,
              "cache_seed": seed_physics_caches(),
              "excluded": ["__pycache__", "*.pyc", "*.pyo", "runtime/experiments/cache/**", "runtime/.cache/**", "runtime/tmp/**"],
              "activation": "Call activate_runtime before project imports; execute the runner with UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync. No dependency synchronization performed."}
    atomic_json(CERTIFICATE, record)
    return verify_snapshot()


def activate_runtime(*, change_directory: bool = True, verify: bool = True) -> Path:
    """Contain project paths and caches before importing any runtime modules."""
    if verify:
        verify_snapshot()
    for key in THREAD_VARS:
        os.environ[key] = "1"
    os.environ["STOWAGE_IN_PORT_WORKERS"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["STOWAGE_VESSEL_CACHE_DIR"] = str(RUNTIME / "experiments/cache/vessels")
    os.environ["XDG_CACHE_HOME"] = str(RUNTIME / ".cache")
    os.environ["MPLCONFIGDIR"] = str(RUNTIME / ".cache/matplotlib")
    os.environ["TMPDIR"] = str(RUNTIME / "tmp")
    for relative in ("experiments/cache/vessels", "experiments/cache/stability",
                     ".cache/matplotlib", "tmp"):
        (RUNTIME / relative).mkdir(parents=True, exist_ok=True)
    paths = [RUNTIME / "src", RUNTIME / "experiments/mvp/frontier", RUNTIME]
    sys.path[:0] = [str(p) for p in paths]
    os.environ["PYTHONPATH"] = os.pathsep.join(str(p) for p in paths)
    sys.dont_write_bytecode = True
    if change_directory:
        os.chdir(RUNTIME)
    return RUNTIME


if __name__ == "__main__":
    record = freeze_snapshot()
    print(json.dumps({"status": "verified", "sources": len(record["sources"]),
                      "inputs": len(record["inputs"]), "source_sha256": record["source_sha256"],
                      "cache_seed_files": record["cache_seed"].get("files"),
                      "cache_seed_bytes": record["cache_seed"].get("bytes")}))
