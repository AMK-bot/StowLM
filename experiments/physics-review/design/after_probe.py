"""Stability-only probe of the after set: condition x declared ballast pattern (no finisher).

Cheap (one check_stability per point, 1-7 s) to choose loading-manual ballast and design values
before the full after_run.py. Writes probe_<label>.json.
Run: OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/physics-review/design/after_probe.py \
     --ships feeder_500 --conditions C1,C4 --patterns "none;DB;DB,FPT" [--set key=value ...]
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import harness as H
import after_run as AR

KEYS = ("gm_m", "trim_m", "draught_aft_m", "draught_fwd_m", "draught_mean_m", "kg_m", "displacement_kg",
        "weather_roll_period_s", "angle_flooding_rad", "weather_deck_edge_rad", "gz_max_m", "area_0_flood_mrad")


def probe(name, ov, conds, patterns, label):
    from stowage.checker.stability import check_stability
    vessel, params = AR.apply(name, ov)
    rows = []
    for cond in conds:
        for pat in patterns:
            prefixes = [p for p in pat.split(",") if p and p != "none"]
            declared = AR._pattern(vessel, prefixes)
            with AR.condition_context(declared, ov.get("draught_margin", 0.0)):
                inst, info = H.build_condition(name, vessel, params, cond)
            t0 = time.perf_counter()
            st = check_stability(inst, inst.onboard, call_index=0)
            m, g = st.metrics, st.margins
            fails = {k: round(v, 4) for k, v in g.items() if v < -1e-12}
            row = dict(label=label, vessel=name, condition=cond, pattern=pat, ballast_t=sum(declared.values()) / 1e3,
                       boxes=info["boxes"], boxes_hold=info["boxes_hold"], left_ashore=info["left_ashore"],
                       cargo_t=info["cargo_mass_kg"] / 1e3, t_per_teu=info.get("t_per_teu"),
                       seconds=time.perf_counter() - t0, error=st.error,
                       metrics={k: m.get(k) for k in KEYS}, failing_margins=fails)
            rows.append(row)
            mm = row["metrics"]
            print(f"{label} {name} {cond} [{pat}] WB={row['ballast_t']:.0f}t boxes={row['boxes']}({row['boxes_hold']}h,{row['left_ashore']} ashore) "
                  f"GM={mm['gm_m'] and round(mm['gm_m'],3)} trim={mm['trim_m'] and round(mm['trim_m'],2)} "
                  f"Ta={mm['draught_aft_m'] and round(mm['draught_aft_m'],2)} Tf={mm['draught_fwd_m'] and round(mm['draught_fwd_m'],2)} "
                  f"Tr={mm['weather_roll_period_s'] and round(mm['weather_roll_period_s'],1)} "
                  f"phif={mm['angle_flooding_rad'] and round(mm['angle_flooding_rad']*57.2958,1)} fails={fails} err={st.error} ({row['seconds']:.1f}s)",
                  flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ships", default=",".join(H.FLEET))
    ap.add_argument("--conditions", default="C1,C2,C3,C4")
    ap.add_argument("--patterns", default="none")
    ap.add_argument("--overrides", default=str(H.HERE / "after.json"))
    ap.add_argument("--set", action="append", default=[], help="json-path=value, e.g. envelope.vent_dz_m=2")
    ap.add_argument("--label", default="probe")
    a = ap.parse_args()
    ovs = json.loads(Path(a.overrides).read_text())
    out = H.HERE / f"probe_{a.label}.json"
    rows = json.loads(out.read_text()) if out.exists() else []
    for name in a.ships.split(","):
        ov = copy.deepcopy(ovs[name])
        for s in a.set:
            k, v = s.split("=", 1)
            *path, last = k.split(".")
            d = ov
            for p in path:
                d = d.setdefault(p, {})
            d[last] = json.loads(v)
        rows += probe(name, ov, a.conditions.split(","), a.patterns.split(";"), a.label)
        out.write_text(json.dumps(rows, indent=1, default=float) + "\n")


if __name__ == "__main__":
    main()
