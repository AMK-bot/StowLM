"""Regenerate frozen validation requests with the current v3/voyage producers.

This runner iterates allocated requests before any screening. Failed requests
remain in the campaign audit; neither failures nor solver outcomes change the
allocation. Production C2 qualification and the heavy-job reservation belong to
the leader. --dry-run only validates and prints the requested allocation.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
from time import perf_counter

from eval.splits import DEFAULT_MANIFEST, load_inventory
from stowage.generate import instances_v3 as v3


def build_specs(*, profiles, c2, max_attempts=200, split_manifest=DEFAULT_MANIFEST):
    if type(max_attempts) is not int or not 1 <= max_attempts <= 1_000_000:
        raise ValueError("max_attempts must be 1..1,000,000 to preserve disjoint frozen retry ranges")
    inventory = load_inventory(split_manifest)
    if inventory.version != "v2":
        raise ValueError("current-epoch validation requires the frozen v2 release")
    if not profiles or set(profiles) - {"tiny", "small", "medium", "full"}:
        raise ValueError("profiles must select tiny/small/medium/full")
    specs = []
    for row in inventory.manifest["entries"]:
        allocated = row["spec"]
        if row["split"] != "val" or allocated["rung"] not in profiles:
            continue
        if allocated["without_dg"] or allocated["cargo_profile"] != "feeder_export":
            raise ValueError("v3 campaign adapter does not support this allocation's cargo conditioning")
        random = allocated["generator"] == "random_yard"
        spec = {**allocated, "campaign_version": v3.CAMPAIGN_VERSION,
                "generator": "random_yard" if random else "simulated_yard",
                "profile": allocated["rung"], "size": allocated["rung"],
                "noise": allocated["blocking_ratio"] if random else allocated["noise"],
                "max_attempts": max_attempts, "full_export": allocated["rung"] == "full",
                "c2_screened": c2, "split_entries_sha256": inventory.entries_sha256}
        metadata = v3.split_metadata(spec)
        if metadata.get("split_spec") != allocated or metadata.get("split") != "val":
            raise ValueError("v3 adapter cannot preserve the allocated request identity")
        inventory.require_membership(metadata["split_spec"], split="val")
        specs.append(spec)
    return sorted(specs, key=lambda s: v3.split_metadata(s)["spec_key"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", type=Path, default=Path("experiments/base-mask/instances-v2-val-current"))
    ap.add_argument("--profiles", nargs="+", default=["small"])
    ap.add_argument("--split-manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--max-attempts", type=int, default=200)
    ap.add_argument("--request-limit", type=int, default=None, help="preselect a stable prefix before generation")
    ap.add_argument("--c2", action="store_true", help="require the real, qualified necessary C2 screen")
    ap.add_argument("--dry-run", action="store_true", help="print allocated requests without generation or physics")
    args = ap.parse_args()
    if not 1 <= args.workers <= 6:
        ap.error("workers must be 1..6")
    specs = build_specs(profiles=args.profiles, c2=args.c2, max_attempts=args.max_attempts,
                        split_manifest=args.split_manifest)
    if args.request_limit is not None:
        if not 1 <= args.request_limit <= len(specs):
            ap.error("request-limit must be between 1 and the allocated request count")
        specs = specs[:args.request_limit]
    if args.dry_run:
        print(json.dumps(specs, indent=2))
        return
    if args.c2:
        qualification = Path(v3.__file__).resolve().parents[3] / "experiments/cache/stability/warmup/summary.json"
        if not qualification.exists() or not json.loads(qualification.read_text()).get("complete"):
            raise SystemExit("C2 requires a successful current whole-fleet cache_warm qualification")
        # The engine independently rejects a stale policy; a historical complete
        # warmup file never authorizes bypassing that production guard.
    physics = v3.physics_metadata(c2_screened=args.c2)
    inventory = load_inventory(args.split_manifest)
    specs = [{**spec, "physics_sha256": physics["sha256"]} for spec in specs]
    manifest_path = args.out / "manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        if (existing.get("physics") != physics or existing.get("split_version") != inventory.version
                or existing.get("split_entries_sha256") != inventory.entries_sha256):
            raise ValueError("resume requires the identical physics and frozen allocation; use a fresh output root")
    args.out.mkdir(parents=True, exist_ok=True)
    started, records = perf_counter(), []
    jobs = [(str(args.out), spec) for spec in specs]

    def consume(results):
        for rec in results:
            records.append(rec)
            manifest = v3.write_manifests(args.out, records, "val-subset", perf_counter() - started,
                                          planned_requested=len(jobs))
            manifest.update(split_version=inventory.version, split_entries_sha256=inventory.entries_sha256,
                            allocated_requests=len(jobs))
            v3._atomic_json(manifest_path, manifest)
            print(rec["status"], rec["path"], flush=True)

    if args.workers == 1:
        consume(map(v3.shard, jobs))
    else:
        with ProcessPoolExecutor(args.workers) as pool:
            consume(pool.map(v3.shard, jobs, chunksize=1))
    print(f"done in {perf_counter() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
