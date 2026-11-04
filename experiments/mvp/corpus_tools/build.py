"""Resumable, switch-OFF MVP corpus; instance + audit is one atomic shard.

APPROXIMATION: MVP partitions replace the historical held-out-family policy.
All ships and both yard families occur in fitting and evaluation, with disjoint
root seeds. This measures familiar-family generalisation, not unseen-ship transfer.
The generator's necessary screens and independent D58 witness remain enabled;
an accepted instance is not a certified loading plan.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import inspect
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import resource
import time

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "experiments/mvp/corpus"
POLICY = "mvp-v1-seed-disjoint-all-ships-all-rungs"
# APPROXIMATION: disjoint draws of familiar ship/family combinations replace
# the legacy unseen-combination holdout; no unseen-ship transfer claim follows.
VESSELS = ("feeder_500", "feeder_800", "feeder_1700")
FAMILIES = ("random_yard", "simulated_yard")
RUNGS = ("tiny", "small", "medium")
SEED_BASES = {"test": 8_000_000, "train": 10_000_000, "val": 20_000_000}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_snapshot():
    """Bind code/parameters; numerical-cache identity is retained by D58 itself."""
    package = Path(next(iter(importlib.util.find_spec("stowage").submodule_search_locations)))
    return {str(p.relative_to(ROOT)): sha256(p)
            for directory in (package, ROOT / "params")
            for p in sorted(directory.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json", ".toml"}}


def booking_slack_kwargs(producer, setting, mass_fraction=None):
    """Explicit opt-ins; reject unsupported controls rather than silently dropping."""
    result = {}
    for name, value in (("booking_slack", setting), ("booking_mass_fraction", mass_fraction)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value < 1:
            raise ValueError(f"{name} must be a fraction in [0, 1)")
        if name not in inspect.signature(producer).parameters:
            raise ValueError(f"{name} producer API not available; see solve47/slack/STATUS.md")
        result[name] = float(value)
    return result


def booking_settings(booking_slack=None, booking_mass_fraction=None, ship_settings=None):
    """Normalize per-ship controls supplied as a mapping, JSON, or JSON file."""
    if isinstance(ship_settings, (str, Path)):
        value = str(ship_settings)
        ship_settings = json.loads(value) if value.lstrip().startswith("{") else json.loads(Path(value).read_text())
    if ship_settings is None:
        if booking_slack is None and booking_mass_fraction is None:
            return {}
        ship_settings = {v: {} for v in VESSELS}
    if not isinstance(ship_settings, dict) or not ship_settings:
        raise ValueError("ship settings must be a nonempty ship-to-controls mapping")
    normalized = {}
    for vessel, row in ship_settings.items():
        if vessel not in VESSELS or not isinstance(row, dict):
            raise ValueError(f"invalid ship settings: {vessel}")
        unknown = set(row) - {"booking_slack", "booking_mass_fraction"}
        if unknown:
            raise ValueError(f"unknown booking controls: {sorted(unknown)}")
        controls = {"booking_slack": row.get("booking_slack", booking_slack),
                    "booking_mass_fraction": row.get("booking_mass_fraction", booking_mass_fraction)}
        for name, value in controls.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value < 1):
                raise ValueError(f"{name} must be a fraction in [0, 1)")
        if controls["booking_slack"] is None:
            raise ValueError(f"booking_slack required for {vessel}")
        normalized[vessel] = {k: float(v) for k, v in controls.items() if v is not None}
    return normalized


@contextmanager
def generation_environment():
    """D84 study generated backgrounds with legacy ballast, then finalized with V4.

    APPROXIMATION: separate generator and solver ballast stages reproduce the
    qualified study; a generator acceptance is never a checked loading plan.
    """
    inherited = os.environ.pop("STOWAGE_BALLAST_V4", None)
    try:
        yield {"generation": {"STOWAGE_BALLAST_V4": None},
               "finalization": {"STOWAGE_BALLAST_V4": "1"}}
    finally:
        if inherited is not None:
            os.environ["STOWAGE_BALLAST_V4"] = inherited
        else:
            os.environ.pop("STOWAGE_BALLAST_V4", None)


def specs_for(split, counts):
    """Five instances per ship/rung for test, alternating the odd family slot."""
    if split not in SEED_BASES:
        raise ValueError(split)
    specs = []
    for ri, rung in enumerate(RUNGS):
        for i in range(counts.get(rung, 0)):
            ship_index = i % 3
            # Every complete group of six covers the full ship x family grid.
            family_index = ((i // 3) + ship_index + ri) % 2
            specs.append(dict(split=split, rung=rung, vessel=VESSELS[ship_index],
                generator=FAMILIES[family_index], seed=SEED_BASES[split] + ri * 100_000 + i,
                port="laem_chabang_like" if i % 4 < 2 else "singapore_like",
                noise=(0.2, 0.5, 0.8)[(i // 6) % 3], n_boxes=None,
                dense_20ft=False, tiny_dg_enabled=True, max_attempts=100))
    return specs


def shard_path(spec, root=CORPUS):
    return Path(root) / spec["split"] / spec["rung"] / spec["vessel"] / spec["generator"] / f"seed-{spec['seed']:09d}.json"


def validate_shard(path, *, current_sources=None):
    from stowage.frozen import require_clean
    from stowage.generate.background_acceptance import requires_background_gate
    from stowage.io import load_instance
    audit_path = path.with_suffix(".audit.json")
    audit = json.loads(audit_path.read_text())
    assert sha256(path) == audit["instance_sha256"], path
    assert audit["accepted"] is True and audit["rule_freeze_v1_enabled"] is False, path
    instance = load_instance(path)
    require_clean(instance, what="MVP corpus validation")
    spec = audit["config"]
    assert instance.seed == spec["seed"] and instance.size_class == spec["rung"]
    assert len(instance.load_list) == audit["requested_targets"]
    assert instance.voyage is not None
    if requires_background_gate(instance.vessel, size=instance.size_class):
        assert audit["background_checks"] > 0
        assert audit["background_last"]["accepted"] is True
        assert audit["background_last"]["physics_sha256"]
    if current_sources is not None:
        assert audit["source_files"] == current_sources, f"source drift: {path}"
    return audit, instance


def generate_one(job):
    spec, root, admitted_sources = job
    atomic_json(Path(root) / ".runtime" / f"worker-{os.getpid()}.json",
                dict(pid=os.getpid(), owner="mvp-corpus", registered_at_utc=datetime.now(timezone.utc).isoformat()))
    from stowage.frozen import require_clean
    from stowage.generate.instances import GenerationReport, generate_simulated_instance
    from stowage.generate.instances_v2_random import generate_random_v2
    from stowage.generate.instances_v2 import generator_components
    from stowage.io import load_instance, save_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.versions import SCHEMA_VERSION
    assert rule_freeze_v1_enabled() is False, "MVP requires switch OFF"
    if source_snapshot() != admitted_sources:
        raise RuntimeError("source epoch changed; restart workers before continuing")
    path = shard_path(spec, root)
    if path.with_suffix(".audit.json").exists():
        # APPROXIMATION: completed shards retain their original gate and source
        # pins across fleet edits; they are never relabelled as current-epoch
        # generation. Config equality prevents adopting a different demand draw.
        audit, _ = validate_shard(path)
        assert audit["config"] == spec
        return {"status": "resumed", "path": str(path), "seconds": audit["elapsed_generation_s"]}
    if spec["rung"] in {"small", "medium"} and spec.get("booking_slack") is None:
        raise ValueError("D79: baseline small/medium generation stopped; use the adopted booking_slack setting")
    report = GenerationReport()
    started = time.perf_counter()
    producer = generate_random_v2 if spec["generator"] == "random_yard" else generate_simulated_instance
    try:
        with generation_environment() as stage_environment:
            instance = producer(spec["rung"], seed=spec["seed"], noise=spec["noise"],
                vessel_name=spec["vessel"], port_name=spec["port"], report=report,
                max_attempts=spec["max_attempts"], n_boxes=spec["n_boxes"],
                dense_20ft=spec["dense_20ft"], tiny_dg_enabled=spec["tiny_dg_enabled"],
                **booking_slack_kwargs(producer, spec.get("booking_slack"), spec.get("booking_mass_fraction")))
    except Exception as error:
        failure = dict(config=spec, error_type=type(error).__name__, error=str(error),
                       report=asdict(report), elapsed_s=time.perf_counter()-started)
        atomic_json(path.with_suffix(".failure.json"), failure)
        return {"status": "failed", "path": str(path), "error": str(error)}
    elapsed = time.perf_counter() - started
    assert report.accepted is True and report.requested_targets == len(instance.load_list)
    assert report.c2_screened is False, "D79 requires generator C2 OFF"
    assert source_snapshot() == admitted_sources, "source changed during generation"
    assert rule_freeze_v1_enabled() is False
    require_clean(instance, what="MVP corpus output")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.partial.json")
    save_instance(instance, temp)
    restored = load_instance(temp)
    assert restored == instance
    temp.replace(path)
    audit = dict(asdict(report), config=spec, split_policy=POLICY,
        rule_freeze_v1_enabled=False, instance_sha256=sha256(path),
        instance_versions=dict(instance.provenance.versions), schema_version=SCHEMA_VERSION,
        generation_components=generator_components(spec), source_files=admitted_sources,
        elapsed_generation_s=elapsed, peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        generated_at_utc=datetime.now(timezone.utc).isoformat(), plan_checked=False,
        global_screen=0, booking_slack=spec.get("booking_slack"),
        booking_mass_fraction=spec.get("booking_mass_fraction"), stage_environment=stage_environment)
    atomic_json(path.with_suffix(".audit.json"), audit)
    validate_shard(path, current_sources=admitted_sources)
    return {"status": "generated", "path": str(path), "seconds": elapsed,
            "background_s": report.background_seconds, "targets": len(instance.load_list)}


def run(specs, *, workers=1, root=CORPUS):
    sources = source_snapshot()
    started = time.perf_counter()
    jobs = [(spec, str(root), sources) for spec in specs]
    if workers == 1:
        for job in jobs:
            print(json.dumps(generate_one(job), sort_keys=True), flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = [pool.submit(generate_one, job) for job in jobs]
            for future in as_completed(futures):
                print(json.dumps(future.result(), sort_keys=True), flush=True)
    print(json.dumps({"elapsed_wall_s": time.perf_counter()-started, "requested": len(specs)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=SEED_BASES, required=True)
    parser.add_argument("--tiny", type=int, default=15)
    parser.add_argument("--small", type=int, default=15)
    parser.add_argument("--medium", type=int, default=15)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--root", type=Path, default=CORPUS)
    parser.add_argument("--booking-slack", type=float, help="opt-in booking reduction fraction; API must exist")
    parser.add_argument("--booking-mass-fraction", type=float)
    parser.add_argument("--ship-settings", help="JSON mapping or path to ship-to-booking-controls mapping")
    args = parser.parse_args()
    settings = booking_settings(args.booking_slack, args.booking_mass_fraction, args.ship_settings)
    assert 1 <= args.workers <= 8
    assert 0 <= args.shard_index < args.shard_count
    counts = {rung: getattr(args, rung) for rung in RUNGS}
    assert min(counts.values()) >= 0
    if (counts["small"] or counts["medium"]) and not settings:
        parser.error("D79: use regenerate.py after the adopted booking-slack GO; tiny-only generation is available now")
    specs = specs_for(args.split, counts)[args.shard_index::args.shard_count]
    if settings:
        specs = [{**s, **settings[s["vessel"]]} if s["rung"] != "tiny" else s
                 for s in specs if s["rung"] == "tiny" or s["vessel"] in settings]
    if args.limit is not None:
        specs = specs[:args.limit]
    inventory = specs_for(args.split, counts)
    if settings:
        inventory = [{**s, **settings[s["vessel"]]} if s["rung"] != "tiny" else s
                     for s in inventory if s["rung"] == "tiny" or s["vessel"] in settings]
    atomic_json(args.root / f"{args.split}.requested.json", dict(split_policy=POLICY, counts=counts,
        specs=inventory))
    run(specs, workers=args.workers, root=args.root)


if __name__ == "__main__":
    main()
