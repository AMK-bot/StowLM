"""Small, single-core sensitivity run; does not certify any departure plan."""
from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter

from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model.port import PortCall, Rotation
from stowage.model.yard import YardState
from stowage.params import load_param_file
from stowage.ports.profiles import load_port, load_simulator_params, scaled_yard
from stowage.yard.simulator import SIMULATOR_VERSION, sample_ship_calls, simulate_port


STATISTICS = ("occupancy_teu_fraction", "mean_nonempty_stack_height",
              "mean_height_per_teu_ground_position", "max_stack_height",
              "blocked_target_share", "target_presence_fraction")


def build_scenario():
    """Retain the original small experiment's workload and layout."""
    # APPROXIMATION: this experiment uses the existing model fixture's whole
    # hull and small ship geometry, not a calibrated fleet allocation. Yard
    # dimensions and traffic volumes shrink; five-tier equipment limits remain.
    fixture = build_instance()
    port = scaled_yard(load_port("laem_chabang_like"), blocks=1, bays=12, rows=2)
    route = load_param_file("cargo", "feeder_export")["rotations"][0]
    rotation = Rotation(service=route["id"], calls=[PortCall(locode=p, index=i)
                        for i, p in enumerate(route["port_calls"])])
    payload = fixture.model_dump()
    payload.update(port=port.model_dump(), rotation=rotation.model_dump(),
                   yard=YardState(yard_id=port.yard.id).model_dump(),
                   containers=[], load_list=[])
    template = Instance.model_validate(payload)
    params = load_simulator_params("laem_chabang_like")
    calls, workload = sample_ship_calls(template, params, days=14, seed=41,
                                        volume_scale=0.04)
    return calls, params, workload


def _mean(snapshots):
    return {key: sum(s["statistics"][key] for s in snapshots) / len(snapshots)
            if snapshots else None for key in STATISTICS}


def match_complete_populations(rows):
    """Pair identical call IDs with complete targets across every seed/noise.

    All-run summaries retain missing-target outcomes. Matched summaries answer
    the narrower placement question without changing blocked-share denominators
    between settings. Exclusions are explicit; this is not a service success rate.
    """
    eligible = [{s["call_id"] for s in row["snapshot_statistics"]
                 if s["targets_requested"] > 0
                 and s["statistics"]["target_presence_fraction"] == 1.0}
                for row in rows]
    matched = set.intersection(*eligible) if eligible else set()
    for row in rows:
        snapshots = row["snapshot_statistics"]
        selected = [s for s in snapshots if s["call_id"] in matched]
        row["matched_snapshots"] = len(selected)
        row["matched_statistics_mean"] = _mean(selected)
        row["excluded_call_ids"] = sorted(s["call_id"] for s in snapshots
                                           if s["call_id"] not in matched)
    return {"matched_population_rule": "all targets present in every run",
            "matched_call_ids": sorted(matched), "runs": rows}


def compare_noise_settings(calls, params, *, seeds=(2026, 2027, 2028),
                           noise_levels=(0.0, 0.2, 1.0),
                           warmup_s=4 * 86400, until_s=14 * 86400):
    rows = []
    calls_by_id = {call.id: call for call in calls}
    for seed in seeds:
        for noise in noise_levels:
            run_start = perf_counter()
            result = simulate_port(calls, params, seed=seed, noise=noise, until_s=until_s)
            # This empty-start diagnostic uses a shared warm-up cutoff, not the
            # profile's synthetic 60% initial inventory calibration target.
            snapshots = [s for s in result.snapshots if s.time_s >= warmup_s]
            summaries = []
            for snapshot in snapshots:
                targets = set(calls_by_id[snapshot.call_id].instance.load_list)
                heights = [len(stack.boxes) for stack in snapshot.yard.stacks]
                summaries.append({"call_id": snapshot.call_id, "time_s": snapshot.time_s,
                    "statistics": snapshot.statistics,
                    "targets_requested": len(targets),
                    "targets_present": sum(cid in targets for stack in snapshot.yard.stacks for cid in stack.boxes),
                    "targets_blocked": sum(cid in targets for stack in snapshot.yard.stacks for cid in stack.boxes[:-1]),
                    "nonempty_stack_height_histogram": {str(height): heights.count(height)
                                                       for height in sorted(set(heights))}})
            rows.append({"seed": seed, "noise": noise, "snapshots": len(snapshots),
                "snapshot_statistics": summaries,
                "statistics_mean": _mean(summaries),
                "max_observed_stack_height": max((s.statistics["max_stack_height"] for s in snapshots), default=0),
                "moves": len(result.moves), "relocations": sum(t.move.kind == "relocate" for t in result.moves),
                "queued_gate_events": result.queued_gate_events,
                "missed_cutoffs": len(result.missed_cutoffs),
                "unserved_loads": len(result.unserved_loads),
                "residual_boxes": sum(len(s.boxes) for s in result.final_yard.stacks),
                "wall_s": perf_counter() - run_start})
    return match_complete_populations(rows)


def main():
    started = perf_counter()
    calls, params, workload = build_scenario()
    sampling_s = perf_counter() - started
    comparison = compare_noise_settings(calls, params)
    output = {"simulator_version": SIMULATOR_VERSION,
              "seed_workload": 41, "seed_placement": 2026, "days": 14,
              "warmup_days": 4, "volume_scale": 0.04,
              "yard_storage_teu": 120, "calls": len(calls),
              "initial_inventory_boxes": 0,
              "initial_occupancy_teu_fraction": 0.0,
              "plausibility_status": "uncalibrated: no matched reference distribution",
              "sampling_s": sampling_s, "workload": asdict(workload),
              # Preserve the original three-setting/one-seed audit keys.
              "runs": [r for r in comparison["runs"] if r["seed"] == 2026],
              "controlled_comparison": comparison,
              "wall_s": perf_counter() - started}
    path = Path(__file__).with_name("metrics.json")
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
