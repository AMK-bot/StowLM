"""Physics-only production warmer; no linear hook or qualification overrides."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import sys
from time import perf_counter
import traceback

from stowage.cache_warm import _manifest_valid, _save, _warm_stability
from stowage.checker import stability_liquid
from stowage.vessel import load_ship_params
from stowage.vessel.tanks import TankGeometry

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def source_hashes():
    paths = list((ROOT / "src/stowage").rglob("*.py"))
    paths += list((ROOT / "experiments/stability").rglob("*.py"))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths}


def main():
    name = sys.argv[1]
    started = perf_counter()
    before = source_hashes()
    params = load_ship_params(name)
    result = dict(ship=name, pid=os.getpid(), phase="physics-only", success=False,
                  thread_limits={key: os.environ.get(key) for key in (
                      "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
                      "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS")},
                  expected_manifest=str(stability_liquid.manifest_path(params)),
                  metadata=stability_liquid.metadata(params))
    _save(OUT / f"{name}-running.json", result)
    print(json.dumps(result), flush=True)
    try:
        result["caches"] = _warm_stability(name, params)
        solids = {}
        for raw in params.get("tanks", []):
            solid = TankGeometry.from_params(raw, ship_params=params)
            if solid.shape == "hull_sections":
                solids[raw["id"]] = solid
        result["manifest_valid"] = _manifest_valid(params, solids)
        result["current_metadata_matches"] = (
            stability_liquid.metadata(load_ship_params(name)) == result["metadata"])
        manifest = json.loads(Path(result["expected_manifest"]).read_text())
        result["validation"] = manifest["validation"]
        if not result["manifest_valid"] or not result["current_metadata_matches"]:
            raise RuntimeError("manifest is not valid for the current parameter epoch")
        result["success"] = True
    except BaseException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    finally:
        after = source_hashes()
        result["source_changes"] = sorted(
            key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        result["elapsed_seconds"] = perf_counter() - started
        result["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        _save(OUT / f"{name}-summary.json", result)
        print(json.dumps({k: v for k, v in result.items() if k != "caches"}), flush=True)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
