"""Reconcile existing sizing evidence without importing physics or a tokenizer.

This is arithmetic over recorded evidence, not a new token/latency measurement.
Run: uv run python experiments/action-space/sizing.py
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def planning_tokens(loads: int) -> int:
    """Ceil(8 tokens/action * 1.05 actions/load), excluding EOS.

    This expectation is not a worst-case bound: rounded override counts,
    longer indices and non-Place actions can exceed it.
    """
    if isinstance(loads, bool) or not isinstance(loads, int) or loads < 0:
        raise ValueError("loads must be a nonnegative integer")
    return (loads * 84 + 9) // 10


def current_ceilings() -> dict[str, int]:
    """Read only literal box ceilings; do not load vessel physics caches."""
    tree = ast.parse((ROOT / "src/stowage/generate/random_yard.py").read_text())
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "SIZE_LADDER"
                              for t in node.targets))
    return {ast.literal_eval(key): ast.literal_eval(call.args[0])
            for key, call in zip(assignment.value.keys, assignment.value.values)}


def report() -> dict:
    # These are reported feeder_500 defaults, not regenerated/certified instances.
    documented = (ROOT / "experiments/instances/README.md").read_text()
    defaults = {match[0]: int(match[1]) for match in re.findall(
        r"^\| (tiny|small|medium|full) \| [^|]+ \| (\d+) \|", documented, re.M)}
    if set(defaults) != set(current_ceilings()):
        raise ValueError("default-workload evidence table is missing a rung")
    measurements = json.loads((ROOT / "experiments/format/extended_measurements.json").read_text())
    return {
        "assumption": "ceil(loads * 8 * 1.05), excludes EOS; not a worst-case bound",
        "rungs": {name: {"ceiling": ceiling, "ceiling_tokens": planning_tokens(ceiling),
                          "documented_feeder_500_loads": defaults[name],
                          "documented_feeder_500_tokens": planning_tokens(defaults[name])}
                  for name, ceiling in current_ceilings().items()},
        "direct_planning_decision": {"loads": 1000, "tokens": planning_tokens(1000)},
        "recorded_dense_measurements": [
            {"source": row["source"], "loads": row["load_boxes"],
             "output_tokens_including_eos": row["output_tokens"],
             "planning_tokens_excluding_eos": planning_tokens(row["load_boxes"])}
            for row in measurements["rows"]],
    }


if __name__ == "__main__":
    print(json.dumps(report(), indent=2))
