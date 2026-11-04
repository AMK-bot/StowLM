"""Generate honest, frozen MVP showcases using the existing OFF-state producer.

APPROXIMATION: select a deterministic seed with DG, reefers and buried targets
for the three port-call illustrations. This is a presentation-conditioned set,
not a representative evaluation sample. All rejected seed audits are retained.
The hero uses the full vessel decision region; cargo volume is measured, never
described as 1,700 loaded TEU. No loading plan is certified by this script.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import resource
from time import perf_counter

from stowage.frozen import require_clean
from stowage.generate.background_acceptance import requires_background_gate
from stowage.generate.instances import GenerationReport
from stowage.generate.instances_v2_random import generate_random_v2
from stowage.generate.physics_identity import current_physics_identity
from stowage.io import load_instance, save_instance
from stowage.rules.catalogue import rule_freeze_v1_enabled
from build import source_snapshot


ROWS = [
    ("feeder_500", "small", 9_000_100),
    ("feeder_800", "medium", 9_000_200),
    ("feeder_1700", "medium", 9_000_300),
    ("feeder_1700", "full", 9_000_400),
]


def write_json(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def features(instance) -> dict:
    targets = set(instance.load_list)
    cargo = [box for box in instance.containers if box.id in targets]
    blocked = set()
    for stack in instance.yard.stacks:
        for index, box_id in enumerate(stack.boxes):
            if box_id in targets and any(above not in targets for above in stack.boxes[index + 1:]):
                blocked.add(box_id)
    teu = sum(box.length_ft / 20 for box in cargo)
    return {
        "targets": len(cargo), "target_teu": teu,
        "dg_targets": sum(box.dg is not None for box in cargo),
        "reefer_targets": sum(box.reefer for box in cargo),
        "buried_targets": len(blocked),
        "buried_target_share": len(blocked) / len(cargo),
        "decision_region_nominal_teu": instance.vessel.nominal_teu,
        "decision_region_cells": len(instance.vessel.cells),
        "target_teu_over_decision_region_nominal_teu": teu / instance.vessel.nominal_teu,
        "onboard_boxes_initial": len(instance.onboard.placements),
        "yard_boxes_initial": sum(len(stack.boxes) for stack in instance.yard.stacks),
    }


def generate_row(root: Path, vessel: str, size: str, seed_start: int, *, max_seeds=10,
                 booking_slack=None, booking_mass_fraction=None, preserve_existing=False):
    assert not rule_freeze_v1_enabled(), "MVP showcase requires rule-freeze OFF"
    stem = f"{vessel}-{size}"
    path = root / f"{stem}.json"
    audit_path = root / f"{stem}.audit.json"
    if path.exists() and audit_path.exists():
        audit = json.loads(audit_path.read_text())
        assert audit["instance_sha256"] == sha256(path.read_bytes()).hexdigest()
        instance = load_instance(path)
        assert features(instance) == audit["showcase_features"]
        if not preserve_existing:
            # APPROXIMATION: resume the original accepted draw across fleet
            # edits, retaining its actual source pins rather than relabelling it.
            assert audit["config"].get("booking_slack") == booking_slack, "booking slack mismatch"
            assert audit["config"].get("booking_mass_fraction") == booking_mass_fraction, "booking mass fraction mismatch"
            assert seed_start <= audit["config"]["seed"] < seed_start + max_seeds, "seed namespace mismatch"
        if audit["background_gate_required"]:
            assert audit["background_last"]["accepted"] is True
            assert audit["background_last"]["physics_sha256"]
        return audit
    if preserve_existing:
        raise ValueError(f"preserved showcase is missing: {path}")
    from build import booking_slack_kwargs, generation_environment
    producer_options = booking_slack_kwargs(generate_random_v2, booking_slack, booking_mass_fraction)
    selection_path = root / f"{stem}.selection.json"
    selection = json.loads(selection_path.read_text()) if selection_path.exists() else []
    # APPROXIMATION: presentation-only rejected draws are not re-run on resume;
    # their original measured rejection evidence remains in the selection log.
    tried = {entry["seed"] for entry in selection if entry.get("status") in
             {"presentation_features_missing", "producer_rejected"}
             and entry.get("producer_report", {}).get("booking_slack") == booking_slack
             and entry.get("producer_report", {}).get("booking_mass_fraction") == booking_mass_fraction}
    # APPROXIMATION: deterministic feature selection conditions presentation
    # examples; these draws are excluded from evaluation and fitting.
    for offset in range(max_seeds):
        seed = seed_start + offset
        if seed in tried:
            continue
        spec = {"generator": "random_yard", "vessel": vessel, "size": size, "rung": size,
                "profile": size, "seed": seed, "port": "laem_chabang_like",
                "noise": 0.7, "n_boxes": None, "dense_20ft": False,
                "tiny_dg_enabled": True, "max_attempts": 50}
        if booking_slack is not None:
            spec["booking_slack"] = booking_slack
        if booking_mass_fraction is not None:
            spec["booking_mass_fraction"] = booking_mass_fraction
        before = current_physics_identity()
        admitted_sources = source_snapshot()
        report = GenerationReport()
        start = perf_counter()
        try:
            with generation_environment() as stage_environment:
                before = current_physics_identity()
                instance = generate_random_v2(size, seed=seed, noise=spec["noise"],
                    vessel_name=vessel, port_name=spec["port"], report=report,
                    max_attempts=spec["max_attempts"], tiny_dg_enabled=True, **producer_options)
        except ValueError as error:
            selection.append({"seed": seed, "status": "producer_rejected", "error": str(error),
                              "elapsed_generation_s": perf_counter()-start, "producer_report": asdict(report)})
            write_json(root / f"{stem}.selection.json", selection)
            print(json.dumps({"row": stem, "seed": seed, "status": "producer_rejected", "error": str(error)}), flush=True)
            continue
        elapsed = perf_counter() - start
        assert report.accepted
        assert admitted_sources == source_snapshot(), "source changed during showcase generation"
        metrics = features(instance)
        wanted = metrics["buried_targets"] > 0 and metrics["dg_targets"] > 0 and metrics["reefer_targets"] > 0
        selection.append({"seed": seed, "status": "selected" if wanted else "presentation_features_missing",
                          "elapsed_generation_s": elapsed, "showcase_features": metrics,
                          "producer_report": asdict(report)})
        write_json(root / f"{stem}.selection.json", selection)
        print(json.dumps({"row": stem, "seed": seed, "elapsed_s": elapsed,
                          "features": metrics, "selected": wanted}), flush=True)
        if not wanted:
            continue
        require_clean(instance, what="MVP showcase")
        audit = asdict(report)
        gate_required = requires_background_gate(instance.vessel, size=instance.size_class)
        if gate_required:
            assert audit["background_last"]["accepted"] is True
            assert audit["background_last"]["physics_sha256"]
        save_instance(instance, path)
        loaded = load_instance(path)
        assert loaded == instance
        audit.update(config=spec, split="showcase", rule_freeze_v1_enabled=False,
            stage_environment=stage_environment, booking_slack=booking_slack,
            booking_mass_fraction=booking_mass_fraction, global_screen=0,
            physics_before=before, physics_after=current_physics_identity(), source_files=admitted_sources,
            instance_versions=dict(instance.provenance.versions), schema_version=instance.schema_version,
            elapsed_generation_s=elapsed, instance_sha256=sha256(path.read_bytes()).hexdigest(),
            showcase_features=metrics, selection_attempts=len(selection),
            selection_total_seconds=sum(row["elapsed_generation_s"] for row in selection),
            requested_count=1, emitted_count=1, seed_selection=selection,
            generator_gate_passed=True,
            background_gate_required=gate_required,
            plan_checker_passed=None,
            hero_view_only=size == "full",
            measured_peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            approximations=["Presentation-conditioned deterministic seed selection; not evaluation data"],
            occupancy_label=("Full feeder_1700 vessel geometry; load volume is target_teu, not 1700 loaded TEU"
                             if size == "full" else "Restricted decision region with synthetic fixed background cargo"))
        # The gate owns its exact certificate hash. Concurrent creation of
        # qualified acceleration files can change an outer inventory snapshot
        # without changing any source/parameter bytes. Do not mislabel that
        # earlier snapshot as the gate's certificate.
        if not gate_required or audit["background_last"]["physics_sha256"] == before["sha256"]:
            audit["physics"] = before
        else:
            audit["physics_inventory_note"] = (
                "Source inputs unchanged; acceleration inventory moved between outer snapshot and D58. "
                "The accepted certificate is background_last.physics_sha256.")
        write_json(audit_path, audit)
        return audit
    raise RuntimeError(f"{stem}: no seed met presentation features in {max_seeds} attempts")


def write_manifest(root: Path, records, *, generation_wall_s=0.0, booking_slack=None,
                   ship_settings=None, pending=None, revision=1):
    write_json(root / "showcase-manifest.json", {
        "rule_freeze_v1_enabled": False, "instances": len(records),
        "revision": revision, "booking_slack": booking_slack,
        "ship_settings": ship_settings, "pending": pending or [],
        "generation_wall_s": generation_wall_s,
        "records": [{key: row[key] for key in ("config", "instance_sha256", "instance_versions",
                     "elapsed_generation_s", "selection_total_seconds", "showcase_features",
                     "generator_gate_passed", "background_gate_required", "hero_view_only")} for row in records],
        "hashes": {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob("*.json"))
                   if p.name != "showcase-manifest.json"},
    })
    (root / "SHA256SUMS").write_text("".join(
        f"{sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted(root.glob("*.json"))))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiments/mvp/corpus/showcase"))
    parser.add_argument("--max-seeds", type=int, default=10)
    parser.add_argument("--row", choices=[f"{v}-{s}" for v,s,_ in ROWS])
    parser.add_argument("--booking-slack", type=float, help="opt-in adopted producer booking slack; omitted preserves v1")
    parser.add_argument("--booking-mass-fraction", type=float)
    parser.add_argument("--seed-base", type=int, help="override seed namespace; rows offset by 100")
    parser.add_argument("--preserve-full", action="store_true", help="reuse existing hero byte-for-byte")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    records = []
    for index, (vessel, size, seed_start) in enumerate(ROWS):
        if args.row and args.row != f"{vessel}-{size}":
            continue
        seed_start = args.seed_base + index * 100 if args.seed_base is not None else seed_start
        records.append(generate_row(args.output, vessel, size, seed_start, max_seeds=args.max_seeds,
            booking_slack=args.booking_slack, booking_mass_fraction=args.booking_mass_fraction, preserve_existing=args.preserve_full and size == "full"))
    write_manifest(args.output, records, generation_wall_s=perf_counter()-started,
                   booking_slack=args.booking_slack, revision=2 if args.booking_slack is not None else 1)


if __name__ == "__main__":
    main()
