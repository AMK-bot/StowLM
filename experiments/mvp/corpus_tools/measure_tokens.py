"""Measure local Glimmer prompt tokens and propose the MVP rung allocation.

APPROXIMATION: allocation uses prompt plus mandatory-Place lower bounds from
available test examples; full solver completions and future draws are unknown.
No model is loaded and no plan is invented or treated as gold.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import resource
from statistics import mean
from time import perf_counter

os.environ["TOKENIZERS_PARALLELISM"] = "false"
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

RUNGS = ("tiny", "small", "medium")
SHARES = {"tiny": 0.08, "small": 0.30, "medium": 0.62}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def allocation(means: dict[str, float], total: int, *, granularity: int = 1) -> dict:
    if total % granularity:
        raise ValueError("total must be divisible by allocation granularity")
    inverse_weight = {r: SHARES[r] / means[r] for r in RUNGS}
    scale = (total / granularity) / sum(inverse_weight.values())
    raw = {r: inverse_weight[r] * scale for r in RUNGS}
    counts = {r: math.floor(raw[r]) for r in RUNGS}
    for r in sorted(RUNGS, key=lambda r: raw[r] - counts[r], reverse=True)[:total // granularity - sum(counts.values())]:
        counts[r] += 1
    counts = {r: counts[r] * granularity for r in RUNGS}
    val_total = round(total * 0.05)
    val_granularity = granularity if val_total % granularity == 0 else 1
    val_raw = {r: counts[r] * val_total / total / val_granularity for r in RUNGS}
    val = {r: math.floor(val_raw[r]) for r in RUNGS}
    for r in sorted(RUNGS, key=lambda r: val_raw[r] - val[r], reverse=True)[:val_total // val_granularity - sum(val.values())]:
        val[r] += 1
    val = {r: val[r] * val_granularity for r in RUNGS}
    tokens = {r: counts[r] * means[r] for r in RUNGS}
    return {"total_instances": total, "target_token_shares": SHARES, "count_granularity": granularity,
            "counts_by_rung": counts, "train_counts_by_rung": {r: counts[r] - val[r] for r in RUNGS},
            "val_counts_by_rung": val, "estimated_tokens_by_rung": tokens,
            "estimated_token_shares": {r: tokens[r] / sum(tokens.values()) for r in RUNGS},
            "method": "counts proportional to target token share / measured mean length; largest remainder integer rounding",
            "limitation": "training draw lengths and full solver completion lengths are unmeasured"}


def mandatory_place_lower_bound(instance, tokenizer) -> dict:
    """Token sizing only: optimistic independent slots, never a loading plan.

    The public codec encodes box index and slot fields independently. Find the
    shortest actual ship-slot encoding once; every actual target is then sized
    through encode_action at that slot. Reusing a slot is permitted ONLY for this
    length lower bound and says nothing about physical or checker feasibility.
    """
    from llm.stowlm.format import ActionCodec
    from stowage.actions.space import Place
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    targets = list(instance.load_list)
    if not targets:
        return {"place_tokens": 0, "end_plan_tokens": len(codec.end_plan_tokens),
                "total_completion_lower_bound_tokens": len(codec.end_plan_tokens), "per_target": []}
    slot_costs = [(len(codec.encode_action(Place(targets[0], cell.id))), cell.id)
                  for cell in instance.vessel.cells]
    _, shortest_slot = min(slot_costs)
    reference = codec.action_fragments(Place(targets[0], shortest_slot))
    per_target = []
    for target in targets:
        action = Place(target, shortest_slot)
        fragments = codec.action_fragments(action)
        assert fragments[0] == reference[0] and fragments[2:] == reference[2:]
        per_target.append({"container_id": target, "min_place_tokens": len(codec.encode_action(action))})
    place_tokens = sum(row["min_place_tokens"] for row in per_target)
    return {"place_tokens": place_tokens, "end_plan_tokens": len(codec.end_plan_tokens),
            "total_completion_lower_bound_tokens": place_tokens + len(codec.end_plan_tokens),
            "min_tokens_per_place": min(row["min_place_tokens"] for row in per_target),
            "max_tokens_per_place": max(row["min_place_tokens"] for row in per_target),
            "per_target": per_target,
            "method": "Minimum encoding across actual slots; independent mandatory target Place lengths plus end token. No feasibility or legal-plan claim."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-root", type=Path, default=Path("experiments/mvp/corpus/test"))
    parser.add_argument("--output", type=Path, default=Path("experiments/mvp/corpus_tools/token_measurements.json"))
    parser.add_argument("--total", type=int, default=3000)
    parser.add_argument("--minimum-per-rung", type=int, default=3)
    args = parser.parse_args()
    paths = sorted(p for p in args.test_root.rglob("seed-*.json")
                   if p.stem.removeprefix("seed-").isdigit()
                   and p.with_suffix(".audit.json").exists())
    counts = Counter(p.relative_to(args.test_root).parts[0] for p in paths)
    if any(counts[r] < args.minimum_per_rung for r in RUNGS):
        print(json.dumps({"status": "insufficient samples", "counts": dict(counts), "minimum_per_rung": args.minimum_per_rung}))
        return 2
    from llm.stowlm.format import DEFAULT_TOKENIZER_PATH, load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt, prompt_version
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    assert rule_freeze_v1_enabled() is False
    started = perf_counter()
    tokenizer = load_tokenizer()
    rows = []
    for path in paths:
        relative = path.relative_to(args.test_root)
        rung, ship, family = relative.parts[:3]
        if rung not in RUNGS:
            continue
        instance = load_instance(path)
        before = digest(path)
        tokens = encode_prompt(instance, tokenizer, include_layout=True)
        lower_bound = mandatory_place_lower_bound(instance, tokenizer)
        assert before == digest(path), f"instance changed while measuring: {path}"
        rows.append({"path": str(path), "sha256": before, "seed": instance.seed,
                     "rung": rung, "ship": ship, "family": family,
                     "prompt_tokens": len(tokens), "prompt_version": prompt_version(instance),
                     "targets": len(instance.load_list), "containers": len(instance.containers),
                     "cells": len(instance.vessel.cells),
                     "mandatory_place_completion_lower_bound": lower_bound,
                     "prompt_plus_mandatory_places_tokens": len(tokens) + lower_bound["total_completion_lower_bound_tokens"]})
    summary = {}
    for rung in RUNGS:
        samples = [row for row in rows if row["rung"] == rung]
        values = [row["prompt_tokens"] for row in samples]
        summary[rung] = {"samples": len(samples), "mean_prompt_tokens": mean(values),
                         "mean_mandatory_place_completion_lower_bound_tokens": mean(s["mandatory_place_completion_lower_bound"]["total_completion_lower_bound_tokens"] for s in samples),
                         "mean_prompt_plus_mandatory_places_tokens": mean(s["prompt_plus_mandatory_places_tokens"] for s in samples),
                         "min_prompt_tokens": min(values), "max_prompt_tokens": max(values),
                         "ship_family_counts": dict(Counter(f"{s['ship']}/{s['family']}" for s in samples))}
    result = {"measured_utc": datetime.now(timezone.utc).isoformat(),
              "rule_freeze_v1_enabled": False, "include_layout": True,
              "tokenizer_path": str(DEFAULT_TOKENIZER_PATH),
              "tokenizer_sha256": digest(DEFAULT_TOKENIZER_PATH / "tokenizer.json"),
              "source_sha256": {p: digest(Path(p)) for p in (
                  "src/llm/stowlm/format.py", "src/llm/stowlm/format_prompt.py")},
              "rows": rows, "summary_by_rung": summary,
              "allocation_prompt_only": allocation({r: summary[r]["mean_prompt_tokens"] for r in RUNGS}, args.total),
              "allocation": allocation({r: summary[r]["mean_prompt_plus_mandatory_places_tokens"] for r in RUNGS}, args.total, granularity=6),
              "elapsed_s": perf_counter() - started,
              "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "limitation": "APPROXIMATION: prompt plus measured mandatory-Place completion lower bound proxies token share pending checked gold completions. No legal loading plan is emitted; solver completions can be longer."}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "summary": summary, "allocation": result["allocation"],
                      "elapsed_s": result["elapsed_s"], "peak_rss_bytes": result["peak_rss_bytes"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
