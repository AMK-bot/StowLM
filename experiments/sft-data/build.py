"""Serialize only published task-6.1 gold; no solver, GPU, or cloud calls.

The CLI checks frozen membership before reading a record, and byte hashes before
parsing record/input files. The shared builder then enforces checker/D50 lineage
and independently replays every completion through the production mask.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

from eval.splits import load_inventory, spec_key
from eval.snapshot_io import read_snapshot
from eval.splits_epoch import is_epoch_manifest, validate_epoch_instance, validate_epoch_manifest
from llm.stowlm import sft_data
from stowage.io import load_instance
from stowage.model import MovePlan

write_dataset = sft_data.write_dataset


def admit_gold(*args, **kwargs):
    return sft_data.build_gold_example(*args, **kwargs)


def parse_plan(raw):
    return MovePlan.model_validate(raw)


def current_gold_epoch():
    """Use the producer's source inventory rather than duplicating its policy."""
    name = "stowlm_sft_gold_source_epoch"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "gold-plans/tiny/driver.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].source_fingerprint()["sha256"]


def _contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("publication paths must be relative")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError("publication path escapes its root")
    return target


def _atomic_json(path: Path, value):
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _epoch(root: Path, inventory, *, required=False, snapshot=None):
    path = root / "manifest.json"
    snapshot = snapshot or (read_snapshot(path) if path.is_file() else None)
    manifest = snapshot.json() if snapshot is not None else {}
    if required or is_epoch_manifest(root, manifest):
        validate_epoch_manifest(root, manifest, inventory)
        return manifest
    return None


def _finished_dataset(out: Path, contract):
    marker = out / "build.done.json"
    if not marker.exists():
        return None
    done = read_snapshot(marker).json()
    if done.get("format") != "sft-build-done@1.0" or done.get("contract") != contract:
        raise ValueError("dataset resume input/source drift; use a new --out directory")
    files = done.get("files", {})
    if set(files) != {"stats.json", *(f"{s}.jsonl" for s in contract["required_splits"])}:
        raise ValueError("dataset resume marker has invalid output membership")
    snapshots = {}
    for name, digest in files.items():
        path = _contained(out, name)
        if not path.is_file():
            raise ValueError(f"dataset resume output drift: {name}")
        snapshots[name] = read_snapshot(path)
        if snapshots[name].sha256 != digest:
            raise ValueError(f"dataset resume output drift: {name}")
    for split in {"train", "val", "test"} - set(contract["required_splits"]):
        if (out / f"{split}.jsonl").exists():
            raise ValueError(f"dataset resume unexpected split: {split}")
    return snapshots['stats.json'].json()


