"""CPU-only train-corpus prompt census/sample, never opens held-out map bodies."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from experiments.mvp.stowlm9 import prompt_v9 as v9
from llm.stowlm.format import load_tokenizer
from stowage.io import load_instance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-stratum", type=int, default=3, help="0 = all frozen train maps")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("prompt_tokens.json"))
    args = parser.parse_args()
    corpus = ROOT / "experiments/mvp/corpus/train"
    groups = defaultdict(list)
    for line in (corpus / "SHA256SUMS").read_text().splitlines():
        _, relative = line.split(maxsplit=1)
        if relative.endswith(".json") and not relative.endswith((".audit.json", ".failure.json")):
            parts = Path(relative).parts
            groups[parts[:3]].append(relative)
    tokenizer = load_tokenizer()
    rows = []
    for stratum, paths in sorted(groups.items()):
        selected = sorted(paths)[:args.per_stratum or None]
        for relative in selected:
            instance = load_instance(corpus / relative)
            old_wire = json.loads(v9._base.instance_to_prompt(instance))
            wire = json.loads(v9.instance_to_prompt(instance))
            expected = sorted(cover.id for cover in instance.vessel.hatch_covers)
            if sorted(row[0] for row in old_wire["g"]["covers"]) != expected:
                raise AssertionError(f"base hatch cover loss: {relative}")
            if [row[0] for row in wire["h"]] != expected:
                raise AssertionError(f"v9 hatch cover loss: {relative}")
            facts = v9.parse_prompt(v9.instance_to_prompt(instance))
            if facts["cargo"] != v9._base.prompt_facts(instance)["cargo"]:
                raise AssertionError(f"base cargo semantics changed: {relative}")
            base_n = len(v9._base.encode_prompt(instance, tokenizer))
            new_n = len(v9.encode_prompt(instance, tokenizer))
            rows.append({"path": relative, "size": stratum[0], "base_version": old_wire["v"],
                         "n_covers": len(expected), "base_prompt_tokens": base_n,
                         "prompt_tokens": new_n, "delta_tokens": new_n - base_n})
        print(f"{stratum}: {len(selected)}/{len(paths)}", flush=True)
    by_size = {}
    for size in sorted({row["size"] for row in rows}):
        selected = [row for row in rows if row["size"] == size]
        by_size[size] = {"sampled_maps": len(selected),
                         "corpus_maps": sum(len(paths) for key, paths in groups.items() if key[0] == size)}
        for name in ("base_prompt_tokens", "prompt_tokens", "delta_tokens"):
            values = sorted(row[name] for row in selected)
            by_size[size][name] = {"min": min(values), "mean": statistics.mean(values),
                                  "median": statistics.median(values), "max": max(values)}
    output = {"prompt_version": v9.PROMPT_VERSION, "base_hatch_cover_field_present": True,
              "base_hatch_field": "g.covers", "new_hatch_field": "h", "gpu_used": False,
              "sample_method": f"first {args.per_stratum or 'all'} sorted paths per size/ship/yard stratum",
              "source": "train/SHA256SUMS map entries excluding audit/failure sidecars", "by_size": by_size, "rows": rows}
    args.out.write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
