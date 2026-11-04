"""Regenerate one immutable split allocation under one source epoch.

The v2 release contains requests, not instance bytes. Raw archived requests and
keys are preserved; only physical generator calls canonicalise vessel aliases.
No split release, published instance set, or linear qualification is written.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
from time import perf_counter

sys.dont_write_bytecode = True
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_name] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
from eval.snapshot_io import read_snapshot
from freeze import ROOT, DEFAULT_MANIFEST, atomic_json, check_manifest, sha256_file
from pin_transport import PIN_PATH, PIN_SHA256, LEGACY, pin_environment
from mix_audit import audit_instance, summarize_mix

HERE = Path(__file__).resolve().parent
V2_MANIFEST = ROOT / "experiments/splits/v2/manifest.json"
SPLIT_RELEASES = ("v2", "dg-stress-v3")
DEFAULT_WARM_REPORT = ROOT / "experiments/cache/stability/warmup/summary.json"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def build_release(epoch, diagnostic=False, count=2, *, split_release="v2"):
    """Read one independently pinned release unchanged; never allocate anew."""
    from eval.splits import load_manifest
    if split_release not in SPLIT_RELEASES:
        raise ValueError("regeneration requires a supported frozen split release")
    release = load_manifest(ROOT / "experiments/splits" / split_release / "manifest.json")
    if release["version"] != split_release:
        raise ValueError("regeneration release disagrees with selected allocation")
    # Keep the complete, trusted release intact, even for diagnostic subsets.
    return release


def selected_entries(release, diagnostic=False, count=2):
    _reject_superseded_release(release)
    if not diagnostic:
        return release["entries"]
    from stowage.cache_warm import PRODUCTION_SHIPS
    from stowage.conventions import canonical_vessel_id
    selected = []
    for generator in ("random_yard", "simulated"):
        tiny = [entry for entry in release["entries"]
                if entry["spec"]["rung"] == "tiny" and entry["spec"]["generator"] == generator]
        if not tiny:
            continue
        # Exercise the landed alias with a second physical ship, without changing
        # the archived feeder_1000 identity. Other samples retain the first ship.
        middle = next(entry for entry in tiny
                      if canonical_vessel_id(entry["spec"]["vessel"]) == PRODUCTION_SHIPS[1])
        first = [entry for entry in tiny
                 if canonical_vessel_id(entry["spec"]["vessel"]) == PRODUCTION_SHIPS[0]][:count-1]
        selected.extend(first + [middle])
    return selected


def _release_config(entry, max_attempts):
    from stowage.generate.instances_v3 import CAMPAIGN_VERSION
    from stowage.conventions import canonical_vessel_id
    s = entry["spec"]
    config = dict(campaign_version=CAMPAIGN_VERSION,
                generator="random_yard" if s["generator"] == "random_yard" else "simulated_yard",
                profile=s["rung"], size=s["rung"], seed=s["seed"], vessel=canonical_vessel_id(s["vessel"]),
                port=s["port"], noise=s["blocking_ratio"] if s["generator"] == "random_yard" else s["noise"],
                n_boxes=s["n_boxes"], max_attempts=max_attempts, dense_20ft=s["dense_20ft"],
                tiny_dg_enabled=s["tiny_dg_enabled"], full_export=s["rung"] == "full",
                cargo_profile=s["cargo_profile"], c2_screened=s["rung"] == "medium")
    if s["cargo_profile"] == "dg_stress":
        config.update(identity_version=s["identity_version"],
                      equipment_profile=s["equipment_profile"], dg_stress_pair=s["dg_stress_pair"])
    return config


def _reject_superseded_release(release):
    if release.get("version") in {"dg-stress-v1", "dg-stress-v2"}:
        raise ValueError(f"{release['version']} is superseded and never used; select dg-stress-v3")


def _require_stress_producer_adoption(config, *, allocated=None):
    """Do not turn requested profile labels into evidence of sampled cargo.

    Profile routing for the earlier cohorts has landed, but the re-frozen
    release adds a new cohort. Signature/capability checks cannot prove initial
    draws, redraws, equipment conditioning and audits use those parameters.
    The producer owner must verify that complete path for the active release,
    including the new cohort, before replacing this explicit admission refusal.
    """
    profile = config.get("cargo_profile", "feeder_export")
    expected = (allocated or {}).get("spec", {}).get("cargo_profile", profile)
    if profile != expected:
        raise ValueError("cargo profile mismatch between allocated spec and generation config")
    if profile == "dg_stress":
        raise ValueError("DG-stress producer adoption is incomplete: profile/cohort routing and "
                         "identity from actually sampled parameters are required before regeneration")


def instance_root(base, epoch):
    return Path(base) / f"instances-e{epoch}"


def make_jobs(release, base, diagnostic, count, max_attempts, epoch=None, *, entries=None):
    """One resumable generation shard per exact v2 request (no extra sets)."""
    if epoch is None:
        raise ValueError("an explicit source epoch is required")
    _reject_superseded_release(release)
    jobs = []
    for entry in selected_entries(release, diagnostic, count) if entries is None else entries:
        job = dict(kind="release", root=str(instance_root(base, epoch)),
                   config=_release_config(entry, max_attempts), allocated=entry)
        job["id"] = job["kind"] + "-" + _digest(job)[:20]
        jobs.append(job)
    return jobs


def qualification_exclusions(value, path="report"):
    """Keep exclusion lineage visible, including nested qualification reports."""
    excluded = []
    if isinstance(value, dict):
        for key in ("diagnostic", "allow_drift", "tiny_run"):
            if value.get(key):
                excluded.append(f"{path}.{key}")
        for key in ("evidence_eligible", "official"):
            if value.get(key) is False:
                excluded.append(f"{path}.{key}")
        for key, item in value.items():
            excluded.extend(qualification_exclusions(item, f"{path}.{key}"))
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            excluded.extend(qualification_exclusions(item, f"{path}[{index}]"))
    return excluded


def qualification_preflight(report_path=DEFAULT_WARM_REPORT, frozen=None):
    """Read-only admission: completed central warm-up, all linear geometries.

    This never calls a builder or requalification hook. A diagnostic has exactly
    the same admission gate as evidence. Source/epoch fields, when supplied by
    an upstream report, are checked as well; older central reports lack them.
    """
    from stowage.cache_warm import PRODUCTION_SHIPS
    from stowage.conventions import canonical_vessel_id
    from stowage.vessel import SLICE_PRESETS
    path = Path(report_path).resolve()
    report_snapshot = read_snapshot(path)
    report = report_snapshot.json()
    exclusions = qualification_exclusions(report)
    if exclusions and not (frozen or {}).get("diagnostic", False):
        raise ValueError("linear qualification has diagnostic/ineligible excluded lineage: " + ", ".join(exclusions))
    if report.get("complete") is not True:
        raise ValueError(f"linear qualification preflight incomplete: {path}; leader must requalify before freeze")
    if frozen:
        for key in ("source_sha256", "epoch_id"):
            if key in report and report[key] != frozen[key]:
                raise ValueError(f"linear qualification report {key} differs from frozen source")
    ships = {canonical_vessel_id(row["ship"]): row for row in report.get("ships", [])}
    needed = set(SLICE_PRESETS) | {"tiny_production"}
    for ship in PRODUCTION_SHIPS:
        row = ships.get(ship, {})
        if row.get("complete") is not True or row.get("errors") or row.get("warnings"):
            raise ValueError(f"linear qualification preflight incomplete: {ship}")
        linear = {item.get("geometry"): item for item in row.get("caches", []) if item.get("cache") == "linear"}
        for geometry in sorted(needed):
            item = linear.get(geometry, {})
            evidence = item.get("report", {})
            sha = evidence.get("margin_policy_sha256")
            if (item.get("status") not in ("built", "reused", "prepared")
                    or evidence.get("margin_policy_status") not in ("empirical_measured",)
                    or evidence.get("mandatory_exact_checker") is not True
                    or not isinstance(sha, str) or len(sha) != 64
                    or any(character not in "0123456789abcdef" for character in sha)):
                raise ValueError(f"linear qualification preflight incomplete: {ship}/{geometry}")
    result = dict(path=str(path), sha256=report_snapshot.sha256, complete=True,
                source_binding="verified" if "source_sha256" in report else "upstream report has no source fingerprint",
                ships=list(PRODUCTION_SHIPS), geometries=sorted(needed), report=report,
                excluded_lineage=exclusions)
    # Do not sanitize diagnostic lineage into a seemingly production report.
    for marker in ("diagnostic", "evidence_eligible", "allow_drift", "tiny_run", "official"):
        if marker in report:
            result[marker] = report[marker]
    return result


def _publish_bound(job, producer_result):
    """Publish identity metadata without altering the generator's commit/audit.

    The canonical producer retains its own resumable bytes in .producer. The
    consumer copy adds only the archived split vessel label, preserving the
    physical family vessel and all generated physics.
    """
    _require_stress_producer_adoption(job["config"], allocated=job.get("allocated"))
    from stowage.io import save_instance
    from stowage.model import Instance
    root = Path(job["root"])
    source = Path(producer_result["path"])
    relative = source.relative_to(root / ".producer")
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_audit = source.with_suffix(".audit.json")
    source_snapshot = read_snapshot(source)
    producer_outputs = {str(source): source_snapshot.sha256}
    if source_audit.exists():
        audit_snapshot = read_snapshot(source_audit)
        if audit_snapshot.json() != producer_result["audit"]:
            raise ValueError("producer audit bytes differ from returned generation claims")
        producer_outputs[str(source_audit)] = audit_snapshot.sha256
    elif producer_result["audit"].get("status") != "rejected":
        raise ValueError("producer audit sidecar is missing")
    if producer_result["audit"].get("status") == "rejected":
        if source_snapshot.json() != producer_result["audit"]:
            raise ValueError("producer audit rejection bytes differ from returned claims")
    elif producer_result["audit"].get("instance_sha256") != source_snapshot.sha256:
        raise ValueError("producer audit does not bind the published source bytes")
    temporary = destination.with_suffix(".partial.json")
    audit = dict(producer_result["audit"])
    try:
        if audit.get("status") == "rejected":
            temporary.write_bytes(source_snapshot.data)
        else:
            instance = source_snapshot.model(Instance)
            instance = instance.model_copy(update={"family": {
                **instance.family, "split_vessel": job["allocated"]["spec"]["vessel"]}})
            save_instance(instance, temporary)
        published_snapshot = read_snapshot(temporary)
        if destination.exists() and read_snapshot(destination).sha256 != published_snapshot.sha256:
            raise ValueError(f"published instance differs from reproducible producer output: {destination}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    if audit.get("status") != "rejected":
        from eval.splits import spec_key
        audit.update(producer_instance_sha256=producer_outputs[str(source)],
                     producer_split_spec=audit.get("split_spec"),
                     instance_sha256=published_snapshot.sha256,
                     split_spec=job["allocated"]["spec"],
                     spec_key=spec_key(job["allocated"]["spec"]),
                     identity_annotation="split_vessel retains the archived v2 label; physical vessel is canonical")
        atomic_json(destination.with_suffix(".audit.json"), audit)
    return {**producer_result, "path": str(destination), "audit": audit,
            "publication_sha256": published_snapshot.sha256}, producer_outputs


def _worker(job):
    """Run the existing production shard, measuring its actual time and RAM."""
    _require_stress_producer_adoption(job["config"], allocated=job.get("allocated"))
    started = perf_counter()
    os.environ["CAMPAIGN_CACHE_ROOT"] = job["cache_output"]
    os.environ.pop(LEGACY, None)
    os.environ[PIN_PATH] = job["numerical_cache_pin_path"]
    os.environ[PIN_SHA256] = job["numerical_cache_pin_sha256"]
    from cache_adapter import redirect_cache, enforce_scope
    redirect_cache(Path(job["cache_output"]))
    enforce_scope()
    from stowage.generate.instances_v3 import shard
    producer = shard((str(Path(job["root"]) / ".producer"), job["config"]))
    result, producer_outputs = _publish_bound(job, producer)
    return dict(job=job, result=result, producer_outputs=producer_outputs, elapsed_s=perf_counter()-started,
                peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1e9 if sys.platform == "darwin" else 1e6))


def load_marker(path, job, epoch, diagnostic):
    """Return only the checkpoint row validated from this single byte snapshot."""
    path = Path(path)
    if not path.exists():
        return None
    record = read_snapshot(path).json()
    expected_job = record.get("job") if job is None else job
    if record.get("epoch_id") != epoch or record.get("diagnostic") != diagnostic or record.get("job") != expected_job:
        raise ValueError(f"incompatible done marker: {path}")
    for name, expected in record["outputs"].items():
        try:
            read_snapshot(name, expected_sha256=expected)
        except (OSError, ValueError) as exc:
            raise ValueError(f"done-marker output missing or edited: {name}") from exc
    return record


def marker_valid(path, job, epoch, diagnostic):
    return load_marker(path, job, epoch, diagnostic) is not None


def _commit(record, marker, epoch, diagnostic):
    path = Path(record["result"]["path"])
    snapshot = read_snapshot(path, expected_sha256=record["result"].get("publication_sha256"))
    outputs = {**record.get("producer_outputs", {}), str(path): snapshot.sha256}
    audit = path.with_suffix(".audit.json")
    if audit.exists():
        outputs[str(audit)] = sha256_file(audit)
    atomic_json(marker, {**record, "epoch_id": epoch, "diagnostic": diagnostic, "outputs": outputs})


def _write_indices(records, release, base, epoch, diagnostic, frozen=None, tranches=None):
    """Bind unchanged split requests to exact accepted/rejected output bytes."""
    _reject_superseded_release(release)
    from eval.splits import canonical_spec, spec_from_instance, spec_key
    from stowage.conventions import canonical_vessel_id
    from stowage.frozen import current_provenance
    from stowage.model import Instance
    root = instance_root(base, epoch)
    groups, bindings, rejected = {}, {}, []
    instances, instance_audits = {}, {}
    allocated = {spec_key(entry["spec"]): entry for entry in release["entries"]}
    records = list({row["job"]["id"]: row for row in records}.values())
    for row in records:
        job, result = row["job"], row["result"]
        raw = job["allocated"]
        if allocated.get(spec_key(raw["spec"])) != raw:
            raise ValueError("record is outside the selected release allocation")
        _require_stress_producer_adoption(job["config"], allocated=raw)
        path = Path(result["path"])
        expected_digest = row.get("outputs", {}).get(str(path), result.get("publication_sha256"))
        instance_snapshot = read_snapshot(path, expected_sha256=expected_digest)
        s = raw["spec"]
        group_key = f"{job['config']['generator']}/{s['rung']}"
        group_root = root / group_key
        group = groups.setdefault(group_key, dict(generator=job["config"]["generator"], profile=s["rung"], shards=[], rejected_shards=[]))
        key = spec_key(s)
        if result["audit"].get("status") == "rejected":
            rejection = dict(spec=s, spec_key=key, split=raw["split"],
                             rejection=str(path.relative_to(group_root)), sha256=instance_snapshot.sha256)
            group["rejected_shards"].append(rejection)
            rejected.append(rejection)
            continue
        instance = instance_snapshot.model(Instance)
        if frozen is not None:
            instance_audits[key] = audit_instance(instance, result["audit"], job["config"], frozen=frozen)
        elif not diagnostic:
            raise ValueError("post-regeneration audit requires the frozen source binding")
        instances[key] = instance
        actual = canonical_spec(spec_from_instance(instance))
        expected = dict(s, vessel=canonical_vessel_id(s["vessel"]))
        actual["vessel"] = canonical_vessel_id(actual["vessel"])
        if actual != expected:
            raise ValueError(f"generated instance differs from frozen {release['version']} spec: {path}")
        item = dict(instance=str(path.relative_to(group_root)), sha256=instance_snapshot.sha256,
                    split_spec=s, spec_key=key, split=raw["split"], split_policy=release["policy"]["hash_salt"],
                    split_version=release["version"], split_entries_sha256=release["entries_sha256"],
                    seed=s["seed"], vessel=canonical_vessel_id(s["vessel"]), test_family=raw["test_family"])
        group["shards"].append(item)
        if key in bindings:
            raise ValueError(f"duplicate regenerated spec {key}")
        bindings[key] = dict(spec=s, split=raw["split"], instance=str(path.relative_to(root)), sha256=item["sha256"])
    payload = dict(schema_version="stowlm-epoch-instances-v1", epoch_id=epoch, diagnostic=diagnostic,
                   label="diagnostic, not evidence" if diagnostic else "frozen source epoch",
                   versions=dict(current_provenance().versions), split_version=release["version"], split_entries_sha256=release["entries_sha256"],
                   groups=groups, bindings=bindings, rejected_bindings=rejected, count=len(bindings), rejected=len(rejected),
                   source_sha256=(frozen or {}).get("source_sha256"), freeze_manifest=frozen)
    population = summarize_mix(release, records, instances, frozen=frozen)
    if release["version"] == "dg-stress-v3":
        # The shared auditor predates named stress releases. Keep its measured
        # counts, but label intent with the selected release's frozen recipe.
        recipe_name = "params/cargo/dg_stress.toml"
        recipe_root = Path((frozen or {}).get("root", ROOT))
        recipe = read_snapshot(recipe_root / recipe_name,
                               expected_sha256=(frozen or {}).get("files", {}).get(recipe_name))
        population["requested_recipe"] = dict(path=str(recipe.path), sha256=recipe.sha256,
            text=recipe.data.decode("utf-8"), status="profile_intent_not_observed_raw_draws")
    payload.update(instance_audits=instance_audits, selection_audit=population,
                   post_regeneration_audit=dict(passed=len(instance_audits)==len(bindings) and frozen is not None,
                   checked=len(instance_audits), accepted=len(bindings)))
    atomic_json(root / "selection-audit.json", population)
    if tranches is not None:
        accepted_keys = set(bindings)
        rejected_keys = {item["spec_key"] for item in rejected}
        attempted = accepted_keys | rejected_keys
        requested = set()
        membership = {}
        for name, tranche in tranches.items():
            keys = set(tranche["spec_keys"])
            requested.update(keys)
            for key in keys:
                membership.setdefault(key, []).append(name)
            pending = keys - attempted
            tranches[name] = {**tranche, "requested": len(keys), "accepted": len(keys & accepted_keys),
                "rejected": len(keys & rejected_keys), "pending": len(pending), "complete": not pending}
        allocated = {spec_key(entry["spec"]) for entry in release["entries"]}
        payload.update(tranches=tranches, spec_tranches=membership,
            requested_spec_keys=sorted(requested), pending_spec_keys=sorted(requested-attempted),
            not_generated_spec_keys=sorted(allocated-attempted),
            allocation_complete=allocated <= attempted,
            missing_status="not generated yet")
    atomic_json(root / "manifest.json", payload)
    return payload


def _calibration(records, path, epoch, diagnostic):
    """Measured rung costs only: medium/full remain uncalibrated until observed."""
    path = Path(path)
    previous = read_snapshot(path).json() if path.exists() else {}
    if previous and (previous.get("epoch_id") != epoch or previous.get("diagnostic") != diagnostic):
        raise ValueError("calibration belongs to a different source epoch or mode")
    observations = previous.get("observations", {})
    for row in records:
        config = row["job"]["config"]
        key = f"{config['generator']}/{config['size']}/{config['vessel']}"
        observations[row["job"].get("id", _digest(row["job"]))] = dict(
            cell=key, elapsed_s=row["elapsed_s"], peak_rss_gb=row["peak_rss_gb"])
    groups = {}
    for row in observations.values():
        key = row["cell"]
        item = groups.setdefault(key, dict(samples=0, wall_s=0., peak_rss_gb=0.))
        item["samples"] += 1
        item["wall_s"] += row["elapsed_s"]
        item["peak_rss_gb"] = max(item["peak_rss_gb"], row["peak_rss_gb"])
    for item in groups.values():
        item["mean_wall_s"] = item["wall_s"] / item["samples"]
    rungs = {}
    for row in observations.values():
        rung = row["cell"].split("/")[1]
        item = rungs.setdefault(rung, dict(samples=0, wall_s=0., peak_rss_gb=0.))
        item["samples"] += 1
        item["wall_s"] += row["elapsed_s"]
        item["peak_rss_gb"] = max(item["peak_rss_gb"], row["peak_rss_gb"])
    for item in rungs.values():
        item["mean_wall_s"] = item["wall_s"] / item["samples"]
    payload = dict(epoch_id=epoch, diagnostic=diagnostic, measured=groups, observations=observations, rung_measured=rungs,
                   policy="Only scale measured generator/rung/ship cells; no tiny-to-medium/full projection")
    atomic_json(path, payload)
    return payload


def measured_schedule(calibration, jobs, records, workers=1):
    """Print only measured matching generator/rung/ship projections."""
    from schedule import measured_schedule as project
    completed = {row["job"]["id"] for row in records}
    pending = Counter(f"{job['config']['generator']}/{job['config']['size']}/{job['config']['vessel']}"
        for job in jobs if job["id"] not in completed)
    result = dict(label="MEASURED diagnostic, not evidence" if calibration["diagnostic"] else "MEASURED",
        **project(calibration, counts=pending, workers=workers))
    print(json.dumps(result), flush=True)
    return result


def load_completed(output, epoch, diagnostic):
    """Load every historical completed per-spec shard, including earlier tranches."""
    records = []
    for marker in sorted((Path(output) / "done").glob("release-*.json")):
        row = load_marker(marker, None, epoch, diagnostic)
        if row is not None:
            records.append(row)
    return records


def bind_tranche(output, name, jobs, *, train_per_rung=None, diagnostic_eval_per_rung=None):
    """A named tranche's exact cumulative request list cannot be changed on resume."""
    from eval.splits import spec_key
    path = Path(output) / "tranches.json"
    tranches = read_snapshot(path).json() if path.exists() else {}
    config = dict(train_per_rung=train_per_rung, diagnostic_eval_per_rung=diagnostic_eval_per_rung,
        spec_keys=[spec_key(job["allocated"]["spec"]) for job in jobs])
    if name in tranches and tranches[name] != config:
        raise ValueError(f"tranche {name!r} configuration differs from prior request")
    tranches[name] = config
    atomic_json(path, tranches)
    return tranches


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--split-release", choices=SPLIT_RELEASES, default="v2",
                        help="Frozen allocation; DG-stress outputs and results stay separate from v2")
    parser.add_argument("--cache-warm-report", type=Path, default=DEFAULT_WARM_REPORT)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--allow-drift", action="store_true")
    parser.add_argument("--count", type=int, default=2, help="diagnostic samples per generator (2 or 3)")
    parser.add_argument("--tranche", help="immutable named cumulative request tranche; later names extend the same epoch")
    parser.add_argument("--train-per-rung", help="cumulative balanced train targets, e.g. tiny=2,small=2,medium=1,full=0")
    parser.add_argument("--diagnostic-eval-per-rung", type=int, help="DIAGNOSTIC ONLY: cap EACH val/test split per rung (0 permitted)")
    parser.add_argument("--max-attempts", type=int, default=200)
    parser.add_argument("--stages", nargs="+", choices=("instances", "bindings"), default=["instances", "bindings"])
    parser.add_argument("--calibrate-first-shard", action="store_true", help="stop after first new shard with calibration.json; omit this flag to resume")
    parser.add_argument("--calibration-rung", choices=("tiny", "small", "medium", "full"), help="with --calibrate-first-shard, measure the first pending shard of this rung")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 12 or args.count not in (2, 3) or not 1 <= args.max_attempts <= 1_000_000:
        parser.error("workers 1..12, diagnostic count 2..3, max-attempts 1..1000000 required")
    if args.calibration_rung and not args.calibrate_first_shard:
        parser.error("--calibration-rung requires --calibrate-first-shard")
    if args.train_per_rung and not args.tranche:
        parser.error("--train-per-rung requires --tranche NAME")
    if args.diagnostic_eval_per_rung is not None and (not args.diagnostic or not args.tranche):
        parser.error("--diagnostic-eval-per-rung requires --diagnostic and --tranche")
    runner_pins = {}
    def check():
        frozen = check_manifest(args.manifest, args.allow_drift, args.diagnostic)
        changed = [name for name, digest in runner_pins.items()
                   if not Path(name).is_file() or sha256_file(name) != digest]
        if changed:
            raise RuntimeError("regeneration runner drift:\n" + "\n".join(changed))
        return frozen
    try:
        frozen = check()
        epoch = frozen["epoch_id"]
        # Strict even in diagnostic mode; drift tolerance cannot waive admission.
        qualification = qualification_preflight(args.cache_warm_report, frozen)
        runner_pins[qualification["path"]] = qualification["sha256"]
        split_manifest = ROOT / "experiments/splits" / args.split_release / "manifest.json"
        if not args.diagnostic:
            scripts = {HERE / name for name in ("freeze.py", "regen.py", "cache_adapter.py", "pin_transport.py", "tranches.py", "schedule.py", "rule_policy.py", "mix_audit.py", "REQUIRED_RULES.json", "migration_contract.py", "migration_canaries.py", "migration_evidence.py", "trusted_receipts.py", "qualified_canary_cases.py", "admission-trust.json")}
            scripts.add(split_manifest)
            scripts.update((ROOT / "experiments/stability").rglob("*.py"))
            runner_pins.update({str(path): sha256_file(path) for path in sorted(scripts)})
        output = HERE / epoch / "regen"
        if args.split_release != "v2":
            output = output / args.split_release
        base = output / "release/experiments"
        release = build_release(epoch, args.diagnostic, args.count, split_release=args.split_release)
        train_counts = None
        entries = None
        if args.tranche:
            from tranches import parse_train_counts, select_entries
            train_counts = parse_train_counts(args.train_per_rung) if args.train_per_rung else None
            entries = select_entries(release, train_counts, args.diagnostic_eval_per_rung, args.diagnostic)
        jobs = make_jobs(release, base, args.diagnostic, args.count, args.max_attempts, epoch, entries=entries)
        for job in jobs:
            job["cache_output"] = str(output / "cache")
        plan = dict(epoch_id=epoch, diagnostic=args.diagnostic, label=frozen["label"],
                    allocated_specs=len(release["entries"]),
                    instance_root=str(instance_root(base, epoch)), split_manifest=str(split_manifest),
                    split_version=release["version"], split_entries_sha256=release["entries_sha256"],
                    publication=("new epoch content only; existing v2 split allocation unchanged" if args.split_release == "v2"
                                 else "DG-stress release and results separate from v2; never pooled"),
                    qualification=qualification, source_sha256=frozen.get("source_sha256"), freeze_manifest=frozen,
                    numerical_caches=frozen.get("numerical_caches"), freeze_sha256=frozen.get("freeze_sha256"))
        if runner_pins:
            plan["scripts_sha256"] = runner_pins
        print(json.dumps({**plan, "current_tranche": args.tranche or "legacy", "generation_shards": len(jobs),
            "workers": args.workers, "train_per_rung": train_counts}), flush=True)
        if args.plan_only:
            return 0
        for job in jobs:
            _require_stress_producer_adoption(job["config"], allocated=job["allocated"])
        output.mkdir(parents=True, exist_ok=True)
        if frozen.get("numerical_caches"):
            cache_environment = pin_environment(frozen["numerical_caches"], output)
            for job in jobs:
                job["numerical_cache_pin_path"] = cache_environment[PIN_PATH]
                job["numerical_cache_pin_sha256"] = cache_environment[PIN_SHA256]
        plan_path = output / "plan.json"
        if plan_path.exists() and read_snapshot(plan_path).json() != {**plan, "max_attempts": args.max_attempts}:
            raise ValueError("regen configuration differs from prior plan")
        atomic_json(plan_path, {**plan, "max_attempts": args.max_attempts})
        tranches = bind_tranche(output, args.tranche or "legacy", jobs, train_per_rung=train_counts,
            diagnostic_eval_per_rung=args.diagnostic_eval_per_rung)
        started = perf_counter()
        records = load_completed(output, epoch, args.diagnostic)
        completed_ids = {row["job"]["id"] for row in records}
        # Checkpoint the union immediately: requested but absent specs are pending,
        # never fabricated as generator rejections.
        def checkpoint():
            index = _write_indices(records, release, base, epoch, args.diagnostic, frozen, tranches)
            state = index["tranches"][args.tranche or "legacy"]
            atomic_json(output / "manifest.json", {**plan, "current_tranche": args.tranche or "legacy",
                "complete": state["complete"], "allocation_complete": index["allocation_complete"],
                "tranches": index["tranches"], "requested_spec_keys": index["requested_spec_keys"],
                "pending_spec_keys": index["pending_spec_keys"],
                "not_generated_spec_keys": index["not_generated_spec_keys"],
                "diagnostic_finished": args.diagnostic and state["complete"],
                "evidence_eligible": not args.diagnostic and not qualification["excluded_lineage"],
                "cache_warm_complete": qualification["complete"],
                "elapsed_s": perf_counter()-started, "workers": args.workers,
                "stages_completed": ["cache_warm_preflight", *args.stages] if state["complete"] else ["cache_warm_preflight"],
                "accepted": index["count"], "rejected": index["rejected"],
                "post_regeneration_audit": index["post_regeneration_audit"],
                "selection_audit": index["selection_audit"],
                "timings": [dict(job=row["job"]["id"], kind=row["job"]["kind"], elapsed_s=row["elapsed_s"], peak_rss_gb=row["peak_rss_gb"]) for row in records]})
            return index
        checkpoint()
        measured_rungs = set(_calibration(records, output / "calibration.json", epoch, args.diagnostic)["rung_measured"])
        def observe():
            calibration = _calibration(records, output / "calibration.json", epoch, args.diagnostic)
            fresh_rungs = set(calibration["rung_measured"]) - measured_rungs
            if fresh_rungs:
                measured_schedule(calibration, jobs, records, args.workers)
                measured_rungs.update(fresh_rungs)
        if "instances" in args.stages:
            batch_width = 1 if args.calibrate_first_shard else args.workers
            scheduled = [job for job in jobs if not args.calibration_rung or job["config"]["size"] == args.calibration_rung]
            for start in range(0, len(scheduled), batch_width):
                check()
                pending = []
                for job in scheduled[start:start+batch_width]:
                    marker = output / "done" / f"{job['id']}.json"
                    completed = load_marker(marker, job, epoch, args.diagnostic)
                    if completed is not None:
                        if job["id"] not in completed_ids:
                            records.append(completed)
                            completed_ids.add(job["id"])
                    else:
                        pending.append(job)
                if pending:
                    if args.workers == 1 or args.calibrate_first_shard:
                        results = map(_worker, pending)
                        for record in results:
                            check()
                            _commit(record, output / "done" / f"{record['job']['id']}.json", epoch, args.diagnostic)
                            records.append(record)
                            completed_ids.add(record["job"]["id"])
                            checkpoint()
                            observe()
                    else:
                        with ProcessPoolExecutor(max_workers=min(args.workers, len(pending))) as pool:
                            for record in pool.map(_worker, pending):
                                check()
                                _commit(record, output / "done" / f"{record['job']['id']}.json", epoch, args.diagnostic)
                                records.append(record)
                                completed_ids.add(record["job"]["id"])
                                checkpoint()
                                observe()
                    if args.calibrate_first_shard:
                        print("First new shard measured; inspect calibration.json, then rerun without --calibrate-first-shard", flush=True)
                        return 0
                print(json.dumps(dict(completed=len(records), planned=len(jobs))), flush=True)
            if args.calibrate_first_shard:
                _calibration(records, output / "calibration.json", epoch, args.diagnostic)
                print("Requested calibration shards already complete; calibration.json refreshed", flush=True)
                return 0
        else:
            for job in jobs:
                marker = output / "done" / f"{job['id']}.json"
                completed = load_marker(marker, job, epoch, args.diagnostic)
                if completed is None:
                    raise ValueError("instances stage must complete before bindings")
                if job["id"] not in completed_ids:
                    records.append(completed)
                    completed_ids.add(job["id"])
        check()
        index = checkpoint()
        _calibration(records, output / "calibration.json", epoch, args.diagnostic)
        if "bindings" in args.stages:
            atomic_json(output / "done/bindings.json", dict(epoch_id=epoch, diagnostic=args.diagnostic,
                        sha256=sha256_file(instance_root(base, epoch) / "manifest.json")))
        check()
        checkpoint()
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__mp_main__" and os.environ.get("CAMPAIGN_CACHE_ROOT"):
    from cache_adapter import redirect_cache, enforce_scope
    redirect_cache(Path(os.environ["CAMPAIGN_CACHE_ROOT"]))
    enforce_scope()

if __name__ == "__main__":
    raise SystemExit(main())
