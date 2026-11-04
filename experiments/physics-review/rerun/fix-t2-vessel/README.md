# Current-ship convergence replay

The **8/9 historical standard-condition pass count does not persist** under the
current ships and loading recipes: **3/9 completed full checks pass**. The
historical eight-witness subset passes **3/8**, matching the preexisting
`experiments/ballast/effectiveness/witness_recheck.md` outcome. No hull/tank
numerical physics or fleet parameters were changed by fix-t2-vessel; this is
not attributed to its validation/capacity fixes. Historical capacity, tank and
consumable changes alter the rebuilt C2 discontinuous placement recipes and
other cargo budgets, as the effectiveness report already documents.

| Ship | C1 | C2 | C3 |
|---|---|---|---|
| feeder_500 | fail: aft draught | fail: negative GM, intact stability, visibility | fail: aft draught |
| feeder_1000 (D33 pending ~800 relabel) | pass | fail: draught, securing | pass |
| feeder_1700 | pass | fail: draught, trim, securing | fail: draught |

This replays the eight historical manual witness labels using the existing
`harness2.build` loading-budget/placement definition. Exact binary peak fills
are recovered to 1/32, and former FPT fill is applied to the current FPT plus
FWD-DEEP physical subdivisions, following the existing effectiveness runner.
The ninth, 1700 C2, has no saved witness file: this run evaluates the original
search's first DB+HEEL candidate and clearly labels it as a nonwitness.
A failed candidate/recipe is **not** an infeasibility proof. This bounded replay
does not redo the earlier 75-minute manual search or claim maximum attainable
GM, intake, or ballast-finisher success.

The lower-tier CSC contract now requires explicit individual plate ratings.
The historical harness predates this: its unchanged pilot 1000 C1 fails solely
for missing ratings (`pilot/`). The final synthetic experiment explicitly
assigns every test container a synthetic **30,480 kg certified maximum gross**,
independent of size. No weight, physics criterion or decision-cargo placement
changes. This is labelled ASSUMPTION in the runner and every result's info;
it is synthetic test evidence, not a claim about actual container plates.

All nine checks used the unchanged full checker, bounded to 30 seconds by its
owned worker helper, and completed in **2.90–3.29 seconds each**. Total runner
wall was **76.488 s**, parent peak RSS **0.265 GB**, versus admitted estimate
2–5 minutes /1 CPU /2 GB /no GPU. No process stopped by pattern. Raw reports,
input/version hashes, cargo counts and exact ballast masses are checkpointed
per condition under `results/`, with `summary.json`. The runner reuses complete
rows in its output directory; choose a new `--out` for a fresh epoch.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
UV_CACHE_DIR=/private/tmp/stowage-review-uv UV_NO_SYNC=1 nice -n 10 \
uv run python experiments/physics-review/rerun/fix-t2-vessel/recheck.py
```

Exit 0 means all requested experiments completed, not that their ships passed.
The final summary records 3 passes and 9 completed checks. Ship labels remain
unchanged here under the explicit D33 rename-job deferral.