def build_dataset(gold_manifest: str | Path, instances_root: str | Path, out: str | Path, *,
                  split_manifest: str | Path | None = None,
                  required_splits=("train", "val", "test"), tokenizer=None,
                  resume=False, require_epoch=False, g1_summary=None, production=None):
    """Admit every requested row before writing; reject failures, never drop rows.

    An explicit subset is useful for a tiny pipeline run; it does not establish
    campaign-wide train/validation/test coverage. Empty campaigns never qualify.
    Writer replacement is per file with statistics last, not a directory-wide
    transaction; this adapter guarantees admission completes before writing.
    """
    required_splits = tuple(required_splits)
    if not required_splits or len(set(required_splits)) != len(required_splits) or any(
            split not in ("train", "val", "test") for split in required_splits):
        raise ValueError("required_splits must be distinct supported split names")
    inventory = load_inventory(split_manifest)
    if inventory.version != "v2":
        raise ValueError("production SFT requires frozen splits v2")
    pin = {"split_version": inventory.version, "split_entries_sha256": inventory.entries_sha256}
    source = Path(gold_manifest).resolve()
    manifest_snapshot = read_snapshot(source)
    manifest_bytes, manifest = manifest_snapshot.data, manifest_snapshot.json()
    if manifest.get("diagnostic") is True or manifest.get("evidence_eligible") is False:
        raise ValueError("diagnostic gold cannot produce a production SFT dataset")
    if manifest.get("format") != "tiny-gold-manifest@1.0" or manifest.get("c2") != "on/default":
        raise ValueError("published production gold manifest required")
    if any(manifest.get(key) != value for key, value in pin.items()):
        raise ValueError("gold manifest differs from selected frozen inventory release")
    entries = manifest.get("shards")
    if not isinstance(entries, list) or not entries:
        raise ValueError("no published gold; run the clean task-6.1 campaign first")
    if type(manifest.get("accepted")) is not int or manifest["accepted"] != len(entries):
        raise ValueError("gold publication count mismatch")
    if manifest.get("relevant_source_sha256") != current_gold_epoch():
        raise ValueError("gold publication source epoch differs from current task-6.1 producer")
    instances_root = Path(instances_root).resolve()
    epoch_path = instances_root/'manifest.json'
    epoch_snapshot = read_snapshot(epoch_path) if epoch_path.is_file() else None
    epoch = _epoch(instances_root, inventory, required=require_epoch, snapshot=epoch_snapshot)
    members = {spec_key(row["spec"]): row for row in inventory.manifest["entries"]}
    selected = []
    seen = set()
    for entry in entries:
        member = members.get(entry.get("spec_key"))
        if member is None or entry.get("split") != member["split"]:
            raise ValueError("gold shard differs from selected frozen inventory membership/label")
        if any(entry.get(key) != value for key, value in pin.items()):
            raise ValueError("gold shard differs from selected frozen inventory release")
        if entry["spec_key"] in seen:
            raise ValueError("duplicate gold frozen specification")
        seen.add(entry["spec_key"])
        if entry["split"] in required_splits:
            selected.append(entry)
    missing = set(required_splits) - {row["split"] for row in selected}
    if missing:
        raise ValueError(f"missing required splits: {', '.join(sorted(missing))}")
    rungs = sorted({members[entry['spec_key']]['spec']['rung'] for entry in selected})
    epoch_id = (epoch or {}).get('epoch_id', manifest.get('epoch_id'))
    approval_snapshot = read_snapshot(g1_summary) if g1_summary is not None else None
    approval = (approval_snapshot.json() if approval_snapshot else manifest.get('g1_approval') or
                (manifest.get('g1_summary') or {}).get('approval'))
    policy_pin = manifest.get('required_rules')
    production_eligible = approval is not None
    if production_eligible:
        if epoch is None or manifest.get('epoch_id') != epoch_id:
            raise ValueError('production_gold requires a same-epoch frozen instance manifest and G1 approval')
        frozen_pin = (epoch.get('freeze_manifest') or {}).get('required_rules')
        if not isinstance(policy_pin, dict) or frozen_pin != policy_pin:
            raise ValueError('gold required_rules differ from frozen instance epoch')
        policy = sft_data.gold_rule_policy()
        policy.enforce_rule_policy(Path(__file__).resolve().parents[2], policy_pin)
        approval = policy.validate_g1_approval(approval, epoch_id, rungs)
        policy._module('migration_evidence').assert_component_epoch({'component_epoch':manifest.get('component_epoch')})
    if production is True and not production_eligible:
        raise ValueError('pipeline_test cannot produce production_gold without same-epoch G1 approval')
    production_eligible = production_eligible and production is not False
    data_mode = 'production' if production_eligible else 'pipeline_test'
    prepared = []
    for entry in selected:
        raw = _contained(source.parent, entry["path"]).read_bytes()
        digest = sha256(raw).hexdigest()
        if digest != entry.get("sha256"):
            raise ValueError("gold record SHA256 mismatch")
        record = json.loads(raw)
        if production_eligible and record.get('epoch_id') != epoch_id:
            raise ValueError('gold record epoch differs from same-epoch G1 approval')
        if production_eligible and record.get('component_epoch')!=manifest.get('component_epoch'):
            raise ValueError('M5_GOLD_COMPONENT_MISMATCH: gold record/publication component epochs differ')
        if record.get("input_sha256") != entry.get("input_sha256"):
            raise ValueError("gold record input SHA256 differs from publication")
        # Input identity is checked before loading model objects; the shared
        # admission also binds the loaded content to the full checker report.
        input_path = _contained(Path(instances_root), record["input_relative_path"])
        input_bytes = input_path.read_bytes()
        if sha256(input_bytes).hexdigest() != entry["input_sha256"]:
            raise ValueError("input SHA256 mismatch")
        if epoch is not None:
            binding = epoch["bindings"].get(entry["spec_key"])
            if (binding is None or binding["instance"] != input_path.relative_to(instances_root).as_posix()
                    or binding["sha256"] != entry["input_sha256"] or binding["split"] != entry["split"]):
                raise ValueError("gold input differs from frozen epoch binding")
        prepared.append((entry, record, raw, input_path, input_bytes))
    contract = {"manifest_sha256": sha256(manifest_bytes).hexdigest(), "instances_root": str(instances_root),
                "source_sha256": manifest["relevant_source_sha256"], "required_splits": sorted(required_splits),
                "builder_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                "epoch_manifest_sha256": epoch_snapshot.sha256
                if epoch is not None else None, 'data_mode': data_mode, 'g1_approval': approval,
                'required_rules': policy_pin, 'g1_summary_sha256': approval_snapshot.sha256 if approval_snapshot else None, **pin}
    out = Path(out).resolve()
    if resume and (finished := _finished_dataset(out, contract)) is not None:
        return finished
    examples = []
    by_rung = {}
    for entry, record, raw, input_path, input_bytes in prepared:
        digest = sha256(raw).hexdigest()
        instance = (validate_epoch_instance(input_path, epoch["bindings"][entry["spec_key"]])
                    if epoch is not None else load_instance(input_path))
        plan = parse_plan(record["plan"])
        example = admit_gold(instance, plan, split=entry["split"], gold_record=record,
                             publication={**manifest, "entry": entry, "record_sha256": digest,
                                          "record_bytes": raw, "input_bytes": input_bytes,
                                          'epoch_id': epoch_id, 'data_mode': data_mode,
                                          'g1_approval': approval if production_eligible else None,
                                          'required_rules': policy_pin},
                             tokenizer=tokenizer, split_manifest=split_manifest)
        examples.append(example)
        rung = members[entry["spec_key"]]["spec"]["rung"]
        by_rung.setdefault(rung, {}).setdefault(entry["split"], []).append(example)
    if manifest.get("relevant_source_sha256") != current_gold_epoch():
        raise ValueError("gold source epoch changed during serialization")
    try:
        manifest_snapshot.require_fresh()
        if approval_snapshot:
            approval_snapshot.require_fresh()
    except ValueError as exc:
        raise ValueError('gold publication or G1 approval changed during serialization') from exc
    if epoch is not None:
        epoch_snapshot.require_fresh()
        _epoch(instances_root, inventory, required=True, snapshot=epoch_snapshot)
    for entry, _, _, input_path, _ in prepared:
        read_snapshot(input_path, expected_sha256=entry['input_sha256'])
    if production_eligible:
        policy=sft_data.gold_rule_policy()
        policy.enforce_rule_policy(Path(__file__).resolve().parents[2], policy_pin)
        policy.validate_g1_approval(approval,epoch_id,rungs)
        policy._module('migration_evidence').assert_component_epoch({'component_epoch':manifest.get('component_epoch')})
    stats = write_dataset(examples, out, production=production_eligible, required_splits=required_splits)
    if "splits" in stats:
        stats["rungs"] = {rung: {"all": sft_data.length_stats([e for rows in splits.values() for e in rows]),
                                 "splits": {split: sft_data.length_stats(rows) for split, rows in splits.items()}}
                          for rung, splits in sorted(by_rung.items())}
        stats["acceptance"] = {"published_selected": len(selected), "accepted": len(examples),
                               "rejected": 0, "mask_replay_rate": 1.0}
        stats["epoch_id"] = epoch["epoch_id"] if epoch else None
        stats['dataset_kind'] = 'production_gold' if production_eligible else 'pipeline_test'
        stats['data_mode'] = data_mode
        stats['g1_approval'] = approval if production_eligible else None
        stats['required_rules'] = policy_pin
        _atomic_json(out / "stats.json", stats)
    if resume:
        files = {name: sha256((out / name).read_bytes()).hexdigest()
                 for name in ["stats.json", *(f"{s}.jsonl" for s in required_splits)]}
        _atomic_json(out / "build.done.json", {"format": "sft-build-done@1.0", "contract": contract, "files": files})
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-manifest", type=Path, required=True)
    parser.add_argument("--instances-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, default=None,
                        help="trusted splits v2 release; defaults to current v2")
    parser.add_argument("--required-splits", choices=("train", "val", "test"), nargs="+",
                        default=["train", "val", "test"])
    parser.add_argument("--resume", action="store_true", help="reuse only a completed, hash-verified matching build")
    parser.add_argument("--require-epoch", action="store_true", help="require shared frozen-epoch validation")
    parser.add_argument('--g1-summary', type=Path, help='same-epoch leader G1 approval with filled min_yield')
    parser.add_argument('--production', action='store_true', default=None,
                        help='refuse pipeline-test output; require approved production lineage')
    args = parser.parse_args()
    try:
        stats = build_dataset(args.gold_manifest, args.instances_root, args.out,
                              split_manifest=args.split_manifest, required_splits=args.required_splits,
                              resume=args.resume, require_epoch=args.require_epoch,
                              g1_summary=args.g1_summary, production=args.production)
    except (ValueError, RuntimeError, OSError) as error:
        print(f"SFT publication refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
