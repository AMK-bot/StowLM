"""Why did the production stability check not use its tables? (diagnostic, read-only)."""
import sys
import time
import traceback

import stowage.vessel.fleet as fleet
from stowage.checker import stability as stab
from stowage.checker.stability_tables import load_table_geometry, cached_geometry

for name in sys.argv[1:]:
    v = fleet.load_vessel(name, with_hull=True)
    geometry, params = stab._context(v)
    print(name, "vessel_hash", getattr(geometry, "_stability_vessel_hash", None)[:16])
    t0 = time.perf_counter()
    try:
        t = load_table_geometry(geometry, params, 1025.0)
        print("  load_table_geometry ->", type(t).__name__ if t is not None else None,
              getattr(t, "cache_path", None), f"{time.perf_counter()-t0:.2f}s")
    except Exception as e:
        print("  load_table_geometry raised", type(e).__name__, e)
    try:
        g = cached_geometry(geometry, params, 1025.0)
        print("  cached_geometry ->", g is not None)
    except Exception as e:
        print("  cached_geometry raised", type(e).__name__, e)
        traceback.print_exc()
    try:
        from stowage.checker import stability_liquid as sl
        key = sl._manifest_path(params, getattr(geometry, "_stability_vessel_hash", None)) if hasattr(sl, "_manifest_path") else None
        print("  liquid manifest:", key, key.exists() if key else None)
    except Exception as e:
        print("  liquid manifest probe raised", type(e).__name__, e)
