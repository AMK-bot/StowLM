"""Summarise raw draws, preserving the all-screen and background-only denominators."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import statistics
import sys


def span(values):
    values = [v for v in values if v is not None]
    if not values:
        return "—"
    return f"{min(values):.3f}/{statistics.median(values):.3f}/{max(values):.3f}"


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("draws.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    print("Ranges are min/median/max; gate acceptance excludes draws rejected before the gate.")
    print("| Ship | Rung | Generator | Accepted/raw | Background pass/checks | D56 design pass/checks | Arrival B2.3 | GM m | KG m | Ballast t | Background TEU utilisation | s/accepted |")
    print("|---|---|---|---:|---:|---:|---|---|---|---|---|---:|")
    reasons = Counter()
    for vessel in ("feeder_500", "feeder_800", "feeder_1700"):
        for rung in ("tiny", "small", "medium"):
            for generator in ("random", "simulated"):
                cell = [r for r in rows if (r["vessel"], r["rung"], r["generator"]) == (vessel, rung, generator)]
                if not cell:
                    continue
                accepted = [r for r in cell if r["accepted"]]
                # Only one background per raw draw; exclude observer's explicit cache repeat.
                calls = [r["gate_calls"][0] for r in cell if r["gate_calls"]]
                gates = [c for c in calls if c["accepted"]]
                arrival = [c["states"].get("arrival", {}) for c in calls]
                util = [c["utilisation"]["background_teu_fraction"] for c in calls]
                ballast = [c["ballast_t"] for c in calls]
                seconds = sum(r["wall_s"] for r in cell) / len(accepted) if accepted else None
                designs = [r["design_lashing"] for r in cell if r.get("design_lashing")]
                design_pass = sum(all(s["passed"] for s in d.values()) for d in designs)
                time_text = f"{seconds:.2f}" if seconds is not None else "—"
                print(f"| {vessel} | {rung} | {generator} | {len(accepted)}/{len(cell)} | {len(gates)}/{len(calls)} | "
                      f"{design_pass}/{len(designs)} | {span([s.get('b23_ratio') for s in arrival])} | "
                      f"{span([s.get('gm_m') for s in arrival])} | {span([s.get('kg_m') for s in arrival])} | "
                      f"{span(ballast)} | {span(util)} | {time_text} |")
                for r in cell:
                    reasons.update(r["report"].get("rejections", {}))
    print("\nRejection reason counts:", json.dumps(reasons, sort_keys=True))
    print("Concurrent source changes:", sum(r["source_start"] != r["source_end"] for r in rows), "draws")
    print("Total measured wall seconds:", round(sum(r["wall_s"] for r in rows), 2))


if __name__ == "__main__":
    main()
