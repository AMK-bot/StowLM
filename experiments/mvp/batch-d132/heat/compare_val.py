"""Classify immediate engine escapes; never infer a full-plan cure."""
from collections import Counter
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
baseline = json.loads((HERE / "baseline-prefix-replay.json").read_text())
candidate = json.loads((HERE / "candidate-prefix-replay.json").read_text())
assert baseline["manifest_sha256"] == candidate["manifest_sha256"]
assert baseline["source_stable"] and candidate["source_stable"]
left = {r["id"]: r for r in baseline["results"]}
right = {r["id"]: r for r in candidate["results"]}
assert left.keys() == right.keys()
results, counts = [], Counter()
for key, before in left.items():
    after = right[key]
    result = {"id": key, "arm": before["arm"], "both_replayed": before["replayed"] and after["replayed"],
              "structural_heat_rejection_at_prefix": before["structural_heat_rejection_at_prefix"],
              "full_plan_cure": None, "full_checker_run": False}
    if not result["both_replayed"]:
        result.update(classification="unreplayable_current_prefix", errors=[before.get("error"), after.get("error")])
    else:
        canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
        a, b = {canonical(v) for v in before["legal"]}, {canonical(v) for v in after["legal"]}
        removed, added = a - b, b - a
        heat_isolated = bool(result["structural_heat_rejection_at_prefix"]
            and all(v["inventory_missing"] and v["e8_only_allowed"] == 0 for v in before["raw_heat"].values())
            and any(not v["inventory_missing"] and v["e8_only_allowed"] > 0 for v in after["raw_heat"].values()))
        immediate = not a and bool(b) and heat_isolated and not removed
        result.update(baseline_legal=len(a), candidate_legal=len(b), removed=[json.loads(v) for v in sorted(removed)],
            added=[json.loads(v) for v in sorted(added)], baseline_raw_heat=before["raw_heat"],
            candidate_raw_heat=after["raw_heat"], heat_filter_isolated=heat_isolated,
            immediate_engine_escape=immediate,
            classification="unexpected_deletion" if removed else "heat_immediate_engine_escape" if immediate
                else "heat_filter_fixed_but_no_engine_escape" if heat_isolated else "no_isolated_heat_escape",
            backtracking_caveat=("Original decoder ban/rewind state is not archived in these committed action rows; this is engine-prefix replay."
                                if before["arm"] in {"c", "d"} else None))
    counts[f"{result['arm']}:{result['classification']}"] += 1
    results.append(result)
value = {"results": results, "counts": dict(counts), "n_prefixes": len(results),
         "test_effect_measured": False, "test_bodies_read": False,
         "no_full_plan_cure_claimed": True,
         "qualified_prefix_comparison": all(r["both_replayed"] and r["classification"] != "unexpected_deletion" for r in results)}
(HERE / "val-comparison.json").write_text(json.dumps(value, indent=2) + "\n")
print(json.dumps({k: value[k] for k in ("counts", "n_prefixes", "qualified_prefix_comparison")}))
