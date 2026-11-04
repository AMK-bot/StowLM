"""Reproduce task 4.10's inert-data scan and published-target comparisons.

Run from the repo root: UV_CACHE_DIR=$PWD/.uv-cache uv run python
experiments/benchmarks/run.py. No solver or downloaded program is executed.
Tightness is bound / published optimum, with zero-optimum cases excluded from
means and reported separately. Restricted optima are comparison ceilings only;
they do not certify unrestricted planner optimality.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from hashlib import sha256
from zipfile import ZipFile
from pathlib import Path
import resource
from time import perf_counter

from stowage.bench import iter_zhu_instances
from stowage.bench.adapter import benchmark_bounds, replay_relocations
from stowage.bench.evidence import verify_zhu_source_identity

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, default=HERE / "results.json")
    parser.add_argument("--archive", type=Path,
        default=ROOT / "data/raw/move-benchmarks-tanaka/Zhu_dataset.zip")
    parser.add_argument("--source-archive", type=Path,
        default=ROOT / "data/raw/move-benchmarks-bacci/group6.zip")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    solution = json.loads((HERE / "published-solution.json").read_text())
    for required in (args.archive, args.source_archive, ROOT / solution["local_image"]):
        if not required.is_file():
            parser.error(f"required public evidence unavailable: {required}")
    started = perf_counter()
    identity = verify_zhu_source_identity(args.archive, args.source_archive)
    identity_seconds = perf_counter() - started
    image = ROOT / solution["local_image"]
    assert sha256(image.read_bytes()).hexdigest() == solution["image_sha256"]
    replay = replay_relocations(solution["stacks"], solution["height_limit"],
        solution["relocations"], expected_relocations=solution["expected_relocations"],
        restricted=solution["restricted"])
    assert replay.count.gate_moves == solution["expected_gate_moves"]
    evidence_file = HERE / "evidence.json"
    evidence = json.loads(evidence_file.read_text())
    # Evidence schema is deliberately explicit: a record must declare its exact
    # archive member, operational height, variant and proven optimum.
    records = evidence.get("instances", [])
    targets = {r["name"]: r for r in records}
    assert len(targets) == len(records), "duplicate published target"
    archive = args.archive
    with ZipFile(archive) as zipped:
        for name, target in targets.items():
            assert sha256(zipped.read(name)).hexdigest() == target["instance_sha256"], name
            assert target["published_lower_bound"] == target["optimum"], name
    groups = defaultdict(lambda: dict(count=0, matched=0, engine_valid=0, ordered_valid=0,
        engine_tightness_sum=0.0, ordered_tightness_sum=0.0, positive_optima=0,
        zero_optima=0, seconds=0.0))
    seen = set()
    scan_started = perf_counter()
    for index, item in enumerate(iter_zhu_instances(
        archive,
        # Unmatched cases are mapping coverage only. H+2 is not inferred as a
        # published operational limit; matched cases use the source's h below.
        height_limit=lambda n: int(n.split('-')[0]) + 2, variant="restricted",
    )):
        if args.limit is not None and index >= args.limit:
            break
        seen.add(item.name)
        tick = perf_counter()
        target = targets.get(item.name)
        height = target["height_limit"] if target else item.height_limit
        result = benchmark_bounds(item.stacks, height, name="brp")
        group = groups[item.name.split('/')[0]]
        group["count"] += 1
        if target:
            assert target["variant"] == "restricted"
            assert target["stacks"] == len(item.stacks), item.name
            assert target["blocks"] == item.container_count, item.name
            optimum = target["optimum"]
            assert result.engine_yard <= optimum, item.name
            assert result.ordered_lb1 <= optimum, item.name
            group["matched"] += 1
            group["engine_valid"] += int(result.engine_yard <= optimum)
            group["ordered_valid"] += int(result.ordered_lb1 <= optimum)
            if optimum:
                group["positive_optima"] += 1
                group["engine_tightness_sum"] += result.engine_yard / optimum
                group["ordered_tightness_sum"] += result.ordered_lb1 / optimum
            else:
                group["zero_optima"] += 1
        group["seconds"] += perf_counter() - tick
    missing = sorted(set(targets) - seen)
    if args.limit is None:
        assert not missing, missing
    payload = dict(wall_s=perf_counter()-started,
        bound_scan_s=perf_counter()-scan_started,
        source_identity_seconds=identity_seconds, source_identity=identity,
        scan_complete=args.limit is None,
        public_checks=dict(archive_availability="passed", source_identity="passed",
            published_solution_replay="passed", bound_comparison="passed"),
        published_solution_replay=dict(name=solution["name"], source_url=solution["source_url"],
            image_sha256=solution["image_sha256"], relocations=replay.count.relocations,
            gate_moves=replay.count.gate_moves),
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        protocol="restricted published h for matches; H+2 mapping-only for unmatched",
        groups=dict(sorted(groups.items())), evidence_records=len(records), missing_targets=missing)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({k: v for k,v in payload.items() if k not in ("groups", "missing_targets")}))
    print("instances", sum(g["count"] for g in groups.values()),
          "published_matches", sum(g["matched"] for g in groups.values()))


if __name__ == "__main__":
    main()
