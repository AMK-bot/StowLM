"""D199 fresh, manifest-bound SFT snapshots (CPU only; never a completion gate).

The existing SFT builder owns loading, full-report binding and mask admission.
Only current verified winners from the supplied manifest can reach that builder.
Each publication switches one symlink to a fresh immutable generation, so removed
or failed winners cannot survive a later export. Frozen 8.2 validation is copied
byte-for-byte, and its train-corpus identities are excluded before serialization.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
VAL = REPO / "experiments/mvp/sft/stowlm-v2/data/val.jsonl"
THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")
CACHE_SCHEMA = "stowlm9-sft-row-cache-v1"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_sha(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _repo_path(raw):
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (REPO / path).resolve()


def cache_binding(manifest, row_sha, entry, prompt_sha):
    return dict(schema=CACHE_SCHEMA, manifest_id=manifest["manifest_id"],
                outcome_epoch=manifest["outcome_epoch"], row_sha256=row_sha,
                input_sha256=entry["input_sha256"], instance_id=entry["instance_id"],
                prompt_source_sha256=prompt_sha, producer_sources_sha256=_json_sha(manifest.get("sources", {})))


def read_cached_example(path, binding):
    """A cache hit is serialized replay evidence, never an admission decision."""
    from llm.stowlm.sft_data import SFTExample, example_sha256, mvp_row_failure

    try:
        saved = json.loads(path.read_bytes())
        payload = saved["payload"]
        if (saved.get("binding") != binding or saved.get("payload_sha256") != _json_sha(payload)):
            return None
        example = SFTExample(**payload["example"])
        if (saved.get("example_sha256") != example_sha256(example) or mvp_row_failure(example) is not None or
                example.instance_id != binding["instance_id"] or example.split != "train" or
                example.meta.get("gold", {}).get("input_sha256") != binding["input_sha256"] or
                example.meta.get("gold", {}).get("manifest_id") != binding["manifest_id"] or
                example.meta.get("gold", {}).get("outcome_epoch") != binding["outcome_epoch"]):
            return None
        tokens = {tuple(token) for token in payload["identity_tokens"]}
        if (("instance_id", example.instance_id) not in tokens or
                ("sha256", binding["input_sha256"]) not in tokens):
            return None
        return example, tokens
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_cached_example(path, binding, example, tokens):
    from llm.stowlm.sft_data import example_sha256, mvp_row_failure

    if (failure := mvp_row_failure(example)) is not None:
        raise ValueError(f"cannot cache unverified SFT row: {failure}")
    payload = dict(example=asdict(example), identity_tokens=sorted(tokens))
    saved = dict(binding=binding, payload=payload, payload_sha256=_json_sha(payload),
                 example_sha256=example_sha256(example))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(saved, sort_keys=True, allow_nan=False))
    os.replace(temporary, path)


def _assert_epoch(expected):
    from llm.exit import outcome_epoch

    if outcome_epoch.process_snapshot() != expected:
        raise ValueError("export process loaded outcome epoch differs")
    outcome_epoch.assert_current(expected)


def validation_snapshot(path):
    """Frozen row bytes and identity tokens; never opens a held-out map body."""
    from llm.exit.identity import IdentityGuard

    raw = Path(path).read_bytes()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) != 80 or any(row.get("split") != "val" for row in rows):
        raise ValueError("frozen validation must contain exactly 80 val rows")
    tokens = set()
    for row in rows:
        gold = row.get("meta", {}).get("gold", {})
        if not row.get("instance_id") or not gold.get("input_sha256"):
            raise ValueError("validation row lacks input identity")
        tokens |= {("instance_id", str(row["instance_id"])), ("sha256", gold["input_sha256"])}
        # Forty historical val rows were held out of corpus/train. Read their
        # original train input to exclude equivalent reformattings/root seeds.
        source = gold.get("source")
        if source:
            record_path = _repo_path(source)
            if not record_path.is_file():
                raise ValueError(f"validation provenance record missing: {source}")
            record = json.loads(record_path.read_bytes())
            input_path = record.get("instance_path") or record.get("input_path")
            if input_path:
                input_path = _repo_path(input_path)
                if input_path.is_relative_to((REPO / "experiments/mvp/corpus/train").resolve()):
                    from stowage.io import load_instance

                    input_raw = input_path.read_bytes()
                    if _sha(input_raw) != gold["input_sha256"]:
                        raise ValueError("frozen validation source input changed")
                    tokens |= IdentityGuard.tokens(load_instance(input_path), _sha(input_raw))
    return raw, rows, tokens


def admitted_rows(manifest, stream, root):
    """Only exact current-manifest complete winners; reject bad rows atomically."""
    if stream not in ("rule", "frontier"):
        raise ValueError("stream must be rule or frontier")
    if manifest.get("schema") != "stowlm9-manifest-v1" or not manifest.get("manifest_id") or not manifest.get("outcome_epoch"):
        raise ValueError("missing admitted manifest/epoch")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_id"}
    if _sha(json.dumps(unsigned, sort_keys=True).encode()) != manifest["manifest_id"]:
        raise ValueError("manifest content binding differs")
    entries = manifest.get("entries", [])
    if stream == "rule":
        limit = manifest.get('config', {}).get('rule_entry_limit',1700)
        entries = entries if limit is None else entries[:limit]
    keys = [entry["key"] for entry in entries]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate manifest keys")
    admitted, counts, snapshots = [], Counter(), []
    known = set(keys)
    # Historical rows outside the current manifest are counted, never discovered
    # as candidates by the existing recursive builder.
    counts["ignored_stale_files"] = sum(p.stem not in known for p in (Path(root) / stream / "rows").glob("*.json"))
    for entry in entries:
        key = entry["key"]
        if Path(key).name != key or key in (".", ".."):
            raise ValueError("unsafe manifest key")
        path = Path(root) / stream / "rows" / f"{key}.json"
        if not path.exists():
            counts["pending"] += 1
            continue
        raw = path.read_bytes()
        row = json.loads(raw)
        binding = dict(manifest_id=manifest["manifest_id"], input_sha256=entry["input_sha256"],
                       baseline_record_sha256=entry.get("baseline_record_sha256"), stream=stream)
        if (row.get("key") != key or row.get("manifest_id") != manifest["manifest_id"] or
                row.get("outcome_epoch") != manifest["outcome_epoch"] or row.get("binding") != binding or
                row.get("input_sha256") != entry["input_sha256"] or row.get("input") != entry["input"]):
            raise ValueError(f"stale epoch/input/candidate binding: {key}")
        snapshots.append(dict(key=key, row_sha256=_sha(raw), status=row.get("status")))
        issues = []
        for candidate in row.get("candidates", []):
            error = candidate.get("error") or candidate.get("check", {}).get("error")
            if error:
                issues.append(dict(source=candidate.get("source"), error=error))
                if any(word in error.lower() for word in ("canonical", "one-placement", "one placement", "final stow", "expanded label")):
                    counts["label_consistency_rejects"] += 1
        if issues:
            snapshots[-1]["candidate_issues"] = issues
        if row.get("status") == "error":
            counts["errors"] += 1
            continue
        if row.get("status") != "complete":
            counts["incomplete"] += 1
            continue
        finished = row.get("finished_at")
        if not isinstance(finished, (int, float)) or not math.isfinite(finished) or finished > time.time():
            raise ValueError(f"invalid/future completion timestamp: {key}")
        if stream == "rule" and manifest.get("config", {}).get("rule_stop_at_deadline", True) and finished > datetime.fromisoformat(manifest["rule_deadline"]).timestamp():
            counts["after_deadline"] += 1
            continue
        counts["complete"] += 1
        if row.get("passed") is not True:
            counts["no_teacher"] += 1
            continue
        ipath = _repo_path(entry["input"])
        if not ipath.is_relative_to((REPO / "experiments/mvp/corpus/train").resolve()):
            raise ValueError(f"non-train input: {key}")
        if _sha(ipath.read_bytes()) != entry["input_sha256"]:
            raise ValueError(f"input bytes changed: {key}")
        if entry.get("baseline_record"):
            if _sha(_repo_path(entry["baseline_record"]).read_bytes()) != entry.get("baseline_record_sha256"):
                raise ValueError(f"baseline candidate bytes changed: {key}")
        record = row.get("record") or {}
        if (_repo_path(record.get("instance_path", "")) != ipath or
                record.get("outcome_epoch") != manifest["outcome_epoch"] or
                record.get("report", {}).get("passed") is not True or
                record.get("mask", {}).get("passed") is not True or
                record.get("canonical_version") != "stowlm9-final-placement-v1"):
            raise ValueError(f"winner lacks fresh canonical checker/mask evidence: {key}")
        score = row.get("moves")
        if (isinstance(score, bool) or not isinstance(score, (int, float)) or
                not math.isfinite(score) or score < 0 or record.get("weighted_moves") != score):
            raise ValueError(f"winner cost binding differs: {key}")
        if row.get("source") not in (stream, "best-d172"):
            raise ValueError(f"wrong winner source: {key}")
        if record.get("actions_sha256") != _json_sha(record.get("plan", {}).get("actions")):
            raise ValueError(f"canonical action binding differs: {key}")
        from llm.exit.checker_watchdog import _validate_epoch_ack
        from llm.exit.outcome_epoch import epoch_id

        watched = record.get("checker_watchdog") or {}
        ack = watched.get("epoch_ack") or {}
        expected = ack.get("loaded_pre")
        if (watched.get("available") is not True or watched.get("epoch_ack_error") or
                not expected or epoch_id(expected) != manifest["outcome_epoch"] or
                _validate_epoch_ack(watched, expected, ack.get("request_id")) is not None):
            raise ValueError(f"winner lacks guarded consumed-byte epoch acknowledgement: {key}")
        admitted.append((entry, row))
    return admitted, counts, snapshots


def export_stream(manifest_path, stream, *, root=HERE, val_path=None):
    """Publish available passing winners; return honest partial-coverage metrics.

    `root` is the stowlm9 run directory, not repository root. No COMPLETE or
    EXPORT.DONE marker is written here: the controller owns deadline lifecycle.
    """
    for var in THREAD_VARS:
        os.environ[var] = "1"
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / f".{stream}-export.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _export_locked(Path(manifest_path), stream, root, Path(val_path or VAL))


def _export_locked(manifest_path, stream, root, val_path):
    started = time.monotonic()
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    for name, digest in manifest.get("sources", {}).items():
        if _sha(_repo_path(name).read_bytes()) != digest:
            raise ValueError(f"export source changed: {name}")
    admitted, counts, row_snapshots = admitted_rows(manifest, stream, root)
    expected_epoch = admitted[0][1]["record"]["checker_watchdog"]["epoch_ack"]["loaded_pre"] if admitted else None
    if expected_epoch is not None:
        _assert_epoch(expected_epoch)
    val_raw, val_rows, val_tokens = validation_snapshot(val_path)
    if manifest.get("val_sha256") and manifest["val_sha256"] != _sha(val_raw):
        raise ValueError("validation differs from pinned manifest")
    from llm.exit.identity import IdentityGuard
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.sft_data import SFTExample, build_mvp_example, length_stats, plan_actions, write_dataset
    from stowage.actions.space import Place

    builder = _module("_stowlm9_existing_sft_builder", REPO / "experiments/mvp/sft/dataset/build.py")
    prompt = _module("_stowlm9_export_prompt", HERE / "prompt_v9.py")
    prompt.install()
    tokenizer = load_tokenizer()
    prompt_sha = _sha((HERE / "prompt_v9.py").read_bytes())
    row_hashes = {row["key"]: row["row_sha256"] for row in row_snapshots}
    tag = f"data-{'85' if stream == 'rule' else '86'}"
    generations = root / ".exports"
    generations.mkdir(exist_ok=True)
    stage = generations / f"{tag}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    stage.mkdir()
    examples, kept_rows, refusals, seen, pending_cache = [], [], [], set(), []
    published = False
    try:
        for entry, row in admitted:
            record_path = stage / "records" / entry["key"] / "record.json"
            record_path.parent.mkdir(parents=True)
            record_path.write_text(json.dumps(row["record"], sort_keys=True, allow_nan=False))
            binding = cache_binding(manifest, row_hashes[entry["key"]], entry, prompt_sha)
            cache_path = root / ".sft-cache" / CACHE_SCHEMA / f"{_json_sha(binding)}.json"
            cached = read_cached_example(cache_path, binding)
            if cached is not None:
                example, tokens = cached
                counts["sft_cache_hits"] += 1
            else:
                counts["sft_cache_misses"] += 1
                instance, plan, report, input_sha, ipath = builder.load_triple(record_path)
                tokens = IdentityGuard.tokens(instance, input_sha)
                if instance.id != entry["instance_id"] or input_sha != entry["input_sha256"]:
                    raise ValueError(f"loaded identity differs from manifest: {entry['key']}")
                actions = plan_actions(instance, plan)
                if (not all(isinstance(action, Place) for action in actions) or
                        len(actions) != len(instance.load_list) or
                        {action.box for action in actions} != set(instance.load_list)):
                    raise ValueError(f"not one placement per box: {entry['key']}")
                if not tokens & val_tokens:
                    # Cache misses alone run the existing builder's expensive
                    # full-report binding and mask replay, with no legacy cache.
                    source = str(record_path.relative_to(REPO)) if record_path.is_relative_to(REPO) else str(record_path)
                    example = build_mvp_example(instance, plan, report, split="train", source=source, tokenizer=tokenizer)
                    example.meta["gold"].update(input_sha256=input_sha, teacher_source=row["source"],
                                                 outcome_epoch=manifest["outcome_epoch"], manifest_id=manifest["manifest_id"])
                    example.meta["rung"] = instance.size_class
                    if len(example.input_ids) <= builder.MAX_LEN:
                        pending_cache.append((cache_path, binding, example, tokens))
            if tokens & val_tokens:
                counts["excluded_frozen_val"] += 1
                record_path.unlink()
                continue
            if tokens & seen:
                raise ValueError(f"duplicate train identity: {entry['key']}")
            if len(example.input_ids) > builder.MAX_LEN:
                refusals.append(dict(key=entry["key"], reason="over_length", tokens=len(example.input_ids)))
                counts["over_length"] += 1
                record_path.unlink()
                continue
            examples.append(example)
            kept_rows.append(row)
            seen |= tokens
        if examples:
            stats = write_dataset(examples, stage, mvp=True, required_splits=["train"])
        else:
            (stage / "train.jsonl").write_bytes(b"")
            from llm.stowlm.sft_data import DATASET_VERSION, MVP_GOLD
            from llm.stowlm.format import FORMAT_VERSION
            from stowage.actions import ACTION_SPACE_VERSION
            stats = dict(dataset=DATASET_VERSION, format=FORMAT_VERSION, action_space=ACTION_SPACE_VERSION,
                         dataset_kind=MVP_GOLD, splits={}, replay={}, training_ready=False)
        # Validation is historical evidence: preserve its bytes and versions.
        (stage / "val.jsonl").write_bytes(val_raw)
        val_examples = [SFTExample(**row) for row in val_rows]
        versions = sorted({example.meta["prompt"] for example in [*examples, *val_examples]})
        stats.update(prompt=versions[0] if len(versions) == 1 else None, prompt_versions=versions,
                     training_prompt=prompt.PROMPT_VERSION, frozen_val_sha256=_sha(val_raw),
                     frozen_val_prompt_versions=sorted({row["meta"]["prompt"] for row in val_rows}),
                     training_ready=bool(examples))
        stats["splits"]["val"] = length_stats(val_examples)
        counts["train"] = len(examples)
        counts["val"] = len(val_rows)
        paired = [row for row in kept_rows if row.get("baseline_moves") is not None]
        mean = lambda values: sum(values) / len(values) if values else None
        report = dict(schema="stowlm9-export-v1", stream=stream, manifest_id=manifest["manifest_id"],
                      manifest_sha256=_sha(manifest_raw), outcome_epoch=manifest["outcome_epoch"],
                      partial=True, manifest_maps=len(manifest["entries"]) if stream != "rule" or manifest.get("config", {}).get("rule_entry_limit",1700) is None else min(manifest.get("config", {}).get("rule_entry_limit",1700), len(manifest["entries"])), counts=dict(counts),
                      source_counts=dict(Counter(row["source"] for row in kept_rows)),
                      by_size={size: length_stats([e for e in examples if e.meta["rung"] == size])
                               for size in sorted({e.meta["rung"] for e in examples})},
                      token_lengths={"train": length_stats(examples), "val": length_stats(val_examples)},
                      paired_best_d172=dict(maps=len(paired), winner_mean_moves=mean([r["moves"] for r in paired]),
                                           baseline_mean_moves=mean([r["baseline_moves"] for r in paired])),
                      mean_moves=mean([row["moves"] for row in kept_rows]),
                      pass_rate=counts["complete"] and len(admitted) / counts["complete"] or None,
                      label_consistency=dict(checked=len(examples), one_placement_per_box=True,
                                             deterministic_canonical_version="stowlm9-final-placement-v1",
                                             rejected_candidates=counts["label_consistency_rejects"],
                                             builder_mask_replay="all admitted rows; no repair"),
                      frozen_val=dict(path=str(val_path), sha256=_sha(val_raw), byte_identical=True,
                                      train_identity_overlap=0, rows=80),
                      rows=row_snapshots, refusals=refusals, wall_s=round(time.monotonic() - started, 3),
                      serialized_row_cache=dict(schema=CACHE_SCHEMA, path=str(root / ".sft-cache" / CACHE_SCHEMA),
                                                hits=counts["sft_cache_hits"], misses=counts["sft_cache_misses"],
                                                legacy_cache_used=False),
                      output=str(root / tag), generation=str(stage))
        (stage / "stats.json").write_text(json.dumps(stats, indent=2, allow_nan=False) + "\n")
        (stage / "build_report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        (stage / "admitted_manifest.json").write_bytes(manifest_raw)
        if manifest_path.read_bytes() != manifest_raw or val_path.read_bytes() != val_raw:
            raise ValueError("manifest or frozen validation changed during export")
        if expected_epoch is not None:
            _assert_epoch(expected_epoch)
        for name, digest in manifest.get("sources", {}).items():
            if _sha(_repo_path(name).read_bytes()) != digest:
                raise ValueError(f"export source changed during replay: {name}")
        target = root / tag
        if target.exists() and not target.is_symlink():
            raise ValueError("atomic export target must be absent or an existing generation symlink")
        temporary = root / f".{tag}-{uuid.uuid4().hex}.tmp"
        temporary.symlink_to(stage.relative_to(root), target_is_directory=True)
        os.replace(temporary, target)
        published = True
        # Cache evidence only after every source/epoch gate and publication.
        # Gold source paths therefore always refer to a retained generation.
        for arguments in pending_cache:
            write_cached_example(*arguments)
        # Readers needing the current report should follow data-85/data-86.
        # This convenient stream copy is independently atomic and points back
        # to that generation; it is never a success/completion receipt.
        metrics_path = root / stream / "export-metrics.json"
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_tmp = metrics_path.with_name(f".export-metrics-{uuid.uuid4().hex}.tmp")
        metrics_tmp.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        os.replace(metrics_tmp, metrics_path)
        return report
    except BaseException:
        if not published:
            shutil.rmtree(stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--stream", choices=("rule", "frontier"), required=True)
    parser.add_argument("--root", type=Path, default=HERE)
    parser.add_argument("--val", type=Path, default=VAL)
    args = parser.parse_args()
    result = export_stream(args.manifest, args.stream, root=args.root, val_path=args.val)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
