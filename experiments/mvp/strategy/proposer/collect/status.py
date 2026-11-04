"""Read-only progress table from the per-map summaries. Not a verdict: only report.py computes D185's."""
from __future__ import annotations

import glob
import json
import sys


def main() -> int:
    size = lambda folder: "medium" if "/medium-" in folder else "small"  # noqa: E731
    any_medium_k4 = False
    for arm in ("rule", "search", "spark", "glimmer"):
        rows = {"small": [], "medium": []}
        for path in glob.glob(f"experiments/mvp/strategy/proposer/runs/d185-{arm}/maps/*/summary.json"):
            try:
                rows[size(path)].append(json.load(open(path)))
            except ValueError:
                continue
        for name, group in rows.items():
            k1 = [r["prefixes"]["1"] for r in group if "1" in r.get("prefixes", {})]
            k4 = [r["prefixes"]["4"] for r in group if "4" in r.get("prefixes", {})]
            moves = [r["best_moves"] for r in k4 if r["best_moves"] is not None]
            print(f"{arm:8s}{name:7s}started {len(group):2d} k1 {len(k1):2d} (pass {sum(r['passed'] for r in k1):2d}) "
                  f"k4 {len(k4):2d} (pass {len(moves):2d}, mean {sum(moves) / len(moves) if moves else 0:.1f}) "
                  f"incomplete-final {sum(1 for r in group if r.get('stop_reason') and not r.get('complete'))}")
            any_medium_k4 |= name == "medium" and bool(k4)
    return 0 if any_medium_k4 or "--all" in sys.argv else 3


if __name__ == "__main__":
    raise SystemExit(main())
