"""C5 sizing: what the generator's 'full' rung realistic export asks each ship to load (no planning)."""
import json, sys, time
from pathlib import Path
from stowage.generate.instances_v2_random import generate_random_v2
from stowage.generate.random_export import full_export_policy
from stowage.params import load_param_file

def main():
    out = {}
    for name in ("feeder_500", "feeder_1000", "feeder_1700"):
        t0 = time.perf_counter()
        gi = generate_random_v2("full", seed=11, noise=0.2, vessel_name=name, port_name="laem_chabang_like")
        gi = gi if not isinstance(gi, tuple) else gi[0]
        v = gi.vessel
        boxes = {c.id: c for c in gi.containers}
        load = [boxes[b] for b in gi.load_list]
        by = {}
        for b in load:
            by[str(b.length_ft)] = by.get(str(b.length_ft), 0) + 1
        teu = sum(1 if b.length_ft == 20 else 2 for b in load)
        adj = {t.id for t in v.tanks if t.adjustable}
        ballast = sum(m for k, m in gi.onboard.tank_masses_kg.items() if k in adj)
        cons = sum(m for k, m in gi.onboard.tank_masses_kg.items() if k not in adj)
        pol = full_export_policy(v, load_param_file("cargo", "feeder_export"))
        cap = sum(t.capacity_m3 * t.content_density_kg_m3 for t in v.tanks if t.adjustable)
        cargo = sum(b.gross_mass_kg for b in load)
        disp = v.hull.lightship_mass_kg + sum(l.mass_kg for l in v.fixed_loads) + cons + ballast + cargo
        out[name] = dict(gen_s=time.perf_counter() - t0, cells=len(v.cells), load_list=len(load), by_length=by,
                         teu=teu, cargo_t=cargo / 1e3, mean_box_t=cargo / 1e3 / len(load),
                         initial_ballast_t=ballast / 1e3, ballast_capacity_t=cap / 1e3,
                         consumables_t=cons / 1e3, departure_displacement_t=disp / 1e3,
                         design_displacement_t=(pol.design_displacement_kg / 1e3) if pol else None,
                         export_count_ok=pol.is_export_count(len(load)) if pol else None,
                         target_count=pol.target_count if pol else None, mass_limited=pol.mass_limited if pol else None)
        print(name, json.dumps(out[name], default=float))
    (Path(__file__).parent / "c5_sizing.json").write_text(json.dumps(out, indent=1, default=float) + "\n")

if __name__ == "__main__":
    main()
