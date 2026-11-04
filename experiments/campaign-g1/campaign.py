"""Resumable frozen-source G1 campaign; diagnostic output is never evidence.

The first four tasks share a bounded worker pool. Once they finish, uniform
C2-on sampling and token-level replay run, followed by the 5.4 synthesis.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import uuid

from eval.snapshot_io import read_snapshot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
PRIMARY = ("4.2", "4.6", "4.7", "4.8")
STAGES = (*PRIMARY, "5.1", "replay", "5.4")


def sha(path):
    return read_snapshot(path).sha256


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.partial")
    data = (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode('utf-8')
    temporary.write_bytes(data)
    temporary.replace(path)
    return hashlib.sha256(data).hexdigest()


def validate_modes(diagnostic, allow_drift):
    if allow_drift and not diagnostic:
        raise ValueError("--allow-drift requires --diagnostic; it cannot produce evidence")


def require_evidence(rows):
    """Reject diagnostic provenance at any nesting depth before publication."""
    def visit(value):
        if isinstance(value, dict):
            if (value.get("diagnostic") or value.get("allow_drift") or value.get("tiny_run")
                or value.get("evidence_eligible") is False or value.get("official") is False):
                raise ValueError("diagnostic, not evidence: refusing evidence summary")
            for item in value.values():
                visit(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                visit(item)
    visit(rows)


def ensure_identity(path, identity):
    path = Path(path)
    if path.exists():
        if read_snapshot(path).json() != identity:
            raise ValueError(f"resume configuration/epoch/input changed: {path}; use a fresh epoch/output")
    else:
        atomic_json(path, identity)


CHECKPOINT_SCHEMA = "campaign-artifacts-v2"
CONTROL_FILES = {"done.json", "result.json", "identity.json", "pid.json", "job.json", "attempt.json"}


def candidate_path(path, data=None):
    path = Path(path)
    if path.suffix != ".json" or "input-plan" in path.name:
        return False
    try:
        value = read_snapshot(path).json() if data is None else json.loads(data)
        if isinstance(value, dict) and "instance_id" in value and ("moves" in value or "actions" in value):
            return True
    except (OSError, ValueError, UnicodeError):
        pass
    # An unreadable file explicitly named as a returned plan is still an
    # obligation; its failure to parse must never remove it from replay.
    return "plan" in path.name


def shard_artifacts(directory):
    """Inventory outputs, including unreadable candidates, before checkpointing.

    Controller metadata is separately bound by identity/result hashes. Previous
    attempts are retained for audit in quarantine and never enter this attempt.
    """
    directory = Path(directory).resolve()
    return [path for path in sorted(directory.rglob("*"))
            if path.is_file() and "quarantine" not in path.relative_to(directory).parts
            and not (path.parent == directory and path.name in CONTROL_FILES)]


def artifact_inventory(directory, extra_paths=()):
    paths = set(shard_artifacts(directory)) | {Path(p).resolve() for p in extra_paths}
    return [artifact_record(path) for path in sorted(paths)]


def artifact_record(path):
    """Classify exactly the JSON bytes whose digest enters the inventory."""
    path = Path(path)
    if path.suffix == ".json":
        snapshot = read_snapshot(path)
        return dict(path=str(path), sha256=snapshot.sha256,
                    candidate=candidate_path(path, snapshot.data))
    return dict(path=str(path), sha256=sha(path), candidate=False)


def write_done(marker, identity, result, extra_paths=(), *, expected_dependencies=None,
               expected_result_sha256=None):
    """Commit one invocation and every artifact it returned, never just its row."""
    marker, result = Path(marker), Path(result).resolve()
    result_snapshot = read_snapshot(result, expected_sha256=expected_result_sha256)
    value = result_snapshot.json()
    if not value.get("attempt_id"):
        raise ValueError("checkpoint requires this attempt's identity")
    artifacts = artifact_inventory(marker.parent, extra_paths)
    observed = {str(Path(item["path"]).resolve()): item["sha256"] for item in artifacts}
    for name, digest in (expected_dependencies or {}).items():
        if observed.get(str(Path(name).resolve())) != digest:
            raise ValueError(f"validated checkpoint dependency changed before publication: {name}")
    atomic_json(marker, dict(schema_version=CHECKPOINT_SCHEMA, identity=identity,
        attempt_id=value["attempt_id"], result=str(result), result_sha256=result_snapshot.sha256,
        artifacts=artifacts))


def verified_done(marker, identity, *, marker_snapshot=None):
    """Return the row parsed from its verified bytes, never a later reread."""
    marker = Path(marker)
    if not marker.exists():
        return None
    try:
        value = (marker_snapshot or read_snapshot(marker)).json()
        result = Path(value["result"])
        result_snapshot = read_snapshot(result)
        if (value.get("schema_version") != CHECKPOINT_SCHEMA or value["identity"] != identity
            or result_snapshot.sha256 != value["result_sha256"]):
            return None
        row = result_snapshot.json()
        if not value.get("attempt_id") or row.get("attempt_id") != value["attempt_id"]:
            return None
        artifacts = value["artifacts"]
        if not isinstance(artifacts, list):
            return None
        if (row.get("stage") in {"4.6", "4.7", "4.8"} and "plan_returned" in row
                and row["plan_returned"] is not any(item.get("candidate") is True for item in artifacts)):
            return None  # return-rate claims must match this checkpoint's artifact inventory
        expected = {str(Path(item["path"]).resolve()): item for item in artifacts}
        if len(expected) != len(artifacts):
            return None
        for name, item in expected.items():
            path = Path(name)
            if not path.is_file() or artifact_record(path) != item:
                return None
        for path_key, hash_key in (("gold_file", "gold_sha256"), ("diagnostic_file", "diagnostic_sha256")):
            if row.get(path_key):
                entry = expected.get(str(Path(row[path_key]).resolve()))
                if entry is None or entry["sha256"] != row.get(hash_key):
                    return None
        # An unrecorded output added after completion is also a provenance change.
        if not all(str(path.resolve()) in expected for path in shard_artifacts(marker.parent)):
            return None
        return row
    except (ValueError, KeyError, OSError, TypeError, AttributeError):
        return None


def valid_done(marker, identity):
    return verified_done(marker, identity) is not None


def quarantine_attempt(directory, extra_paths=()):
    """Move interrupted outputs aside before a child gets an empty output area."""
    directory = Path(directory)
    attempt_id = uuid.uuid4().hex
    destination = directory / "quarantine" / attempt_id
    paths = [p for p in directory.iterdir() if p.name not in {"identity.json", "quarantine"}]
    paths.extend(Path(p) for p in extra_paths if Path(p).exists())
    for index, path in enumerate(paths):
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / (path.name if path.parent == directory else f"external-{index}-{path.name}")
        path.replace(target)
    atomic_json(directory / "attempt.json", dict(attempt_id=attempt_id,
        started_utc=datetime.now(timezone.utc).isoformat()))
    return attempt_id


def freeze_module():
    spec = importlib.util.spec_from_file_location("campaign_freeze", HERE / "freeze.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pin_transport_module():
    spec = importlib.util.spec_from_file_location("campaign_pin_transport", Path(__file__).with_name("pin_transport.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze_check(args):
    return freeze_module().check_manifest(args.freeze_manifest, allow_drift=args.allow_drift,
                                          diagnostic=args.diagnostic)


def runner_check(args, identity):
    changed = [name for name, digest in identity["scripts_sha256"].items()
               if not (ROOT / name).is_file() or sha(ROOT / name) != digest]
    if changed and not args.allow_drift:
        raise ValueError("runner drift: " + ", ".join(changed))
    artifacts = [name for name, digest in identity.get("artifacts_sha256", {}).items()
                 if not Path(name).is_file() or sha(name) != digest]
    if artifacts and not args.allow_drift:
        raise ValueError("tokenizer/experiment artifact drift: " + ", ".join(artifacts))
    inputs = [row["path"] for row in identity.get("inputs", [])
              if not Path(row["path"]).is_file() or sha(row["path"]) != row["sha256"]]
    if inputs:
        raise ValueError("instance content drift: " + ", ".join(inputs))
    provenance = [name for name, digest in identity.get("provenance_sha256", {}).items()
                  if not Path(name).is_file() or sha(name) != digest]
    if provenance:
        raise ValueError("regeneration/split provenance drift: " + ", ".join(provenance))


def contained(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE):
        raise ValueError(f"output must remain inside campaign-g1: {path}")
    return path


def generation_coverage(manifest):
    """Keep unattempted members separate from explicit generator rejections."""
    return dict(allocation_complete=manifest.get("allocation_complete"),
                generated=len(manifest.get("bindings", {})),
                rejected=manifest.get("rejected", 0),
                not_generated=len(manifest.get("not_generated_spec_keys", [])),
                pending=len(manifest.get("pending_spec_keys", [])),
                tranches={name: {key: value for key, value in row.items() if key != "spec_keys"}
                          for name, row in manifest.get("tranches", {}).items()})


def select_inputs(args, epoch):
    from eval.splits_inventory import FrozenInventory
    from eval.splits_epoch import validate_epoch_manifest, validate_epoch_instance
    from stowage.conventions import canonical_vessel_id
    root = args.instance_root
    regen = HERE / epoch / "regen/manifest.json"
    preparation_snapshot = read_snapshot(regen) if regen.exists() else None
    preparation = preparation_snapshot.json() if preparation_snapshot is not None else None
    provenance = {} if preparation_snapshot is None else {str(regen.resolve()): preparation_snapshot.sha256}
    if root is None:
        if preparation is None:
            raise ValueError("regeneration manifest missing; run regen.py first or pass --instance-root")
        root = preparation["instance_root"]
    root = Path(root).resolve()
    manifest_path = root / "manifest.json"
    manifest_snapshot = read_snapshot(manifest_path)
    manifest = manifest_snapshot.json()
    provenance[str(manifest_path.resolve())] = manifest_snapshot.sha256
    if manifest.get("epoch_id") != epoch:
        raise ValueError("instance manifest belongs to a different source epoch")
    # Resolve the trusted release against the live repository explicitly: a
    # diagnostic source snapshot may import eval from a different directory.
    release_path = getattr(args, "split_manifest", None) or ROOT / "experiments/splits/v2/manifest.json"
    release_snapshot = read_snapshot(release_path)
    inventory = FrozenInventory(release_snapshot.json())
    provenance[str(Path(release_path).resolve())] = release_snapshot.sha256
    if inventory.version != "v2":
        raise ValueError("campaign requires the existing trusted v2 allocation")
    validate_epoch_manifest(root, manifest, inventory, allow_diagnostic=args.diagnostic,
                            check_source=not getattr(args, "allow_drift", False))
    if not args.diagnostic:
        require_evidence(manifest)
        if preparation is None:
            raise ValueError("real campaign requires a completed same-epoch regeneration tranche")
        require_evidence(preparation)
        if (not preparation.get("complete") or preparation.get("epoch_id") != epoch
            or preparation.get("cache_warm_complete") is not True
            or not {"instances", "bindings", "cache_warm_preflight"}.issubset(preparation.get("stages_completed", []))):
            raise ValueError("instances/bindings and prior cache_warm qualification must finish in the frozen epoch")
        # A completed tranche is sufficient. The untouched v2 allocation may
        # contain thousands of specs that have not been generated yet.
        tranche = preparation.get("current_tranche")
        if tranche and not preparation.get("tranches", {}).get(tranche, {}).get("complete"):
            raise ValueError(f"regeneration tranche {tranche!r} has not completed")
        if Path(preparation["instance_root"]).resolve() != root:
            raise ValueError("instance root differs from same-epoch regeneration")
        if sha(preparation["split_manifest"]) != provenance[str(Path(release_path).resolve())]:
            raise ValueError("regeneration and campaign use different split allocations")
    ship = canonical_vessel_id(args.ship) if args.ship else None
    rows = []
    for key, group in sorted(manifest.get("groups", {}).items()):
        rung = group.get("profile", key.split("/")[-1])
        if rung not in args.rungs:
            continue
        for shard in group.get("shards", []):
            if shard.get("split") not in ("train", "val"):
                continue
            vessel = shard.get("vessel") or shard.get("spec", {}).get("vessel") or shard.get("split_spec", {}).get("vessel")
            vessel = canonical_vessel_id(vessel) if vessel else vessel
            if ship and vessel != ship:
                continue
            relative = Path(shard["instance"])
            # New releases store root-relative paths; old manifests use group-relative.
            path = root / relative
            if not path.is_file():
                path = root / key / relative
            if not path.resolve().is_relative_to(root):
                raise ValueError("instance path escapes its release")
            if not path.is_file():
                raise ValueError(f"generated instance binding has no file: {path}; regenerate/restore this spec")
            expected = shard.get("sha256")
            observed = sha(path)
            if expected and observed != expected:
                raise ValueError(f"instance digest mismatch: {path}")
            rows.append(dict(path=str(path), sha256=observed, rung=rung,
                group=key, vessel=vessel, shard=shard))
    # Count is per rung across generator/ship identities, selected before outcomes.
    chosen = []
    for rung in args.rungs:
        eligible = [r for r in rows if r["rung"] == rung]
        # Alternate generator groups so a two-case diagnostic covers both producers.
        groups = sorted({r["group"] for r in eligible})
        interleaved = []
        for offset in range(max((sum(r["group"] == g for r in eligible) for g in groups), default=0)):
            for group in groups:
                candidates = [r for r in eligible if r["group"] == group]
                if offset < len(candidates):
                    interleaved.append(candidates[offset])
        if len(interleaved) < args.count:
            raise ValueError(f"{rung}: requested {args.count} generated train/val inputs, found {len(interleaved)}; "
                             "missing allocation members may be not generated yet; extend the tranche or lower --count. "
                             "Unavailable specs are not generation rejections.")
        chosen.extend(interleaved[:args.count])
    if not chosen:
        raise ValueError("no eligible instances; empty campaigns cannot qualify a gate")
    for row in chosen:
        validate_epoch_instance(row["path"], row["shard"])
    args._validated_provenance = provenance
    args._validated_instance_manifest = manifest
    return chosen, manifest_path


def calibrated_stage_estimate(sample, stage, rungs, count, workers):
    """Only scale measurements of the same task and rung, never tiny to full."""
    stats = sample.get("stages", {}).get(stage, {}) if sample else {}
    measured = (sample or {}).get("solve_time_per_size", {})
    means = {rung: measured.get(f"{stage}/{rung}", {}).get("mean_s") for rung in rungs}
    complete = all(value is not None for value in means.values())
    core_s = sum(means.values()) * count if complete else None
    return dict(measured_mean_core_s_by_rung=means,
        uncalibrated_rungs=[rung for rung, value in means.items() if value is None],
        scaled_core_s=core_s, scaled_wall_s=core_s / workers if core_s is not None else None,
        measured_peak_ram_mb=stats.get("peak_ram_mb"),
        scaling_qualification="Same-rung timings only; first real shard must calibrate every unmeasured rung, especially after D58")


def command(args, job, directory):
    cmd = [sys.executable, str(HERE / "stage_worker.py"), "--stage", job["stage"],
           "--input", job["input"]["path"], "--output", str(directory), "--rung", job["input"]["rung"]]
    for key in ("search", "oracle", "ballast", "check", "timeout"):
        cmd.extend([f"--{key}-s", str(getattr(args, key + "_s"))])
    if job.get("plan"):
        cmd.extend(["--plan", job["plan"]])
    if args.diagnostic:
        cmd.append("--diagnostic")
    cmd.extend(["--split", job["input"].get("shard", {}).get("split", "train"),
                "--instance-seed", str(job["input"].get("shard", {}).get("seed", 0))])
    return cmd


def run_shard(args, identity, job, output):
    frozen = freeze_check(args)
    runner_check(args, identity)
    if job.get("plan") and not job.get("artifact_error"):
        try:
            if sha(job["plan"]) != job["plan_sha256"]:
                raise ValueError("returned plan artifact changed before replay/reuse")
        except OSError as exc:
            raise ValueError("returned plan artifact missing before replay/reuse") from exc
    directory = output / job["stage"] / job["id"]
    shard_identity = dict(campaign=identity, job=job)
    marker = directory / "done.json"
    if marker.exists():
        result = verified_done(marker, shard_identity)
        if result is None:
            raise ValueError(f"done marker or artifact drift: {marker}; restore the recorded attempt")
        return {**result, "checkpoint": str(marker)}
    ensure_identity(directory / "identity.json", shard_identity)
    directory.mkdir(parents=True, exist_ok=True)
    attempt_id = quarantine_attempt(directory)
    if job.get("artifact_error"):
        # A missing/corrupt candidate is still a replay obligation. Its original
        # inventory remains immutable; this completed observation cannot pass.
        result_path = directory / "result.json"
        result = dict(stage=job["stage"], epoch_id=identity["epoch_id"],
            diagnostic=args.diagnostic, evidence_eligible=not args.diagnostic,
            allow_drift=args.allow_drift, input=job["input"], shard_id=job["id"],
            attempt_id=attempt_id, adapter_completed=True, process_wall_s=0,
            process_returncode=None, artifact_error=job["artifact_error"],
            measurement=dict(verified=False, checker_passed=None,
                finalized_status="artifact_unavailable", gold_eligible=False))
        freeze_check(args)
        runner_check(args, identity)
        result_digest = atomic_json(result_path, result)
        write_done(marker, shard_identity, result_path, expected_result_sha256=result_digest)
        return {**result, "checkpoint": str(marker)}
    cache_root = HERE / identity["epoch_id"] / "regen/cache"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
        CAMPAIGN_ATTEMPT_ID=attempt_id,
        CAMPAIGN_CACHE_ROOT=str(cache_root),
        STOWAGE_VESSEL_CACHE_DIR=str(cache_root / "cache/vessels"))
    env.pop("CAMPAIGN_NUMERICAL_CACHE_PIN", None)
    if frozen and frozen.get("numerical_caches"):
        env = pin_transport_module().pin_environment(frozen["numerical_caches"], output, env)
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = "1"
    started = time.monotonic()
    error = None
    with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
        child = subprocess.Popen(command(args, job, directory), cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
        atomic_json(directory / "pid.json", dict(pid=child.pid, owner_pid=os.getpid(), started_utc=datetime.now(timezone.utc).isoformat()))
        try:
            returncode = child.wait(timeout=args.timeout_s)
        except subprocess.TimeoutExpired:
            child.terminate()  # exact PID created immediately above
            try:
                returncode = child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                returncode = child.wait()
            error = f"owned shard process timed out after {args.timeout_s}s"
        except BaseException:
            if child.poll() is None:
                child.kill()
            child.wait()
            raise
    result_path = directory / "result.json"
    result = dict(adapter_completed=False)
    if returncode == 0 and error is None:
        try:
            result = read_snapshot(result_path).json()
            if not isinstance(result, dict):
                raise ValueError("result must be an object")
            if result.get("attempt_id") != attempt_id:
                raise ValueError("result belongs to another/missing child attempt")
            if not args.diagnostic:
                require_evidence(result)  # never sanitize child diagnostic lineage
        except (OSError, ValueError, TypeError) as exc:
            error = f"this attempt has no eligible readable result: {exc}"
            result = dict(adapter_completed=False)
    else:
        error = error or f"owned shard process exited {returncode}"
    completed = returncode == 0 and error is None and result.get("adapter_completed") is True
    if not completed:
        # Approval/checker fields from a failed invocation are not observations.
        result = dict(adapter_completed=False)
    result.update(stage=job["stage"], epoch_id=identity["epoch_id"], diagnostic=args.diagnostic,
        evidence_eligible=completed and not args.diagnostic, allow_drift=args.allow_drift, input=job["input"],
        shard_id=job["id"], process_returncode=returncode, process_reaped=True,
        process_wall_s=time.monotonic()-started, attempt_id=attempt_id)
    if job["stage"] in {"4.6", "4.7", "4.8"}:
        # Presence is observed independently of solver status: a timeout may
        # return an incumbent, and a returned plan may never reach checking.
        result["plan_returned"] = any(item["candidate"] for item in artifact_inventory(directory))
    if job.get("replay_probe"):
        result["replay_probe"] = True
        result.setdefault("measurement", {})
        result["measurement"]["gold_eligible"] = False
        result["measurement"]["qualification"] = "Diagnostic invalid empty-plan control; excluded from all solver/gold denominators"
    if error:
        result["error"] = error
    freeze_check(args)  # no done marker is written across a source transition
    runner_check(args, identity)
    result_digest = atomic_json(result_path, result)
    # A negative solver/checker result is a completed experiment; a broken
    # adapter or killed process is retriable and is never a done checkpoint.
    if completed:
        write_done(marker, shard_identity, result_path, expected_result_sha256=result_digest)
        persisted = verified_done(marker, shard_identity)
        if persisted is None:
            raise ValueError(f"failed artifact checkpoint verification: {marker}")
        return {**persisted, "checkpoint": str(marker)}
    return result


def execute(args, identity, jobs, output):
    rows = []
    # Completion order is irrelevant: shard identities and final sorting are stable.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_shard, args, identity, job, output) for job in jobs]
        try:
            for future in as_completed(futures):
                row = future.result()
                rows.append(row)
                print(json.dumps(dict(stage=row.get("stage"), shard=row["shard_id"],
                    completed=row.get("adapter_completed"), wall_s=row["process_wall_s"])), flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    return sorted(rows, key=lambda r: (r.get("stage", ""), r["shard_id"]))


def primary_jobs(selected):
    """Interleave tasks so four workers start the independent task stages."""
    return [dict(stage=stage, id=f"{row['rung']}-{index:04d}-{row['sha256'][:8]}", input=row)
        for index, row in enumerate(selected) for stage in PRIMARY
        if stage != "4.6" or row["rung"] in ("tiny", "small")]


def discover_plans(output, inputs, *, checkpoint_snapshots=None):
    """Keep every checkpointed candidate, even when its bytes have been lost.

    The committed inventory is the denominator. File-system discovery alone
    cannot distinguish a solver that returned no plan from a deleted plan.
    """
    from stowage.model import Instance, MovePlan
    selections = {row["sha256"]: row for row in inputs}
    jobs = []
    for stage in PRIMARY:
        for marker in sorted((output / stage).glob("*/done.json")):
            marker_snapshot = read_snapshot(marker)
            if checkpoint_snapshots is not None:checkpoint_snapshots[str(marker.resolve())]=marker_snapshot
            committed = marker_snapshot.json()
            if committed.get("schema_version") != CHECKPOINT_SCHEMA or not isinstance(committed.get("artifacts"), list):
                raise ValueError(f"candidate inventory missing from done marker: {marker}")
            observed = Path(committed["result"])
            observed_snapshot = read_snapshot(observed)
            if observed_snapshot.sha256 != committed["result_sha256"]:
                raise ValueError(f"result drift prevents candidate inventory admission: {marker}")
            result = observed_snapshot.json()
            if result.get("attempt_id") != committed.get("attempt_id"):
                raise ValueError(f"candidate inventory belongs to a different attempt: {marker}")
            if result.get("measurement", {}).get("diagnostic_ballast_probe"):
                continue  # invalid instrumentation controls are never solver candidates
            selected = committed["identity"]["job"]["input"]
            if selected["sha256"] not in selections:
                raise ValueError(f"returned plan belongs to an unselected input: {marker}")
            for artifact in committed["artifacts"]:
                if not artifact["candidate"]:
                    continue
                path, digest = Path(artifact["path"]), artifact["sha256"]
                error = None
                try:
                    plan_snapshot = read_snapshot(path, expected_sha256=digest)
                    plan = plan_snapshot.model(MovePlan)
                    input_snapshot = read_snapshot(selected["path"], expected_sha256=selected["sha256"])
                    if plan.instance_id != input_snapshot.model(Instance).id:
                        raise ValueError("candidate has a different instance identity")
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                row = dict(stage="replay", id=f"{stage}-{len(jobs):04d}-{digest[:8]}",
                    input=selected, plan=str(path), plan_sha256=digest, source_stage=stage,
                    source_checkpoint=str(marker))
                if error:
                    row["artifact_error"] = error
                jobs.append(row)
    return jobs


def migration_plan_records(output, replay_jobs, identity, *, checkpoint_snapshots=None):
    """Bind each original candidate to its committed post-ballast replay plan.

    The original artifact remains the obligation. Failed/missing replay cannot
    remove it or let an unfinished raw plan stand in for the finalized plan.
    """
    result=[]
    for job in replay_jobs:
        if job.get("source_stage") not in {"4.6","4.7","4.8"}:continue
        record=dict(job,candidate_plan=job["plan"],candidate_plan_sha256=job["plan_sha256"])
        marker=Path(output)/"replay"/job["id"]/"done.json"
        record["replay_checkpoint"]=str(marker.resolve())
        try:
            snapshot=read_snapshot(marker)
            if checkpoint_snapshots is not None:checkpoint_snapshots[str(marker.resolve())]=snapshot
            committed=snapshot.json()
            artifacts=[item for item in committed["artifacts"] if Path(item["path"]).name=="finalized-plan.json"]
            if len(artifacts)!=1:raise ValueError("exactly one committed finalized plan required")
            # Retain the recorded obligation even when these bytes are now lost.
            record.update(plan=artifacts[0]["path"],plan_sha256=artifacts[0]["sha256"])
            persisted=verified_done(marker,committed["identity"],marker_snapshot=snapshot)
            if committed["identity"].get("campaign")!=identity or persisted is None:
                raise ValueError("post-ballast replay checkpoint not committed for this campaign")
            if persisted.get("measurement",{}).get("finalized_status")!="ok":
                raise ValueError("post-ballast finalization was not successful")
        except (OSError,ValueError,KeyError,TypeError) as exc:
            record["artifact_error"]=f"{type(exc).__name__}: {exc}"
        result.append(record)
    return result


def plan_returned(row):
    """Count returned incumbents even when timeout/checker work fails.

    New checkpoints record candidate inventory presence. Measurement fallback is
    for legacy diagnostic rows only; it never promotes a checker result.
    """
    m = row.get("measurement", {})
    if "plan_returned" in row:
        return row["plan_returned"] is True
    if "plan_returned" in m:
        return m["plan_returned"] is True
    if "runner_version" in m:
        return "checker_seconds" in m or m.get("ballast_present") is True
    return (m.get("checker_passed") is not None or m.get("approved") is True
            or m.get("legal") is True or m.get("physical_objective") is not None)


def family_identity(row):
    from stowage.conventions import canonical_vessel_id
    selected = row.get("input", {})
    shard = selected.get("shard", {})
    spec = shard.get("split_spec", shard.get("spec", {}))
    ship = selected.get('vessel', spec.get('vessel'))
    generator = spec.get('generator', selected.get('group', 'unknown').split('/')[0])
    noise = spec.get('noise')
    if generator == 'random_yard' and noise is None:
        noise = spec.get('blocking_ratio')
    return dict(ship=canonical_vessel_id(ship) if ship else ship,
        generator=generator, port=spec.get('port'), noise=noise)


def solver_yield_summary(rows):
    """Keep stage-specific denominators; never pool methods into one success."""
    result = {}
    for stage in ("4.6", "4.7", "4.8"):
        current = [row for row in rows if row.get("stage") == stage]
        per_rung, strata = {}, {}
        for row in current:
            rung = row.get("input", {}).get("rung", "unknown")
            per_rung.setdefault(rung, []).append(row)
            family = family_identity(row)
            key = (rung, json.dumps(family, sort_keys=True))
            strata.setdefault(key, (family, []))[1].append(row)
        result[stage] = dict(per_rung={rung: stage_stats(group) for rung, group in sorted(per_rung.items())},
            per_rung_family=[dict(rung=rung, family=family, **stage_stats(group))
                for (rung, _), (family, group) in sorted(strata.items())])
    return result


def d62_crossing_coverage(rows,rungs=(),*,frozen=None,population=None,attempted_population=None):
    spec=importlib.util.spec_from_file_location('_campaign_migration_evidence',Path(__file__).with_name('migration_evidence.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.d62_coverage(rows,rungs,frozen=frozen,population=population,attempted_population=attempted_population)


def stage_stats(rows):
    measurements = [r.get("measurement", {}) for r in rows]
    attempted_rows = [row for row in rows if row.get("attempted", True)]
    attempted = len(attempted_rows)
    returned = sum(plan_returned(row) for row in attempted_rows)
    checks = []
    costs, gaps = [], []
    for row in attempted_rows:
        m = row.get("measurement", {})
        checked = m.get("checker_passed")
        if "plan_returned" in m and "checker_violations" not in m:
            checked = None  # g1.measure initializes False before validation runs
        if "runner_version" in m and "checker_seconds" not in m:
            checked = None  # two-step initializes False before any plan exists
        if checked is not None:
            checks.append(bool(checked))
        if m.get("approved") or m.get("legal"):
            if m.get("objective") is not None:
                costs.append(m["objective"])
            if m.get("gap_to_lb") is not None:
                gaps.append(m["gap_to_lb"])
    return dict(attempted=attempted, plans_returned=returned,
        plans_returned_per_attempt=returned/attempted if attempted else None,
        checker_passed_per_attempt=sum(checks)/attempted if attempted else None,
        pass_among_returned_pct=100*sum(checks)/returned if returned else None,
        checker_observed_pass_pct=100*sum(checks)/len(checks) if checks else None,
        adapters_completed=sum(bool(r.get("adapter_completed")) for r in rows),
        full_checker_observed=len(checks), full_checker_passed=sum(checks),
        pass_pct=100*sum(checks)/attempted if attempted else None,
        mean_approved_moves=sum(costs)/len(costs) if costs else None,
        mean_approved_gap_to_lb=sum(gaps)/len(gaps) if gaps else None,
        total_core_s=sum(r.get("process_wall_s", 0) for r in rows),
        peak_ram_mb=max((r.get("peak_ram_upper_bound_mb", 0) for r in rows), default=0),
        physical_proofs=sum(m.get("independent_proof") is True for m in measurements),
        ballast_calls=sum(m.get("calls", m.get("ballast_calls", 0)) for m in measurements),
        ballast_not_found=sum(m.get("statuses", {}).get("not_found", 0) for m in measurements),
        diagnostic_ballast_probe_calls=sum(m.get("diagnostic_control", {}).get("calls", 0) for m in measurements),
        diagnostic_ballast_probe_not_found=sum(m.get("diagnostic_control", {}).get("statuses", {}).get("not_found", 0) for m in measurements),
        mask_outcomes={name: sum(m.get("outcome") == name for m in measurements)
                       for name in ("passed", "failed", "dead_end")},
        infrastructure_blocked=sum(m.get("infrastructure_blocked") is True for m in measurements),
        qualified_mask_outcomes={name:sum(m.get("outcome") == name and not m.get("infrastructure_blocked")
            and m.get("production_c2_active") is True for m in measurements) for name in ("passed", "failed", "dead_end")},
        mask_verified=sum(m.get("verified") is True for m in measurements))


def synthesize(args, identity, rows, replay_jobs, output):
    if not args.diagnostic:
        require_evidence(rows)
        for row in rows:
            marker = Path(row.get("checkpoint", "missing-done-marker"))
            try:
                marker_snapshot = read_snapshot(marker)
                checkpoint = marker_snapshot.json()
                persisted = verified_done(marker, checkpoint["identity"], marker_snapshot=marker_snapshot)
                if checkpoint["identity"]["campaign"] != identity or persisted is None:
                    raise ValueError("invalid checkpoint")
                if {key: value for key, value in row.items() if key != "checkpoint"} != persisted:
                    raise ValueError("row differs from checkpoint")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"evidence synthesis requires a verified done marker/checkpoint: {marker}") from exc
    grouped = {stage: stage_stats([r for r in rows if r.get("stage") == stage]) for stage in STAGES if stage != "5.4"}
    replay_rows = [r for r in rows if r.get("stage") == "replay" and not r.get("replay_probe")]
    solver_replay = stage_stats(replay_rows)
    checked = solver_replay["full_checker_observed"]
    passed = solver_replay["full_checker_passed"]
    qualified = [r for r in replay_rows if r.get("measurement", {}).get("gold_eligible") is True]
    # A raw candidate and its approved artifact can finish as the same plan.
    # Count distinct physical actions/ballast, excluding audit IDs/timestamps.
    approved = len({(r.get("input", {}).get("sha256"), r["measurement"]["finalized_plan_identity"])
                    for r in qualified if r["measurement"].get("finalized_plan_identity")})
    core_s = sum(r.get("process_wall_s", 0) for r in rows)
    exact_small = {r["input"]["sha256"]:r.get("measurement", {}) for r in rows
        if r.get("stage") == "4.6" and r.get("input", {}).get("rung") == "small"
        and r.get("measurement", {}).get("independent_proof") is True
        and r.get("measurement", {}).get("oracle_checker_passed") is True}
    matched = []
    for row in rows:
        measurement = row.get("measurement", {})
        reference = exact_small.get(row.get("input", {}).get("sha256"))
        if row.get("stage") != "4.7" or not measurement.get("approved") or reference is None:
            continue
        optimum, cost = reference.get("physical_objective"), measurement.get("objective")
        if optimum is None or cost is None:
            continue
        matched.append(dict(input_sha256=row["input"]["sha256"], exact_physical_objective=optimum,
            scalable_approved_objective=cost, absolute_gap=cost-optimum,
            relative_gap=(cost-optimum)/optimum if optimum else (0 if cost == 0 else None)))
    unproved = []
    unproved_attempts = [row for row in rows if row.get("stage") == "4.7"
        and row.get("input", {}).get("rung") == "small"
        and row.get("input", {}).get("sha256") not in exact_small]
    for row in unproved_attempts:
        measurement = row.get("measurement", {})
        if not measurement.get("approved"):
            continue
        cost = measurement.get("objective")
        bound = measurement.get("lower_bound")
        bound = bound.get("weighted") if isinstance(bound, dict) else bound
        gap = measurement.get("gap_to_lb")
        if gap is None and isinstance(cost, (int, float)) and isinstance(bound, (int, float)):
            gap = (cost-bound)/cost if cost else (0 if bound == 0 else None)
        if gap is None:
            continue
        unproved.append(dict(input_sha256=row["input"]["sha256"], scalable_approved_objective=cost,
            lower_bound=bound, relative_gap_to_objective=gap))
    yields = solver_yield_summary(rows)
    rungs = sorted({row.get("input", {}).get("rung") for row in rows
                    if row.get("stage") in {"4.6", "4.7", "4.8"} and row.get("input", {}).get("rung")})
    solve_yield = {}
    for rung in rungs:
        required = ("4.6", "4.7", "4.8") if rung in {"tiny", "small"} else ("4.7", "4.8")
        rates = [yields[stage]["per_rung"].get(rung, {}).get("plans_returned_per_attempt") for stage in required]
        solve_yield[rung] = min(rates) if all(rate is not None for rate in rates) else None
    timings = {}
    for row in rows:
        rung = row.get("input", {}).get("rung")
        if rung is None:
            continue
        key = row.get("stage", "unknown") + "/" + rung
        timings.setdefault(key, []).append(row.get("process_wall_s", 0))
    # Every committed candidate remains in the denominator, including missing files.
    solver_inventory = dict(schema_version=2,epoch_id=identity["epoch_id"],
        source_sha256=identity.get("source_sha256"),freeze_sha256=identity.get("freeze_sha256"),
        component_epoch=identity.get("component_epoch"),diagnostic=args.diagnostic,
        evidence_eligible=not args.diagnostic,
        campaign_identity=dict(path=str((output/"campaign-manifest.json").resolve()),
            sha256=sha(output/"campaign-manifest.json")) if (output/"campaign-manifest.json").exists() else None,
        records=migration_plan_records(output,replay_jobs,identity))
    solver_inventory_path=output/"5.4/solver-plans.json"
    solver_inventory_digest=atomic_json(solver_inventory_path,solver_inventory)
    summary = dict(epoch_id=identity["epoch_id"], diagnostic=args.diagnostic,
        label="diagnostic, not evidence" if args.diagnostic else "frozen-source evidence; gate decision pending",
        evidence_eligible=not args.diagnostic, stages=grouped,
        generation_rejected_shards=identity.get("generation_rejected_shards", 0),
        generation_coverage=identity.get("generation_coverage"),
        solver_checker_passed=passed, solver_checker_observed=checked,
        solver_full_checker_rate=passed/checked if checked else None,
        returned_plan_replay_denominator=len(replay_jobs),
        diagnostic_invalid_replay_probes=sum(bool(r.get("replay_probe")) for r in rows),
        returned_plan_replay_passed=solver_replay["mask_verified"],
        replay_gate_met=(len(replay_jobs)>0 and not any(j.get("artifact_error") for j in replay_jobs)
            and solver_replay["mask_verified"] == len(replay_jobs)),
        solver_checker_gate_met=(len(replay_jobs)>0 and checked == len(replay_jobs) and passed == checked
            and not any(j.get("artifact_error") for j in replay_jobs)
            and all(r.get("measurement", {}).get("finalized_status") == "ok" for r in replay_rows)),
        expert_rule_coverage="unverified; requires catalogue/checker/engine/solver rule audit",
        exact_vs_scalable_small_gap=matched or None,
        matched_small_optimum_count=len(matched),
        exact_vs_scalable_small_gap_population=f"on proved-optimal small instances only (n = {len(matched)})",
        scalable_vs_lower_bound_unproved_small=dict(
            population="small scalable attempts without an independently proved physical optimum; gaps only for approved plans with available bounds",
            normalization="(scalable objective - lower bound) / scalable objective",
            attempted_count=len(unproved_attempts), matched_count=len(unproved), rows=unproved,
            mean_relative_gap_to_objective=sum(item["relative_gap_to_objective"] for item in unproved)/len(unproved) if unproved else None),
        solver_yield=yields,solve_yield=solve_yield,
        solver_plan_inventory=dict(path=str(solver_inventory_path.resolve()),sha256=solver_inventory_digest),
        d62_crossing=d62_crossing_coverage(replay_rows,rungs,frozen=identity,
            population=[row["input"] for row in replay_jobs if row.get("source_stage") in {"4.6","4.7","4.8"}],
            attempted_population=identity.get("inputs")),
        d62_zero_acknowledgements={},
        solve_yield_basis="minimum plans-returned/attempted across required solver stages per rung; unknown if any required stage is absent; not checker agreement",
        min_yield={rung: None for rung in rungs},
        min_yield_approval=dict(status="leader_approval_required", approved_by=None, approval_epoch_id=None),
        solve_time_per_size={k:dict(attempts=len(v), mean_s=sum(v)/len(v), total_s=sum(v)) for k,v in timings.items()},
        gold_plans_per_core_hour=approved*3600/core_s if core_s else None,
        gold_plan_count=approved, gold_qualified_candidates=len(qualified), total_core_s=core_s,
        gold_affordability_basis="Unique returned plans passing post-ballast full checker and production token replay; all campaign attempt core-seconds included, cache warming excluded",
        g1_met=False,
        limitations=["Experiment completion does not imply any G1 criterion passes.",
            "No returned plan is a zero denominator, never a vacuous 100%.",
            "D11 tertiary margin optimum is not established by an engine-domain weighted-lift proof."])
    atomic_json(output / "5.4/summary.json", summary)
    date = datetime.now(timezone.utc).date().isoformat()
    def fmt(value):
        return "unknown" if value is None else f"{value:.3g}" if isinstance(value, (int, float)) else str(value)
    text = [f"{summary['label']}\n", "| Date | Method | Instances | Checker passed / attempted % | Moves (non-prod.) | Gap to LB | Gap to solver | Time | Fallback | Run |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for stage, stats in grouped.items():
        text.append(f"| {date} | {stage} | {stats['attempted']} attempted; {stats['plans_returned']} plans returned ({fmt(100*stats['plans_returned_per_attempt'] if stats['plans_returned_per_attempt'] is not None else None)}%); pass among returned {fmt(stats['pass_among_returned_pct'])}% | {fmt(stats['pass_pct'])} | {fmt(stats['mean_approved_moves'])} | {fmt(stats['mean_approved_gap_to_lb'])} | unknown | {stats['total_core_s']:.3f} core-s | n/a | {identity['epoch_id']}/{stage} |")
    text.append(f"| {date} | 5.4 synthesis | {len(replay_jobs)} returned-plan replay attempts; {checked} observed checker verdicts | {fmt(100*passed/len(replay_jobs) if replay_jobs else None)} | unknown | unknown | unknown | {core_s:.3f} total core-s | n/a | {identity['epoch_id']}/5.4; G1 unproved |")
    text.extend(["", summary["exact_vs_scalable_small_gap_population"] + ".",
        f"Unproved small: scalable-vs-lower-bound gap on {len(unproved)} approved plans / {len(unproved_attempts)} attempts; normalization (objective - LB) / objective.",
        "Move averages are over each method's own approved plans and are not comparable across rows. Cross-method move claims require paired-instance comparisons (n pairs / n excluded).",
        "G1 checker agreement does not establish adequate solve yield; the leader must fill min_yield for every rung before approval."])
    name = "diagnostic-summary.md" if args.diagnostic else "summary.md"
    (output / name).write_text("\n".join(text) + "\n")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--freeze-manifest", type=Path, default=HERE / "freeze-manifest.json")
    p.add_argument("--instance-root", type=Path)
    p.add_argument("--split-manifest", type=Path)
    p.add_argument("--diagnostic", action="store_true")
    p.add_argument("--allow-drift", action="store_true")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--count", type=int, default=100, help="preselected accepted inputs per rung")
    p.add_argument("--rungs", nargs="+", choices=("tiny", "small", "medium", "full"), default=["tiny", "small", "medium", "full"])
    p.add_argument("--ship")
    p.add_argument("--output", type=Path)
    p.add_argument("--estimate-only", action="store_true")
    p.add_argument("--calibration", type=Path, help="diagnostic 5.4/summary.json used solely for compute scaling")
    for name, default in (("search", 60.), ("oracle", 30.), ("ballast", 30.), ("check", 30.), ("timeout", 240.)):
        p.add_argument(f"--{name}-s", type=float, default=default)
    args = p.parse_args(argv)
    validate_modes(args.diagnostic, args.allow_drift)
    if args.workers < 1 or args.workers > 12 or args.count < 1:
        p.error("positive count and 1..12 workers required (4 cores reserved elsewhere)")
    if any(not math.isfinite(getattr(args, k + "_s")) or getattr(args, k + "_s") <= 0
           for k in ("search", "oracle", "ballast", "check", "timeout")):
        p.error("budgets must be positive finite seconds")
    if args.diagnostic:
        if args.count > 3 or args.rungs != ["tiny"]:
            p.error("diagnostic is limited to 1..3 tiny instances and one ship")
        if not args.ship:
            from stowage.cache_warm import PRODUCTION_SHIPS
            args.ship = PRODUCTION_SHIPS[0]
    frozen = freeze_check(args)
    epoch = frozen.get("epoch_id") or frozen.get("epoch")
    if not epoch:
        raise ValueError("freeze manifest missing epoch_id")
    if bool(frozen.get("diagnostic")) != args.diagnostic:
        raise ValueError("diagnostic/evidence mode differs from freeze manifest")
    output = contained(args.output or HERE / epoch)
    selected, manifest = select_inputs(args, epoch)
    scripts = [HERE / "campaign.py", HERE / "stage_worker.py", HERE / "freeze.py", HERE / "cache_adapter.py", HERE / "pin_transport.py",HERE / "rule_policy.py",HERE / "REQUIRED_RULES.json",
        HERE / "migration_contract.py",HERE / "migration_canaries.py",HERE / "migration_evidence.py",
        HERE / "trusted_receipts.py",HERE / "qualified_canary_cases.py",HERE / "admission-trust.json"]
    if (HERE / "diagnostic.py").exists():
        scripts.append(HERE / "diagnostic.py")
    for relative in ("exact-solver/fix_epoch_evidence.py", "scalable-solver/g1_pilot.py",
        "scalable-solver/canonical_benchmark.py", "twostep/benchmark.py",
        "ballast/review_fix/campaign.py", "mask/pass_or_dead_end.py", "gold-plans/tiny/driver.py"):
        scripts.append(ROOT / "experiments" / relative)
    from llm.stowlm.format import DEFAULT_TOKENIZER_PATH
    tokenizer = DEFAULT_TOKENIZER_PATH / "tokenizer.json" if DEFAULT_TOKENIZER_PATH.is_dir() else DEFAULT_TOKENIZER_PATH
    provenance = {**args._validated_provenance,
        str(args.freeze_manifest.resolve()): frozen["_manifest_sha256"]}
    identity = dict(epoch_id=epoch, diagnostic=args.diagnostic, allow_drift=args.allow_drift,
        source_sha256=frozen.get("source_sha256"),freeze_sha256=frozen.get("freeze_sha256"),
        component_epoch=(frozen.get("migration") or {}).get("component_epoch") or frozen.get("component_epoch"),
        numerical_caches=frozen.get("numerical_caches"),
        freeze_manifest_sha256=frozen["_manifest_sha256"], instance_manifest_sha256=provenance[str(manifest.resolve())],
        scripts_sha256={str(path.relative_to(ROOT)): sha(path) for path in scripts},
        artifacts_sha256={str(tokenizer.resolve()): sha(tokenizer)},
        provenance_sha256=provenance,
        inputs=selected, generation_rejected_shards=args._validated_instance_manifest.get("rejected", 0),
        generation_coverage=generation_coverage(args._validated_instance_manifest),
        budgets_s={k:getattr(args, k + "_s") for k in ("search", "oracle", "ballast", "check", "timeout")})
    ensure_identity(output / "campaign-manifest.json", identity)
    jobs = primary_jobs(selected)
    for stage in (*PRIMARY, "5.1", "replay", "5.4"):
        count = sum(j["stage"] == stage for j in jobs) if stage in PRIMARY else len(selected) if stage == "5.1" else "all returned plans" if stage == "replay" else 1
        # These are configured hard ceilings, explicitly distinct from the
        # measured scaling estimates generated by the diagnostic SCHEDULE.
        calibration = args.calibration
        if calibration is None:
            choices = [path for path in HERE.glob("*/5.4/summary.json")
                       if path != output / "5.4/summary.json"]
            calibration = max(choices, key=lambda p:p.stat().st_mtime) if choices else None
        sample = None
        if calibration:
            sample = read_snapshot(calibration).json()
        measured_rungs = sorted({j["input"]["rung"] for j in jobs if j["stage"] == stage}) if stage in PRIMARY else args.rungs
        estimate = calibrated_stage_estimate(sample, stage, measured_rungs, args.count, args.workers)
        print(json.dumps(dict(stage=stage, shards=count, cores_per_shard=1,
            configured_wall_cap_s=args.timeout_s, packing_workers=args.workers,
            **estimate,
            calibration=str(calibration) if calibration else None,
            measured_estimates="see SCHEDULE.md; record actual result.json wall_s and peak_ram_upper_bound_mb")), flush=True)
    if args.estimate_only:
        return 0
    started = time.monotonic()
    rows = execute(args, identity, jobs, output)
    replay_jobs = discover_plans(output, selected)
    probe_jobs = []
    if args.diagnostic:
        from stowage.io import save_plan
        from stowage.model import Instance
        from stowage.model import MovePlan, Provenance
        represented = {job["input"]["sha256"] for job in replay_jobs}
        for index, row in enumerate(selected):
            if row["sha256"] in represented:
                continue
            instance = read_snapshot(row["path"], expected_sha256=row["sha256"]).model(Instance)
            path = output / "replay-probes" / f"{index:04d}.invalid-empty-plan.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            save_plan(MovePlan(id=f"{instance.id}.diagnostic-invalid-empty", instance_id=instance.id,
                call_index=instance.call_index, moves=[], provenance=Provenance(
                    created_by="campaign-g1.diagnostic-invalid-replay-probe")), path)
            probe_jobs.append(dict(stage="replay", id=f"diagnostic-invalid-probe-{index:04d}",
                input=row, plan=str(path), plan_sha256=sha(path), replay_probe=True))
    if not replay_jobs and not probe_jobs:
        empty = dict(stage="replay", epoch_id=epoch, diagnostic=args.diagnostic,
            evidence_eligible=not args.diagnostic, attempted=False, adapter_completed=True,
            shard_id="no-returned-plans", process_wall_s=0,
            attempt_id=uuid.uuid4().hex,
            measurement=dict(status="no_returned_plans", verified=False, checker_passed=None))
        result_path = output / "replay/no-returned-plans/result.json"
        result_digest = atomic_json(result_path, empty)
        marker = result_path.parent / "done.json"
        write_done(marker, dict(campaign=identity, job=dict(stage="replay", id="no-returned-plans")), result_path,
                   expected_result_sha256=result_digest)
        rows.append({**empty, "checkpoint": str(marker)})
    followups = [dict(stage="5.1", id=f"tiny-{i:04d}-{row['sha256'][:8]}", input=row)
                 for i,row in enumerate(selected) if row["rung"] != "full"]
    followups.extend(replay_jobs)
    followups.extend(probe_jobs)
    rows.extend(execute(args, identity, followups, output))
    freeze_check(args)
    summary_started = time.monotonic()
    summary = synthesize(args, identity, rows, replay_jobs, output)
    summary["summary_elapsed_s"] = time.monotonic()-summary_started
    summary["parent_peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**20 if sys.platform == "darwin" else 1024)
    summary["stages"]["5.4"] = dict(attempted=1, adapters_completed=1,
        total_core_s=summary["summary_elapsed_s"], peak_ram_mb=summary["parent_peak_rss_mb"])
    summary["campaign_wall_s"] = time.monotonic()-started
    summary["attempt_id"] = uuid.uuid4().hex
    summary_digest = atomic_json(output / "5.4/summary.json", summary)
    solver_pin=summary["solver_plan_inventory"]
    dependencies = {solver_pin["path"]}
    dependency_pins = {solver_pin["path"]:solver_pin["sha256"]}
    for row in rows:
        if row.get("checkpoint"):
            checkpoint = Path(row["checkpoint"])
            checkpoint_snapshot = read_snapshot(checkpoint)
            committed = checkpoint_snapshot.json()
            persisted = verified_done(checkpoint, committed["identity"], marker_snapshot=checkpoint_snapshot)
            if persisted is None or persisted != {key: value for key, value in row.items() if key != "checkpoint"}:
                raise ValueError(f"summary dependency differs from validated stage row: {checkpoint}")
            dependency_pins[str(checkpoint)] = checkpoint_snapshot.sha256
            dependency_pins[committed["result"]] = committed["result_sha256"]
            dependency_pins.update({item["path"]: item["sha256"] for item in committed["artifacts"]})
            dependencies.update([str(checkpoint), committed["result"]])
            dependencies.update(item["path"] for item in committed["artifacts"])
    write_done(output / "5.4/done.json", identity, output / "5.4/summary.json", dependencies,
               expected_dependencies=dependency_pins, expected_result_sha256=summary_digest)
    broken = [r for r in rows if not r.get("adapter_completed")]
    print(json.dumps(dict(epoch_id=epoch, diagnostic=args.diagnostic, wall_s=summary["campaign_wall_s"],
                         stages_completed=all(not [r for r in rows if r.get("stage") == s and not r.get("adapter_completed")] for s in STAGES[:-1]),
                         broken_adapters=len(broken), g1_met=False)), flush=True)
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
