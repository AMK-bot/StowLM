"""Canonical Stage A campaign with auditable, independently resumable shards.

A candidate must pass necessary capacity and depth-0 C1 completion bounds;
rejection rates describe demand conditioning, not a feasibility certificate
(Wolsey, Integer Programming, 1998, chapter 1). Impossible DG shipments can be
redrawn separately; audits count shipment redraws apart from candidate failures
(experiments/instances/d27-capacity.md). C2 remains disabled. Both yard generators
retain their published yard assumptions. Tiny retains six dry20 boxes with DG sampling enabled; other profiles keep
the feeder-export mix, including fail-closed DG entries.

The random-yard adapter uses the same optimistic physical/IMDG screen as the
simulator. Dense realistic services explicitly condition equipment to twenty-foot
boxes to fit the fleet cells; all DG and special-stowage rates remain active.
The v2 specifications below preserve historical explicit target counts and split
identities. They are not specifications for a future capacity-derived campaign.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import fields
import json
import hashlib
import math
from pathlib import Path
from time import perf_counter

from stowage.conventions import canonical_vessel_id
from stowage.frozen import require_clean
from stowage.generate.instances_cli import (_atomic_json, _sha256, _completion_summary,
    _capacity_audit, _validate_background_binding, _report_payload, _producer_shard_fields)
from stowage.generate.random_yard import SIZE_LADDER
from stowage.io import load_instance, save_instance

CAMPAIGN_VERSION = "8.0.0"
COUNTS = {"tiny": 200, "small": 200, "medium": 100, "full": 40,
          "realistic_500": 20, "realistic_800": 20}
GENERATORS = ("random_yard", "simulated_yard")
VESSELS = ("feeder_500", "feeder_800", "feeder_1700")
PORTS = ("laem_chabang_like", "singapore_like", "small_feeder", "us_west_coast")
NOISE = (0.0, 0.2, 0.8)


def _validate_fixed_tiny_booking_caps(profiles, booking_slack, booking_mass_fraction):
    """Reject unsupported campaign controls without changing explicit counts."""
    includes_tiny = profiles is None or "tiny" in profiles
    if includes_tiny and (booking_slack is not None or booking_mass_fraction is not None):
        raise ValueError(
            "v2 tiny uses a fixed n_boxes=6 and does not support booking cap flags; "
            "select profiles without tiny, or use instances_v3 for automatic underbooking"
        )



def scenario_specs(*, pilot=False, seed=0, profiles=None, generators=None,
                   realistic_500_boxes=400, realistic_800_boxes=800,
                   max_attempts=10000, congestion_mix_share=.25, booking_slack=None,
                   booking_mass_fraction=None):
    """Stable per-profile seed addresses; pilot shards are campaign prefixes.

    The first five specifications retain the admitted pilot schedule. Subsequent
    draws cover every vessel/port/noise combination for each profile.
    Medium/full use the larger two ships
    because the feeder_500 decision regions cannot hold their usual cargo mix.
    """
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if isinstance(congestion_mix_share, bool) or not isinstance(congestion_mix_share, (int, float)) or not math.isfinite(congestion_mix_share) or not 0 <= congestion_mix_share <= 1:
        raise ValueError("congestion_mix_share must be finite and in [0, 1]")
    if not rule_freeze_v1_enabled() and congestion_mix_share != .25:
        raise ValueError("congestion_mix_share requires rule-freeze-v1 activation")
    from stowage.generate.random_yard import validate_booking_slack, validate_booking_mass_fraction
    validate_booking_slack(booking_slack)
    validate_booking_mass_fraction(booking_mass_fraction)
    profiles = None if profiles is None else {canonical_vessel_id(profile) for profile in profiles}
    _validate_fixed_tiny_booking_caps(profiles, booking_slack, booking_mass_fraction)
    result = []
    for gi, generator in enumerate(GENERATORS):
        if generators is not None and generator not in generators:
            continue
        for pi, (profile, count) in enumerate(COUNTS.items()):
            if profiles is not None and profile not in profiles:
                continue
            size = "full" if profile.startswith("realistic") else profile
            n_boxes = {"realistic_500": realistic_500_boxes,
                       "realistic_800": realistic_800_boxes}.get(profile, SIZE_LADDER[size].boxes)
            if type(n_boxes) is not int or n_boxes < 1:
                raise ValueError("profile target counts must be positive integers")
            for i in range(5 if pilot else count):
                vessel = VESSELS[i % 3]
                if size in {"medium", "full"}:
                    vessel = VESSELS[1 + i % 2]
                if profile == "realistic_500":
                    vessel = "feeder_500"
                elif profile == "realistic_800":
                    vessel = "feeder_800"
                ports = tuple(p for p in PORTS if p != "small_feeder") if profile.startswith("realistic") else PORTS
                port = ports[i % len(ports)]
                noise = NOISE[(i // 4) % 3]
                if i >= 5:
                    choices = VESSELS[1:] if size in {"medium", "full"} else VESSELS
                    if profile == "realistic_500":
                        choices = ("feeder_500",)
                    elif profile == "realistic_800":
                        choices = ("feeder_800",)
                    combinations = [(v, p, n) for n in NOISE for p in ports for v in choices
                        if not (profile == "small" and v == "feeder_500" and p == "small_feeder")]
                    vessel, port, noise = combinations[(i - 5) % len(combinations)]
                result.append({"campaign_version": CAMPAIGN_VERSION, "generator": generator,
                    "profile": profile, "size": size, "seed": seed + gi*10_000_000 + pi*100_000 + i,
                    "vessel": vessel, "port": port, "noise": noise,
                    "n_boxes": n_boxes, "max_attempts": max_attempts,
                    "dense_20ft": profile.startswith("realistic"),
                    "cargo_profile": "feeder_export",
                    **({"congestion_mix_share": float(congestion_mix_share)}
                       if rule_freeze_v1_enabled() and generator == "simulated_yard" else {}),
                    **({"tiny_dg_enabled": True} if size == "tiny" else {})})
    if booking_slack is not None:
        for spec in result:
            spec["booking_slack"] = float(booking_slack)
    if booking_mass_fraction is not None:
        for spec in result:
            spec["booking_mass_fraction"] = float(booking_mass_fraction)
    return result


def _canonical_labels(spec):
    """Resolve lookup labels without mutating a stored campaign specification."""
    return {**spec, **{key: canonical_vessel_id(spec[key])
                      for key in ("vessel", "profile") if key in spec}}


def family_tags(spec):
    spec = _canonical_labels(spec)
    tags = {k: str(spec[k]) for k in ("vessel", "port", "size", "seed", "generator", "cargo_profile", "profile")}
    tags["noise"] = repr(float(spec["noise"]))
    tags["slice"] = spec["size"]
    tags["n_boxes"] = str(spec["n_boxes"])
    tags["requested_n_boxes"] = str(spec["n_boxes"])
    tags["dense_20ft"] = str(spec.get("dense_20ft", False)).lower()
    tags["tiny_dg_enabled"] = str(spec.get("tiny_dg_enabled", False)).lower()
    empty_tiny = spec["generator"] == "simulated_yard" and spec["size"] == "tiny" and not spec.get("tiny_dg_enabled", False)
    if empty_tiny:
        tags["cargo_profile"] = "empty_dry20"
    tags["equipment_profile"] = ("empty_dry20" if empty_tiny else "dry20_with_dg"
        if spec["size"] == "tiny" else "dense_20ft" if spec["dense_20ft"] else "full_mix")
    if spec.get("cargo_profile") == "dg_stress":
        tags.update(identity_version="v3", equipment_profile="dry20_dg_stress",
                    dg_stress_pair=spec.get("dg_stress_pair") or "mixed")
    if spec["generator"] == "random_yard":
        tags["blocking_ratio"] = repr(float(spec["noise"]))
    if spec.get("booking_slack") is not None:
        tags["booking_slack"] = repr(float(spec["booking_slack"]))
        tags["booking_capacity_basis"] = "decision_cells_geometric_teu"
    if spec.get("booking_mass_fraction") is not None:
        tags["booking_mass_fraction"] = repr(float(spec["booking_mass_fraction"]))
        tags["booking_mass_basis"] = "scaled_full_export_cargo_budget_at_realised_mass"
    return tags



def _require_current_campaign(spec):
    if spec.get("campaign_version") != CAMPAIGN_VERSION:
        raise ValueError("stale campaign version; preserve the archive and regenerate under the current campaign")


def physics_metadata():
    """The same fail-closed physical inputs used by the D58 acceptance gate."""
    from stowage.generate.physics_identity import current_physics_identity
    return current_physics_identity()


def generator_components(spec):
    from stowage.versions import SIMULATED_INSTANCES_VERSION
    from stowage.generate.cargo import effective_cargo_generator_version
    from stowage.generate.random_yard import effective_random_yard_version
    from stowage.generate.instances_v2_random import effective_random_v2_version
    from stowage.yard.simulator import effective_yard_simulator_version
    components = {"instances_campaign": CAMPAIGN_VERSION,
                  "cargo_generator": effective_cargo_generator_version()}
    if spec["generator"] == "random_yard":
        components.update(random_yard=effective_random_yard_version(),
                          random_yard_v2=effective_random_v2_version())
    else:
        components.update(instances=SIMULATED_INSTANCES_VERSION,
                          yard_simulator=effective_yard_simulator_version())
    return components


def _require_current_binding(audit, spec, physics):
    if (audit.get("physics") != physics
            or audit.get("generation_components") != generator_components(spec)):
        raise ValueError("stale or missing physics/component/D58 binding; regenerate legacy shard")


def generate(spec):
    """Return one normalized clean instance and candidate-level audit."""
    spec = _canonical_labels(spec)
    _require_current_campaign(spec)
    physics = physics_metadata()
    started = perf_counter()
    expected_split = split_metadata(spec)
    if expected_split["split"] is None:
        raise ValueError(f"canonical split assignment failed before generation: {expected_split}")
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    options = {}
    if spec.get("booking_slack") is not None:
        options["booking_slack"] = spec["booking_slack"]
    if spec.get("booking_mass_fraction") is not None:
        options["booking_mass_fraction"] = spec["booking_mass_fraction"]
    if rule_freeze_v1_enabled():
        options["cargo_profile"] = spec.get("cargo_profile", "feeder_export")
        options["instance_id"] = f"v2.{spec['generator']}.{spec['profile']}.{spec['seed']}"
        if spec["generator"] == "random_yard":
            options["d62_crossing_share"] = spec.get("d62_crossing_share", .25)
            if spec.get("dg_stress_pair") is not None:
                options["dg_stress_pair"] = spec["dg_stress_pair"]
        else:
            options["congestion_mix_share"] = spec.get("congestion_mix_share", .25)
    elif spec.get("cargo_profile", "feeder_export") == "dg_stress":
        raise ValueError("dg_stress requires rule-freeze-v1 activation")
    if spec["generator"] == "random_yard":
        from stowage.generate.instances import GenerationReport
        from stowage.generate.instances_v2_random import generate_random_v2
        report = GenerationReport()
        instance = generate_random_v2(spec["size"], seed=spec["seed"], noise=spec["noise"],
            vessel_name=spec["vessel"], port_name=spec["port"], n_boxes=spec["n_boxes"],
            dense_20ft=spec["dense_20ft"], report=report, max_attempts=spec["max_attempts"],
            tiny_dg_enabled=spec.get("tiny_dg_enabled", False), **options)
        audit = _report_payload(report)
    elif spec["generator"] == "simulated_yard":
        from stowage.generate.instances import generate_simulated_instance, GenerationReport
        report = GenerationReport()
        instance = generate_simulated_instance(spec["size"], seed=spec["seed"], noise=spec["noise"],
            vessel_name=spec["vessel"], port_name=spec["port"], report=report,
            max_attempts=spec["max_attempts"], n_boxes=spec["n_boxes"], dense_20ft=spec["dense_20ft"],
            tiny_dg_enabled=spec.get("tiny_dg_enabled", False), **options)
        audit = _report_payload(report)
    else:
        raise ValueError(f"unknown generator {spec['generator']!r}")
    # Historical v2 specifications are explicit volume commitments. Capacity-
    # derived defaults may not silently turn them into different split members.
    if len(instance.load_list) != spec["n_boxes"]:
        raise ValueError(f"generated target count {len(instance.load_list)} disagrees with "
                         f"explicit v2 specification {spec['n_boxes']}")
    if instance.voyage is None:
        raise ValueError("producer returned an instance without declared voyage")
    instance = instance.model_copy(update={"family": {**instance.family, **family_tags(spec)},
        "id": f"v2.{spec['generator']}.{spec['profile']}.{spec['seed']}"})
    expected_components = generator_components(spec)
    for name, expected in expected_components.items():
        actual = instance.provenance.versions.get(name)
        if name != "instances_campaign" and actual is not None and actual != expected:
            raise ValueError(f"producer returned stale component {name}; regenerate shard")
    # Fresh, input-bound construction supplies missing producer audit labels.
    # Resume never mutates provenance or upgrades an archived instance.
    instance.provenance.versions.update(expected_components)
    require_clean(instance, what="canonical v2 campaign")
    from eval.splits import spec_from_instance, canonical_spec
    if canonical_spec(spec_from_instance(instance)) != expected_split["split_spec"]:
        raise ValueError("instance family disagrees with pre-generation split specification")
    if physics != physics_metadata():
        raise ValueError("physics inputs changed during generation; regenerate shard")
    audit.update(physics=physics, generation_components=generator_components(spec),
                 config=spec, elapsed_generation_s=perf_counter()-started,
                 screening_rate=(audit["attempts"]-1)/audit["attempts"],
                 provision_rejections=audit.get("provision_rejections", 0))
    if rule_freeze_v1_enabled():
        audit["instance_family"] = dict(instance.family)
    return instance, audit


def _paths(root, spec):
    spec = _canonical_labels(spec)
    path = Path(root)/spec["generator"]/spec["profile"]/f"seed-{spec['seed']:08d}.json"
    return path, path.with_suffix(".audit.json")


def configs_compatible(old, new, attempts):
    """Operational rejection caps may differ; accepted draws must fit the new cap.

    The original audit is immutable evidence of the cap used for its generation.
    Every other scenario field remains part of exact resume identity.
    """
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    if type(attempts) is not int or attempts < 1:
        return False
    old_cap, new_cap = old.get("max_attempts"), new.get("max_attempts")
    if type(old_cap) is not int or type(new_cap) is not int or min(old_cap, new_cap) < 1:
        return False
    return (attempts <= min(old_cap, new_cap)
            and {k:v for k,v in old.items() if k != "max_attempts"}
                == {k:v for k,v in new.items() if k != "max_attempts"})


def shard(job):
    """Audit file is the commit marker; interrupted shards regenerate exactly."""
    root, spec = job
    spec = _canonical_labels(spec)
    _require_current_campaign(spec)
    physics = physics_metadata()
    path, sidecar = _paths(root, spec)
    if sidecar.exists():
        audit = json.loads(sidecar.read_text())
        if not path.exists() or not configs_compatible(audit.get("config"), spec, audit.get("attempts")) or audit.get("instance_sha256") != _sha256(path):
            raise ValueError(f"resume configuration/checksum mismatch: {path}")
        _require_current_binding(audit, spec, physics)
        instance = load_instance(path)
        require_clean(instance, what="canonical v2 resume")
        if instance.voyage is None:
            raise ValueError(f"resume requires declared voyage; regenerate legacy shard: {path}")
        _validate_background_binding(instance, audit, physics["sha256"])
        if any(instance.provenance.versions.get(k) != v for k, v in generator_components(spec).items()):
            raise ValueError(f"stale component provenance; regenerate legacy shard: {path}")
        if any(instance.family.get(key) != value for key, value in family_tags(spec).items()) or len(instance.load_list) != spec["n_boxes"]:
            raise ValueError(f"resume family/target mismatch: {path}")
        return {"status": "resumed", "path": str(path), "audit": audit}
    instance, audit = generate(spec)
    _require_current_binding(audit, spec, physics)
    _validate_background_binding(instance, audit, physics["sha256"])
    if physics != physics_metadata():
        raise ValueError("physics inputs changed during generation; regenerate shard")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".partial.json")
    try:
        save_instance(instance, temp)
        if path.exists() and _sha256(path) != _sha256(temp):
            raise ValueError(f"incomplete shard differs from regeneration: {path}")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    audit["instance_sha256"] = _sha256(path)
    _atomic_json(sidecar, audit)
    return {"status": "generated", "path": str(path), "audit": audit}


def split_metadata(spec):
    """Use only the canonical split owner API; expose pending incompatibilities."""
    try:
        from eval.splits import InstanceSpec, canonical_spec, spec_key, split_of, test_family, SPLIT_POLICY, SPLITS_VERSION
        values = {**spec, "generator": "simulated" if spec["generator"] == "simulated_yard" else spec["generator"],
                  "cargo_profile": family_tags(spec)["cargo_profile"],
                  "rung": spec["size"], "slice": spec["size"],
                  "blocking_ratio": spec["noise"] if spec["generator"] == "random_yard" else None,
                  "noise": spec["noise"] if spec["generator"] == "simulated_yard" else None}
        if spec.get("cargo_profile") == "dg_stress":
            values.update(identity_version="v3", equipment_profile="dry20_dg_stress",
                          dg_stress_pair=spec.get("dg_stress_pair"))
        canonical = InstanceSpec(**{f.name: values[f.name] for f in fields(InstanceSpec) if f.name in values})
        # The frozen split owner currently omits these generation controls.
        # Preserve a separate complete demand identity without silently changing
        # its pinned split policy/key; the split owner must version that migration.
        generation_spec = {**canonical_spec(canonical), "n_boxes": spec["n_boxes"],
            "dense_20ft": spec.get("dense_20ft", False),
            "tiny_dg_enabled": spec.get("tiny_dg_enabled", False),
            "equipment_profile": family_tags(spec)["equipment_profile"]}
        if spec.get("booking_slack") is not None:
            generation_spec["booking_slack"] = float(spec["booking_slack"])
        if spec.get("booking_mass_fraction") is not None:
            generation_spec["booking_mass_fraction"] = float(spec["booking_mass_fraction"])
        if "d62_crossing_share" in spec:
            generation_spec["d62_crossing_share"] = spec["d62_crossing_share"]
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        if rule_freeze_v1_enabled() and spec["generator"] == "simulated_yard":
            generation_spec["congestion_mix_share"] = spec.get("congestion_mix_share", .25)
        generation_key = hashlib.sha256(json.dumps(generation_spec, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        split = split_of(canonical)
        return {"split": split, "split_spec": canonical_spec(canonical), "spec_key": spec_key(canonical),
                "generation_spec": generation_spec, "generation_key": generation_key,
                "test_family": test_family(canonical) if split == "test" else None, "split_policy": SPLIT_POLICY,
                "split_version": SPLITS_VERSION}
    except (ImportError, ValueError, TypeError) as exc:
        return {"split": None, "split_error": f"{type(exc).__name__}: {exc}"}


def summarize(records):
    """Aggregate overlapping reason rates with candidate draws as denominator."""
    reasons = Counter()
    attempts = sum(r["audit"]["attempts"] for r in records)
    provisions = sum(r["audit"].get("provision_rejections", 0) for r in records)
    elapsed = sum(r["audit"]["elapsed_generation_s"] for r in records)
    for record in records:
        reasons.update(record["audit"]["rejections"])
    return {"count": len(records), "attempts": attempts, "screened_out": attempts-len(records),
        **_completion_summary([record["audit"] for record in records]),
        "screening_rate": (attempts-len(records))/attempts if attempts else 0,
        "rejections": dict(sorted(reasons.items())),
        "screening_rates_by_reason": {k:v/attempts for k,v in sorted(reasons.items())} if attempts else {},
        "provision_rejections": provisions, "infeasible_by_provision_rate": provisions/attempts if attempts else 0,
        "elapsed_generation_s": elapsed, "mean_instance_s": elapsed/len(records) if records else 0,
        "shards": [{"instance": Path(r["path"]).name, "sha256": r["audit"]["instance_sha256"],
                    **_capacity_audit(r["audit"]),
                    **_producer_shard_fields(r["audit"]),
                    "spec": r["audit"]["config"],
                    "family": {**r["audit"].get("instance_family", {}), **family_tags(r["audit"]["config"])},
                    "seed": r["audit"]["config"]["seed"], **split_metadata(r["audit"]["config"])} for r in records]}


def write_manifests(root, records, mode, wall_s):
    physics = physics_metadata()
    groups = defaultdict(list)
    for record in records:
        spec = record["audit"]["config"]
        _require_current_campaign(spec)
        _require_current_binding(record["audit"], spec, physics)
        groups[(spec["generator"], spec["profile"])].append(record)
    manifest_path = Path(root)/"manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"groups": {}}
    if manifest.get("groups") and manifest.get("campaign_version") != CAMPAIGN_VERSION:
        raise ValueError("stale campaign manifest; preserve the archive and regenerate in a new directory")
    if manifest.get("groups") and manifest.get("physics") != physics:
        raise ValueError("stale physics manifest; preserve the archive and regenerate in a new directory")
    for group in manifest.get("groups", {}).values():
        for row in group.get("shards", []):
            _require_current_campaign(row.get("spec", {}))
    manifest.update(campaign_version=CAMPAIGN_VERSION, physics=physics, mode=mode, wall_s=wall_s,
                    split_status="pending canonical src/eval/splits.py")
    for (generator, profile), group in sorted(groups.items()):
        summary = summarize(sorted(group, key=lambda r:r["audit"]["config"]["seed"]))
        summary.update(generator=generator, profile=profile)
        _atomic_json(Path(root)/generator/profile/"manifest.json", summary)
        manifest["groups"][f"{generator}/{profile}"] = summary
    pending = sum(row["split"] is None for group in manifest["groups"].values() for row in group["shards"])
    manifest["split_status"] = "assigned" if not pending else f"pending canonical API compatibility for {pending} shards"
    _atomic_json(Path(root)/"manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiments/instances-v2"))
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--finalize-splits", action="store_true", help="verify existing shards and refresh manifests using canonical splits API")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--profiles", nargs="+", type=canonical_vessel_id, choices=COUNTS)
    parser.add_argument("--generators", nargs="+", choices=GENERATORS)
    parser.add_argument("--realistic-500-boxes", type=int, default=400)
    parser.add_argument("--realistic-800-boxes", type=int, default=800)
    parser.add_argument("--max-attempts", type=int, default=10000)
    parser.add_argument("--congestion-mix-share", type=float, default=.25,
                        help="activated D72 simulated-yard mixing share")
    parser.add_argument("--booking-slack", type=float, default=None,
                        help="synthetic demand TEU cap = (1-slack) * geometric decision cells; default unchanged")
    parser.add_argument("--booking-mass-fraction", type=float, default=None,
                        help="synthetic booking mass / scaled full-export reference cargo budget; default unchanged")
    args = parser.parse_args(argv)
    from stowage.generate.random_yard import validate_booking_slack, validate_booking_mass_fraction
    try:
        validate_booking_slack(args.booking_slack)
        validate_booking_mass_fraction(args.booking_mass_fraction)
        _validate_fixed_tiny_booking_caps(args.profiles, args.booking_slack, args.booking_mass_fraction)
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= args.workers <= 6 or args.seed < 0 or args.max_attempts < 1:
        parser.error("workers must be 1..6; seed nonnegative; max-attempts positive")
    if args.finalize_splits:
        records = []
        for audit_path in sorted(args.output.glob("*/*/seed-*.audit.json")):
            config = json.loads(audit_path.read_text())["config"]
            records.append(shard((str(args.output), config)))
        if not records:
            parser.error("no committed shards to finalize")
        manifest = write_manifests(args.output, records, "finalized", 0.0)
        print(json.dumps({"split_status": manifest["split_status"], "count": len(records)}))
        return 0 if manifest["split_status"] == "assigned" else 1
    specs = scenario_specs(pilot=args.pilot, seed=args.seed, profiles=args.profiles,
        generators=args.generators, realistic_500_boxes=args.realistic_500_boxes,
        realistic_800_boxes=args.realistic_800_boxes, max_attempts=args.max_attempts,
        congestion_mix_share=args.congestion_mix_share, booking_slack=args.booking_slack,
        booking_mass_fraction=args.booking_mass_fraction)
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    started = perf_counter()
    jobs = [(str(args.output), spec) for spec in specs]
    if args.workers == 1:
        results = map(shard, jobs)
        for record in results:
            records.append(record)
            write_manifests(args.output, records, "pilot" if args.pilot else "canonical", perf_counter()-started)
            print(json.dumps({"status": record["status"], "path": record["path"]}), flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for record in pool.map(shard, jobs, chunksize=1):
                records.append(record)
                write_manifests(args.output, records, "pilot" if args.pilot else "canonical", perf_counter()-started)
                print(json.dumps({"status": record["status"], "path": record["path"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
