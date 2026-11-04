"""Verify all 2,240 C1+C2 requests without regenerating or solving cargo.

Accepted instances and rejected audits partition the frozen specifications.
Rejected requests contain no solver instance. These integrity and necessary
screening checks do not certify a full-checker-approved loading plan. Read one
Instance at a time, retaining only compact audit accounting in memory.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from time import perf_counter

from eval.splits import canonical_spec, spec_from_instance
from stowage.frozen import require_clean
from stowage.generate import instances_v3 as campaign
from stowage.generate.instances_cli import _atomic_json, _sha256
from stowage.io import load_instance

FULL_CELLS = {"feeder_500": 516, "feeder_1000": 816, "feeder_1700": 1604}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def equal(actual, expected, name):
    if isinstance(expected, float):
        require(type(actual) in (float, int) and math.isfinite(actual)
                and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-9), f"{name}: {actual!r} != {expected!r}")
    else:
        require(actual == expected, f"{name}: mismatch")


def integer(value, name, *, minimum=0, maximum=None):
    require(type(value) is int and value >= minimum and (maximum is None or value <= maximum), f"invalid {name}: {value!r}")
    return value


def policy_bank_pin(root):
    """Check the supplemental late pin without treating mtime as a checksum."""
    pin_path = root / "policy-bank-pin.json"
    pin = json.loads(pin_path.read_text())
    equal(pin.get("path"), "src/stowage/linear/margin_policy_data.json", "policy bank path")
    repository = Path(campaign.__file__).resolve().parents[3]
    bank = repository / pin["path"]
    equal(_sha256(bank), pin.get("sha256"), "supplemental policy bank checksum")
    equal(bank.stat().st_size, pin.get("size_bytes"), "supplemental policy bank size")
    require(bool(pin.get("limitation")), "late policy bank capture limitation missing")
    require(pin.get("captured_after_main_start") is True and
            datetime.fromisoformat(pin["captured_at"]) > datetime.fromisoformat(pin["main_started_at"]),
            "supplemental pin capture timing mismatch")
    log = root / pin["qualification_log"]
    equal(_sha256(log), pin.get("qualification_log_sha256"), "qualified warm log checksum")
    policies = []
    for number, line in enumerate(log.read_text().splitlines(), 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        for cache in entry.get("caches", []):
            report = cache.get("report", {})
            if "margin_policy_sha256" in report:
                policies.append(dict(ship=entry["ship"], geometry=cache["geometry"], log_line=number,
                    water_density_kg_m3=report.get("water_density_kg_m3"),
                    margin_policy_status=report.get("margin_policy_status"),
                    margin_policy_sha256=report["margin_policy_sha256"]))
    equal(len(policies), 15, "qualified policy geometry count")
    equal(policies, pin.get("qualified_margin_policies"), "qualified policy hash summary")
    return pin, _sha256(pin_path)


def inspect_instance(path, audit, planned):
    instance = load_instance(path)
    require_clean(instance, what="canonical v3 verification")
    equal(instance.provenance.versions, audit.get("instance_versions"), "instance provenance")
    equal(instance.provenance.versions.get("instances_campaign"), campaign.CAMPAIGN_VERSION, "instance campaign")
    equal((instance.seed, instance.size_class, instance.stage), (planned["seed"], planned["size"], "A"), "instance identity")
    equal(instance.family, campaign.family_tags(planned, len(instance.load_list)), "instance family")
    equal(canonical_spec(spec_from_instance(instance)), audit["split_spec"], "embedded split identity")
    containers = {box.id: box for box in instance.containers}
    boxes = [containers[box_id] for box_id in instance.load_list]
    count = len(boxes)
    integer(audit.get("requested_targets"), "requested_targets", minimum=1)
    equal(audit["requested_targets"], count, "capacity target count")
    equal(audit.get("capacity_teu"), len(instance.vessel.cells), "physical capacity")
    teu = sum(box.length_ft / 20 for box in boxes)
    equal(audit.get("realised_teu"), float(teu), "cargo TEU")
    integer(audit.get("teu_budget"), "teu_budget", minimum=1)
    require(teu <= audit["teu_budget"] + 1e-9, "TEU budget exceeded")
    require(sum(1 if box.length_ft == 20 else 2 for box in boxes) <= len(instance.vessel.cells), "physical cell budget exceeded")
    equal(set(audit["length_slot_budgets"]), {"20", "40", "45"}, "length budget keys")
    for length, budget in audit["length_slot_budgets"].items():
        integer(budget, f"length {length} budget")
        require(sum(box.length_ft == int(length) for box in boxes) <= budget, "length budget exceeded")
    if planned["size"] == "full":
        equal(len(instance.vessel.cells), FULL_CELLS[planned["vessel"]], "full vessel geometry")
        export = audit.get("full_export")
        require(isinstance(export, dict) and bool(export), "missing full export audit")
        equal(export["capacity_teu"], len(instance.vessel.cells), "export capacity")
        equal(export["cargo_mass_kg"], float(sum(box.gross_mass_kg for box in boxes)), "export cargo mass")
        equal(export["teu_fraction"], teu/len(instance.vessel.cells), "export TEU fraction")
        require(.75 <= export["cargo_mass_fraction"] <= .90, "export cargo mass band")
        require(.90 <= export["design_displacement_fraction"] <= 1.0, "export displacement band")
        require(export["teu_fraction"] <= .90 and (export["mass_limited"] is True or export["teu_fraction"] >= .75), "export TEU band")
    return dict(targets=count, dg_bookings=sum(box.dg is not None for box in boxes))


def report_totals(groups):
    """Aggregate candidate audits, keeping fail-closed provisions explicit."""
    fields = ("attempts", "screened_out", "provision_rejections", "elapsed_generation_s",
              "shipment_redraws", "completion_checks")
    totals = {field: sum(group[field] for group in groups.values()) for field in fields}
    attempts = totals["attempts"]
    requested = sum(group["requested"] for group in groups.values())
    accepted = sum(group["accepted"] for group in groups.values())
    totals.update(screening_rate=totals["screened_out"]/attempts if attempts else 0,
        infeasible_by_provision_rate=totals["provision_rejections"]/attempts if attempts else 0,
        mean_request_s=totals["elapsed_generation_s"]/requested if requested else 0,
        mean_instance_s=totals["elapsed_generation_s"]/accepted if accepted else 0)
    for count_field, rate_field in (("rejections", "screening_rates_by_reason"),
                                    ("completion_rejections", "completion_screening_rates_by_reason")):
        reasons = Counter()
        for group in groups.values():
            reasons.update(group[count_field])
        totals[count_field] = dict(sorted(reasons.items()))
        totals[rate_field] = {key: count/attempts for key, count in sorted(reasons.items())} if attempts else {}
    return totals


def verify(root):
    started = perf_counter()
    supplemental_pin, supplemental_pin_sha256 = policy_bank_pin(root)
    admitted = json.loads((root / "admitted-physics.json").read_text())
    equal(campaign.physics_metadata(c2_screened=True), admitted, "current admitted C2 physics")
    manifest = json.loads((root / "manifest.json").read_text())
    qualification = json.loads((root / "c2-qualification.json").read_text())
    require(qualification.get("exit_code") == 0 and qualification.get("complete") is True
            and qualification.get("c2_status") == "C2 screened", "C2 qualification did not succeed")
    equal(manifest.get("physics"), admitted, "root physics")
    equal(manifest.get("campaign_version"), campaign.CAMPAIGN_VERSION, "campaign version")
    from stowage.generate.instances import INSTANCES_VERSION
    from stowage.generate.instances_v2_random import RANDOM_V2_VERSION
    from stowage.generate.random_yard import RANDOM_YARD_VERSION
    equal(manifest.get("generator_versions"), {"random_yard": RANDOM_YARD_VERSION,
        "random_yard_adapter": RANDOM_V2_VERSION, "simulated_yard": INSTANCES_VERSION}, "producer versions")
    equal(manifest.get("campaign_source_sha256"), _sha256(Path(campaign.__file__)), "campaign source release")
    equal(manifest.get("structural_exclusions"), campaign.STRUCTURAL_EXCLUSIONS, "structural exclusions")
    require(manifest.get("complete") is True, "campaign incomplete")
    start_path = root / "main-start.json"
    admission = json.loads(start_path.read_text()) if start_path.exists() else {}
    rows = [row for group in manifest["groups"].values() for row in [*group["shards"], *group["rejected_shards"]]]
    cap = admission.get("requested_main_cap", admission.get("max_attempts"))
    if cap is None:
        cap = max(row["spec"]["max_attempts"] for row in rows)
    integer(cap, "main attempt cap", minimum=1)
    specs = [{**spec, "physics_sha256": admitted["sha256"]} for spec in campaign.scenario_specs(max_attempts=cap, c2_screened=True)]
    equal(len(specs), 2240, "planned request count")
    indexed = {}
    for row in rows:
        identity = (row["spec"]["generator"], row["spec"]["profile"], row["spec"]["seed"])
        require(identity not in indexed, "duplicate manifest request identity")
        indexed[identity] = row
    equal(len(indexed), 2240, "manifest request rows")
    groups, inventory_paths, split_keys = defaultdict(list), set(), set()
    volumes = defaultdict(lambda: dict(targets=0, dg_bookings=0, dg_instances=0, min_targets=None, max_targets=0))
    axes = defaultdict(lambda: {status: {axis: set() for axis in ("vessel", "port", "noise")} for status in ("requested", "accepted", "rejected")})
    for index, planned in enumerate(specs, 1):
        path, sidecar = campaign._paths(root, planned)
        rejected_path = path.with_suffix(".rejected.json")
        rejected = rejected_path.exists()
        require(rejected != sidecar.exists(), f"missing or overlapping request status: {path.name}")
        require(path.exists() == (not rejected), f"invalid instance presence: {path.name}")
        committed = rejected_path if rejected else sidecar
        audit = json.loads(committed.read_text())
        if rejected:
            checksum = audit.pop("rejection_sha256", None)
            equal(checksum, campaign._rejection_checksum(audit), "rejected audit checksum")
            audit["rejection_sha256"] = checksum
            equal(audit.get("status"), "rejected", "rejected status")
            require(audit.get("accepted") is False, "rejected acceptance flag")
            require(bool(audit.get("rejection_diagnostics")), "missing rejection diagnostics")
            if "screening_exhausted" in audit["rejection_diagnostics"]:
                equal(audit["config"]["max_attempts"], cap, "exhausted rejection final cap")
                equal(audit["attempts"], cap, "exhausted candidate attempts")
            if audit.get("balancing_rejections", 0):
                equal(audit["rejection_diagnostics"].get("background_balance_infeasible"),
                      audit["balancing_rejections"], "failed request balancing diagnostic")
        else:
            equal(audit.get("instance_sha256"), _sha256(path), "instance checksum")
            require(audit.get("accepted") is True and audit.get("status") != "rejected", "accepted status")
            inventory_paths.add(path)
        inventory_paths.add(committed)
        attempts = integer(audit.get("attempts"), "attempts", minimum=1, maximum=cap)
        require(campaign.configs_compatible(audit.get("config"), planned, attempts), "audit/planned config mismatch")
        equal(audit.get("physics"), admitted, "shard admitted physics")
        require(audit.get("c2_screened") is True and audit.get("c2_status") == "C2 screened", "C2 regime mismatch")
        split = campaign.split_metadata(planned)
        require(split["split"] in {"train", "val", "test"}, "unassigned split")
        for key, value in split.items():
            equal(audit.get(key), value, f"audit {key}")
        require(split["spec_key"] not in split_keys, "duplicate frozen split identity")
        split_keys.add(split["spec_key"])
        for field in ("provision_rejections", "balancing_rejections"):
            integer(audit.get(field), field, maximum=attempts)
        for field in ("completion_checks", "shipment_redraws"):
            integer(audit.get(field), field)
        for field in ("rejections", "completion_rejections", "rejection_diagnostics"):
            if field == "rejection_diagnostics" and field not in audit:
                continue
            require(isinstance(audit.get(field), dict), f"invalid {field}")
            for reason, count in audit[field].items():
                require(isinstance(reason, str) and bool(reason), "empty screening reason")
                integer(count, reason, maximum=attempts)
        elapsed = audit.get("elapsed_generation_s")
        require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0, "invalid elapsed time")
        equal(audit.get("screening_rate"), (attempts-int(not rejected))/attempts, "candidate screening rate")
        group = f"{planned['generator']}/{planned['profile']}"
        identity = (planned["generator"], planned["profile"], planned["seed"])
        row = indexed.pop(identity)
        equal(row.get("spec"), audit["config"], "manifest immutable config")
        for key, value in split.items():
            equal(row.get(key), value, f"manifest {key}")
        if not rejected:
            require(audit["completion_checks"] >= 1, "accepted request has no completion checks")
            cargo = inspect_instance(path, audit, planned)
            volume = volumes[group]
            volume["targets"] += cargo["targets"]
            volume["dg_bookings"] += cargo["dg_bookings"]
            volume["dg_instances"] += int(cargo["dg_bookings"] > 0)
            volume["min_targets"] = min(cargo["targets"], volume["min_targets"]) if volume["min_targets"] is not None else cargo["targets"]
            volume["max_targets"] = max(cargo["targets"], volume["max_targets"])
        for axis in ("vessel", "port", "noise"):
            axes[group]["requested"][axis].add(planned[axis])
            axes[group]["rejected" if rejected else "accepted"][axis].add(planned[axis])
        compact = {key: value for key, value in audit.items() if key not in ("physics", "conditioning", "timings_s", "simulation_statistics")}
        compact["physics"] = {"sha256": admitted["sha256"]}
        groups[group].append(dict(path=str(rejected_path if rejected else path), audit=compact))
        if index % 100 == 0:
            print(f"verified {index}/{len(specs)} requests", flush=True)
    require(not indexed, "unexpected manifest requests")
    actual_paths = set(root.glob("*/*/seed-*.json"))
    require(actual_paths == inventory_paths, f"inventory paths mismatch: {len(inventory_paths-actual_paths)} missing, {len(actual_paths-inventory_paths)} unexpected")
    require(not list(root.glob("*/*/*.partial.json")), "unfinished partial shards remain")
    summaries, accepted_splits, rejected_splits, rung_splits = {}, Counter(), Counter(), {}
    equal(set(groups), set(manifest["groups"]), "root group set")
    for name, records in sorted(groups.items()):
        expected = campaign.summarize(records)
        local = json.loads((root / name / "manifest.json").read_text())
        equal(local, manifest["groups"][name], "root/profile manifest")
        for key, value in expected.items():
            equal(local.get(key), value, f"{name} aggregate {key}")
        profile = name.split("/")[1]
        equal(local.get("requested"), campaign.COUNTS[profile], f"{name} requested count")
        equal(local.get("planned_requested"), campaign.COUNTS[profile], "profile planned count")
        require(local.get("complete") is True, "profile incomplete")
        for axis in ("vessel", "port", "noise"):
            planned_axis = {spec[axis] for spec in specs if f"{spec['generator']}/{spec['profile']}" == name}
            equal(axes[name]["requested"][axis], planned_axis, "requested family axis")
        expected.update(volumes[name])
        expected["axes"] = {status: {axis: sorted(values) for axis, values in by_axis.items()} for status, by_axis in axes[name].items()}
        expected.pop("shards")
        expected.pop("rejected_shards")
        summaries[name] = expected
        accepted_splits.update(expected["accepted_split_counts"])
        rejected_splits.update(expected["rejected_split_counts"])
        rung = "full" if profile.startswith("realistic") else profile
        rung_row = rung_splits.setdefault(rung, dict(accepted=Counter(), rejected=Counter()))
        rung_row["accepted"].update(expected["accepted_split_counts"])
        rung_row["rejected"].update(expected["rejected_split_counts"])
    for field in ("accepted", "rejected", "requested", "attempts", "screened_out", "elapsed_generation_s", "balancing_rejections"):
        equal(manifest.get(field), sum(group[field] for group in summaries.values()), f"root {field}")
    equal(manifest.get("count"), manifest["accepted"], "root accepted count alias")
    equal(manifest["requested"], 2240, "complete request inventory")
    equal(manifest["planned_requested"], 2240, "planned inventory")
    equal(manifest["accepted"]+manifest["rejected"], 2240, "accepted/rejected partition")
    equal(manifest.get("split_counts"), dict(accepted_splits), "accepted split alias")
    equal(manifest.get("accepted_split_counts"), dict(accepted_splits), "accepted splits")
    equal(manifest.get("rejected_split_counts"), dict(rejected_splits), "rejected splits")
    equal(manifest.get("rung_split_counts"), rung_splits, "rung/split partition")
    for field in ("rejections", "completion_rejections", "rejection_diagnostics"):
        counts = Counter()
        for group in summaries.values():
            counts.update(group[field])
        equal(manifest.get(field), dict(counts), f"root {field}")
    attempts = manifest["attempts"]
    equal(manifest["screening_rate"], manifest["screened_out"]/attempts, "root screening rate")
    equal(manifest["request_rejection_rate"], manifest["rejected"]/2240, "root request rejection rate")
    equal(manifest["balancing_screening_rate"], manifest["balancing_rejections"]/attempts, "root balance screening rate")
    for rate_field, count_field in (("screening_rates_by_reason", "rejections"), ("completion_screening_rates_by_reason", "completion_rejections")):
        equal(manifest[rate_field], {key: count/attempts for key, count in manifest[count_field].items()}, f"root {rate_field}")
    equal(manifest.get("c2_status"), "C2 screened", "root C2 status")
    equal(campaign.physics_metadata(c2_screened=True), admitted, "physics unchanged during verification")
    equal(policy_bank_pin(root), (supplemental_pin, supplemental_pin_sha256), "policy bank unchanged during verification")
    return dict(passed=True, verified_at=datetime.now(timezone.utc).isoformat(), requested=2240,
        accepted=manifest["accepted"], rejected=manifest["rejected"], main_attempt_cap=cap,
        accepted_split_counts=dict(accepted_splits), rejected_split_counts=dict(rejected_splits),
        rung_split_counts=rung_splits, physics_sha256=admitted["sha256"],
        campaign_source_sha256=manifest["campaign_source_sha256"], generator_versions=manifest["generator_versions"],
        c2_screened=True, c2_qualification=qualification, true_feasibility_certified=False,
        supplemental_policy_bank_pin=supplemental_pin,
        supplemental_policy_bank_pin_sha256=supplemental_pin_sha256,
        balancing_rejections=manifest["balancing_rejections"], balancing_screening_rate=manifest["balancing_screening_rate"],
        rejection_diagnostics=manifest["rejection_diagnostics"], groups=summaries,
        dg_bookings=sum(group["dg_bookings"] for group in summaries.values()),
        dg_instances=sum(group["dg_instances"] for group in summaries.values()),
        structural_exclusions=campaign.STRUCTURAL_EXCLUSIONS, elapsed_verification_s=perf_counter()-started,
        **report_totals(summaries))


def results_text(report):
    lines = ["# Canonical v3 request inventory", "",
        f"Verified {report['requested']:,} requested specifications: **{report['accepted']:,} accepted instances, {report['rejected']:,} rejected request audits**. Rejected requests contain no instance and cannot be solver input.",
        f"C1 and C2 are enabled; whole-fleet C2 warm qualification passed (exit 0, {report['c2_qualification'].get('elapsed_s', 0):.2f} s). Main rejection cap: {report['main_attempt_cap']} candidate attempts.",
        f"Accepted target bookings include {report['dg_bookings']:,} DG containers across {report['dg_instances']:,} instances. Physics fingerprint: `{report['physics_sha256']}`.",
        f"Across {report['attempts']:,} candidate attempts, {report['screened_out']:,} were screened out ({report['screening_rate']:.2%}); fail-closed provision rejections were {report['provision_rejections']:,} ({report['infeasible_by_provision_rate']:.2%}). Summed generation time was {report['elapsed_generation_s']:.3f} s: {report['mean_request_s']:.3f} s/request and {report['mean_instance_s']:.3f} s/accepted instance, including rejected-request cost.",
        f"Supplemental policy-bank checksum `{report['supplemental_policy_bank_pin']['sha256']}` was captured at {report['supplemental_policy_bank_pin']['captured_at']}, after main generation began. Final verification checks matching bytes; earlier unobserved bank history is not certified. File mtime is supporting evidence only.",
        "Passing necessary screens does not certify a feasible loading plan. No full-checker-approved plans are claimed.", "",
        "| Generator/profile | Requested | Accepted | Rejected | Accepted train/val/test | Rejected train/val/test | Seconds/request | Seconds/accepted | Candidate screen rate |", "|---|---:|---:|---:|---|---|---:|---:|---:|"]
    for name, group in sorted(report["groups"].items()):
        splits = lambda counts: "/".join(str(counts.get(key, 0)) for key in ("train", "val", "test"))
        accepted_time = f"{group['mean_instance_s']:.3f}" if group["accepted"] else "—"
        lines.append(f"| {name} | {group['requested']} | {group['accepted']} | {group['rejected']} | {splits(group['accepted_split_counts'])} | {splits(group['rejected_split_counts'])} | {group['mean_request_s']:.3f} | {accepted_time} | {group['screening_rate']:.2%} |")
    lines += ["", "| Rung | Split | Accepted | Rejected |", "|---|---|---:|---:|"]
    for rung, counts in sorted(report["rung_split_counts"].items()):
        for split in ("train", "val", "test"):
            lines.append(f"| {rung} | {split} | {counts['accepted'].get(split,0)} | {counts['rejected'].get(split,0)} |")
    lines += ["", "Fail-closed provision counters are separate from generic IMDG reasons; their denominator is candidate attempts, not accepted instances or DG boxes.", "",
        "| Generator/profile | Candidate attempts | Fail-closed provision rejections | Provision rejection rate |", "|---|---:|---:|---:|"]
    for name, group in sorted(report["groups"].items()):
        lines.append(f"| {name} | {group['attempts']} | {group['provision_rejections']} | {group['infeasible_by_provision_rate']:.2%} |")
    lines.append(f"| All profiles | {report['attempts']} | {report['provision_rejections']} | {report['infeasible_by_provision_rate']:.2%} |")
    lines += ["", "Both time averages include rejected requests; seconds/accepted amortizes the full generation cost over accepted output. A zero-acceptance group has no seconds/accepted estimate. Candidate reason rates use all attempts, including exhausted and initialization failures. Reasons overlap; shipment redraws are separate.", "",
        "| Generator/profile | Screen/diagnostic | Reason | Count | Rate per candidate |", "|---|---|---|---:|---:|"]
    for screen, field in (("physical/provision", "rejections"), ("depth-0 C1/C2", "completion_rejections")):
        for reason, count in sorted(report[field].items()):
            lines.append(f"| All profiles | {screen} | {reason} | {count} | {count/report['attempts']:.2%} |")
    for name, group in sorted(report["groups"].items()):
        for screen, field in (("physical/provision", "rejections"), ("depth-0 C1/C2", "completion_rejections"), ("failed request diagnostic", "rejection_diagnostics")):
            for reason, count in sorted(group[field].items()):
                lines.append(f"| {name} | {screen} | {reason} | {count} | {count/group['attempts']:.2%} |")
        lines.append(f"| {name} | candidate initialization | background_balance_infeasible | {group['balancing_rejections']} | {group['balancing_screening_rate']:.2%} |")
        lines.append(f"| {name} | shipment redraws | replacement draws | {group['shipment_redraws']} | — |")
    lines += ["", "Accepted and rejected family coverage is recorded separately in verification.json; requested coverage includes both. The ordinary full feeder_1700/small_feeder request is excluded at all three noise levels: 1,260 crane-reachable TEU versus 1,363.09 expected booking TEU (fit probability ≤ 1.33e-13). This is a request-scheduling exclusion; no physics limit or cargo fact is relaxed.", "",
        "Realistic profiles condition equipment to dry20 and derive counts from current mass/TEU export budgets. Mass-limited profiles may fall below the normal 75% TEU floor; accepted mass and displacement bands remain enforced. Failed background balancing is a rejection, with no invented background loads or invalid instance.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = verify(root)
    _atomic_json(root / "verification.json", report)
    (root / "RESULTS.md").write_text(results_text(report))
    print(json.dumps({key: report[key] for key in ("passed", "requested", "accepted", "rejected", "dg_bookings", "elapsed_verification_s")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
