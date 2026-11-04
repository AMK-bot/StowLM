"""Freeze committed fitting shards after the owner has stopped the producer.

This certificate-only freeze never opens, hashes, imports, or tests evaluation
instances. Existing test/showcase manifest records and hashes are retained.
It verifies the producer's recorded acceptance, not a new checker run.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "experiments/mvp/corpus"
REASON = ("D126: stop small production at its current committed size to release CPU "
          "for the checker fix window; remaining maps cannot be used before the 17:00 EDT freeze.")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.partial")
    tmp.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    tmp.replace(path)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fitting_path(root, config):
    split, rung, vessel, family = (config[k] for k in ("split", "rung", "vessel", "generator"))
    require(split in {"train", "val"}, "only fitting splits are allowed")
    path = root / split / rung / vessel / family / f"seed-{config['seed']:09d}.json"
    require(path.resolve().is_relative_to((root / split).resolve()), "fitting path escaped split")
    return path


def artifact_record(root, config, old_record, release):
    path = fitting_path(root, config)
    audit_path = path.with_suffix(".audit.json")
    audit = read(audit_path)
    relative = str(path.relative_to(root))
    raw_sha, audit_sha = sha(path), sha(audit_path)
    require(audit.get("config") == config, f"request/config mismatch: {relative}")
    require(audit.get("accepted") is True and audit.get("rule_freeze_v1_enabled") is False,
            f"missing producer OFF gate: {relative}")
    global_recorded = "global_screen" in audit
    require(audit.get("c2_screened") is False
            and (audit.get("global_screen") == 0 or (config["rung"] == "tiny" and not global_recorded)),
            f"missing C2 OFF evidence: {relative}")
    require(audit.get("instance_sha256") == raw_sha, f"instance checksum mismatch: {relative}")
    witness = audit.get("background_last", {})
    require(audit.get("background_checks", 0) > 0 and witness.get("accepted") is True
            and bool(witness.get("physics_sha256")), f"missing accepted D58 witness: {relative}")
    if "physics" in audit:
        physics = audit["physics"]
        require(witness["physics_sha256"] == physics.get("input_sha256", physics.get("sha256")),
                f"D58 physics mismatch: {relative}")
    sources = audit.get("source_files", audit.get("physics", {}).get("sources"))
    require(isinstance(sources, dict) and sources, f"missing original source hashes: {relative}")
    require(isinstance(audit.get("instance_versions"), dict) and bool(audit.get("schema_version")),
            f"missing original versions: {relative}")
    if old_record:
        require(old_record["sha256"] == raw_sha and old_record["audit_sha256"] == audit_sha,
                f"previously frozen fitting pair changed: {relative}")
    setting = release.get("ship_settings", {}).get(config["vessel"], {})
    active = config["rung"] == "tiny" or (config["rung"] == "small" and setting and all(
        config.get(k) == setting[k] for k in ("booking_slack", "booking_mass_fraction")))
    record = deepcopy(old_record) if old_record else {}
    record.update(split=config["split"], ship=config["vessel"], rung=config["rung"],
        family=config["generator"], seed=config["seed"], path=relative, sha256=raw_sha,
        audit_path=str(audit_path.relative_to(root)), audit_sha256=audit_sha,
        c2_screened=False, global_screen=audit.get("global_screen"),
        global_screen_evidence="explicit_zero" if global_recorded else "not_recorded_legacy_tiny",
        port=config["port"], schema_version=audit["schema_version"],
        versions=audit["instance_versions"], generator_components=audit.get("generation_components", {}),
        requested_targets=audit["requested_targets"], emitted_targets=audit["requested_targets"],
        generator_gate_passed=True, plan_checker_passed=None,
        generation_seconds=audit["elapsed_generation_s"],
        background_gate_seconds=audit.get("background_seconds", 0),
        background_gate_checks=audit["background_checks"],
        background_gate_cache_hits=audit.get("background_cache_hits", 0),
        background_gate_required=True, background_physics_sha256=witness["physics_sha256"],
        booking_slack=config.get("booking_slack"), booking_mass_fraction=config.get("booking_mass_fraction"),
        active_for_current_release=bool(active),
        release_epoch="tiny_training" if config["rung"] == "tiny" else
            f"{release.get('current_release', 'v2.1')}_training" if active else "v1_pending_replacement",
        source_binding_status=record.get("source_binding_status", "original_epoch_preserved"),
        changed_source_files=record.get("changed_source_files", []),
        source_files_sha256=canonical_sha(sources),
        source_binding_freeze_method="original producer source hashes preserved; current sources not compared")
    if setting and config["rung"] == "small":
        record["generation_settings"] = setting
    runtime = {"config": config, "stage_environment": audit.get("stage_environment"),
               "instance_versions": audit["instance_versions"], "schema_version": audit["schema_version"],
               "background_physics_sha256": witness["physics_sha256"],
               "generated_at_utc": audit.get("generated_at_utc"),
               "rule_freeze_v1_enabled": False, "c2_screened": False,
               "global_screen": audit.get("global_screen"),
               "global_screen_evidence": record["global_screen_evidence"]}
    record["runtime_config_sha256"] = canonical_sha(runtime)
    provenance = {"path": relative, "sha256": raw_sha, "audit_path": record["audit_path"],
                  "audit_sha256": audit_sha, "source_epoch_sha256": record["source_files_sha256"],
                  "runtime_config_sha256": record["runtime_config_sha256"], "runtime": runtime}
    return record, provenance, sources


def assemble(root, stop_evidence, runtime_files=()):
    """Return manifest and D126 report; do not read evaluation paths."""
    root = Path(root)
    original_path = root / "manifest.json"
    original_bytes = original_path.read_bytes()
    original = json.loads(original_bytes)
    require(not (root / "FROZEN-D126.json").exists(), "D126 freeze already exists; preserve its immutable record")
    stop_evidence = Path(stop_evidence)
    stop = read(stop_evidence)
    require(stop.get("stopped") is True, "owner stop evidence must state stopped=true")
    frozen = deepcopy(original)
    old_files = {row["path"]: row for row in original["files"]}
    kept = [deepcopy(row) for row in original["files"] if row["split"] not in {"train", "val"}]
    rows = [deepcopy(row) for row in original["requested_vs_emitted"] if row["split"] not in {"train", "val"}]
    hashes = {name: digest for name, digest in original["hashes"].items()
              if not name.startswith(("train/", "val/"))}
    artifacts, epochs, inventory_hashes, uncommitted, fitting_rows = [], {}, {}, [], []
    requested_counts = {}
    for split in ("train", "val"):
        inventory_path = root / f"{split}.requested.json"
        inventory = read(inventory_path)
        inventory_hashes[inventory_path.name] = sha(inventory_path)
        hashes[inventory_path.name] = inventory_hashes[inventory_path.name]
        specs = inventory["specs"]
        require(Counter(s["rung"] for s in specs) == Counter({k: n for k, n in inventory["counts"].items() if n}),
                f"invalid original requested counts: {split}")
        requested_counts[split] = len(specs)
        expected = {fitting_path(root, spec): spec for spec in specs}
        require(len(expected) == len(specs), f"duplicate inventory paths: {split}")
        actual_audits = {p for p in (root / split).rglob("*.audit.json")
                         if not any(part.startswith(".") for part in p.relative_to(root / split).parts)}
        require(all(p.with_name(p.name.removesuffix(".audit.json") + ".json") in expected for p in actual_audits),
                f"unrequested fitting audit: {split}")
        for instance_path in (root / split).rglob("*.json"):
            if (any(part.startswith(".") for part in instance_path.relative_to(root / split).parts)
                    or instance_path.name.endswith((".audit.json", ".failure.json", ".selection.json", ".partial.json"))
                    or instance_path.name == "manifest.json"):
                continue
            require(instance_path in expected, f"unrequested fitting instance: {instance_path}")
        grouped = {}
        for path, config in expected.items():
            key = (split, config["vessel"], config["rung"], config["generator"])
            bucket = grouped.setdefault(key, {"requested": 0, "emitted": [], "failed": 0})
            bucket["requested"] += 1
            audit_path = path.with_suffix(".audit.json")
            failure = path.with_suffix(".failure.json")
            if not audit_path.exists():
                if path.exists():
                    uncommitted.append(str(path.relative_to(root)))
                if failure.exists():
                    require(read(failure).get("config") == config, f"failure config mismatch: {failure}")
                    hashes[str(failure.relative_to(root))] = sha(failure)
                    bucket["failed"] += 1
                continue
            record, provenance, sources = artifact_record(root, config, old_files.get(str(path.relative_to(root))),
                                                          original.get("release") or {})
            kept.append(record)
            bucket["emitted"].append(record)
            artifacts.append(provenance)
            epoch = epochs.setdefault(provenance["source_epoch_sha256"], {"source_files": sources, "instances": 0})
            epoch["instances"] += 1
            hashes[record["path"]] = record["sha256"]
            hashes[record["audit_path"]] = record["audit_sha256"]
        # Keep existing zero rows and their format as well as newly emitted rows.
        all_keys = set(grouped) | {tuple(row[k] for k in ("split", "ship", "rung", "family"))
                                  for row in original["requested_vs_emitted"] if row["split"] == split}
        for key in sorted(all_keys):
            bucket = grouped.get(key, {"requested": 0, "emitted": [], "failed": 0})
            emitted = bucket["emitted"]
            seconds = sum(row["generation_seconds"] for row in emitted)
            row = dict(zip(("split", "ship", "rung", "family"), key))
            row.update(requested=bucket["requested"], emitted=len(emitted), failed=bucket["failed"],
                       active_emitted=sum(row["active_for_current_release"] for row in emitted),
                       deferred_release_slots=0, pending=bucket["requested"] - len(emitted) - bucket["failed"],
                       generation_seconds=seconds, mean_generation_seconds=seconds/len(emitted) if emitted else None,
                       background_gate_seconds=sum(row["background_gate_seconds"] for row in emitted))
            fitting_rows.append(row)
    rows.extend(fitting_rows)
    retained_paths = {row["path"] for row in kept}
    require(all(row["path"] in retained_paths for row in original["files"]),
            "previously frozen corpus membership disappeared")
    now = datetime.now(timezone.utc).isoformat()
    frozen.update(generated_at_utc=now, complete=False, partial_release=True,
                  freeze_status="frozen_truncated_D126", freeze_reason=REASON,
                  files=sorted(kept, key=lambda row: row["path"]), requested_vs_emitted=rows,
                  counts={split: sum(row["split"] == split for row in kept) for split in ("test", "showcase", "train", "val")},
                  active_counts={split: sum(row["split"] == split and row["active_for_current_release"] for row in kept)
                                 for split in ("test", "showcase", "train", "val")},
                  uncommitted_instance_files=uncommitted)
    frozen["requested_counts"].update(requested_counts)
    frozen["requested_counts_including_deferred"].update(requested_counts)
    frozen["release_epoch_counts"] = dict(Counter(row["release_epoch"] for row in kept))
    frozen["source_binding_counts"] = dict(Counter(row["source_binding_status"] for row in kept))
    frozen["schema_versions"] = sorted({row["schema_version"] for row in kept})
    frozen["generator_versions"] = {key: sorted({row["versions"][key] for row in kept if key in row["versions"]})
                                     for key in sorted({key for row in kept for key in row["versions"]})}
    frozen["seeds"] = {split: sorted(row["seed"] for row in kept if row["split"] == split) for split in frozen["counts"]}
    frozen["hashes"] = dict(sorted(hashes.items()))
    runtime_paths = {Path(path) for path in runtime_files} | {stop_evidence}
    runtime_paths.update((root / ".runtime/small").rglob("*.json"))
    runtime_hashes = {str(path.resolve()): sha(path) for path in sorted(runtime_paths) if path.is_file()}
    report = {"format": "stowlm-mvp-frozen-D126-v1", "frozen_at_utc": now, "reason": REASON,
              "status": "frozen_truncated", "complete": False, "counts": frozen["counts"],
              "active_counts": frozen["active_counts"], "counts_per_split_ship_rung_family": fitting_rows,
              "original_manifest_sha256": hashlib.sha256(original_bytes).hexdigest(),
              "requested_inventory_sha256": inventory_hashes,
              "requested_inventories_unchanged": True,
              "evaluation_policy": "metadata retained verbatim; evaluation files not opened, tested, or hashed",
              "source_epochs": epochs, "artifacts": artifacts, "runtime_file_sha256": runtime_hashes,
              "stop_evidence": {"path": str(stop_evidence.resolve()), "sha256": sha(stop_evidence), "content": stop},
              "producer_gates": {"artifacts_verified": len(artifacts), "accepted": True,
                                 "rule_freeze_v1_enabled": False, "c2_screened": False,
                                 "global_screen_evidence_counts": dict(Counter(
                                     item["runtime"]["global_screen_evidence"] for item in artifacts)),
                                 "D58_accepted_and_physics_hash_present": True,
                                 "new_checker_run": False},
              "uncommitted_instance_files": uncommitted,
              "limitations": ["The original audit's source and runtime declarations are retained; no current source relabeling.",
                              "Producer/D58 acceptance is not certification of a final loading plan."]}
    legacy_global_count = sum(item["runtime"]["global_screen_evidence"] == "not_recorded_legacy_tiny"
                              for item in artifacts)
    if legacy_global_count:
        report["limitations"].append(
            f"{legacy_global_count} legacy tiny audits explicitly record c2_screened=false but do not record global_screen; retained as null, not inferred zero.")
    return frozen, report, original_bytes


def execute(root, stop_evidence, runtime_files=()):
    started = time.monotonic()
    frozen, report, original_bytes = assemble(root, stop_evidence, runtime_files)
    root = Path(root)
    archive = root / "archive/D126/manifest.pre-D126.json"
    require(not archive.exists(), "pre-D126 manifest archive already exists")
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(original_bytes)
    report["original_manifest_archive"] = str(archive.relative_to(root))
    for split in ("train", "val"):
        entries = {path: digest for path, digest in frozen["hashes"].items() if path.startswith(split + "/")}
        sums_path = root / split / "SHA256SUMS"
        sums_path.parent.mkdir(parents=True, exist_ok=True)
        sums_path.write_text("".join(f"{digest}  {name.removeprefix(split + '/')}\n" for name, digest in sorted(entries.items())))
        frozen["hashes"][f"{split}/SHA256SUMS"] = sha(sums_path)
    frozen["hashes"] = dict(sorted(frozen["hashes"].items()))
    atomic_json(root / "manifest.json", frozen)
    report["manifest_sha256"] = sha(root / "manifest.json")
    report["fitting_hash_list_sha256"] = {split: sha(root / split / "SHA256SUMS") for split in ("train", "val")}
    report["elapsed_freeze_seconds"] = time.monotonic() - started
    atomic_json(root / "FROZEN-D126.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CORPUS)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--stop-evidence", type=Path, required=True,
                        help="Owner-created JSON requiring stopped=true, recorded after producer exits")
    parser.add_argument("--runtime-file", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"mode": "dry_run", "root": str(args.root), "stop_evidence": str(args.stop_evidence),
                          "policy": "only train/val verified; existing evaluation metadata copied", "reason": REASON}))
        return
    report = execute(args.root, args.stop_evidence, args.runtime_file)
    print(json.dumps({key: report[key] for key in ("counts", "active_counts", "manifest_sha256", "elapsed_freeze_seconds")}))


if __name__ == "__main__":
    main()
