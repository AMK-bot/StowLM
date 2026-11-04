"""Capacity-derived canonical Stage A campaign with resumable evidence.

Necessary C1 bounds reject impossible bookings without proving a loading plan
exists (Wolsey, Integer Programming, 1998, chapter 1). Both producers retain
DG sampling and count candidate failures separately from shipment redraws.
Realistic profiles use the full ship's export policy and dry20 equipment mix;
their box counts come from current physical capacity and mass budgets, never
legacy target volumes. This equipment conditioning is an APPROXIMATION inherited
from instances_v2_random. See random_export.py for the export-policy assumptions.

The frozen split identity uses generation inputs, independent of current hull
physics. A separate source/dependency fingerprint prevents resuming shards from
different physics implementations under that same identity. Passing these
screens is not checker approval. All four ports remain in the specifications;
screen exhaustion is reported rather than dropping a difficult family.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
import json
import math
from pathlib import Path
from time import perf_counter

from stowage.conventions import canonical_vessel_id
from stowage.frozen import require_clean
from stowage.generate.instances_cli import (
    _atomic_json, _sha256, _completion_summary, _validate_background_binding,
    _report_payload, _producer_shard_fields)
from stowage.generate.instances_v2 import (GENERATORS, VESSELS, PORTS, NOISE,
    configs_compatible, split_metadata, summarize as _summarize, _canonical_labels)
from stowage.io import load_instance, save_instance
from stowage.rules.catalogue import rule_freeze_v1_enabled

CAMPAIGN_VERSION = "9.0.0"
COUNTS = {"tiny": 400, "small": 400, "medium": 200, "full": 60,
          "realistic_500": 30, "realistic_800": 30}
STRUCTURAL_EXCLUSIONS = [{
    "profile": "full", "vessel": "feeder_1700", "port": "small_feeder",
    "noise_levels": list(NOISE), "rule_id": "expert.physical.slot_occupancy",
    "reachable_capacity_teu": 1260, "expected_cargo_teu": 1363.09,
    "reason": "Full mixed demand exceeds crane-reachable capacity in expectation; "
              "booking fit probability is at most 1.33e-13. Exclude the request "
              "from scheduling; retain every vessel, port and noise axis elsewhere.",
}]


def scenario_specs(*, pilot=False, seed=0, profiles=None, generators=None,
                   max_attempts=50, c2_screened=False, cargo_profile="feeder_export",
                   d62_crossing_share=.25, dg_stress_pair=None, congestion_mix_share=.25,
                   booking_slack=None, booking_mass_fraction=None):
    """Pilot addresses are prefixes; retain every axis within practical requests.

    The full feeder_1700/small_feeder combination is excluded before sampling:
    crane reach leaves 1,260 TEU against 1,363.09 expected booking TEU. This
    scheduling exclusion changes no cargo fact, physics limit or screen.
    """
    if type(seed) is not int or seed < 0 or type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("seed must be nonnegative and max_attempts positive")
    if type(c2_screened) is not bool:
        raise ValueError("c2_screened must be boolean")
    if isinstance(congestion_mix_share, bool) or not isinstance(congestion_mix_share, (int, float)) or not math.isfinite(congestion_mix_share) or not 0 <= congestion_mix_share <= 1:
        raise ValueError("congestion_mix_share must be finite and in [0, 1]")
    if not rule_freeze_v1_enabled() and congestion_mix_share != .25:
        raise ValueError("congestion_mix_share requires rule-freeze-v1 activation")
    if cargo_profile not in {"feeder_export", "dg_stress"}:
        raise ValueError("unsupported cargo_profile")
    if cargo_profile == "dg_stress" and not rule_freeze_v1_enabled():
        raise ValueError("dg_stress requires rule-freeze-v1 activation")
    if cargo_profile == "dg_stress" and (generators is None or set(generators) != {"random_yard"}):
        raise ValueError("v3 DG-stress identity requires the random_yard generator")
    if isinstance(d62_crossing_share, bool) or not math.isfinite(d62_crossing_share) or not 0 <= d62_crossing_share <= 1:
        raise ValueError("d62_crossing_share must be finite and in [0, 1]")
    if dg_stress_pair not in {None, "flammable_water_reactive", "water_reactive_acid"}:
        raise ValueError("unsupported DG-stress pair")
    if dg_stress_pair is not None and cargo_profile != "dg_stress":
        raise ValueError("DG-stress pair requires dg_stress cargo_profile")
    if cargo_profile == "dg_stress":
        profiles = {"tiny", "small"} if profiles is None else set(profiles)
        if any(profile.startswith("realistic") for profile in profiles):
            raise ValueError("DG-stress uses its own dry20 equipment identity, not realistic profiles")
    from stowage.generate.random_yard import validate_booking_slack, validate_booking_mass_fraction
    validate_booking_slack(booking_slack)
    validate_booking_mass_fraction(booking_mass_fraction)
    profiles = None if profiles is None else {canonical_vessel_id(profile) for profile in profiles}
    result = []
    for gi, generator in enumerate(GENERATORS):
        if generators is not None and generator not in generators:
            continue
        for pi, (profile, count) in enumerate(COUNTS.items()):
            if profiles is not None and profile not in profiles:
                continue
            realistic = profile.startswith("realistic")
            vessels = (("feeder_500",) if profile == "realistic_500" else
                       ("feeder_800",) if profile == "realistic_800" else VESSELS)
            combinations = [(v, p, n) for n in NOISE for p in PORTS for v in vessels]
            if profile == "full":
                combinations = [combination for combination in combinations
                    if combination[:2] != ("feeder_1700", "small_feeder")]
            for i in range(5 if pilot else count):
                vessel, port, noise = combinations[i % len(combinations)]
                result.append(dict(campaign_version=CAMPAIGN_VERSION, generator=generator,
                    profile=profile, size="full" if realistic else profile,
                    seed=100_000_000 + seed + gi*10_000_000 + pi*100_000 + i,
                    vessel=vessel, port=port, noise=noise, n_boxes=None,
                    max_attempts=max_attempts, dense_20ft=realistic,
                    full_export=realistic or profile == "full", tiny_dg_enabled=True,
                    cargo_profile=cargo_profile,
                    c2_screened=c2_screened or (rule_freeze_v1_enabled() and profile == "medium"),
                    **({"d62_crossing_share": d62_crossing_share} if rule_freeze_v1_enabled() and generator == "random_yard" else {}),
                    **({"congestion_mix_share": float(congestion_mix_share)} if rule_freeze_v1_enabled() and generator == "simulated_yard" else {}),
                    **({"identity_version": "v3", "equipment_profile": "dry20_dg_stress",
                        "dg_stress_pair": dg_stress_pair} if cargo_profile == "dg_stress" else {})))
    if booking_slack is not None:
        for spec in result:
            spec["booking_slack"] = float(booking_slack)
    if booking_mass_fraction is not None:
        for spec in result:
            spec["booking_mass_fraction"] = float(booking_mass_fraction)
    return result


def _effective_spec(spec):
    spec = _canonical_labels(spec)
    if rule_freeze_v1_enabled() and spec["generator"] == "simulated_yard":
        spec = {"congestion_mix_share": .25, **spec}
    if rule_freeze_v1_enabled() and spec["size"] == "medium":
        spec = {**spec, "c2_screened": True}
    return spec


def _physics_inputs(physics):
    """Separate screening strata from shared physical input identity."""
    if not rule_freeze_v1_enabled() or "input_sha256" not in physics:
        return physics
    return {key: value for key, value in physics.items() if key not in {"sha256", "c2_screened"}}


def physics_metadata(*, c2_screened=False):
    """Bind persisted audits to the same complete inputs as the D58 gate."""
    from stowage.generate.physics_identity import current_physics_identity

    metadata = dict(current_physics_identity())
    metadata["input_sha256"] = metadata["sha256"]
    metadata["c2_screened"] = bool(c2_screened)
    metadata["sha256"] = sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    return metadata


def family_tags(spec, target_count):
    spec = _canonical_labels(spec)
    tags = {key: str(spec[key]) for key in ("vessel", "port", "size", "seed", "generator", "cargo_profile", "profile")}
    empty_tiny = (spec["generator"] == "simulated_yard" and spec["size"] == "tiny"
                  and not spec["tiny_dg_enabled"])
    if empty_tiny:
        tags["cargo_profile"] = "empty_dry20"
    tags.update(noise=repr(float(spec["noise"])), slice=spec["size"], n_boxes=str(target_count),
        equipment_profile="empty_dry20" if empty_tiny else "dry20_with_dg" if spec["size"] == "tiny" else
                          "dense_20ft" if spec["dense_20ft"] else "full_mix")
    if spec.get("cargo_profile") == "dg_stress":
        tags.update(identity_version="v3", equipment_profile="dry20_dg_stress",
                    dg_stress_pair=spec.get("dg_stress_pair") or "mixed")
    if spec.get("booking_slack") is not None:
        tags["booking_slack"] = repr(float(spec["booking_slack"]))
        tags["booking_capacity_basis"] = "decision_cells_geometric_teu"
    if spec["generator"] == "random_yard":
        tags["blocking_ratio"] = repr(float(spec["noise"]))
    if spec.get("booking_mass_fraction") is not None:
        tags["booking_mass_fraction"] = repr(float(spec["booking_mass_fraction"]))
        tags["booking_mass_basis"] = "scaled_full_export_cargo_budget_at_realised_mass"
    return tags


def screen_c2(instance):
    """Qualified depth-0 LP screen; an unavailable model is never a pass.

    Warm-up must first succeed for the whole fleet. The engine can still lack
    a model for a particular booking, so require its explicit availability flag.
    D49 requires the feasibility owner's GlobalScreen to request linear rows
    with explicit direction="necessary". The generator delegates that policy
    to its owning adapter; it never substitutes a plan-constructing LP. Missing
    qualification or an incompatible linear API makes this screen unavailable.
    This is an empirically qualified necessary relaxation, not checker approval.
    """
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    try:
        engine = make_feasibility_engine(instance, get_action_space("boxslot"),
            config=FeasibilityConfig(lookahead=0, extra={"global_screen": 1}))
        if engine.diagnostics.get("global_screen") != "on":
            return ("checker.missing_vessel_data",)
        return () if engine.completion_check().get("c2") is True else ("c2",)
    except (ValueError, KeyError, TypeError):
        # Keep the direct entry point as fail-closed as the candidate hook.
        # In particular, never call missing direction/qualification a C2 pass.
        return ("checker.missing_vessel_data",)


@contextmanager
def _completion_screen(spec, report=None):
    """Add C2 inside candidate rejection, restoring hooks even on exhaustion.

    Each CLI worker generates sequentially in an isolated process. The random
    adapter also modifies module hooks, so this context must never be shared
    across concurrent threads.
    """
    if not spec["c2_screened"] or (rule_freeze_v1_enabled() and spec["size"] == "medium"):
        yield
        return
    from stowage.generate import instances, random_yard
    module = random_yard if spec["generator"] == "random_yard" else instances
    original = module.completion_failures

    def completion(instance):
        failed = original(instance)
        if failed or len(instance.load_list) == 1:
            return failed
        if report is not None and rule_freeze_v1_enabled():
            report.c2_requested = True
            report.c2_checks += 1
            report.c2_screened = True
            report.c2_direction = "necessary"
        try:
            return tuple(screen_c2(instance))
        except Exception:
            return ("checker.missing_vessel_data",)

    module.completion_failures = completion
    try:
        yield
    finally:
        module.completion_failures = original


class CampaignRejection(ValueError):
    """Expected booking/initialization failure, carrying its complete audit."""
    def __init__(self, message, audit):
        super().__init__(message)
        self.audit = audit


def _generation_components(spec):
    from stowage.generate.instances_v2 import generator_components
    from stowage.yard import effective_yard_simulator_version

    return {**generator_components(spec), "instances_campaign": CAMPAIGN_VERSION,
            "yard_simulator": effective_yard_simulator_version()}


def _validate_components(audit, spec):
    if rule_freeze_v1_enabled() and audit.get("generation_components") != _generation_components(spec):
        raise ValueError("resume generator component provenance mismatch")


def _audit_metadata(report, spec, physics, expected, started):
    if physics_metadata(c2_screened=spec["c2_screened"]) != physics:
        raise ValueError("physics implementation changed during generation")
    audit = _report_payload(report)
    screened = report.c2_screened if rule_freeze_v1_enabled() else spec["c2_screened"]
    audit.update(config=spec, physics=physics, elapsed_generation_s=perf_counter()-started,
        screening_rate=report.screening_rate, c2_screened=screened,
        c2_status="C2 screened" if screened else "C2 unscreened", **expected)
    if rule_freeze_v1_enabled():
        audit.update(c2_requested=spec["c2_screened"], generation_components=_generation_components(spec))
    return audit


def generate(spec):
    """Return one clean instance; record the assigned split before sampling."""
    from stowage.generate.instances import GenerationReport, generate_simulated_instance
    from stowage.generate.instances_v2_random import generate_random_v2
    from eval.splits import canonical_spec, spec_from_instance
    spec = _effective_spec(spec)
    if spec.get("cargo_profile") == "dg_stress" and not rule_freeze_v1_enabled():
        raise ValueError("dg_stress requires rule-freeze-v1 activation")
    started = perf_counter()
    expected = split_metadata(spec)
    if expected["split"] is None:
        raise ValueError(f"split assignment failed: {expected}")
    before = physics_metadata(c2_screened=spec["c2_screened"])
    if spec.get("physics_sha256", before["sha256"]) != before["sha256"]:
        raise ValueError("physics implementation changed since campaign admission")
    report = GenerationReport()
    producer = generate_random_v2 if spec["generator"] == "random_yard" else generate_simulated_instance
    if spec["generator"] not in GENERATORS:
        raise ValueError("unknown generator")
    try:
        with _completion_screen(spec, report):
            instance = producer(spec["size"], seed=spec["seed"], noise=spec["noise"],
                vessel_name=spec["vessel"], port_name=spec["port"], n_boxes=spec["n_boxes"],
                dense_20ft=spec["dense_20ft"], report=report, max_attempts=spec["max_attempts"],
                tiny_dg_enabled=spec["tiny_dg_enabled"],
                **({"booking_slack": spec["booking_slack"]} if spec.get("booking_slack") is not None else {}),
                **({"booking_mass_fraction": spec["booking_mass_fraction"]} if spec.get("booking_mass_fraction") is not None else {}),
                **({"cargo_profile": spec.get("cargo_profile", "feeder_export"),
                    "instance_id": f"v3.{spec['generator']}.{spec['profile']}.{spec['seed']}",
                    **({"d62_crossing_share": spec.get("d62_crossing_share", .25),
                        "dg_stress_pair": spec.get("dg_stress_pair")}
                       if spec["generator"] == "random_yard" else
                       {"congestion_mix_share": spec["congestion_mix_share"]})}
                   if rule_freeze_v1_enabled() else {}))
    except ValueError as exc:
        message = str(exc)
        balancing = message.startswith("cannot balance slice background within physical cargo capacities: ")
        exhausted = message.startswith(f"screening exhausted {spec['max_attempts']} attempts: ")
        if type(exc) is not ValueError or not (balancing or exhausted):
            raise
        initialization_attempts = int(report.attempts == 0)
        report.attempts = max(1, report.attempts)
        report.accepted = False
        if balancing:
            report.balancing_rejections = max(1, report.balancing_rejections)
            rule = "expert.physical.slot_occupancy"
            report.rejections[rule] = min(report.attempts, report.rejections.get(rule, 0) + 1)
        if report.balancing_rejections > report.attempts:
            raise ValueError("background balancing rejection count exceeds candidate attempts") from exc
        audit = _audit_metadata(report, spec, before, expected, started)
        diagnostics = {"screening_exhausted": 1} if exhausted else {}
        if report.balancing_rejections:
            diagnostics["background_balance_infeasible"] = report.balancing_rejections
        audit.update(status="rejected", failure_message=message,
            rejection_diagnostics=diagnostics,
            initialization_attempts=initialization_attempts)
        raise CampaignRejection(message, audit) from exc
    if spec["full_export"] and not report.full_export:
        raise ValueError("full export profile lacks an export-policy audit")
    if report.requested_targets != len(instance.load_list) or not report.accepted:
        raise ValueError("accepted target count disagrees with capacity audit")
    if instance.voyage is None:
        raise ValueError("producer lacks the required D50 voyage declaration")
    instance = instance.model_copy(update={"family": {**instance.family, **family_tags(spec, len(instance.load_list))},
        "id": f"v3.{spec['generator']}.{spec['profile']}.{spec['seed']}"})
    _validate_background_binding(instance, _report_payload(report), before.get("input_sha256", before["sha256"]))
    if rule_freeze_v1_enabled():
        instance.provenance.versions.update(_generation_components(spec))
    instance.provenance.versions["instances_campaign"] = CAMPAIGN_VERSION
    require_clean(instance, what="canonical v3 campaign")
    if canonical_spec(spec_from_instance(instance)) != expected["split_spec"]:
        raise ValueError("instance family disagrees with assigned split")
    audit = _audit_metadata(report, spec, before, expected, started)
    audit["instance_versions"] = dict(instance.provenance.versions)
    if rule_freeze_v1_enabled():
        audit["instance_family"] = dict(instance.family)
    return instance, audit


def _paths(root, spec):
    spec = _canonical_labels(spec)
    path = Path(root)/spec["generator"]/spec["profile"]/f"seed-{spec['seed']:09d}.json"
    return path, path.with_suffix(".audit.json")


def shard(job):
    """Audit is the commit marker; resume checks current physics and exact bytes."""
    root, spec = job
    spec = _effective_spec(spec)
    path, sidecar = _paths(root, spec)
    rejected_path = path.with_suffix(".rejected.json")
    if rejected_path.exists():
        audit = json.loads(rejected_path.read_text())
        checksum = audit.pop("rejection_sha256", None)
        _validate_components(audit, spec)
        if (path.exists() or sidecar.exists() or audit.get("status") != "rejected"
                or audit.get("accepted") is not False
                or not configs_compatible(audit.get("config"), spec, audit.get("attempts"))
                or audit.get("physics") != physics_metadata(c2_screened=spec["c2_screened"])
                or checksum != _rejection_checksum(audit)
                or any(audit.get(key) != value for key, value in split_metadata(spec).items())):
            raise ValueError(f"resume rejection configuration/checksum/physics mismatch: {rejected_path}")
        audit["rejection_sha256"] = checksum
        retry = (spec["max_attempts"] > audit["config"]["max_attempts"]
                 and "screening_exhausted" in audit.get("rejection_diagnostics", {}))
        if not retry:
            return dict(status="resumed_rejected", path=str(rejected_path), audit=audit)
    if sidecar.exists():
        from eval.splits import canonical_spec, spec_from_instance

        audit = json.loads(sidecar.read_text())
        _validate_components(audit, spec)
        if (not path.exists() or not configs_compatible(audit.get("config"), spec, audit.get("attempts"))
                or audit.get("instance_sha256") != _sha256(path)
                or audit.get("physics") != physics_metadata(c2_screened=spec["c2_screened"])):
            raise ValueError(f"resume configuration/checksum/physics mismatch: {path}")
        instance = load_instance(path)
        require_clean(instance, what="canonical v3 resume")
        _validate_background_binding(instance, audit, audit["physics"].get("input_sha256", audit["physics"]["sha256"]))
        if instance.voyage is None:
            raise ValueError("resume instance lacks the required D50 voyage declaration")
        # Producer families retain demand/conditioning and booking metadata.
        # Validate the campaign-owned tags plus the complete canonical request,
        # rather than rejecting every additive producer tag on resume.
        if (any(instance.family.get(key) != value
                for key, value in family_tags(spec, len(instance.load_list)).items())
                or canonical_spec(spec_from_instance(instance)) != split_metadata(spec)["split_spec"]
                or audit.get("requested_targets") != len(instance.load_list)
                or audit.get("instance_versions") != instance.provenance.versions
                or instance.seed != spec["seed"] or instance.size_class != spec["size"]
                or instance.stage != "A"
                or audit.get("c2_requested" if rule_freeze_v1_enabled() else "c2_screened") != spec["c2_screened"]
                or (rule_freeze_v1_enabled() and any(instance.provenance.versions.get(key) != value
                    for key, value in _generation_components(spec).items()))
                or any(audit.get(key) != value for key, value in split_metadata(spec).items())):
            raise ValueError(f"resume family/audit/provenance mismatch: {path}")
        return dict(status="resumed", path=str(path), audit=audit)
    try:
        instance, audit = generate(spec)
    except CampaignRejection as exc:
        if path.exists() or sidecar.exists():
            raise ValueError(f"rejected request conflicts with existing instance: {path}") from exc
        audit = exc.audit
        audit["rejection_sha256"] = _rejection_checksum(audit)
        rejected_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(rejected_path, audit)
        return dict(status="rejected", path=str(rejected_path), audit=audit)
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
    rejected_path.unlink(missing_ok=True)
    return dict(status="generated", path=str(path), audit=audit)


def _rejection_checksum(audit):
    return sha256(json.dumps(audit, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def summarize(records):
    accepted = [record for record in records if record["audit"].get("status") != "rejected"]
    rejected = [record for record in records if record["audit"].get("status") == "rejected"]
    summary = _summarize(accepted)
    # v2's summary assumes an explicit quantity in its family helper.
    for row, record in zip(summary["shards"], accepted):
        audit = record["audit"]
        row["family"] = {**audit.get("instance_family", {}),
                         **family_tags(audit["config"], audit["requested_targets"])}
        row.update(_producer_shard_fields(audit))
        row["full_export"] = audit.get("full_export", {})
        row["physics_sha256"] = audit["physics"]["sha256"]
    summary["split_counts"] = dict(sorted(Counter(row["split"] for row in summary["shards"]).items()))
    attempts = sum(record["audit"]["attempts"] for record in records)
    reasons, diagnostics = Counter(), Counter()
    for record in records:
        reasons.update(record["audit"]["rejections"])
        diagnostics.update(record["audit"].get("rejection_diagnostics", {}))
    elapsed = sum(record["audit"]["elapsed_generation_s"] for record in records)
    provisions = sum(record["audit"].get("provision_rejections", 0) for record in records)
    balancing = sum(record["audit"].get("balancing_rejections", 0) for record in records)
    summary.update(_completion_summary([record["audit"] for record in records]))
    summary.update(accepted=len(accepted), rejected=len(rejected), requested=len(records), attempts=attempts,
        screened_out=attempts-len(accepted), screening_rate=(attempts-len(accepted))/attempts if attempts else 0,
        request_rejection_rate=len(rejected)/len(records) if records else 0,
        rejections=dict(sorted(reasons.items())),
        screening_rates_by_reason={key: count/attempts for key, count in sorted(reasons.items())} if attempts else {},
        rejection_diagnostics=dict(sorted(diagnostics.items())), elapsed_generation_s=elapsed,
        mean_instance_s=elapsed/len(accepted) if accepted else 0,
        mean_request_s=elapsed/len(records) if records else 0,
        provision_rejections=provisions, infeasible_by_provision_rate=provisions/attempts if attempts else 0,
        balancing_rejections=balancing, balancing_screening_rate=balancing/attempts if attempts else 0,
        accepted_split_counts=summary["split_counts"],
        rejected_split_counts=dict(sorted(Counter(record["audit"]["split"] for record in rejected).items())),
        rejected_shards=[dict(instance=None, rejection=Path(record["path"]).name,
            rejection_sha256=record["audit"]["rejection_sha256"], spec=record["audit"]["config"],
            rejection_diagnostics=record["audit"]["rejection_diagnostics"],
            **_producer_shard_fields(record["audit"]),
            **split_metadata(record["audit"]["config"])) for record in rejected])
    return summary


def write_manifests(root, records, mode, wall_s, planned_requested=None):
    from stowage.generate.instances import INSTANCES_VERSION
    from stowage.generate.instances_v2_random import effective_random_v2_version
    from stowage.generate.random_yard import effective_random_yard_version
    from stowage.generate.cargo import effective_cargo_generator_version
    from stowage.yard import effective_yard_simulator_version

    groups = defaultdict(list)
    for record in records:
        spec = record["audit"]["config"]
        groups[(spec["generator"], spec["profile"])].append(record)
    path = Path(root)/"manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {"groups": {}}
    campaign_source = _sha256(Path(__file__))
    if manifest.get("campaign_source_sha256", campaign_source) != campaign_source:
        raise ValueError("campaign implementation changed during generation")
    fingerprints = {json.dumps(_physics_inputs(record["audit"]["physics"]), sort_keys=True)
                    for record in records}
    if manifest.get("physics"):
        fingerprints.add(json.dumps(_physics_inputs(manifest["physics"]), sort_keys=True))
    if len(fingerprints) > 1:
        raise ValueError("cannot mix physics versions in one campaign")
    if records:
        manifest["physics"] = records[0]["audit"]["physics"]
    manifest.update(campaign_version=CAMPAIGN_VERSION, mode=mode, wall_s=wall_s,
        campaign_source_sha256=campaign_source,
        generator_versions={"random_yard": effective_random_yard_version(),
                            "random_yard_adapter": effective_random_v2_version(), "simulated_yard": INSTANCES_VERSION,
                            **({"cargo_generator": effective_cargo_generator_version(),
                                "yard_simulator": effective_yard_simulator_version()}
                               if rule_freeze_v1_enabled() else {})},
        structural_exclusions=STRUCTURAL_EXCLUSIONS,
        structural_exclusion_count=len(STRUCTURAL_EXCLUSIONS),
        structurally_excluded_family_combinations=len(GENERATORS) * sum(
            len(exclusion["noise_levels"]) for exclusion in STRUCTURAL_EXCLUSIONS),
        caveats=["Necessary screens do not certify a feasible plan; checker approval is required.",
                 "Realistic profiles condition equipment to dry20; box counts follow current export policy.",
                 "All four port archetypes are requested; infeasible requests fail rather than being omitted."])
    for (generator, profile), group in sorted(groups.items()):
        summary = summarize(sorted(group, key=lambda row: row["audit"]["config"]["seed"]))
        summary.update(generator=generator, profile=profile,
            planned_requested=5 if mode == "pilot" else COUNTS[profile])
        summary["complete"] = summary["requested"] == summary["planned_requested"]
        _atomic_json(Path(root)/generator/profile/"manifest.json", summary)
        manifest["groups"][f"{generator}/{profile}"] = summary
    totals, rejected_totals = Counter(), Counter()
    rung_counts = {}
    for group in manifest["groups"].values():
        totals.update(group["split_counts"])
        rejected_totals.update(group["rejected_split_counts"])
        rung = "full" if group["profile"].startswith("realistic") else group["profile"]
        rung_row = rung_counts.setdefault(rung, {"accepted": Counter(), "rejected": Counter()})
        rung_row["accepted"].update(group["accepted_split_counts"])
        rung_row["rejected"].update(group["rejected_split_counts"])
    manifest["split_counts"] = dict(sorted(totals.items()))
    manifest["accepted_split_counts"] = dict(sorted(totals.items()))
    manifest["rejected_split_counts"] = dict(sorted(rejected_totals.items()))
    manifest["rung_split_counts"] = rung_counts
    manifest["count"] = sum(group["count"] for group in manifest["groups"].values())
    for field in ("accepted", "rejected", "requested", "planned_requested", "attempts", "screened_out", "elapsed_generation_s", "balancing_rejections"):
        manifest[field] = sum(group[field] for group in manifest["groups"].values())
    manifest["planned_requested"] = planned_requested if planned_requested is not None else 60 if mode == "pilot" else 2240
    manifest["request_rejection_rate"] = manifest["rejected"]/manifest["requested"] if manifest["requested"] else 0
    manifest["screening_rate"] = manifest["screened_out"]/manifest["attempts"] if manifest["attempts"] else 0
    manifest["balancing_screening_rate"] = manifest["balancing_rejections"]/manifest["attempts"] if manifest["attempts"] else 0
    manifest["complete"] = manifest["requested"] == manifest["planned_requested"]
    for field in ("rejections", "completion_rejections", "rejection_diagnostics"):
        counts = Counter()
        for group in manifest["groups"].values():
            counts.update(group[field])
        manifest[field] = dict(sorted(counts.items()))
    manifest["screening_rates_by_reason"] = {key: count/manifest["attempts"] for key, count in manifest["rejections"].items()} if manifest["attempts"] else {}
    manifest["completion_screening_rates_by_reason"] = {key: count/manifest["attempts"] for key, count in manifest["completion_rejections"].items()} if manifest["attempts"] else {}
    if rule_freeze_v1_enabled():
        rows = [row for group in manifest["groups"].values()
                for row in [*group["shards"], *group["rejected_shards"]]]
        statuses = {row["producer_report"].get("c2_screened", False) for row in rows}
        manifest["c2_requested"] = any(row["spec"]["c2_screened"] for row in rows)
    else:
        statuses = {row["spec"]["c2_screened"] for group in manifest["groups"].values()
                    for row in [*group["shards"], *group["rejected_shards"]]}
    manifest["c2_status"] = "C2 screened" if statuses == {True} else "C2 unscreened" if statuses == {False} else "mixed"
    _atomic_json(path, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiments/instances-v3"))
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--profiles", nargs="+", type=canonical_vessel_id, choices=COUNTS)
    parser.add_argument("--generators", nargs="+", choices=GENERATORS)
    parser.add_argument("--max-attempts", type=int, default=50)
    parser.add_argument("--cargo-profile", choices=["feeder_export", "dg_stress"], default="feeder_export")
    parser.add_argument("--d62-crossing-share", type=float, default=.25)
    parser.add_argument("--congestion-mix-share", type=float, default=.25,
                        help="activated D72 simulated-yard mixing share")
    parser.add_argument("--dg-stress-pair", choices=["flammable_water_reactive", "water_reactive_acid"])
    parser.add_argument("--c2", action="store_true", help="require an approved C2 hook (fails closed if unavailable)")
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
    if not 1 <= args.workers <= 6 or args.seed < 0 or args.max_attempts < 1:
        parser.error("workers must be 1..6; seed nonnegative; max-attempts positive")
    specs = scenario_specs(pilot=args.pilot, seed=args.seed, profiles=args.profiles,
        generators=args.generators, max_attempts=args.max_attempts, c2_screened=args.c2,
        cargo_profile=args.cargo_profile, d62_crossing_share=args.d62_crossing_share,
        dg_stress_pair=args.dg_stress_pair, congestion_mix_share=args.congestion_mix_share,
        booking_slack=args.booking_slack, booking_mass_fraction=args.booking_mass_fraction)
    if any(spec["c2_screened"] for spec in specs):
        qualification = Path(__file__).resolve().parents[3] / "experiments/cache/stability/warmup/summary.json"
        if not qualification.exists() or not json.loads(qualification.read_text()).get("complete"):
            raise ValueError("C2 requires a successful whole-fleet cache_warm qualification")
    physics = physics_metadata(c2_screened=args.c2)
    existing = args.output / "manifest.json"
    if existing.exists() and _physics_inputs(json.loads(existing.read_text()).get("physics", {})) != _physics_inputs(physics):
        raise ValueError("campaign root physics fingerprint disagrees with current implementation")
    if existing.exists() and json.loads(existing.read_text()).get("campaign_source_sha256", _sha256(Path(__file__))) != _sha256(Path(__file__)):
        raise ValueError("campaign root implementation fingerprint disagrees with current implementation")
    specs = [{**spec, "physics_sha256": physics_metadata(c2_screened=spec["c2_screened"])["sha256"]}
             for spec in specs]
    args.output.mkdir(parents=True, exist_ok=True)
    records, started = [], perf_counter()
    jobs = [(str(args.output), spec) for spec in specs]

    def consume(results):
        for record in results:
            records.append(record)
            write_manifests(args.output, records, "pilot" if args.pilot else "canonical", perf_counter()-started,
                            planned_requested=len(jobs))
            print(json.dumps(dict(status=record["status"], path=record["path"])), flush=True)
    if args.workers == 1:
        consume(map(shard, jobs))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            consume(pool.map(shard, jobs, chunksize=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
