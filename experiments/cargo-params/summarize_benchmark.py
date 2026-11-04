"""Summarize inert, locally pinned CC0 benchmark lists; never execute source data.

Run: uv run python experiments/cargo-params/summarize_benchmark.py
These deep-sea benchmark frequencies are context, not fitted feeder parameters.
"""

from collections import Counter
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    paths = sorted((root / "data/raw/larsen-pacino/container_instances").glob("*/*.txt"))
    assert paths, "Pinned public benchmark data missing"
    counts = {key: Counter() for key in ("length_ft", "equipment", "weight_class")}
    total = 0
    for path in paths:
        lines = path.read_text().splitlines()
        declared_count = int(lines[1].split()[1])
        start = next(i for i, line in enumerate(lines) if line.startswith("# Container:"))
        types = {}
        for line in lines[3:start]:
            type_id, length, weight, equipment = line.split()
            types[type_id] = (length, weight, equipment)
        rows = [line.split() for line in lines[start + 1:] if line.strip() and not line.startswith("#")]
        assert len(rows) == declared_count, path
        for row in rows:
            length, weight, equipment = types[row[2]]
            counts["length_ft"][length] += 1
            counts["equipment"][equipment] += 1
            counts["weight_class"][weight] += 1
        total += len(rows)
    print(json.dumps({
        "source": "https://github.com/maritimelab/Stowage-Planning-Benckmark/tree/4b54b2100662f7d928af47b1139a914cde8f71e3/container_instances",
        "read_date": "2026-10-27",
        "files": len(paths), "boxes": total,
        "note": "Weight field unit is unverified; weight_class preserves source labels. Full/empty not declared. RC+HR are reefers; HC+HR are high cube.",
        "counts": {key: dict(sorted(value.items())) for key, value in counts.items()},
        "shares": {key: {label: count / total for label, count in sorted(value.items())} for key, value in counts.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
