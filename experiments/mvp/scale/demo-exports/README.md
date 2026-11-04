These are the two largest saved V6 TRAIN passes, ranked by requested load-list
box count, then fewer checked weighted nonproductive moves. They preserve the
original `baseline.twostep` planner identity and are solver results.

| Rank | TRAIN map | Requested/loaded boxes | Weighted nonproductive moves |
| --- | --- | ---: | ---: |
| 1 | feeder_800 f60 low seed 192200102 | 163/163 | 222 |
| 2 | feeder_800 f60 low seed 192200101 | 162/162 | 214 |

Each directory contains the exact original instance bytes, versioned
`PlannerResult`, original `MovePlan`, original checker report, a new fresh checker
report, and a self-contained `import.json`. `manifest.json` binds original input,
V6 result, certificate and exported-file hashes. `fresh-check.json` records the
new check's execution, count, binding and native thread settings.

The import contract comes from `app/inputs.py`, which accepts a `result` wrapper,
a complete embedded `instance`, and a `check_report`. The final import envelope
uses the fresh report's canonical instance binding; the original input-file hash
is retained in the manifest. This lets `--import-plan` resolve the embedded world
without scanning corpora. The actual app parser is exercised for each export.
Original plan actions, ballast, status and provenance are retained. The app's
normal provenance union is recorded separately and is the exact fresh-check input.

After `manifest.json` reports `status: passed`, launch from the repository root:

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m app \
  --instance experiments/mvp/scale/demo-exports/01-medium-feeder_800-f60-low-s192200102/instance.json \
  --plan experiments/mvp/scale/demo-exports/01-medium-feeder_800-f60-low-s192200102/import.json \
  --import-plan experiments/mvp/scale/demo-exports/02-medium-feeder_800-f60-low-s192200101/import.json \
  --no-discovery --no-companions --no-check-cache
```

No new plan is generated. Fresh export checks run serially in separate processes
with one native thread per library, using unchanged frozen
`stowage.checker.full.check_reference`. That API calls the full checker while
bypassing six optional numerical caches; shared canonical projection/baseline
resolution and parsed-parameter caches remain unchanged. This is fresh checking
through the existing implementation, with no repair or stored-verdict cache.
Each exact owned child has a 1200-second watchdog and is recorded in
`local-ledger.json`. The frozen V6 outcomes and implementation are unchanged.
