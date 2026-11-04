"""D76: input-token cost of the rules appendix on the W2 pilot instances (no HTTP).

For each pilot instance (instances/*/*/seed-*.json, skipping *.audit.json and
*.rejected.json) measure, with the local Glimmer tokenizer, (a) the current
Spark prompt ``build_prompt(instance)`` and (b) ``rules_appendix(instance)``
alone, the % increase and the added $ at $1.25 per million input tokens.
Everything runs under rule-freeze-v1, as the pilot did. Writes
appendix-tokens.json next to this file.

Run from the repo:  uv run python experiments/spark-planner/w2-pilot/appendix_tokens.py
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

from llm.spark.planner import build_prompt
from llm.spark.rules_appendix import APPENDIX_VERSION, rules_appendix
from llm.stowlm.format import load_tokenizer
from stowage.io import load_instance
from stowage.rules.catalogue import rule_freeze_v1

ROOT = Path(__file__).parent
PRICE_PER_M = Decimal("1.25")  # Muse Spark input $/1M tokens (docs/compute.md)


def count(tokenizer, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False).ids)


def main() -> None:
    import llm.spark.rules_appendix as module

    tokenizer = load_tokenizer()
    paths = sorted(p for p in ROOT.glob("instances/*/*/seed-*.json")
                   if not p.name.endswith((".audit.json", ".rejected.json")))
    rows = []
    with rule_freeze_v1(enabled=True):
        for path in paths:
            instance = load_instance(path)
            prompt, appendix = build_prompt(instance), rules_appendix(instance)
            if rules_appendix(instance) != appendix:
                raise AssertionError(f"nondeterministic appendix for {path}")
            base, extra = count(tokenizer, prompt), count(tokenizer, appendix)
            rows.append({
                "instance": str(path.relative_to(ROOT)), "id": instance.id, "size_class": instance.size_class,
                "vessel": instance.vessel.id, "prompt_tokens": base, "appendix_tokens": extra,
                "appendix_chars": len(appendix), "increase_pct": round(100 * extra / base, 1),
                "added_usd": str((Decimal(extra) * PRICE_PER_M / Decimal(1_000_000)).quantize(Decimal("0.000001"))),
            })
    by_size = {}
    for size in sorted({row["size_class"] for row in rows}):
        group = [row for row in rows if row["size_class"] == size]
        by_size[size] = {
            "n": len(group),
            "mean_prompt_tokens": round(sum(r["prompt_tokens"] for r in group) / len(group), 1),
            "mean_appendix_tokens": round(sum(r["appendix_tokens"] for r in group) / len(group), 1),
            "mean_increase_pct": round(sum(r["increase_pct"] for r in group) / len(group), 1),
        }
    source = Path(module.__file__).resolve()
    result = {
        "appendix_version": APPENDIX_VERSION, "rule_freeze_v1": True,
        "source_tree": str(source.parents[3]), "python": sys.version.split()[0],
        "price_usd_per_million_input_tokens": str(PRICE_PER_M),
        "note": "Instances loaded with the live repo's stowage.io.load_instance (no snapshot needed).",
        "by_size": by_size, "rows": rows,
    }
    (ROOT / "appendix-tokens.json").write_text(json.dumps(result, indent=1) + "\n")
    for row in rows:
        print(f"{row['instance']}: prompt {row['prompt_tokens']}, appendix {row['appendix_tokens']} "
              f"(+{row['increase_pct']}%, ${row['added_usd']})")


if __name__ == "__main__":
    main()
