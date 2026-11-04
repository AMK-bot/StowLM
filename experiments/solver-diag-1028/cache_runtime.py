"""Run the existing cache warmer with every writable cache inside the epoch.

Only storage paths change. Physics, qualification samples and production warm
logic remain the repository implementations. Existing caches are cloned before
use, so qualification cannot mutate shared experiment artifacts.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def scoped_path(value):
    path = Path(value).resolve()
    if not path.is_relative_to(HERE) or path == HERE:
        raise ValueError("cache output must be a dedicated directory under campaign-g1")
    return path


def prepare_cache(output):
    """Clone the immutable-at-admission cache snapshot, once per epoch."""
    output = scoped_path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".cache-copy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = output / "cache"
        marker = cache / "snapshot.json"
        if not marker.exists():
            started = time.perf_counter()
            cache.mkdir(exist_ok=True)
            records = []
            for name in ("stability", "vessels", "linear"):
                source = ROOT / "experiments/cache" / name
                destination = cache / name
                if destination.exists():
                    shutil.rmtree(destination)
                if source.exists():
                    if sys.platform == "darwin":
                        # APFS copy-on-write cloning keeps the 3 GB snapshot cheap.
                        subprocess.run(["/bin/cp", "-cR", str(source), str(destination)], check=True)
                    else:
                        shutil.copytree(source, destination)
                    records.append(dict(source=str(source), destination=str(destination)))
                else:
                    destination.mkdir()
            marker.write_text(json.dumps(dict(sources=records,
                copy_wall_s=time.perf_counter()-started), indent=2) + "\n")
    return cache


def redirect_cache(output):
    """Redirect existing module globals and their bound cache-dir defaults."""
    output = scoped_path(output)
    cache = prepare_cache(output)
    os.environ["STOWAGE_VESSEL_CACHE_DIR"] = str(cache / "vessels")
    os.environ["TMPDIR"] = str(output / "tmp")
    (output / "tmp").mkdir(exist_ok=True)
    tempfile.tempdir = str(output / "tmp")
    mappings = {
        "stowage.checker.stability_tables": ("CACHE_DIR", "stability"),
        "stowage.checker.stability_sounding": ("CACHE_DIRECTORY", "stability"),
        "stowage.checker.stability_liquid_tables": ("CACHE_DIRECTORY", "stability"),
        "stowage.checker.stability_liquid": ("CACHE_DIRECTORY", "stability"),
        "stowage.linear.cache": ("_DEFAULT_ROOT", "linear"),
    }
    for name, (attribute, directory) in mappings.items():
        module = importlib.import_module(name)
        previous = getattr(module, attribute)
        replacement = cache / directory
        setattr(module, attribute, replacement)
        for item in vars(module).values():
            if not callable(item) or getattr(item, "__module__", None) != name:
                continue
            defaults = getattr(item, "__defaults__", None)
            if defaults:
                item.__defaults__ = tuple(replacement if isinstance(v, Path) and v == previous else v
                                          for v in defaults)
            keywords = getattr(item, "__kwdefaults__", None)
            if keywords:
                item.__kwdefaults__ = {k: replacement if isinstance(v, Path) and v == previous else v
                                       for k, v in keywords.items()}
    return cache


def enforce_scope():
    """Fail closed if an imported runner attempts an out-of-fence mutation."""
    def permitted(path):
        if isinstance(path, int):
            return True
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        try:
            return Path(path).resolve().is_relative_to(HERE)
        except (TypeError, ValueError):
            return False

    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and not permitted(path):
                raise PermissionError(f"campaign write outside scope: {path}")
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate"}:
            if not permitted(args[0]):
                raise PermissionError(f"campaign mutation outside scope: {args[0]}")
        elif event in {"os.rename", "os.link", "os.symlink"}:
            if not all(permitted(p) for p in args[:2]):
                raise PermissionError(f"campaign mutation outside scope: {args[:2]}")
    sys.addaudithook(audit)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ship", required=True)
    parser.add_argument("--diagnostic", action="store_true")
    args = parser.parse_args(argv)
    output = scoped_path(args.output)
    started = time.perf_counter()
    for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                     "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[variable] = "1"
    sys.dont_write_bytecode = True
    redirect_cache(output)
    enforce_scope()
    from stowage import cache_warm
    if args.ship not in cache_warm.PRODUCTION_SHIPS:
        parser.error("ship must be a current PRODUCTION_SHIPS member")
    cache_warm.REPORTS = output / "warmup"
    # This is exactly the production --workers 1 per-ship call.
    result = cache_warm._warm_ship(args.ship)
    result.update(diagnostic=args.diagnostic,
                  label="diagnostic, not evidence" if args.diagnostic else "frozen epoch",
                  adapter_wall_s=time.perf_counter()-started,
                  peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss /
                    (1024**3 if sys.platform == "darwin" else 1024**2))
    path = output / f"warm-{args.ship}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
