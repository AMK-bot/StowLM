"""Checkpoint synthetic simulator samples as independently resumable shards.

One instance plus its audit is a shard. The audit is written last, acting as a
commit marker; interrupted writes without an audit are regenerated and checked
byte for byte. Hashes detect accidental edits, rather than certifying physical
feasibility. Screening reports only necessary conditions: the full checker
remains the authority (see docs/architecture.md, Frozen interfaces).

The benchmark uses five instances per rung, retaining the first cold hull build
in elapsed time; subsequent samples reuse the process-local vessel cache. Run
under ``nice`` after compute admission; this CLI deliberately uses one process.
``--without-dg`` explicitly conditions demand on non-dangerous cargo for starter
sets when the vessel has no certified DG positions; every physics screen remains
active. The default retains the original feeder-export cargo distribution.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from stowage.frozen import require_clean
from stowage.generate.physics_identity import current_physics_identity
from stowage.generate.random_yard import SIZE_LADDER
from stowage.io import load_instance, save_instance

STARTER_COUNTS = {"tiny": 50, "small": 50, "medium": 20, "full": 5}


def _report_payload(report) -> dict:
    """Retain additive producer facts while preserving the legacy audit shape."""
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    payload = asdict(report)
    if payload.get("booking_mass_fraction") is None:
        payload.pop("booking_mass_fraction", None)
    if payload.get("booking_slack") is None:
        payload.pop("booking_slack", None)
    if rule_freeze_v1_enabled():
        payload.update(deepcopy(vars(report)))
        if payload.get("booking_mass_fraction") is None:
            payload.pop("booking_mass_fraction", None)
        if payload.get("booking_slack") is None:
            payload.pop("booking_slack", None)
    else:
        if not payload.get("background_unknowns"):
            payload.pop("background_unknowns", None)
        for key in ("cargo_draws", "d62_crossing", "c2_requested", "c2_checks", "c2_direction",
                    "c2_errors", "requested_blocking_ratio", "realised_blocking_ratio",
                    "blocked_targets", "targets", "producer_report", "congestion_mixing"):
            payload.pop(key, None)
    return payload


def _producer_audit(audit: dict) -> dict:
    """Keep the complete producer record; campaign envelope fields live outside."""
    envelope = {"config", "physics", "elapsed_generation_s", "screening_rate", "c2_status",
        "instance_sha256", "instance_versions", "split", "split_spec", "spec_key",
        "generation_spec", "generation_key", "test_family", "split_policy", "split_version",
        "generation_components", "status", "failure_message", "rejection_diagnostics",
        "initialization_attempts", "rejection_sha256", "instance_family"}
    return deepcopy({key: value for key, value in audit.items() if key not in envelope})


def _producer_shard_fields(audit: dict) -> dict:
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    if not rule_freeze_v1_enabled():
        return {}
    payload = _producer_audit(audit)
    return {"producer_report": payload,
            **{key: deepcopy(audit[key]) for key in ("cargo_draws", "d62_crossing", "congestion_mixing") if key in audit}}


def _completion_summary(audits: list[dict]) -> dict:
    """Report overlapping depth-0 C1 failures per candidate, not per shipment.

    A single candidate can fail several necessary bounds. Shipment redraws are
    separate replacement draws and therefore never inflate that denominator.
    See experiments/instances/d27-capacity.md for the conditioning semantics.
    """
    attempts = sum(audit["attempts"] for audit in audits)
    reasons = Counter()
    for audit in audits:
        reasons.update(audit.get("completion_rejections", {}))
    return {
        "completion_checks": sum(audit.get("completion_checks", 0) for audit in audits),
        "completion_rejections": dict(sorted(reasons.items())),
        "completion_screening_rates_by_reason": {
            key: count / attempts for key, count in sorted(reasons.items())
        } if attempts else {},
        "shipment_redraws": sum(audit.get("shipment_redraws", 0) for audit in audits),
        "c2_screened": any(audit.get("c2_screened", False) for audit in audits),
    }


def _capacity_audit(audit: dict) -> dict:
    """Keep per-shard physical budgets visible; do not sum unlike vessels."""
    return {key: audit[key] for key in (
        "capacity_teu", "teu_budget", "length_slot_budgets", "requested_targets", "realised_teu",
        "booking_reference", "booking_utilisation", "booking_slack", "booking_mass_fraction",
    ) if key in audit}


def _atomic_json(path: Path, payload: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    try:
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_instance(path: Path, config: dict) -> None:
    instance = load_instance(path)
    require_clean(instance, what="simulated instance resume")
    if instance.voyage is None:
        raise ValueError(f"resume instance lacks the required D50 voyage declaration: {path}")
    expected = {key: str(config[key]) for key in ("vessel", "port", "size", "seed", "generator")}
    expected["noise"] = repr(float(config["noise"]))
    expected["cargo_profile"] = "no_dg" if config["without_dg"] else "feeder_export"
    empty_tiny = config["size"] == "tiny" and not config.get("tiny_dg_enabled", True)
    if empty_tiny:
        expected["cargo_profile"] = "empty_dry20"
    expected["equipment_profile"] = (
        "empty_dry20" if empty_tiny else "dry20_with_dg" if config["size"] == "tiny"
        else "dense_20ft" if config.get("dense_20ft", False) else "full_mix")
    if "booking_slack" in config:
        expected["booking_slack"] = repr(float(config["booking_slack"]))
    elif "booking_slack" in instance.family:
        raise ValueError(f"resume configuration mismatch: {path}")
    if "booking_mass_fraction" in config:
        expected["booking_mass_fraction"] = repr(float(config["booking_mass_fraction"]))
    elif "booking_mass_fraction" in instance.family:
        raise ValueError(f"resume configuration mismatch: {path}")
    if "congestion_mix_share" in config:
        expected["congestion_mix_share"] = repr(float(config["congestion_mix_share"]))
    if (any(instance.family.get(k) != v for k, v in expected.items())
            or instance.seed != config["seed"] or instance.size_class != config["size"]
            or instance.stage != "A"):
        raise ValueError(f"resume configuration mismatch: {path}")


def _validate_background_binding(instance, audit: dict, physics_sha256: str) -> None:
    """A stored slice needs its successful D58 witness under these exact inputs."""
    from stowage.generate.background_acceptance import requires_background_gate

    if not requires_background_gate(instance.vessel, size=instance.size_class):
        return
    binding = audit.get("background_last")
    if (not isinstance(binding, dict) or binding.get("accepted") is not True
            or binding.get("physics_sha256") != physics_sha256):
        raise ValueError("missing or stale D58 background physics binding")


def _validate_audit(path: Path, config: dict, instance_path: Path) -> dict:
    audit = json.loads(path.read_text())
    physics = current_physics_identity()
    if (audit.get("physics") != physics
            or config.get("physics_sha256") != physics["sha256"]):
        raise ValueError(f"resume physics mismatch: {path}")
    if audit.get("config") != config or audit.get("instance_sha256") != _sha256(instance_path):
        raise ValueError(f"resume configuration mismatch or checksum mismatch: {path}")
    _validate_background_binding(load_instance(instance_path), audit, physics["sha256"])
    attempts = audit.get("attempts")
    rejections = audit.get("rejections")
    if (not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1
            or not isinstance(rejections, dict)
            or any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in rejections.values())
            or audit.get("screening_rate") != (attempts - 1) / attempts):
        raise ValueError(f"invalid screening audit: {path}")
    return audit


def main(argv: list[str] | None = None) -> int:
    """Generate a seed range; validate committed shards before skipping them."""
    # Lazy import keeps instances.py free to forward its module entry point here.
    from stowage.generate.instances import GenerationReport, INSTANCES_VERSION, generate_simulated_instance

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", choices=[*SIZE_LADDER, "all"], default="all")
    parser.add_argument("--count", type=int, default=1, help="instances per selected rung")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--starter", action="store_true", help="50 tiny, 50 small, 20 medium, 5 full")
    mode.add_argument("--benchmark", action="store_true", help="five instances per selected rung")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--noise", type=float, default=0.2)
    parser.add_argument("--congestion-mix-share", type=float, default=.25,
                        help="activated D72 independent-yard mixing share")
    parser.add_argument("--without-dg", action="store_true",
                        help="explicit demand conditioning: omit DG cargo; retain every physics screen")
    from stowage.conventions import canonical_vessel_id

    parser.add_argument("--vessel", type=canonical_vessel_id, default="feeder_500")
    parser.add_argument("--port", default="laem_chabang_like")
    parser.add_argument("--max-attempts", type=int, default=200)
    parser.add_argument("--output", type=Path, default=Path("experiments/instances-sim"))
    parser.add_argument("--booking-slack", type=float, default=None,
                        help="synthetic demand TEU cap = (1-slack) * geometric decision cells; default unchanged")
    parser.add_argument("--booking-mass-fraction", type=float, default=None,
                        help="synthetic booking mass / scaled full-export reference cargo budget; default unchanged")
    args = parser.parse_args(argv)
    from stowage.generate.random_yard import validate_booking_slack, validate_booking_mass_fraction
    try:
        validate_booking_slack(args.booking_slack)
        validate_booking_mass_fraction(args.booking_mass_fraction)
    except ValueError as exc:
        parser.error(str(exc))
    if args.count < 1 or args.max_attempts < 1:
        parser.error("count and max-attempts must be positive")
    if not math.isfinite(args.noise) or not 0 <= args.noise <= 1:
        parser.error("noise must be finite and in [0, 1]")
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if not math.isfinite(args.congestion_mix_share) or not 0 <= args.congestion_mix_share <= 1:
        parser.error("congestion-mix-share must be finite and in [0, 1]")
    if not rule_freeze_v1_enabled() and args.congestion_mix_share != .25:
        parser.error("congestion-mix-share requires rule-freeze-v1 activation")
    sizes = list(SIZE_LADDER) if args.size == "all" else [args.size]
    counts = {size: STARTER_COUNTS[size] if args.starter else 5 if args.benchmark else args.count for size in sizes}
    summary = {"generated": 0, "resumed": 0, "recovered": 0, "rungs": {}}
    for size, count in counts.items():
        directory = args.output / size
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {"generator": INSTANCES_VERSION, "size": size, "seed_start": args.seed,
                    "requested": count, "count": 0, "attempts": 0, "screened_out": 0,
                    "provision_rejections": 0, "infeasible_by_provision_rate": 0.0,
                    "rejections": {}, "screening_rates_by_reason": {}, "shards": [],
                    "elapsed_generation_s": 0.0, "mode": "benchmark" if args.benchmark else "starter" if args.starter else "range"}
        if args.booking_slack is not None:
            manifest.update(booking_slack=args.booking_slack,
                booking_capacity_basis="decision_cells_geometric_teu")
        if args.booking_mass_fraction is not None:
            manifest.update(booking_mass_fraction=args.booking_mass_fraction,
                booking_mass_basis="scaled_full_export_cargo_budget_at_realised_mass")
        reasons = Counter()
        audits = []
        for seed in range(args.seed, args.seed + count):
            path = directory / f"seed-{seed:08d}.json"
            sidecar = path.with_suffix(".audit.json")
            config = {"vessel": args.vessel, "port": args.port, "size": size, "noise": args.noise,
                      "seed": seed, "generator": INSTANCES_VERSION, "max_attempts": args.max_attempts,
                      "without_dg": args.without_dg}
            if rule_freeze_v1_enabled():
                config["congestion_mix_share"] = args.congestion_mix_share
            if args.booking_slack is not None:
                config["booking_slack"] = args.booking_slack
            if args.booking_mass_fraction is not None:
                config["booking_mass_fraction"] = args.booking_mass_fraction
            physics = current_physics_identity()
            config["physics_sha256"] = physics["sha256"]
            if sidecar.exists() and not path.exists():
                raise ValueError(f"audit without instance: {sidecar}")
            if path.exists():
                _validate_instance(path, config)
            if sidecar.exists():
                audit = _validate_audit(sidecar, config, path)
                summary["resumed"] += 1
            else:
                report = GenerationReport()
                started = time.perf_counter()
                instance = generate_simulated_instance(size, seed=seed, noise=args.noise,
                    vessel_name=args.vessel, port_name=args.port, report=report, max_attempts=args.max_attempts,
                    **({"booking_slack": args.booking_slack} if args.booking_slack is not None else {}),
                    **({"booking_mass_fraction": args.booking_mass_fraction} if args.booking_mass_fraction is not None else {}),
                    **({"congestion_mix_share": args.congestion_mix_share} if rule_freeze_v1_enabled() else {}),
                    **({"without_dg": True} if args.without_dg else {}))
                elapsed = time.perf_counter() - started
                if current_physics_identity() != physics:
                    raise ValueError("physics implementation changed during generation")
                _validate_background_binding(instance, _report_payload(report), physics["sha256"])
                temp = path.with_name(f".{path.name}.{uuid4().hex}.partial.json")
                try:
                    save_instance(instance, temp)
                    _validate_instance(temp, config)
                    if path.exists() and _sha256(path) != _sha256(temp):
                        raise ValueError(f"incomplete shard differs from regenerated instance: {path}")
                    recovered = path.exists()
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
                audit = {**_report_payload(report), "screening_rate": report.screening_rate, "config": config,
                         "instance_sha256": _sha256(path), "elapsed_generation_s": elapsed,
                         "physics": physics}
                _atomic_json(sidecar, audit)
                summary["recovered" if recovered else "generated"] += 1
            manifest["count"] += 1
            audits.append(audit)
            manifest.update(_completion_summary(audits))
            manifest["attempts"] += audit["attempts"]
            manifest["screened_out"] += audit["attempts"] - 1
            manifest["provision_rejections"] += audit.get("provision_rejections", 0)
            manifest["elapsed_generation_s"] += audit["elapsed_generation_s"]
            reasons.update(audit["rejections"])
            manifest["rejections"] = dict(sorted(reasons.items()))
            manifest["screening_rate"] = manifest["screened_out"] / manifest["attempts"]
            manifest["infeasible_by_provision_rate"] = manifest["provision_rejections"] / manifest["attempts"]
            manifest["screening_rates_by_reason"] = {k: v / manifest["attempts"] for k, v in sorted(reasons.items())}
            manifest["mean_instance_s"] = manifest["elapsed_generation_s"] / manifest["count"]
            manifest["shards"].append({"seed": seed, "instance": path.name, "audit": sidecar.name,
                                       "sha256": audit["instance_sha256"], **_capacity_audit(audit), **_producer_shard_fields(audit)})
            _atomic_json(directory / "manifest.json", manifest)
        summary["rungs"][size] = manifest
    _atomic_json(args.output / "manifest.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
