"""Derive mass realism from persisted D58 audits without rerunning any physics.

TEU counts refer to D42 continuous equivalent ISO20 background records. A mass
below the C3 empty-box reference of 2.2 t is reported explicitly: such a record
is an equivalent point load, not an actual empty ISO container. The checker is
unchanged; this report prevents interpreting continuous occupancy as measured
physical container utilisation.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent


def distribution(values):
    return {"min": min(values), "median": statistics.median(values), "max": max(values)} if values else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path,
                        default=[HERE / "matrix-random.jsonl", HERE / "matrix-simulated.jsonl"])
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()
    rows = [json.loads(line) for path in args.inputs
            for line in path.read_text().splitlines()]
    output = []
    grouped = defaultdict(list)
    for row in rows:
        if not row.get("gate_calls"):
            continue
        call = row["gate_calls"][0]
        audit = call["utilisation"]["condition"]
        masses = [cell["mass_kg"] for cell in audit["background_cells"]]
        stacks = {stack["stack_id"]: stack for stack in audit["background_stacks"]}
        deck = [cell["mass_kg"] for cell in audit["background_cells"] if stacks[cell["stack_id"]]["on_deck"]]
        record = {"vessel": row["vessel"], "rung": row["rung"], "generator": row["generator"], "seed": row["seed"],
                  "equivalent_boxes": len(masses), "sub_2200kg_boxes": sum(mass < 2200-1e-6 for mass in masses),
                  "sub_2350kg_boxes": sum(mass < 2350-1e-6 for mass in masses),
                  "box_mass_kg": distribution(masses), "deck_mass_kg": distribution(deck),
                  "background_equivalent_teu_fraction": call["utilisation"]["background_teu_fraction"],
                  "background_cargo_t": call["utilisation"]["background_cargo_t"],
                  "mean_background_box_t": call["utilisation"]["background_mean_box_t"],
                  "mean_deck_box_t": call["utilisation"]["deck_mean_box_t"],
                  "accepted_instance_whole_teu_fraction": row.get("utilisation", {}).get("whole_ship_teu_fraction"),
                  "maximum_background_stack_mass_rating_fraction": max(
                      stack["mass_kg"] / stack["max_mass_20_kg"] for stack in audit["background_stacks"]),
                  "source_start": row["source_start"], "source_end": row["source_end"]}
        output.append(record)
        grouped[(row["vessel"], row["rung"], row["generator"])].append(record)
    (HERE / f"{args.prefix}realism-draws.jsonl").write_text("".join(json.dumps(row, sort_keys=True)+"\n" for row in output))
    summary = []
    for (vessel, rung, generator), records in sorted(grouped.items()):
        summary.append({"vessel": vessel, "rung": rung, "generator": generator, "draws": len(records),
            **{key: distribution([r[key] for r in records if r.get(key) is not None]) for key in (
                "equivalent_boxes", "sub_2200kg_boxes", "sub_2350kg_boxes", "background_equivalent_teu_fraction",
                "accepted_instance_whole_teu_fraction", "background_cargo_t", "mean_background_box_t", "mean_deck_box_t",
                "maximum_background_stack_mass_rating_fraction")},
            "minimum_equivalent_box_mass_kg": min(r["box_mass_kg"]["min"] for r in records),
            "maximum_deck_equivalent_box_mass_kg": max(r["deck_mass_kg"]["max"] for r in records if r["deck_mass_kg"]),
        })
    (HERE / f"{args.prefix}realism-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True)+"\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
