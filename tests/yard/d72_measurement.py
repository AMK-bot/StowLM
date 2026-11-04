"""Paired, seeded D72 synthetic diagnostic; run explicitly, not as a pytest.

The fixed cohort is 20 seeds, two overlapping small calls, and 24 twenty-foot
boxes. Each vessel imports four boxes before loading eight exports. Call A's
exports enter before call B's, allowing B exports and A imports to bury A's
exports. This measures an illustrative synthetic workload, not terminal data
or a general performance estimate. Both runs enable A8/rule freeze; the baseline
must be a source snapshot from before D72, not the switch-off legacy simulator.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys

from stowage.conventions import make_container_id
from stowage.model.fixtures import build_instance
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import Placement, ShipState
from stowage.model.yard import YardState
from stowage.ports import load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import ShipCall
from stowage.yard import simulator


SEEDS = tuple(range(20))
NOISES = (0, 0.2, 0.5, 0.8)


def workload():
    """Fixed demand and gate schedule, independent of seed and simulator."""
    calls = []
    for number, call_id in enumerate(("a", "b")):
        instance = build_instance()
        instance.port.max_crane_height_tiers = 4
        instance.rotation = Rotation(service="synthetic_d72", calls=[
            PortCall(index=i, locode=p)
            for i, p in enumerate(("THLCH", "SGSIN", "MYPKG", "THLCH"))])
        prototype = instance.containers[2]
        boxes = [prototype.model_copy(update={
            "id": make_container_id("QZXU", 9000 + number * 100 + i),
            "pol": "THLCH" if i < 8 else "SGSIN",
            "pod": "SGSIN" if i < 8 else "THLCH",
            "category": None, "special_stowage": [], "oog": None,
            "weight_class": "light", "direct_delivery": False,
        }) for i in range(12)]
        instance.containers = boxes
        instance.load_list = [box.id for box in boxes[:8]]
        instance.discharge_list = [box.id for box in boxes[8:]]
        instance.stage = "B"
        instance.onboard = ShipState(vessel_id=instance.vessel.id, placements=[
            Placement(slot=slot, container_id=box.id)
            for slot, box in zip(("0010182", "0010282", "0030182", "0030282"), boxes[8:])])
        instance.yard = YardState(yard_id=instance.port.yard.id)
        start = 1000 + 1000 * number
        calls.append(ShipCall(call_id, instance, start, start - 100,
                              {cid: 100 * number + i for i, cid in enumerate(instance.load_list)}))
    return calls


def measure(module, *, seed, noise):
    calls = workload()
    with rule_freeze_v1():
        result = module.simulate_port(calls, load_simulator_params("laem_chabang_like"),
                                      seed=seed, noise=noise)
    snapshot = next(s for s in result.snapshots if s.call_id == "a")
    targets = set(calls[0].instance.load_list)
    present = {cid for stack in snapshot.yard.stacks for cid in stack.boxes}
    other = present - targets
    mixed = set()
    for stack in snapshot.yard.stacks:
        target_below = False
        for cid in stack.boxes:
            if cid in targets:
                target_below = True
            elif target_below:
                mixed.add(cid)
    return {
        "seed": seed,
        "simulator_version": result.simulator_version,
        "target_rehandles": sum(record.call_id == "a" and record.move.kind == "relocate"
                                for record in result.moves),
        "own_export_rehandles": sum(record.call_id == "a" and record.move.kind == "relocate"
                                    and record.move.container_id in targets for record in result.moves),
        "other_box_rehandles": sum(record.call_id == "a" and record.move.kind == "relocate"
                                   and record.move.container_id not in targets for record in result.moves),
        "target_snapshot_boxes": len(present & targets),
        "other_snapshot_boxes": len(other),
        "other_above_target_boxes": len(mixed),
        "other_above_target_share": len(mixed) / len(other) if other else 0.0,
        "target_unserved_loads": len(targets.intersection(result.unserved_loads)),
        "all_unserved_loads": len(result.unserved_loads),
        "all_unserved_discharges": len(result.unserved_discharges),
        "max_snapshot_height": max((len(s.boxes) for snapshot in result.snapshots
                                     for s in snapshot.yard.stacks), default=0),
        "mixing_counters": dict(result.__dict__.get("_congestion_mixing", {})),
    }


def load_baseline(path):
    name = "stowage.yard._d72_before_measurement"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def summarize(records):
    instrumented = any(r["mixing_counters"] for r in records)
    eligible = sum(r["mixing_counters"].get("eligible_boxes", 0) for r in records)
    selected = sum(r["mixing_counters"].get("mixing_selected_boxes", 0) for r in records)
    reached = sum(r["mixing_counters"].get("mixed_boxes", 0) for r in records)
    return {
        "mean_target_rehandles": statistics.mean(r["target_rehandles"] for r in records),
        "mean_own_export_rehandles": statistics.mean(r["own_export_rehandles"] for r in records),
        "mean_other_box_rehandles": statistics.mean(r["other_box_rehandles"] for r in records),
        "mean_other_above_target_boxes": statistics.mean(r["other_above_target_boxes"] for r in records),
        "mean_other_above_target_share": statistics.mean(r["other_above_target_share"] for r in records),
        "complete_target_snapshot_cohorts": sum(r["target_snapshot_boxes"] == 8 for r in records),
        "target_unserved_loads": sum(r["target_unserved_loads"] for r in records),
        "all_unserved_loads": sum(r["all_unserved_loads"] for r in records),
        "all_unserved_discharges": sum(r["all_unserved_discharges"] for r in records),
        "max_snapshot_height": max(r["max_snapshot_height"] for r in records),
        "eligible_boxes_all_calls": eligible if instrumented else None,
        "mixing_selected_boxes_all_calls": selected if instrumented else None,
        "mixed_boxes_all_calls": reached if instrumented else None,
        "selected_share_all_calls": selected / eligible if eligible else None,
        "reached_share_all_calls": reached / eligible if eligible else None,
    }


def complete_pair_summary(paired):
    complete = [i for i, (before, after) in enumerate(zip(paired["before"], paired["after"]))
                if all(r["target_snapshot_boxes"] == 8 and r["target_unserved_loads"] == 0
                       for r in (before, after))]
    return {
        "seed_count": len(complete),
        "seeds": [paired["before"][i]["seed"] for i in complete],
        "before_mean_target_rehandles": statistics.mean(paired["before"][i]["target_rehandles"]
                                                         for i in complete) if complete else None,
        "after_mean_target_rehandles": statistics.mean(paired["after"][i]["target_rehandles"]
                                                        for i in complete) if complete else None,
    }


def run(baseline):
    rows = []
    for noise in NOISES:
        paired = {label: [measure(module, seed=seed, noise=noise) for seed in SEEDS]
                  for label, module in (("before", baseline), ("after", simulator))}
        row = {"noise": noise, "runs": paired}
        for label, records in paired.items():
            row[label] = summarize(records)
        row["paired_complete_targets"] = complete_pair_summary(paired)
        rows.append(row)
    return {
        "label": "synthetic diagnostic, not a general terminal performance estimate",
        "seeds": list(SEEDS), "switch": "rule freeze v1 enabled for both runs",
        "cohort": "2 calls x (8 exports + 4 imports); all 20ft; yard3/H4; target call a",
        "gate_times": "a exports 0..7; b exports 100..107; starts a=1000,b=2000 seconds",
        "share_denominator": "non-target boxes present in target loading-start snapshot",
        "confounding": "active unmixed D25 clustering also differs from pre-D72 cluster-error dispersal; own-export and other-box rehandles are split; limited spare atop-export tiers can saturate realized mixing",
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(load_baseline(args.baseline))
    result["baseline_source_sha256"] = hashlib.sha256(args.baseline.read_bytes()).hexdigest()
    result["current_source_sha256"] = hashlib.sha256(Path(simulator.__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    for row in result["rows"]:
        print(json.dumps({key: value for key, value in row.items() if key != "runs"}, sort_keys=True))
