"""Aggregate decode_sweep*.jsonl into the per-B table (markdown) used in REPORT.md.

Run: uv run python experiments/mvp/speed/analyze.py [tag]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else ""
    rows = [json.loads(l) for l in (OUT / f"decode_sweep{tag}.jsonl").read_text().splitlines()]
    by = defaultdict(list)
    for r in rows:
        by[r["B"]].append(r)
    print("| B | maps | agg tok/s (decode) | tok/s incl. prefill | ms/action | GPU-host stall | step ms p50 | "
          "peak GB | map wall s (mean) | complete/map | complete samples per GPU-min |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for B in sorted(by):
        rs = by[B]
        tok = sum(r["tokens"] for r in rs)
        dw = sum(r["decode_wall_s"] for r in rs)
        mw = sum(r["map_wall_s"] for r in rs)
        act = sum(r["actions"] for r in rs)
        stall = sum(r["host_wait_s"] for r in rs) / dw
        comp = sum(r["statuses"].get("complete", 0) for r in rs)
        p50 = sorted(r["step_ms_p50"] for r in rs)[len(rs) // 2]
        print(f"| {B} | {len(rs)} | {tok / dw:.1f} | {tok / mw:.1f} | {1e3 * dw / act:.0f} | {stall:.0%} | {p50:.0f} | "
              f"{max(r['peak_gb'] for r in rs):.1f} | {mw / len(rs):.1f} | {comp / len(rs):.1f} | {60 * comp / mw:.1f} |")
    print("\nprompt tokens:", sorted({r["instance_id"]: r["prompt_tokens"] for r in rows}.items()))
    print("prefill s (B=1):", [r["prefill_s"] for r in by.get(1, [])])


if __name__ == "__main__":
    main()
