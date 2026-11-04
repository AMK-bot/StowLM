"""Separate, conditional six-trajectory DG audit; never pool with raw draws."""
import json
from pathlib import Path
import statistics

from summarise import dg_group, deck_heavy_low

HERE = Path(__file__).resolve().parent


def main():
    rows = [json.loads(line) for line in (HERE/"retry-trajectories.jsonl").read_text().splitlines()]
    assert len(rows) == 6 and all(row["accepted"] for row in rows)
    observed = [{**row, "emitted": row["emitted_dg"]} for row in rows]
    summary = dict(measurement="Conditional production retry trajectories; not independent raw draws.",
        raw_draw_comparison="Never pooled: seed0 overlaps the independent108-raw-draw matrix.",
        overall=dg_group(observed), by_trajectory={}, emitted_mass_spread={})
    for row in observed:
        key = f"{row['vessel']}/{row['generator']}/seed{row['seed']}"
        summary["by_trajectory"][key] = dg_group([row])
        prefix = f"retry-{row['generator']}-{row['vessel']}-seed{row['seed']}"
        instance = json.loads((HERE/f"{prefix}.instance.json").read_text())
        condition = next(json.loads(source.split(":", 1)[1]) for source in instance["vessel"]["sources"]
                         if source.startswith("stowage.slice_condition:"))
        masses = [cell["mass_kg"] for cell in condition["background_cells"]]
        decks = {stack["stack_id"] for stack in condition["background_stacks"] if stack["on_deck"]}
        deck_masses = [cell["mass_kg"] for cell in condition["background_cells"] if cell["stack_id"] in decks]
        summary["emitted_mass_spread"][key] = dict(minimum_background_mass_kg=min(masses),
            deck_mass_min_kg=min(deck_masses), deck_mass_max_kg=max(deck_masses),
            deck_population_sd_kg=statistics.pstdev(deck_masses),
            deck_heavy_low=deck_heavy_low(condition))
    lines = ["# D5: six conditional production retry trajectories", "",
        "These six seed0 trajectories overlap seeds in the independent108-raw-draw matrix and are reported separately. They condition on production retry acceptance; they are not six new independent raw draws or a population-composition estimate. Every screen remains unchanged. The comparison uses the first target-provider inventory and the final emitted target inventory; replacement-provider calls and simulated yard blockers are not added to the initial denominator.", "",
        "| Ship | Generator | Attempts | Initial target DG | Final emitted DG | Cumulative gate s | Total/replay s |",
        "|---|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['vessel']} | {row['generator']} | {row['report']['attempts']} | "
            f"{row['cargo_events'][0]['inventory']['dg_boxes']} | {row['emitted_dg']['dg_boxes']} | "
            f"{row['gate_seconds']:.2f} | {row['wall_s']:.2f} |")
    overall = summary["overall"]
    lines += ["", f"Initial target inventory: {overall['candidate_boxes']} boxes/{overall['candidate_dg']} DG; "
        f"final emitted inventory: {overall['emitted_boxes']} boxes/{overall['emitted_dg']} DG. Shares below are conditional on DG boxes.", ""]
    for key, label in (("by_class", "IMDG class"), ("by_un", "UN"), ("by_un_group", "UN group")):
        lines += [f"| {label} | Profile expected share | Initial count/share | Emitted count/share |", "|---|---:|---:|---:|"]
        for name, values in overall[key].items():
            def pct(value):
                return f"{100*value:.2f}%" if value is not None else "—"
            lines.append(f"| {name} | {pct(values['expected_share'])} | {values['candidate_count']}/{pct(values['candidate_share'])} | "
                         f"{values['emitted_count']}/{pct(values['emitted_share'])} |")
        lines.append("")
    lines += ["The named UN group remains structurally excluded by unknown shipment/context assessments. The other final class shares are conditional, small-sample observations; sampling error and retry selection both affect them.", "",
        "| Emitted trajectory | Minimum background kg | Deck min/max/SD kg | Strict heavy-low / adjacent pairs | Heavier-above pairs (>1kg) |",
        "|---|---:|---:|---:|---:|"]
    for key, values in summary["emitted_mass_spread"].items():
        heavy = values["deck_heavy_low"]
        lines.append(f"| {key} | {values['minimum_background_mass_kg']:.6f} | "
            f"{values['deck_mass_min_kg']:.3f}/{values['deck_mass_max_kg']:.3f}/{values['deck_population_sd_kg']:.3f} | "
            f"{heavy['strict_heavy_low_pairs']}/{heavy['pairs']} | {heavy['heavier_above_pairs']} |")
    assert all(values["minimum_background_mass_kg"] >= 2200 and values["deck_population_sd_kg"] > 0
               and values["deck_heavy_low"]["heavier_above_pairs"] == 0 for values in summary["emitted_mass_spread"].values())
    (HERE/"retry-dg-shift.json").write_text(json.dumps(summary, indent=2, sort_keys=True)+"\n")
    (HERE/"retry-dg-shift.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({key:overall[key] for key in ("draws", "candidate_boxes", "candidate_dg", "emitted_boxes", "emitted_dg", "by_un_group")}))


if __name__ == "__main__":
    main()
