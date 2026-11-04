"""One real KN row to estimate fresh hull-table preparation; no source/cache writes."""
from hashlib import sha256
import json
from pathlib import Path
import resource
import sys
import time


def main():
    from stowage.vessel.fleet import load_ship_params
    from stowage.vessel.hydrostatics import HullGeometry
    import numpy as np
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=False)
    start, cpu = time.perf_counter(), time.process_time()
    params = load_ship_params("feeder_500")
    geometry = HullGeometry.from_params(params)
    h = params["hull"]
    rho = float(h.get("water_density_kg_m3", 1025.))
    t = float(h["design_draught_m"])
    row = geometry.hydrostatic_row(t, rho, kg_m=float(h["lightship_vcg_m"]))
    before, before_cpu = time.perf_counter(), time.process_time()
    angles = np.radians(np.arange(0, 61, 2))
    values = geometry.kn_curve(angles, row.displacement_kg, lcg_m=row.lcb_m, density_kg_m3=rho)
    record = {"ship": "feeder_500", "draught_m": t, "angles": len(angles),
              "row_wall_s": time.perf_counter() - before, "row_cpu_s": time.process_time() - before_cpu,
              "total_wall_s": time.perf_counter() - start, "total_cpu_s": time.process_time() - cpu,
              "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "kn_values_m": values.tolist(), "kn_bytes_sha256": sha256(values.tobytes()).hexdigest(),
              "finite": bool(np.isfinite(values).all()), "builder_row_count_upper_bound": 19,
              "estimate_limit": "One design draught; light/high-angle fallback and manual conditions can cost more",
              "file_caches_written": False, "full_vessel_built": False}
    assert record["finite"] and len(values) == 31
    (output / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: value for key, value in record.items() if key != "kn_values_m"}))


if __name__ == "__main__":
    main()
