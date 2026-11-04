"""Summarise D58 review draws and the D5 selection shift without changing demand.

Candidate denominators use only the first cargo-provider call in each raw draw:
the simulated generator later requests empty yard blockers from the same API.
Shares are conditional on DG boxes, not all boxes. We report every class and UN,
including zeros, and stratify to avoid comparing small and large bookings as if
they had the same selection probability. This is a selection audit, not a plan
certificate or a statistically precise population estimate.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent


def distribution(values):
    values = [value for value in values if value is not None]
    return dict(min=min(values), median=statistics.median(values), max=max(values)) if values else None


def span(values):
    values = distribution(values)
    return "/".join(f"{values[key]:.3f}" for key in ("min", "median", "max")) if values else "—"


def histogram(records, key):
    total = Counter()
    for record in records:
        total.update(record.get(key, {}))
    return total


def deck_heavy_low(condition):
    from stowage.conventions import parse_slot
    stacks = {row["stack_id"] for row in condition["background_stacks"] if row["on_deck"]}
    columns = defaultdict(list)
    for cell in condition["background_cells"]:
        if cell["stack_id"] in stacks:
            columns[cell["stack_id"]].append((parse_slot(cell["cell_id"]).tier, cell["mass_kg"]))
    pairs = [(lower[1], upper[1]) for cells in columns.values()
             for lower, upper in zip(sorted(cells), sorted(cells)[1:])]
    strict = sum(lower > upper+1. for lower, upper in pairs)
    reverse = sum(upper > lower+1. for lower, upper in pairs)
    return dict(pairs=len(pairs), strict_heavy_low_pairs=strict, heavier_above_pairs=reverse,
                strict_heavy_low_fraction=strict/len(pairs) if pairs else None)


def dg_group(records):
    candidates = [row["cargo_events"][0] for row in records if row.get("cargo_events")]
    inventories = [event["inventory"] for event in candidates if "inventory" in event]
    emitted = [row["emitted"] for row in records if row.get("accepted") and row.get("emitted")]
    expected_counts = {"by_class": Counter(), "by_un": Counter()}
    for event in candidates:
        if "inventory" not in event:
            continue
        request = event["requested"]
        # Profile expectations are weighted by observed eligible loaded boxes.
        expected_dg = event["realised_eligible_loaded_boxes"] * request["eligible_loaded_box_dg_probability"]
        for key, source in (("by_class", "conditional_dg_class_probability"),
                            ("by_un", "conditional_dg_un_probability")):
            for label, share in request[source].items():
                expected_counts[key][label] += expected_dg * share
    output = dict(draws=len(records), emitted_draws=len(emitted), candidate_provider_events=len(candidates),
                  candidate_provider_errors=sum("error" in event for event in candidates),
                  candidate_boxes=sum(row["boxes"] for row in inventories),
                  emitted_boxes=sum(row["boxes"] for row in emitted),
                  candidate_dg=sum(row["dg_boxes"] for row in inventories),
                  emitted_dg=sum(row["dg_boxes"] for row in emitted),
                  expected_dg=sum(expected_counts["by_un"].values()),
                  unknown_required_facts_by_un=dict(histogram(inventories, "unknown_required_facts_by_un")),
                  unknown_sg_verification_by_un=dict(histogram(inventories, "unknown_sg_verification_by_un")))
    for key in ("by_class", "by_un"):
        before, after = histogram(inventories, key), histogram(emitted, key)
        total_expected = sum(expected_counts[key].values())
        rows = {}
        for label in sorted(set(before) | set(after) | set(expected_counts[key])):
            rows[label] = dict(expected_count=expected_counts[key][label], candidate_count=before[label],
                emitted_count=after[label],
                expected_share=expected_counts[key][label]/total_expected if total_expected else None,
                candidate_share=before[label]/output["candidate_dg"] if output["candidate_dg"] else None,
                emitted_share=after[label]/output["emitted_dg"] if output["emitted_dg"] else None)
        output[key] = rows
    groups = {
        "lithium_batteries_UN3480": {"3480"}, "propane_UN1978": {"1978"},
        "hypochlorite_UN1748": {"1748"}, "organic_peroxides_UN3109": {"3109"},
        "chlorine_UN1017": {"1017"},
        "review_named_provision_bearing_UNs": {"3480", "1978", "1748", "3109", "1017"},
    }
    output["by_un_group"] = {}
    for name, members in groups.items():
        members = [output["by_un"][un] for un in members if un in output["by_un"]]
        totals = {key: sum(member[key] for member in members) for key in
                  ("expected_count", "candidate_count", "emitted_count")}
        output["by_un_group"][name] = dict(**totals,
            expected_share=totals["expected_count"]/output["expected_dg"] if output["expected_dg"] else None,
            candidate_share=totals["candidate_count"]/output["candidate_dg"] if output["candidate_dg"] else None,
            emitted_share=totals["emitted_count"]/output["emitted_dg"] if output["emitted_dg"] else None)
    return output


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--cargo-inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=HERE)
    args = parser.parse_args()
    rows = [json.loads(line) for path in args.inputs for line in path.read_text().splitlines()]
    cargo = [json.loads(line) for path in args.cargo_inputs for line in path.read_text().splitlines()]
    keys = [(row["generator"], row["vessel"], row["rung"], row["seed"]) for row in rows]
    assert len(keys) == len(set(keys)), "duplicate raw draw"
    cargo_keys = [(row["generator"], row["vessel"], row["rung"], row["seed"]) for row in cargo]
    assert set(keys) == set(cargo_keys), "raw/cargo observations differ"
    output = dict(draws=len(rows), source_epochs=dict(Counter(row["source_start"] for row in rows)),
        mid_draw_source_changes=sum(row["source_start"] != row["source_end"] for row in rows), cells=[])
    lines = ["Ranges are min/median/max. Every call has max_attempts=1; C1 on, C2 off; the full-checker background gate runs normally.",
        "", "| Ship | Rung | Generator | Emitted/raw | Background pass/checks | D56 pass/checks | Arrival B2.3 | GM m | KG m | Witness ballast t | Background TEU use | s/emitted | Min box t | Deck min/max/SD t |",
        "|---|---|---|---:|---:|---:|---|---|---|---|---|---:|---:|---|"]
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["vessel"], row["rung"], row["generator"])].append(row)
    def cell_order(key):
        return (("feeder_500", "feeder_800", "feeder_1700").index(key[0]),
                ("tiny", "small", "medium").index(key[1]), key[2])
    for key in sorted(grouped, key=cell_order):
        records = grouped[key]
        calls = [row["gate_calls"][0] for row in records if row.get("gate_calls")]
        arrival = [call["states"].get("arrival", {}) for call in calls]
        masses = [call["utilisation"].get("background_box_mass_min_kg") for call in calls]
        deck = [call["utilisation"].get("deck_box_mass_distribution_kg") for call in calls]
        deck = [value for value in deck if value]
        sizing = [call["utilisation"]["mass_sizing_audit"] for call in calls]
        heavy_low = [deck_heavy_low(call["utilisation"]["condition"]) for call in calls]
        designs = [row["design_lashing"] for row in records if row.get("design_lashing")]
        emitted = sum(row["accepted"] for row in records)
        seconds = sum(row["wall_s"] for row in records)/emitted if emitted else None
        cell = dict(vessel=key[0], rung=key[1], generator=key[2], raw_draws=len(records), emitted=emitted,
            background_checks=len(calls), background_passes=sum(call["accepted"] for call in calls),
            design_lashing_checks=len(designs),
            design_lashing_passes=sum(all(state["passed"] for state in design.values()) for design in designs),
            arrival_b23_ratio=distribution([state.get("b23_ratio") for state in arrival]),
            arrival_gm_m=distribution([state.get("gm_m") for state in arrival]),
            arrival_kg_m=distribution([state.get("kg_m") for state in arrival]),
            witness_ballast_t=distribution([call["ballast_t"] for call in calls]),
            background_teu_utilisation=distribution([call["utilisation"]["background_teu_fraction"] for call in calls]),
            background_mass_min_kg=min((mass for mass in masses if mass is not None), default=None),
            deck_box_mass_min_kg=min((value["min"] for value in deck), default=None),
            deck_box_mass_max_kg=max((value["max"] for value in deck), default=None),
            deck_box_population_sd_kg=distribution([value["population_std"] for value in deck]),
            deck_adjacent_pairs=sum(value["pairs"] for value in heavy_low),
            deck_strict_heavy_low_pairs=sum(value["strict_heavy_low_pairs"] for value in heavy_low),
            deck_heavier_above_pairs=sum(value["heavier_above_pairs"] for value in heavy_low),
            deck_strict_heavy_low_fraction=distribution([value["strict_heavy_low_fraction"] for value in heavy_low]),
            generation_seconds=distribution([row["wall_s"] for row in records]),
            seconds_per_emitted=seconds,
            gate_seconds_per_emitted=sum(call["elapsed_s"] for call in calls)/emitted if emitted else None,
            booking_ballast_t=distribution([audit["booking_initial_adjustable_mass_kg"]/1000 for audit in sizing]),
            full_booking_mass_legal=sum(audit["full_booking_initial_mass_legal"] for audit in sizing),
            full_booking_D40_headroom_t=distribution([audit["full_booking_initial_D40_headroom_kg"]/1000 for audit in sizing]),
            full_booking_design_headroom_t=distribution([audit["full_booking_initial_design_headroom_kg"]/1000 for audit in sizing]),
            arrival_B_over_d=distribution([audit["D57"]["arrival"]["beam_over_draught"] for audit in sizing if "arrival" in audit.get("D57", {})]),
            arrival_B_over_d_ge_3p3=sum(audit["D57"]["arrival"]["beam_over_draught_ge_3p3"] for audit in sizing if "arrival" in audit.get("D57", {})),
            fixed_consumables_preserved=all(audit["fixed_consumable_tanks_unchanged"] for audit in sizing),
            reference_sizing_status_counts=dict(Counter(audit["booking_reference_sizing_status"] for audit in sizing)),
            rejections=dict(sum((Counter(row["report"].get("rejections", {})) for row in records), Counter())))
        output["cells"].append(cell)
        time_text = f"{seconds:.2f}" if seconds is not None else "—"
        minimum = f"{cell['background_mass_min_kg']/1000:.3f}" if cell["background_mass_min_kg"] is not None else "—"
        deck_text = (f"{cell['deck_box_mass_min_kg']/1000:.3f}/{cell['deck_box_mass_max_kg']/1000:.3f}/"
            f"{cell['deck_box_population_sd_kg']['median']/1000:.3f}") if deck else "—"
        lines.append(f"| {' | '.join(key)} | {emitted}/{len(records)} | {cell['background_passes']}/{len(calls)} | "
            f"{cell['design_lashing_passes']}/{len(designs)} | "
            f"{span([state.get('b23_ratio') for state in arrival])} | {span([state.get('gm_m') for state in arrival])} | "
            f"{span([state.get('kg_m') for state in arrival])} | {span([call['ballast_t'] for call in calls])} | "
            f"{span([call['utilisation']['background_teu_fraction'] for call in calls])} | {time_text} | {minimum} | {deck_text} |")
    lines += ["", "Witness ballast remains separate from the booking's initial ballast and displacement budget.", "",
        "| Ship | Rung | Generator | Full booking mass legal | Booking ballast t | Min D40/design room t | Max arrival B/d | Arrival B/d≥3.3 | Gate s/emitted |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for cell in output["cells"]:
        def metric(key, statistic="median"):
            value = cell[key]
            return f"{value[statistic]:.3f}" if value else "—"
        gate_time = f"{cell['gate_seconds_per_emitted']:.2f}" if cell["gate_seconds_per_emitted"] is not None else "—"
        lines.append(f"| {cell['vessel']} | {cell['rung']} | {cell['generator']} | {cell['full_booking_mass_legal']}/{cell['background_checks']} | "
            f"{metric('booking_ballast_t')} | {metric('full_booking_D40_headroom_t', 'min')}/{metric('full_booking_design_headroom_t', 'min')} | "
            f"{metric('arrival_B_over_d', 'max')} | {cell['arrival_B_over_d_ge_3p3']} | {gate_time} |")
    lines += ["", "Deck heavy-low is nontrivial when a lower box exceeds the next upper box by more than 1 kg.", "",
        "| Ship | Rung | Generator | Strict heavy-low pairs / adjacent pairs | Heavier-above pairs (>1 kg) | Per-background strict fraction min/median/max |",
        "|---|---|---|---:|---:|---|"]
    for cell in output["cells"]:
        strict = cell["deck_strict_heavy_low_fraction"]
        spread = "/".join(f"{strict[key]:.3f}" for key in ("min", "median", "max")) if strict else "—"
        lines.append(f"| {cell['vessel']} | {cell['rung']} | {cell['generator']} | "
            f"{cell['deck_strict_heavy_low_pairs']}/{cell['deck_adjacent_pairs']} | {cell['deck_heavier_above_pairs']} | {spread} |")
    dg = {"overall": dg_group(cargo), "by_generator_rung": {}, "by_cell": {}}
    for generator, rung in sorted({(row["generator"], row["rung"]) for row in cargo}):
        dg["by_generator_rung"][f"{generator}/{rung}"] = dg_group([row for row in cargo if (row["generator"], row["rung"]) == (generator, rung)])
    for key in sorted(grouped):
        dg["by_cell"]["/".join(key)] = dg_group([row for row in cargo if (row["vessel"], row["rung"], row["generator"]) == key])
    output["dg"] = dg
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir/"final-summary.json").write_text(json.dumps(output, indent=2, sort_keys=True)+"\n")
    (args.out_dir/"final-tables.md").write_text("\n".join(lines)+"\n")
    dg_lines = ["D5 selection audit: shares are conditional on dangerous-goods boxes; all raw draws count, including rejected draws.",
        "The candidate column uses the first target-provider inventory only; empty yard blockers are excluded. Expected counts use profile probabilities and each observed eligible-loaded-box count. Per-cell and generator/rung strata are in final-summary.json.", ""]
    for key, label in (("by_class", "IMDG class"), ("by_un", "UN"), ("by_un_group", "UN group")):
        dg_lines += [f"| {label} | Profile expected share | Candidate count/share | Emitted count/share |", "|---|---:|---:|---:|"]
        for name, record in dg["overall"][key].items():
            def percent(value):
                return f"{100*value:.2f}%" if value is not None else "—"
            dg_lines.append(f"| {name} | {percent(record['expected_share'])} | {record['candidate_count']}/{percent(record['candidate_share'])} | {record['emitted_count']}/{percent(record['emitted_share'])} |")
        dg_lines.append("")
    (args.out_dir/"dg-shift.md").write_text("\n".join(dg_lines)+"\n")
    print(json.dumps({key: output[key] for key in ("draws", "source_epochs", "mid_draw_source_changes")}))


if __name__ == "__main__":
    main()
