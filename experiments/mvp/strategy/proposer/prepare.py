"""Freeze an outcome-blind TRAIN cohort and measure local prompt token sizes.

No inference, network access, solver evaluation or accelerator use occurs here.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys

for _thread_variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                         "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "HIGHS_THREADS"):
    os.environ[_thread_variable] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from experiments.mvp.strategy.headroom import inventory

SELECTION_SALT = "D179-proposer-prep-v1:"
HEADROOM_ALLOWLIST = ROOT / "experiments/mvp/strategy/headroom/train_maps.json"
HEADROOM_RUNS = ROOT / "experiments/mvp/strategy/headroom/runs"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def headroom_seen():
    """Inspect metadata only, including pilots, so exclusion is conservative."""
    sources = [HEADROOM_ALLOWLIST, *sorted(HEADROOM_RUNS.glob("*/run.json"))]
    paths, hashes, provenance = set(), set(), {}
    for source in sources:
        if not source.is_file():
            continue
        provenance[str(source.relative_to(ROOT))] = digest(source)
        for row in json.loads(source.read_text()).get("maps", []):
            if row.get("input"):
                paths.add(row["input"])
            if row.get("sha256", row.get("input_sha256")):
                hashes.add(row.get("sha256", row.get("input_sha256")))
    return paths, hashes, provenance


def select_small(population, count=20):
    """Round robin ships, then least-taken family; salted SHA order in strata."""
    groups = defaultdict(list)
    for entry in population:
        groups[entry["ship"], entry.get("family", "unknown")].append(entry)
    for rows in groups.values():
        rows.sort(key=lambda e: hashlib.sha256((SELECTION_SALT + e["path"]).encode()).hexdigest())
    chosen, taken = [], Counter()
    while len(chosen) < min(count, len(population)):
        for ship in sorted({key[0] for key in groups}):
            keys = [key for key, rows in groups.items() if key[0] == ship and rows]
            if keys and len(chosen) < count:
                key = min(keys, key=lambda k: (taken[k], k))
                chosen.append(groups[key].pop(0))
                taken[key] += 1
    return chosen


def build_manifest(small_count=20):
    seen_paths, seen_hashes, exclusion_sources = headroom_seen()
    active_manifest = inventory.MANIFESTS[0]
    population = [e for e in json.loads(active_manifest.read_text())["files"]
                  if e.get("split") == "train" and e.get("rung") == "small"
                  and e.get("active_for_current_release", True)]
    unseen = []
    for entry in population:
        path = inventory.train_path(entry, active_manifest)
        if path is None:
            continue
        if str(path.relative_to(ROOT)) not in seen_paths and entry.get("sha256") not in seen_hashes:
            unseen.append(entry)
    if len(unseen) < small_count:
        raise ValueError("not enough headroom-disjoint current small train maps")
    # Headroom's inventory already exhaustively identified the eight historical
    # TRAIN medium inputs. Reuse their explicit provenance, never infer a split.
    previous = json.loads(HEADROOM_ALLOWLIST.read_text())
    medium = [dict(row) for row in previous["maps"] if row["size"] == "medium"]
    if len(medium) != 8:
        raise ValueError("expected exactly eight manifest-identified TRAIN medium maps")
    maps = []
    for entry in select_small(unseen, small_count):
        path = inventory.train_path(entry, active_manifest)
        actual = digest(path)
        if actual != entry["sha256"]:
            raise ValueError(f"manifest SHA mismatch: {path}")
        instance = json.loads(path.read_text())
        blocks, uncovered = inventory.blocks_of(instance)
        loads = set(instance["load_list"])
        cargo = [c for c in instance["containers"] if c["id"] in loads]
        counts = Counter((c["pod"], c.get("weight_class", "unknown")) for c in cargo)
        maps.append({"map_id": f"small-{entry['ship']}-{entry.get('family', 'unknown')}-{entry['seed']:09d}",
                     "input": str(path.relative_to(ROOT)), "sha256": actual,
                     "split": "train", "size": "small", "ship": entry["ship"],
                     "family": entry.get("family"), "seed": entry.get("seed"),
                     "manifest": str(active_manifest.relative_to(ROOT)),
                     "manifest_entry_path": entry["path"], "release_epoch": entry.get("release_epoch"),
                     "schema_version": instance.get("schema_version"),
                     "source_binding_status": entry.get("source_binding_status"),
                     "n_loads": len(cargo), "n_onboard": len(instance["onboard"]["placements"]),
                     "hatch_count": len(instance["vessel"]["hatch_covers"]),
                     "block_count": len(blocks), "blocks": blocks,
                     "uncovered_stack_ids": uncovered,
                     "loaded_ports": sorted({c["pod"] for c in cargo}),
                     "loaded_port_weight_counts": [{"port": p, "weight_class": w, "count": n}
                                                   for (p, w), n in sorted(counts.items())]})
    maps.extend(sorted(medium, key=lambda row: row["map_id"]))
    for row in maps:
        path = (ROOT / row["input"]).resolve()
        if row.get("split") != "train" or any(p in {"val", "validation", "test", "showcase"} for p in path.parts):
            raise ValueError("forbidden split in selected cohort")
        if digest(path) != row["sha256"]:
            raise ValueError(f"input SHA mismatch: {path}")
        row["headroom_overlap"] = row["input"] in seen_paths or row["sha256"] in seen_hashes
        if row["size"] == "small" and row["headroom_overlap"]:
            raise ValueError("small cohort unexpectedly overlaps headroom")
    return {"format": "D179-proposer-train-allowlist-v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "frozen_before_proposer_results": True,
            "selection": {"method": "round-robin ship then least-taken family; SHA256(salt+manifest entry path) within stratum",
                          "salt": SELECTION_SALT, "small_requested": small_count,
                          "small_active_population": len(population), "small_unseen_population": len(unseen),
                          "outcomes_read": False,
                          "medium": "all eight explicit historical TRAIN medium records from headroom inventory; unavoidable 8/8 headroom overlap",
                          "medium_caveat": "historical epochs and source_binding_status drift preserved; medium is not an unseen-search evaluation"},
            "exclusion_metadata_sha256": exclusion_sources,
            "manifest_sha256": {str(p.relative_to(ROOT)): digest(p) for p in inventory.MANIFESTS if p.is_file()},
            "counts": dict(Counter(row["size"] for row in maps)),
            "counts_by_ship_size": dict(Counter(row["ship"] + ":" + row["size"] for row in maps)),
            "headroom_overlap_counts": dict(Counter(row["size"] for row in maps if row["headroom_overlap"])),
            "maps": maps}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def synthetic_full_ship(source, target_loads=815):
    """Sizing-only full geometry + replicated TRAIN cargo, never a solved map.

    Actual full public fleet geometry is loaded without hull physics or disk
    cache. Replicated yard blocks preserve their original geometry and stack
    heights; the final partial repetition leaves unused target boxes in-yard.
    No corpus full/showcase/validation/test input is inspected.
    """
    from stowage.conventions import make_container_id, parse_yard, yard_stack_key
    from stowage.model import Instance
    from stowage.vessel.fleet import load_vessel

    previous_cache = os.environ.get("STOWAGE_VESSEL_CACHE")
    os.environ["STOWAGE_VESSEL_CACHE"] = "0"
    try:
        vessel = load_vessel("feeder_1700", with_hull=False)
    finally:
        if previous_cache is None:
            os.environ.pop("STOWAGE_VESSEL_CACHE", None)
        else:
            os.environ["STOWAGE_VESSEL_CACHE"] = previous_cache
    if source.onboard.placements or source.discharge_list or not source.load_list:
        raise ValueError("synthetic sizing requires an empty-arrival TRAIN source with loads")
    copies = math.ceil(target_loads / len(source.load_list))
    containers, stacks, quay, loads, blocks = [], [], [], [], []
    for repeat in range(copies):
        ids = {box.id: make_container_id("QZXU", 100000 + repeat * len(source.containers) + index)
               for index, box in enumerate(source.containers)}
        block_ids = {block.id: f"{repeat + 1}{index:03d}"
                     for index, block in enumerate(source.port.yard.blocks)}
        containers.extend(box.model_copy(update={"id": ids[box.id]}) for box in source.containers)
        blocks.extend(block.model_copy(update={"id": block_ids[block.id]}) for block in source.port.yard.blocks)
        for stack in source.yard.stacks:
            address = parse_yard(stack.key + "-01")
            stacks.append(stack.model_copy(update={
                "key": yard_stack_key(block_ids[address.block], address.bay, address.row),
                "boxes": [ids[cid] for cid in stack.boxes]}))
        quay.extend(ids[cid] for cid in source.yard.quay_buffer)
        loads.extend(ids[cid] for cid in source.load_list)
    port = source.port.model_copy(update={"yard": source.port.yard.model_copy(update={"blocks": blocks})})
    synthetic = source.model_copy(update={
        "id": "proposer-synthetic-full-sizing-only", "size_class": "full",
        "vessel": vessel, "containers": containers, "load_list": loads[:target_loads],
        "yard": source.yard.model_copy(update={"stacks": stacks, "quay_buffer": quay}),
        "port": port, "onboard": source.onboard.model_copy(update={"vessel_id": vessel.id,
                                                                   "tank_masses_kg": {}, "covers_off": {}})})
    return Instance.model_validate(synthetic.model_dump(mode="json"))


def measure_tokens(manifest_path):
    """Exact local Glimmer raw-text tokens; Spark uses a labelled proxy only."""
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=False):
        return _measure_tokens(manifest_path)


def _measure_tokens(manifest_path):
    from tokenizers import Tokenizer
    from stowage.io import load_instance
    from experiments.mvp.strategy.headroom.layout import BlockLayout, BlockModel, unrestricted_layout
    from experiments.mvp.strategy.proposer.format import format_layout
    from experiments.mvp.strategy.proposer.prompt import build_prompt, summary

    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    tokenizer_path = ROOT / "models/glimmer-30b-4bit/tokenizer.json"
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    # Explicit raw text encoding: no automatic BOS/EOS, no model chat template,
    # and no model weights are loaded. This tokenizer runs on CPU only.
    def size(text):
        return {"characters": len(text), "utf8_bytes": len(text.encode("utf-8")),
                "glimmer_raw_tokens": len(tokenizer.encode(text, add_special_tokens=False).ids)}

    rows, instances = [], {}
    for row in manifest["maps"]:
        path = (ROOT / row["input"]).resolve()
        if row["split"] != "train" or any(p in {"val", "validation", "test", "showcase"} for p in path.parts):
            raise ValueError("token sizing only accepts frozen TRAIN inputs")
        if digest(path) != row["sha256"]:
            raise ValueError(f"frozen input changed: {path}")
        instance = load_instance(path)
        instances[row["map_id"]] = instance
        rows.append((row["size"], row["map_id"], instance, {"input": row["input"], "input_sha256": row["sha256"]}))
    full_source = next(row for row in manifest["maps"] if row["size"] == "medium" and row["ship"] == "feeder_1700")
    synthetic = synthetic_full_ship(instances[full_source["map_id"]])
    rows.append(("full", "synthetic-full-sizing-only", synthetic,
                 {"synthetic": True, "source_train_map_id": full_source["map_id"],
                  "source_train_input_sha256": full_source["sha256"],
                  "fleet_parameters": "params/fleet/feeder_1700.toml",
                  "fleet_parameters_sha256": digest(ROOT / "params/fleet/feeder_1700.toml"),
                  "method": "public full geometry (no hull); repeat TRAIN containers/yard blocks, retain first815 load IDs; no solver/checker result"}))
    measurements = []
    for size_name, map_id, instance, provenance in rows:
        model = BlockModel.from_instance(instance)
        unrestricted = unrestricted_layout(instance, model)
        compact = BlockLayout({category: frozenset({model.block_ids[0]}) for category in unrestricted.allowed},
                              "compact-syntax-size-only")
        prompt_size = size(build_prompt(instance))
        compact_size = size(format_layout(compact, instance))
        unrestricted_size = size(format_layout(unrestricted, instance))
        # Rates supplied by existing docs/compute.md; this is a budget estimate,
        # not a current quote or actual Spark billing measurement.
        input_cost = prompt_size["glimmer_raw_tokens"] * 1.25 / 1_000_000
        output_cost = unrestricted_size["glimmer_raw_tokens"] * 4.25 / 1_000_000
        measurements.append({"size": size_name, "map_id": map_id,
                             "loads": len(instance.load_list), "blocks": len(model.blocks),
                             "summary": size(summary(instance)), "prompt": prompt_size,
                             "layout_one_block_per_category_syntax_only": compact_size,
                             "layout_all_blocks_per_category": unrestricted_size,
                             "spark_proxy_usd_per_proposal": input_cost + output_cost,
                             "spark_proxy_usd_by_proposal_count": {str(n): n * (input_cost + output_cost) for n in (1, 4, 16, 256)},
                             "provenance": provenance})
    ranges = {}
    for size_name in ("small", "medium", "full"):
        cohort = [row for row in measurements if row["size"] == size_name]
        ranges[size_name] = {"maps": len(cohort), "prompt_raw_tokens_min": min(r["prompt"]["glimmer_raw_tokens"] for r in cohort),
                             "prompt_raw_tokens_max": max(r["prompt"]["glimmer_raw_tokens"] for r in cohort),
                             "output_all_blocks_raw_tokens_min": min(r["layout_all_blocks_per_category"]["glimmer_raw_tokens"] for r in cohort),
                             "output_all_blocks_raw_tokens_max": max(r["layout_all_blocks_per_category"]["glimmer_raw_tokens"] for r in cohort),
                             "spark_proxy_usd_per_proposal_min": min(r["spark_proxy_usd_per_proposal"] for r in cohort),
                             "spark_proxy_usd_per_proposal_max": max(r["spark_proxy_usd_per_proposal"] for r in cohort)}
    return {"format": "D179-proposer-token-sizes-v2", "manifest_sha256": digest(manifest_path),
            "implementation_sha256": {str(path.relative_to(ROOT)): digest(path)
                                       for path in (HERE / "prompt.py", HERE / "format.py", HERE / "prepare.py")},
            "tokenizer": {"path": str(tokenizer_path.relative_to(ROOT)), "sha256": digest(tokenizer_path),
                          "method": "tokenizers.Tokenizer.from_file; encode(text, add_special_tokens=False)",
                          "exact_for": "local base Glimmer raw text only", "chat_template_included": False,
                          "spark": "Glimmer token counts are a proxy; Spark tokenizer and hidden/reasoning tokens unmeasured"},
            "pricing_assumption": {"source": "docs/compute.md recorded rates; not refreshed online",
                                   "spark_input_usd_per_million": 1.25, "spark_output_usd_per_million": 4.25,
                                   "formula": "proposal_count * (raw_prompt_tokens*1.25 + all_blocks_layout_tokens*4.25) / 1000000",
                                   "budget_caveat": "k counts completed terminal checks; proposal_count can exceed k. These are proposal costs, not a quote for reaching k checks.",
                                   "exclusions": "chat wrappers, hidden/reasoning output, retries, batching, caches and provider-specific tokenization; no calls made"},
            "sizing_caveats": ["Full sample is synthetic text-sizing only, not a benchmark map or feasibility claim.",
                               "One-block layout measures compact grammar size; it need not be capacity-feasible.",
                               "All-block layout bounds serialized permissions for the same category set, not arbitrary model verbosity."],
            "ranges": ranges, "measurements": measurements}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=HERE / "maps.json")
    parser.add_argument("--small-count", type=int, default=20)
    parser.add_argument("--freeze", action="store_true", help="create cohort once; refuses to replace a frozen cohort")
    parser.add_argument("--token-sizes", type=Path, help="measure prompts on existing frozen cohort")
    args = parser.parse_args()
    if args.small_count < 1:
        parser.error("--small-count must be positive")
    if args.freeze:
        if args.manifest.exists():
            parser.error("cohort already frozen; refusing to replace it")
        manifest = build_manifest(args.small_count)
        atomic_json(args.manifest, manifest)
        print(json.dumps({"manifest": str(args.manifest), "counts": manifest["counts"],
                          "headroom_overlap_counts": manifest["headroom_overlap_counts"]}))
    if args.token_sizes:
        atomic_json(args.token_sizes, measure_tokens(args.manifest))
        print(json.dumps({"token_sizes": str(args.token_sizes)}))
    if not args.freeze and not args.token_sizes:
        parser.error("choose --freeze or --token-sizes OUTPUT")


if __name__ == "__main__":
    main()
