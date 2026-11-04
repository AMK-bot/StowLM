"""Summarise witness/booking separation, raw yields and legal-booking mass room."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent


def distribution(values):
    values = [value for value in values if value is not None]
    return {"min": min(values), "median": statistics.median(values), "max": max(values)} if values else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path, default=[HERE / "draws.jsonl"])
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()
    rows = [json.loads(line) for path in args.inputs for line in path.read_text().splitlines()]
    cells = []
    print("| Ship | Rung | Generator | Emitted/raw | Background pass/checks | Booking mass legal | Witness/booking ballast t (median) | Min booking D40/design room t | Max arrival B/d | Arrival >=3.3 | s/emitted |")
    print("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for vessel in ("feeder_500", "feeder_800", "feeder_1700"):
        for rung in ("tiny", "small", "medium"):
            for generator in ("random", "simulated"):
                cell = [row for row in rows if (row["vessel"], row["rung"], row["generator"]) == (vessel, rung, generator)]
                if not cell:
                    continue
                calls = [row["gate_calls"][0] for row in cell if row.get("gate_calls")]
                audits = [call["utilisation"]["mass_sizing_audit"] for call in calls]
                emitted = [row for row in cell if row["accepted"]]
                arrived = [audit["D57"]["arrival"] for audit in audits if "arrival" in audit.get("D57", {})]
                result = dict(vessel=vessel, rung=rung, generator=generator,
                    raw_draws=len(cell), emitted=len(emitted), background_checks=len(calls),
                    accepted_backgrounds=sum(call["accepted"] for call in calls),
                    legal_full_booking_mass=sum(audit["full_booking_initial_mass_legal"] for audit in audits),
                    empty_witness_ballast_t=distribution([audit["empty_witness_adjustable_mass_kg"]/1000 for audit in audits]),
                    booking_initial_ballast_t=distribution([audit["booking_initial_adjustable_mass_kg"]/1000 for audit in audits]),
                    full_booking_initial_displacement_t=distribution([audit["full_booking_initial_displacement_kg"]/1000 for audit in audits]),
                    booking_D40_headroom_t=distribution([audit["full_booking_initial_D40_headroom_kg"]/1000 for audit in audits]),
                    booking_design_headroom_t=distribution([audit["full_booking_initial_design_headroom_kg"]/1000 for audit in audits]),
                    arrival_B_over_d=distribution([state["beam_over_draught"] for state in arrived]),
                    arrival_B_over_d_ge_3p3=sum(state["beam_over_draught_ge_3p3"] for state in arrived),
                    arrival_b23_ratio=distribution([state["b23_ratio"] for state in arrived]),
                    arrival_gm_m=distribution([state["gm_m"] for state in arrived]),
                    arrival_kg_m=distribution([state["kg_m"] for state in arrived]),
                    background_equivalent_teu_fraction=distribution([call["utilisation"]["background_teu_fraction"] for call in calls]),
                    background_cargo_t=distribution([call["utilisation"]["background_cargo_t"] for call in calls]),
                    background_mean_box_t=distribution([call["utilisation"]["background_mean_box_t"] for call in calls]),
                    deck_mean_box_t=distribution([call["utilisation"]["deck_mean_box_t"] for call in calls]),
                    fixed_consumables_preserved=all(audit["fixed_consumable_tanks_unchanged"] for audit in audits),
                    seconds_per_emitted=sum(row["wall_s"] for row in cell)/len(emitted) if emitted else None,
                    gate_seconds_per_emitted=sum(call["elapsed_s"] for call in calls)/len(emitted) if emitted else None,
                    generation_seconds=distribution([row["wall_s"] for row in cell]),
                    gate_seconds=distribution([call["elapsed_s"] for call in calls]),
                    generation_draws_over_120s=sum(row["wall_s"] > 120 for row in cell),
                    emitted_booking_mass_legal=sum(row["utilisation"]["mass_sizing_audit"]["full_booking_initial_mass_legal"] for row in emitted),
                    reference_sizing_status_counts=dict(Counter(audit["booking_reference_sizing_status"] for audit in audits)),
                    emitted_reference_sizing_status_counts=dict(Counter(row["utilisation"]["mass_sizing_audit"]["booking_reference_sizing_status"] for row in emitted)),
                    reference_retained_headroom_t=distribution([audit["booking_reference_retained_headroom_kg"]/1000
                        for audit in audits if audit["booking_reference_retained_headroom_kg"] is not None]),
                    maximum_absolute_reference_mass_residual_kg=max((abs(audit["booking_reference_mass_x_residual"][0])
                        for audit in audits if audit["booking_reference_mass_x_residual"] is not None), default=None),
                    maximum_absolute_reference_x_moment_residual_kgm=max((abs(audit["booking_reference_mass_x_residual"][1])
                        for audit in audits if audit["booking_reference_mass_x_residual"] is not None), default=None),
                    maximum_absolute_reference_y_moment_residual_kgm=max((abs(audit["booking_reference_ballast_sizing"]["mass_x_y_moment_residual"][2])
                        for audit in audits if (audit["booking_reference_ballast_sizing"] or {}).get("mass_x_y_moment_residual")), default=None),
                    reference_transverse_moment_balanced=all((audit["booking_reference_ballast_sizing"] or {}).get("transverse_moment_balanced", False)
                        for audit in audits),
                    booking_tank_capacity_violation_count=sum(len(audit["booking_tank_capacity_violation_ids"]) for audit in audits),
                    source_epoch_counts=dict(Counter(row["source_start"] for row in cell)),
                    mid_draw_source_changes=sum(row["source_start"] != row["source_end"] for row in cell),
                    background_reasons=dict(Counter(reason for call in calls for reason in call["reasons"])),
                    first_six_medium_emitted=sum(row["accepted"] for row in cell if row["seed"] < 6) if rung == "medium" else None,
                    sequential_medium_extension_draws=sum(row["seed"] >= 6 for row in cell) if rung == "medium" else 0,
                    D57={phase: {key: distribution([audit["D57"][phase].get(key) for audit in audits if phase in audit.get("D57", {})])
                                  for key in ("beam_over_draught", "displacement_kg", "gm_m", "kg_m", "b23_ratio")}
                         for phase in ("departure", "mid_voyage", "arrival")},
                )
                cells.append(result)
                def field(key, statistic="median", fallback="—"):
                    value = result[key]
                    return f"{value[statistic]:.3f}" if value is not None else fallback
                print(f"| {vessel} | {rung} | {generator} | {len(emitted)}/{len(cell)} | {result['accepted_backgrounds']}/{len(calls)} | "
                      f"{result['legal_full_booking_mass']}/{len(audits)} | {field('empty_witness_ballast_t')}/{field('booking_initial_ballast_t')} | "
                      f"{field('booking_D40_headroom_t','min')}/{field('booking_design_headroom_t','min')} | "
                      f"{field('arrival_B_over_d','max')} | {result['arrival_B_over_d_ge_3p3']}/{len(arrived)} | "
                      f"{result['seconds_per_emitted']:.2f} |" if emitted else
                      f"| {vessel} | {rung} | {generator} | 0/{len(cell)} | {result['accepted_backgrounds']}/{len(calls)} | "
                      f"{result['legal_full_booking_mass']}/{len(audits)} | {field('empty_witness_ballast_t')}/{field('booking_initial_ballast_t')} | "
                      f"{field('booking_D40_headroom_t','min')}/{field('booking_design_headroom_t','min')} | {field('arrival_B_over_d','max')} | "
                      f"{result['arrival_B_over_d_ge_3p3']}/{len(arrived)} | — |")
    print("\nDistributions below are min/median/max. Background TEU is the continuous equivalent D42 occupancy.")
    print("| Ship | Rung | Generator | Arrival B2.3 | Arrival GM m | Arrival KG m | Background TEU fraction | Background cargo t | Gate s/emitted | Generation s/emitted |")
    print("|---|---|---|---|---|---|---|---|---:|---:|")
    for cell in cells:
        def span(key):
            value = cell[key]
            return "/".join(f"{value[k]:.3f}" for k in ("min", "median", "max")) if value else "—"
        gate_time = f"{cell['gate_seconds_per_emitted']:.2f}" if cell["gate_seconds_per_emitted"] is not None else "—"
        total_time = f"{cell['seconds_per_emitted']:.2f}" if cell["seconds_per_emitted"] is not None else "—"
        print(f"| {cell['vessel']} | {cell['rung']} | {cell['generator']} | {span('arrival_b23_ratio')} | "
              f"{span('arrival_gm_m')} | {span('arrival_kg_m')} | {span('background_equivalent_teu_fraction')} | "
              f"{span('background_cargo_t')} | {gate_time} | {total_time} |")
    output = dict(source_keys=sorted({row["source_start"] for row in rows}),
                  mid_draw_source_changes=sum(row["source_start"] != row["source_end"] for row in rows), cells=cells)
    (HERE / f"{args.prefix}summary.json").write_text(json.dumps(output, indent=2, sort_keys=True)+"\n")


if __name__ == "__main__":
    main()
