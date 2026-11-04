"""Read saved first-difference references and identify sufficient certificates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

import numpy as np

from stowage.feasibility.global_bounds import GlobalScreen, Reference
from stowage.feasibility.tables import build_tables
from stowage.io import load_instance

HERE = Path(__file__).resolve().parent
PERF = HERE.parent
ROOT = HERE.parents[3]


def main():
    start = time.perf_counter()
    evidence = PERF / "promote" / "diagnose-firstdifference.json"
    diagnostic = json.loads(evidence.read_text())
    instance = load_instance(ROOT / diagnostic["instance_path"])
    T = build_tables(instance)
    slot = T.slot_index["0110182"]
    cells = T.slot_cells[slot][T.slot_cells[slot] >= 0].tolist()
    sc, sn = T.slot_cells[[slot]], T.slot_n[[slot]]
    cellmap = {c.id: c for c in instance.vessel.cells}
    xyz = np.array([(cellmap[c].lcg_m, cellmap[c].tcg_m, cellmap[c].vcg_base_m) for c in T.cell_ids])
    distance = np.linalg.norm(xyz - xyz[cells].mean(axis=0), axis=1)
    distance[cells] = np.inf
    donors = np.argsort(distance, kind="stable")[:12]
    masses = np.asarray(diagnostic["variants"]["candidate"]["missing_masses_kg"])
    out = dict(kind="read-only observed certificate attribution; not an LP infeasibility proof",
               source_diagnostic_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
               target_slot="0110182", cells=cells, missing_actions=len(masses), variants={})
    for name in ("reference", "candidate", "parent_disabled"):
        path = PERF / "promote" / f"diagnose-medium-{name}-ref.npz"
        z = np.load(path)
        g = GlobalScreen(*[z[k] for k in ("A", "At", "const", "lo", "hi", "tank_lo", "tank_hi", "u0")])
        ref = Reference(*[z["ref_" + k] for k in ("fixed", "free", "y", "t", "R", "v", "Y")])
        L, U = g.intervals(ref, sc, sn)
        (Lu, Uu), (Ld, Ud) = g.local_intervals(ref, sc, sn, donors[None])
        wide = (masses >= L[0] - 1e-6) & (masses <= U[0] + 1e-6)
        local = ((masses >= Lu[0] - 1e-6) & (masses <= Uu[0] + 1e-6)) | ((masses >= Ld[0] - 1e-6) & (masses <= Ud[0] + 1e-6))
        record = dict(npz_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                      shipwide_interval=[float(L[0]), float(U[0])],
                      local_up=[float(Lu[0]), float(Uu[0])], local_down=[float(Ld[0]), float(Ud[0])],
                      shipwide_admits=int(wide.sum()), local_admits=int(local.sum()),
                      any_cheap_admits=int((wide | local).sum()), constructed=[])
        for mass in np.unique(masses):
            if np.any(wide[masses == mass]):
                child = g.shift(ref, cells, float(mass))
                certificate = "shipwide"
            elif np.any(local[masses == mass]):
                child = g.local_shift(ref, cells, float(mass), donors)
                certificate = "local"
            else:
                continue
            excess = np.maximum(g.lo - child.v, child.v - g.hi)
            bad = np.flatnonzero(excess > 0.)
            record["constructed"].append(dict(mass_kg=float(mass), certificate=certificate,
                broad_feasible=g.feasible(child), strict_row_violations=[dict(row=int(i), value=float(child.v[i]),
                    lo=float(g.lo[i]), hi=float(g.hi[i]), excess=float(excess[i]), broad_slack=float(g._slack()[i])) for i in bad],
                mass_equality_error_kg=float(child.y.sum() - child.R),
                maximum_cell_cap_excess_kg=max(0., float(np.max(child.y - g.u0))),
                negative_cell_mass_kg=max(0., float(-np.min(child.y)))))
        # Record the independent cold LP's actual status under unchanged rows
        # and tolerances. This is solver evidence, not a Farkas certificate.
        statuses = []
        original = g._lp
        def traced(*args, **kwargs):
            result = original(*args, **kwargs)
            statuses.append(dict(status=int(result.status), success=bool(result.success), message=result.message))
            return result
        g._lp = traced
        exact = g._slot_interval_cold(ref, cells, 0., min(float(ref.R), g.u0 * len(cells)))
        record.update(cold_lp_statuses=statuses, cold_lp_interval=None if exact is None else [exact[0][0], exact[1][0]])
        out["variants"][name] = record
    out["wall_s"] = time.perf_counter() - start
    # Infinity is represented explicitly for diagnostic-only unbounded rows.
    (HERE / "diagnose-certificate.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"wall_s": out["wall_s"], "variants": {name: {
        "shipwide_admits": r["shipwide_admits"], "local_admits": r["local_admits"],
        "strict_row_violations": len(r["constructed"][0]["strict_row_violations"]) if r["constructed"] else None,
        "cold_lp_statuses": r["cold_lp_statuses"]} for name, r in out["variants"].items()}}, indent=2))


if __name__ == "__main__":
    main()
