"""Metadata-first D132 heat-effect inventory; TEST inputs/traces are excluded."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SOURCE = ROOT / "experiments/mvp/sft/pilot2/val15"


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


base_root = ROOT / "experiments/mvp/runs/base-mask/small"
base_config = read(base_root / "config.json")
assert all("/corpus/test/" in row["path"] for row in base_config["inputs"])
base_manifest = read(base_root / "manifest.json")
base = {"scope": "historical TEST metadata only; no inputs, traces, or per-instance artifacts read",
        "config_sha256": digest(base_root / "config.json"),
        "manifest_sha256": digest(base_root / "manifest.json"),
        "n_inputs": len(base_config["inputs"]), "outcomes": base_manifest["outcomes"],
        "causal_heat_count": None, "replay_performed": False,
        "policy": "D110/D132 forbid opening original or copied TEST instance bodies"}
records, configs, errors = [], {}, []
for arm in ("a", "c", "d"):
    root = SOURCE / arm
    config = read(root / "config.json")
    inputs = {row["sha256"]: row["path"] for row in config["inputs"]}
    for relative in inputs.values():
        assert (ROOT / relative).resolve().is_relative_to((ROOT / "experiments/mvp/corpus/val").resolve())
    configs[arm] = {"sha256": digest(root / "config.json"), "n_inputs": len(inputs),
                    "lookahead": config["planner_parameters"]["decoder_backend"] == "necessary-future-capacity-before-securing",
                    "c2": config["planner_parameters"]["engine_config"]["extra"]["global_screen"]}
    for path in sorted((root / "instances").glob("*.json")):
        row = read(path)
        assert row["input_sha256"] in inputs and row["input"] == inputs[row["input_sha256"]]
        if row["status"] != "dead_end":
            continue  # n_dead_end counts other samples too; selected final status is authoritative.
        instance_path = (ROOT / row["input"]).resolve()
        assert digest(instance_path) == row["input_sha256"]
        instance = read(instance_path)
        trace = root / "instances" / row["instance_id"] / "trace.jsonl"
        assert trace.resolve().is_relative_to((root / "instances").resolve())
        trace_rows = [json.loads(raw) for raw in trace.read_text().splitlines() if raw.strip()]
        actions = [r["chosen"]["action_id"] for r in trace_rows if r.get("chosen") and r["chosen"].get("action_id")]
        if len(actions) != row["metrics"]["n_actions"]:
            errors.append({"arm": arm, "id": row["instance_id"], "reason": "trace action count mismatch"})
            continue
        aboard = {p["container_id"] for p in instance["onboard"]["placements"]}
        for action in actions:
            if action["op"] == "P": aboard.add(action["box"])
            elif action["op"] in {"O", "D"}: aboard.discard(action["box"])
        load = set(instance["load_list"])
        remaining = [b["id"] for b in instance["containers"] if b["id"] in load and b["id"] not in aboard
                     and "away_from_heat" in b["special_stowage"]]
        sources = instance["vessel"]["heat_sources"]
        old_ids = {h["id"] for h in sources}
        corrected_ids = {h.get("tank_id") if h.get("tank_id") is not None else h["id"]
                         for h in sources if h["kind"] == "heated_tank"}
        heated = [t["id"] for t in instance["vessel"]["tanks"] if t["heated"]]
        old_missing = [t for t in heated if t not in old_ids]
        corrected_missing = [t for t in heated if t not in corrected_ids]
        records.append({"id": f"pilot2-{arm}-{row['instance_id']}", "arm": arm,
            "instance_id": row["instance_id"], "map": row["input"], "input_sha256": row["input_sha256"],
            "row_sha256": digest(path), "trace_sha256": digest(trace),
            "lookahead": configs[arm]["lookahead"], "actions": actions,
            "historical_message": row["message"], "remaining_heat": remaining,
            "old_missing_heated_tanks": old_missing, "corrected_missing_heated_tanks": corrected_missing,
            "structural_heat_rejection_at_prefix": bool(remaining and old_missing and not corrected_missing),
            "actual_prefix_replay_performed": False, "immediate_escape": None,
            "complete_plan_cure": None})
out = {"base_mask_small": base, "pilot2_configs": configs, "prefixes": records, "errors": errors,
       "counts_by_arm": {arm: dict(Counter(
           "structural_heat_rejection" if r["structural_heat_rejection_at_prefix"] else "no_structural_heat_rejection"
           for r in records if r["arm"] == arm)) for arm in configs},
       "test_input_bodies_read": False, "model_or_gpu_used": False,
       "caveat": "Inventory proof isolates one rejecting predicate; it does not prove any added action survives other screens or a completion passes the checker."}
(HERE / "val-prefixes.json").write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps({"prefixes": len(records), "counts_by_arm": out["counts_by_arm"], "errors": errors}))
