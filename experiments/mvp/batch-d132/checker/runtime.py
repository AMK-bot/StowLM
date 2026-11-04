"""Standard-library bootstrap for immutable checker runtimes; no physics calls."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

FENCE = Path(__file__).resolve().parent
LIVE_ROOT = FENCE.parents[3]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def object_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def fenced(path):
    path = Path(path).resolve()
    if not path.is_relative_to(FENCE):
        raise ValueError(f"Harness output outside owned fence: {path}")
    return path


def save(path, data):
    path = fenced(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def source_pins(root):
    root = Path(root).resolve()
    result = {}
    for name in ("src/stowage", "params"):
        for path in sorted((root / name).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                result[str(path.relative_to(root))] = sha(path)
    result["pyproject.toml"] = sha(root / "pyproject.toml")
    result["data/raw/kcs/kcs.fix"] = sha(root / "data/raw/kcs/kcs.fix")
    return result


def freeze_runtime(destination, source_root=LIVE_ROOT):
    destination = fenced(destination)
    source_root = Path(source_root).resolve()
    if destination.exists():
        raise ValueError("Runtime snapshot is immutable; use a fresh destination")
    destination.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(source_root / "src/stowage", destination / "src/stowage", ignore=ignore)
    shutil.copytree(source_root / "params", destination / "params", ignore=ignore)
    shutil.copy2(source_root / "pyproject.toml", destination / "pyproject.toml")
    for name in ("experiments", "data", "docs"):
        (destination / name).symlink_to(source_root / name, target_is_directory=True)
    pins = source_pins(destination)
    mismatches = [name for name,digest in pins.items() if sha(source_root/name) != digest]
    if mismatches:
        raise ValueError(f"Source drift while copying runtime: {mismatches}")
    manifest = dict(format="checker-speed-runtime-v1", created_utc=datetime.now(timezone.utc).isoformat(),
        source_root=str(source_root), runtime_root=str(destination), files=pins,
        source_epoch_sha256=object_sha(pins),
        shared_read_paths={name:str((source_root/name).resolve()) for name in ("experiments", "data", "docs")},
        resource_policy="Existing content-keyed qualified caches/data read through links; harness audit hook blocks writes outside its fence and into this frozen runtime")
    save(destination / "SNAPSHOT.json", manifest)
    for folder in (destination / "src", destination / "params"):
        for path in folder.rglob("*"):
            if not path.is_symlink():
                path.chmod(0o555 if path.is_dir() else 0o444)
        folder.chmod(0o555)
    for name in ("pyproject.toml", "SNAPSHOT.json"):
        (destination/name).chmod(0o444)
    destination.chmod(0o555)
    return manifest


def verify_runtime(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest = read(FENCE / 'runtime-pins' / (snapshot.name + '.json'))
    actual = source_pins(snapshot)
    if actual != manifest['files'] or object_sha(actual) != manifest['source_epoch_sha256']:
        raise ValueError('Frozen runtime source/parameter bytes changed')
    return manifest


def assert_runtime_imports(root):
    root = Path(root).resolve()
    origins = {}
    for name,module in list(sys.modules.items()):
        if name == "stowage" or name.startswith("stowage."):
            filename = getattr(module,"__file__",None)
            if filename:
                path = Path(filename).resolve()
                if not path.is_relative_to(root / "src/stowage"):
                    raise RuntimeError(f"Mixed checker runtime: {name}: {path}")
                origins[name] = str(path)
    return origins


def configure_runtime(snapshot):
    """Call before receiving runtime-typed pickles or importing any Stowage module.

    Exposes no solver/deadline dispatch changes. Shared cache roots are the same
    resolved directories; parameter/code keys retain their relative identities.
    """
    snapshot = Path(snapshot).resolve()
    manifest = verify_runtime(snapshot)
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                 "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    assert_runtime_imports(snapshot)
    sys.path.insert(0, str(snapshot / "src"))
    return manifest


def install_write_guard(protected_roots=()):
    protected_roots = tuple(Path(p).resolve() for p in protected_roots)
    denied, cache_reads = [], {}
    shared_cache = (LIVE_ROOT / "experiments/cache").resolve()
    def audit(event,args):
        def mutation(path):
            if isinstance(path,int):
                return
            path = Path(os.fsdecode(path)).resolve()
            # pytest logging/capture uses the OS null character-device sink.
            # This permits standard I/O only, never unlink/rename/chmod or a
            # persistent file outside the experiment fence.
            if event == 'open' and path == Path(os.devnull).resolve() and path.is_char_device():
                return
            if not path.is_relative_to(FENCE) or any(path.is_relative_to(p) for p in protected_roots):
                denied.append(dict(event=event,path=str(path)))
                raise PermissionError(f"Checker harness read-only guard: {path}")
        if event == "open":
            path,mode,flags = args
            writing = ((isinstance(mode,str) and any(c in mode for c in "wax+")) or
                       flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            if writing:
                mutation(path)
            elif not isinstance(path,int):
                target = Path(os.fsdecode(path)).resolve()
                if target.is_relative_to(shared_cache) and target.is_file() and str(target) not in cache_reads:
                    stat = target.stat()
                    cache_reads[str(target)] = dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns)
        elif event in {"os.mkdir","os.remove","os.rmdir","os.chmod","os.utime","os.truncate"}:
            mutation(args[0])
        elif event in {"os.rename","os.link","os.symlink"}:
            
            for value in args[:2]:
                if event == 'os.symlink' and value == args[0]:
                    continue
                lexical = Path(os.path.abspath(os.fsdecode(value)))
                if not lexical.is_relative_to(FENCE) or any(lexical.is_relative_to(p) for p in protected_roots):
                    mutation(value)
    sys.addaudithook(audit)
    return denied,cache_reads
