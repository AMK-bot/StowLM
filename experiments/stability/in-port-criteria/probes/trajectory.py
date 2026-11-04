"""Read-only probe: in-port intermediate stability/strength trajectory of passing plans.
Usage: uv run python trajectory.py <run_dir> <out_json>"""
import json, math, sys, time
from pathlib import Path
import stowage.rules.catalogue as _cat
# Working-tree catalogue.toml is mid-migration (D61, uncommitted) and the checker code does not yet
# match it; static rules then fail with checker.missing_vessel_data. Use HEAD's catalogue in memory
# (copied to head_catalogue.toml). Stability/strength physics do not read the catalogue.
_cat.load_catalogue.__defaults__ = (Path(__file__).with_name("head_catalogue.toml"),)
from stowage.model import Instance, MovePlan
from stowage.checker import check
from stowage.checker.full import _ballast
from stowage.checker.stability import check_stability
from stowage.checker.strength import check_strength
from stowage.moves import apply_move, initial_state

COEF = {"area_0_30_mrad": .009, "area_0_40_mrad": .016, "area_30_40_mrad": .006,
        "gz_at_or_above_30_m": .033, "gz_max_m": .042, "area_0_flood_mrad": .029}
KEYS = ["gm_m", "gm_solid_m", "fsc_m", "kg_m", "list_rad", "trim_m", "draught_mean_m", "draught_aft_m",
        "draught_fwd_m", "displacement_kg", "weather_ratio", "gz_max_m", "angle_gz_max_rad"]

def stab(inst, ship):
    t = time.perf_counter(); r = check_stability(inst, ship, call_index=inst.call_index); dt = time.perf_counter() - t
    m = r.metrics
    out = {k: m.get(k) for k in KEYS}
    out["list_deg"] = math.degrees(m["list_rad"]) if m.get("list_rad") is not None else None
    C = m.get("containership_form_factor")
    if C and m.get("criteria_part_b_2_3_active"):
        ratios = {k: m[k] / (c / C) for k, c in COEF.items() if k in m}
        out["b23_ratios"] = ratios
        out["b23_worst_ratio"] = min(ratios.values()) if ratios else None
    out["form_factor_C"] = C
    out["margins"] = {k: v for k, v in r.margins.items()}
    out["fail_rules"] = sorted({v.rule_id for v in r.violations if v.severity == "hard"})
    out["ran"] = r.ran; out["t_s"] = dt
    return out

def strg(inst, ship):
    t = time.perf_counter(); r = check_strength(inst, ship, call_index=inst.call_index); dt = time.perf_counter() - t
    return {"max_sf_ratio": r.metrics.get("max_sf_ratio"), "max_bm_ratio": r.metrics.get("max_bm_ratio"),
            "fail_rules": sorted({v.rule_id for v in r.violations if v.severity == "hard"}), "ran": r.ran, "t_s": dt}

run = Path(sys.argv[1]); res = json.loads((run / "result.json").read_text())
inst = Instance.model_validate_json(Path(res["input"]).read_text())
plan = MovePlan.model_validate_json((run / "final.plan.json").read_text())
# Working-tree catalogue.toml (uncommitted edits) lacks E6 allow_on_deck_if_no_compatible_hold_cell;
# restore HEAD's value (true) in memory only so the static rules part can run. Physics untouched.
cmap = {c.id: c for c in inst.containers}
rec = {"run": str(run), "instance": res["input"], "instance_id": inst.id, "vessel": inst.vessel.id,
       "moves": [m.kind for m in plan.moves]}
t = time.perf_counter(); r0 = check(inst, plan); rec["check_s_cold"] = time.perf_counter() - t
t = time.perf_counter(); r0 = check(inst, plan); rec["check_s"] = time.perf_counter() - t; rec["passed"] = r0.passed
rec["normal_hard"] = sorted({v.rule_id for v in r0.violations if v.severity == "hard"})
t = time.perf_counter(); r1 = check(inst, plan, intermediate_stability=True); rec["check_intermediate_s"] = time.perf_counter() - t
rec["passed_intermediate"] = r1.passed
rec["n_load_lifts"] = sum(m.kind == "load" for m in plan.moves)
rec["intermediate_violations"] = [{"rule": v.rule_id, "move_index": v.move_index, "severity": v.severity,
                                   "msg": v.message[:200]} for v in r1.violations if v.severity == "hard"]
# departure (A and B are both: final cargo; departure = with plan ballast)
state = initial_state(inst)
rec["arrival_A"] = stab(inst, state.ship)
traj = []
for i, mv in enumerate(plan.moves):
    state = apply_move(inst, state, mv, move_index=i)
    if mv.kind != "load":
        continue
    c = cmap[mv.container_id]
    shipB, probs = _ballast(inst, plan, state.ship)
    covers_off = dict(state.ship.covers_off)
    # APPROXIMATION (probe only): check_stability refuses any open-hatch state. Variant C forces the
    # covers back "on" in memory (cover masses at their seated position, though physically on the quay)
    # so hydrostatic GM/list/trim can be read for the open-hatch port state.
    extra = {}
    if covers_off:
        extra["C_initial_tanks_covers_forced_on"] = {"stab": stab(inst, state.ship.model_copy(update={"covers_off": {}}, deep=True))}
    traj.append({"move_index": i, "container": mv.container_id, "slot": mv.dst, "len_ft": c.length_ft,
                 "gross_kg": c.gross_mass_kg,
                 "A_initial_tanks": {"stab": stab(inst, state.ship), "strength": strg(inst, state.ship)},
                 "B_departure_ballast": {"stab": stab(inst, shipB), "strength": strg(inst, shipB),
                                         "ballast_problems": [p.rule_id for p in probs]},
                 "covers_off": covers_off, **extra})
rec["trajectory"] = traj
dep, _ = _ballast(inst, plan, state.ship)
rec["departure"] = {"stab": stab(inst, dep), "strength": strg(inst, dep)}
rec["ballast_total_kg"] = sum(plan.ballast.tank_masses_kg.values()) if plan.ballast else None
rec["arrival_adjustable_total_kg"] = sum(v for t_ in inst.vessel.tanks if t_.adjustable
                                          for k, v in inst.onboard.tank_masses_kg.items() if k == t_.id)
Path(sys.argv[2]).write_text(json.dumps(rec, indent=1, default=str))
print(rec["instance_id"], rec["vessel"], rec["passed"], rec["passed_intermediate"],
      round(rec["check_s"], 1), round(rec["check_intermediate_s"], 1), len(traj))
