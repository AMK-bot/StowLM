"""Bounded, paired comparison of tiny/small raw draws before the medium repair."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(paths, include_medium=False):
    rows = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
    return {(row["generator"], row["vessel"], row["rung"], row["seed"]): row
            for row in rows if include_medium or row["rung"] in {"tiny", "small"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior", type=Path, default=HERE.parent / "final-draws.jsonl")
    parser.add_argument("--current", nargs="+", type=Path,
                        default=[HERE / "final-random.jsonl", HERE / "final-simulated.jsonl"])
    parser.add_argument("--out", type=Path, default=HERE / "paired-tiny-small.json")
    parser.add_argument("--include-medium", action="store_true")
    args = parser.parse_args()
    prior, current = load([args.prior], args.include_medium), load(args.current, args.include_medium)
    changes = defaultdict(list)
    numeric = defaultdict(lambda: {"comparisons": 0, "changed": 0, "max_absolute_difference": 0.})
    acceptance = Counter()
    missing_metrics = []
    for key in sorted(prior.keys() & current.keys()):
        left, right = prior[key], current[key]
        acceptance["prior_emitted"] += left["accepted"]
        acceptance["current_emitted"] += right["accepted"]
        if left["accepted"] != right["accepted"]:
            changes["pipeline_acceptance"].append(key)
        if not left["gate_calls"] or not right["gate_calls"]:
            missing_metrics.append(key)
            continue
        a, b = left["gate_calls"][0], right["gate_calls"][0]
        acceptance["prior_background_passed"] += a["accepted"]
        acceptance["current_background_passed"] += b["accepted"]
        if a["accepted"] != b["accepted"]:
            changes["background_acceptance"].append(key)
        def compare(name, x, y):
            if isinstance(x, (int, float)) and not isinstance(x, bool) and isinstance(y, (int, float)) and not isinstance(y, bool):
                item = numeric[name]
                item["comparisons"] += 1
                item["changed"] += x != y
                item["max_absolute_difference"] = max(item["max_absolute_difference"], abs(x-y))
        compare("witness_ballast_t", a["ballast_t"], b["ballast_t"])
        for metric in ("background_cargo_t", "background_teu_fraction", "background_mean_box_t", "deck_mean_box_t", "whole_ship_teu_fraction", "planned_cargo_t"):
            compare("utilisation."+metric, a["utilisation"].get(metric), b["utilisation"].get(metric))
        for phase in ("departure", "mid_voyage", "arrival"):
            sa, sb = a["states"].get(phase, {}), b["states"].get(phase, {})
            for metric in sa.keys() & sb.keys():
                compare(phase+"."+metric, sa[metric], sb[metric])
    output = {
        "scope": "Paired raw draws only; this is not a proof for unmeasured conditions or all checker changes.",
        "pairs": len(prior.keys() & current.keys()),
        "missing_current_pairs": sorted(prior.keys()-current.keys()),
        "prior_source_epochs": sorted({row["source_start"] for row in prior.values()}),
        "current_source_epochs": sorted({row["source_start"] for row in current.values()}),
        "acceptance_counts": dict(acceptance), "acceptance_changes": dict(changes),
        "missing_gate_pairs": missing_metrics, "numeric_metrics": dict(sorted(numeric.items())),
        "changed_numeric_metric_names": sorted(name for name, value in numeric.items() if value["changed"]),
        "numeric_comparisons": sum(value["comparisons"] for value in numeric.values()),
        "changed_numeric_comparisons": sum(value["changed"] for value in numeric.values()),
    }
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True)+"\n")
    print(json.dumps({key: value for key, value in output.items() if key != "numeric_metrics"}, indent=2))


if __name__ == "__main__":
    main()
