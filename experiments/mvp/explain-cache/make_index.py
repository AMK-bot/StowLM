"""Write INDEX.md for the explanation cache from the cached JSON files (no network, no Spark).

    uv run python experiments/mvp/explain-cache/make_index.py
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).parent
VIVID = ["random.tiny.8000004.0.2__exact", "random.tiny.8000006.0.5__exact", "random.tiny.8000006.0.5__base-mask",
         "random.tiny.8000004.0.2__base-mask"]


def main() -> None:
    entries = {p.stem: json.loads(p.read_text()) for p in sorted(HERE.glob("*__*.json"))
               if not p.name.endswith(".old.json")}
    spark = [e for e in entries.values() if e["mode"] == "spark"]
    cost = sum((Decimal(e["spark"]["cost_usd"]) for e in entries.values() if e["spark"]), Decimal(0))
    rows = []
    for key, e in entries.items():
        c = e["comparison"]
        rows.append(f"| `{key}.json` | {e['plan']['label']} | {e['baseline']['label']} | {e['verdict']} | "
                    f"{e['weighted_D11']:g} vs {e['baseline_weighted_D11']:g} | {e['mode']} | "
                    f"{e['spark']['cost_usd'] if e['spark'] else '—'} | {c['why']} |")
    vivid = []
    for key in VIVID:
        if key in entries:
            e = entries[key]
            vivid.append(f"### `{key}.json`\n\n{e['comparison']['why']}\n\n```\n{e['text']}\n```\n")
    summary_path = HERE / "d161/summary.json"
    d161 = json.loads(summary_path.read_text()) if summary_path.is_file() else None
    job_note = ("D161 refresh: see `d161/summary.json` for the capped job's measured spend and holds, and "
                "`d161/verification.json` for CPU-only stage binding results. Original entries are retained as "
                "`*.old.json` and excluded from this index.\n\n" if d161 else "")
    text = f"""# Cached Muse Spark plan explanations (UI Spark panel)

Updated through {max(e['created_utc'] for e in entries.values())[:16]}Z (explainer v3.0,
`src/llm/spark/explain.py`; model `muse-spark-1.3`). **{len(entries)} entries, {len(spark)} shown as Spark selections,
{len(entries) - len(spark)} template fallbacks; retained responses' measured Spark cost ${cost.quantize(Decimal('0.0001'))}**.
This sums the responses attached to current entries, rather than all historical calls. Calls are logged in
`experiments/spark-spend/calls.jsonl` and `docs/compute.md`.

{job_note}## What is cached


- The 15 frozen tiny test instances (`experiments/mvp/corpus/test/tiny/`):
  - the exact solver's plan (`experiments/mvp/runs/test-exact/`), explained against the two-step plan on the same
    instance (`__exact`);
  - the two-step plan (`experiments/mvp/runs/test-twostep/`), explained against the exact plan (`__twostep`).
- 3 base-mask runs (untuned Glimmer + mask, `experiments/mvp/runs/base-mask/`), explained against two-step
  (`__base-mask`):
  - `random.tiny.8000006.0.5`: its only win;
  - `random.tiny.8000002.0.2`: a tie;
  - `random.tiny.8000004.0.2`: its worst gap.
- D161/D171 adds two final StowLM showcase comparisons here (feeder_500 and feeder_1700). The third showcase
  (feeder_800) has a baseline-free `result.json.spark-explanation.json` sidecar in its run folder because the frozen
  two-step rival is a dead end with no plan. D170's shown instruction plan has its own baseline-free sidecar in
  `runs/stowlm-instruction-binding/`; its instruction card is independently checked on stage. These two sidecars are
  additional to the entry count above; see `d161/summary.json` for all targets and measured job spend.
- Plans are read from frozen run folders. D171 authorized the current source after the bundle rollback. D161 checks
  both plans through the app's exact current checker-epoch memo (full checker on a miss) and
  stores full report hashes; no plan is changed.
- Exact runs `random.tiny.8000012.0.8` and `sim.tiny.8000013` are best-found at the time limit, not proven optimal
  (`plan.exact_label`). Their saved planner status is `timeout`, so the app refuses both their cached comparisons
  and the two two-step entries using them as rivals. All four use the app's ordinary template fallback; no paid
  regeneration was attempted for the two unusable exact entries. The explanations never claim optimality.

## File format (`stowlm.explain-cache/1`, one JSON per entry)

| field | meaning |
|---|---|
| `text` | The full panel text, as `PlanExplainer.explain` returned it. It starts with the Spark header or `[Deterministic template fallback]`, then the checker verdict, Spark's chosen headline and points, the carrier-approval status line, and "Other checked statements". |
| `mode` | `spark` (Spark's selection shown) or `template` (fallback) |
| `selection` | The statement ids Spark chose: `headline`, `points` |
| `statements` | Every checked statement (`id`, `kind`, `text`) rendered from the checker report |
| `verdict` | `PASS` / `FAIL` / `NOT VALIDATED` (deterministic, from the report) |
| `comparison` | Lift breakdown of the plan and its baseline from both checker reports, plus `why`: a deterministic sentence naming which lift types differ. Not written by Spark. |
| `plan`, `baseline` | Method, label, run folder and plan id for each |
| `spark` | Model, raw answer, measured `cost_usd`, `request_id`, and the `rejection` reasons if Spark's answer was not shown |
| `report_sha256`, `baseline_report_sha256` | SHA-256 of each complete canonical current checker report. The P1-8 gate checks both before replaying the recorded selection. |

The app loads a file by `<instance_id>__<method>.json` and can show `text` as is, or render `selection` and
`statements` itself. The original `build_cache.py` is historical. D161/D171 refreshes use `prefetch_d161.py --run`,
with the shared $5 job cap, followed by
`verify_d161.py`; `prefetch_instruction_d171.py` handles the two baseline-free sidecars. Originals are preserved
as `*.old.json`.

## Build notes

- **First pass (30 entries):** the 3 base-mask entries were refused by the job guard, and the builder wrongly kept the
  previous entry's raw answer and cost on them. Fixed: the recorder resets before the guard, and the guard uses a
  sound per-call bound. They were re-run.
- **Original build:** 33 unique request ids, each matching its ledger row. D161 retains and revalidates recorded
  selections for entries whose displayed text did not need wording regeneration; its own accounting is separate.

## Vivid examples

{chr(10).join(vivid)}
## All entries

| file | plan | against | verdict | weighted D11 (plan vs baseline) | mode | cost $ | why (deterministic) |
|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}
"""
    (HERE / "INDEX.md").write_text(text)
    print(len(entries), len(spark), cost)


if __name__ == "__main__":
    main()
