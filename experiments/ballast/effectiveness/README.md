# Pressed-tank effectiveness campaign

Run after production cache warming. A linear-only warm-up failure is permitted
by the leader because the exact search can operate without qualified LP tables.

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python -m stowage.cache_warm --workers 1
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/ballast/effectiveness/regression.py
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/ballast/effectiveness/slices.py
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/ballast/effectiveness/summarize.py
```

`regression.py` imports the reviewer's unchanged `harness2.build`, rebuilding
eight historical witness conditions on current production ships. Witness tank
sets define the cargo reserve and are independently checked, then discarded as
solver input. The finisher receives a plan with no ballast. No criterion or
physical parameter is weakened. The historical labels round binary search
fills to two decimals: .38 is recovered as .375; .88 as .875. The old FPT zone
was divided into current FPT and FWD-DEEP zones (CONVERGENCE section 5 item 9);
the historical fill applies to both. The snapshots record this mapping.

`slices.py` generates 10 random-yard and 10 simulated tiny slices, round-robin
across the three production ships. These retain whole hulls, tanks and synthetic
background loads. Seeds are 42000–42009 for each generator. Every complete
six-export input stow must pass move replay and exact static rules. C2 screening
is disabled only during stow construction; the full checker is unchanged and
always records its baseline. Neither cargo nor DG draws are replaced.

Both runners preserve instance/input-plan JSON, two consecutive 10-second
finisher calls, and independent full checks of every returned plan. External
full checks use their own owned worker with a 30-second deadline. First and
second calls are reported separately: an unsuccessful search can discard its
worker, so a second call does not always retain process caches. Full validation
occurs after both calls so it does not disturb this measurement. Load/build,
generation and checker time are outside the finisher budget. Checkpoints are
written after each call. `retry_slices.py` reuses these exact saved physical
instances and input stows when a search change needs measurement.

Old witness failure means the current condition is no longer known feasible;
it is not evidence of infeasibility. Current C2's mass scan is discontinuous as
ship cell counts change (the reviewer documented this behaviour). Only an
independently full-checked finisher result counts as success in `summary.json`.
