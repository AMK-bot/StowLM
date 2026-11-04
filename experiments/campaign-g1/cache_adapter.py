"""Run the existing cache warmer with every writable cache inside the epoch.

Only storage paths change. Physics, qualification samples and production warm
logic remain the repository implementations. Existing caches are cloned before
use, so qualification cannot mutate shared experiment artifacts.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from eval.snapshot_io import read_snapshot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def scoped_path(value):
    path = Path(value).resolve()
    if not path.is_relative_to(HERE) or path == HERE:
        raise ValueError("cache output must be a dedicated directory under campaign-g1")
    return path


def _freeze_helpers():
    spec = importlib.util.spec_from_file_location("campaign_cache_freeze", Path(__file__).with_name("freeze.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pin_transport():
    spec = importlib.util.spec_from_file_location("campaign_cache_pin_transport", Path(__file__).with_name("pin_transport.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clone(source, destination):
    if source.exists():
        if sys.platform == "darwin":
            subprocess.run(["/bin/cp", "-cR", str(source), str(destination)], check=True)
        else:
            shutil.copytree(source, destination)
    else:
        destination.mkdir(parents=True)


def prepare_cache(output, numerical_caches=None):
    """Verify a frozen base, then give this call its own disposable working copy.

    Generated entries may be read again within this attempt. They are never
    adopted into the immutable base or reused by another attempt/spawned worker.
    This avoids silently blessing changed physics or racing on shared additions.
    """
    output = scoped_path(output)
    helpers = _freeze_helpers()
    if numerical_caches is None:
        numerical_caches = _pin_transport().read_pin()
    helpers.verify_numerical_caches(numerical_caches)
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".cache-copy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = output / "cache"
        marker = cache / "snapshot.json"
        if marker.exists():
            recorded = read_snapshot(marker).json()
            if recorded.get("schema_version") != 2 or recorded.get("numerical_caches") != numerical_caches:
                raise ValueError("numerical cache snapshot differs from frozen pin; create a new epoch")
        else:
            if cache.exists():
                raise ValueError("numerical cache snapshot exists without a valid admission marker")
            staging = output / (".cache-clone-" + uuid4().hex)
            staging.mkdir()
            started = time.perf_counter()
            for name, source in numerical_caches["sources"].items():
                _clone(Path(source), staging / name)
            sources = {name: str(staging / name) for name in numerical_caches["sources"]}
            helpers.verify_numerical_caches(numerical_caches)
            helpers.verify_numerical_caches(numerical_caches, sources=sources)
            helpers.atomic_json(staging / "snapshot.json", dict(schema_version=2, numerical_caches=numerical_caches,
                copy_wall_s=time.perf_counter()-started,
                generated_additions="Attempt-local only; working copies are never reused."))
            staging.replace(cache)
        sources = {name: str(cache / name) for name in numerical_caches["sources"]}
        helpers.verify_numerical_caches(numerical_caches, sources=sources)
        # Every caller, including multiprocessing children, gets an independent
        # snapshot. Never import files from a previous mutable working directory.
        work = output / "cache-attempts" / uuid4().hex
        work.mkdir(parents=True)
        for name in numerical_caches["sources"]:
            _clone(cache / name, work / name)
        helpers.verify_numerical_caches(numerical_caches, sources={name: str(work / name) for name in sources})
        helpers.atomic_json(work / "snapshot.json", dict(schema_version=2,
            numerical_cache_sha256=numerical_caches["sha256"], reusable=False,
            generated_additions="Not admitted for reuse; rebuild from the immutable base."))
        helpers.verify_numerical_caches(numerical_caches)
    return work


def redirect_cache(output, numerical_caches=None):
    """Redirect existing module globals and their bound cache-dir defaults."""
    output = scoped_path(output)
    cache = prepare_cache(output, numerical_caches)
    os.environ["STOWAGE_VESSEL_CACHE_DIR"] = str(cache / "vessels")
    from stowage.vessel.fleet_cache import clear_vessel_memo
    clear_vessel_memo()
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
    # Long-lived generation pool workers must not retain memoized payloads
    # from an earlier working snapshot after the filesystem roots change.
    for name, module in list(sys.modules.items()):
        if not (name.startswith("stowage.checker.stability") or name == "stowage.linear.cache"):
            continue
        for attribute, item in list(vars(module).items()):
            clear = getattr(item, "cache_clear", None)
            if callable(clear):
                clear()
            elif attribute in {"_CACHE", "_CENTROID_CACHE", "_POLYNOMIAL_CACHE"} and isinstance(item, dict):
                item.clear()
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
    parser.add_argument("--manifest", type=Path, default=HERE / "freeze-manifest.json")
    args = parser.parse_args(argv)
    output = scoped_path(args.output)
    started = time.perf_counter()
    for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                     "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[variable] = "1"
    sys.dont_write_bytecode = True
    frozen = _freeze_helpers().check_manifest(args.manifest, diagnostic=args.diagnostic)
    os.environ.update(_pin_transport().pin_environment(frozen["numerical_caches"], output))
    os.environ.pop("CAMPAIGN_NUMERICAL_CACHE_PIN", None)
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
