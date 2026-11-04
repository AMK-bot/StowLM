"""Checkpoint one fresh source-qualified full vessel in the owned window cache."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import time


def main():
    run, ship = Path(sys.argv[1]).resolve(), sys.argv[2]
    spec = importlib.util.spec_from_file_location("d126_vessel_cache_paths", "experiments/mvp/showcase_tools/run_hero.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.isolate_caches(run)
    from stowage.vessel.fleet import load_vessel
    started, cpu = time.perf_counter(), time.process_time()
    vessel = load_vessel(ship)
    raw = vessel.model_dump_json().encode()
    record = {"ship": ship, "vessel_sha256": sha256(raw).hexdigest(), "bytes": len(raw),
              "wall_s": time.perf_counter() - started, "cpu_s": time.process_time() - cpu,
              "hull_present": vessel.hull is not None,
              "max_gm_lashing_m": vessel.hull.max_gm_lashing_m,
              "cached_by_unchanged_public_builder": True}
    assert record["hull_present"] and vessel.id == ship and vessel.slice_of is None
    (run / f"build-{ship}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
