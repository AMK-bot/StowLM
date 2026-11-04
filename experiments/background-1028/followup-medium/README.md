# D58 medium follow-up, 28 October 2026

This document preserves the first follow-up grid, before the endpoint repair.
The builder subsequently snapped numerically negligible tank fractions to their
exact endpoints (condition epoch12). The separate epoch12 seed5 replay passes
with both APT masses exactly zero. Final rerun evidence is kept under
`final-epoch12/`; none of the pre-repair observations below is overwritten.

The empty-background witness and booking initializer are different ship states.
The gate proves the former with empty decision cargo and independently checks
departure, mid-voyage and arrival. Emitted instances retain the latter: copying
the empty witness onto a fully booked instance could consume its cargo budget.
The booking-reference tank fit is an upright mass and longitudinal/transverse
moment sizing calculation, explicitly **not a full-checker certificate for an
actual completed plan**.

## Raw matrix and normal production trajectories

`final-{random,simulated}.jsonl` contain 156 independent, maximum-one-attempt
draws: ten per tiny/small cell and six per medium cell. `final-draws.jsonl` is
their concatenation; `final-audit.json` verifies all 156 keys and 18 cells.
Actual C1 screens ran; C2 was explicitly disabled because the unrelated linear
epoch remained stale. The real background finisher/full-checker gate ran for
every draw. A separate diagnostic stresses securing at the vessel's lashing
design GM without changing the actual equilibrium or the production gate.

The complete tables are [final-tables.md](final-tables.md); distributions and
booking audits are in [final-summary.json](final-summary.json).

* Tiny/small: 120/120 background checks pass; 50/120 complete candidates emit.
  Every one of the twelve cells emits at least once. The weakest cell emits
  1/10, below the requested 30% target because later cargo screens reject.
* Medium: 35/36 background checks pass; 0/36 fresh-seed, one-attempt candidates
  emit. Cargo/C1 failures are separate from the single failed background.
* Parent-owned normal retry trajectories use the existing targeted DG shipment
  redraw, unchanged profiles and screens: all six medium cells emit, with
  28/28 background checks passing over 28 candidate attempts. See
  [retry-table.md](retry-table.md) and [retry-summary.json](retry-summary.json).
  Those dependent retry attempts are not mixed into the independent raw matrix.
  Gate cost per emitted instance is 11.86–87.11 s, below 120 s in all six cells;
  total generation plus the independent replay is 15.22–109.00 s.

All 156 initial fully booked mass budgets fit both D40 and design displacement;
the least headroom is 344.985 t. All tanks respect capacity, fixed consumables
are preserved, and all 156 booking reference fits report `sized` with transverse
moment balance. Maximum residuals are 0.000005 kg mass, 0.007261 kg m
longitudinal moment and 0.000368 kg m transverse moment. These small numerical
residuals establish the sizing arithmetic only.

No tiny/small arrival has beam/draught >=3.3; their maximum is 3.215252. All 36
medium arrivals are >=3.3, with maximum 3.442945, below the weather method's 3.5
applicability ceiling. Accepted medium backgrounds have minimum B2.3 ratio
1.783630 and weather-work ratio 2.816786 across all sampled states. The least
beam/draught clearance is 0.057055. The worst measured design-GM securing ratio
is 0.870087, leaving 12.99% capacity clearance; 465/468 design-state checks pass,
with the three failures belonging to the one safely rejected raw background.

## Source epochs and reproducible rejected background

The raw grid spans four disk-source hashes and six draws span concurrent edits.
The only files that changed during the grid are:

* `src/stowage/checker/stability.py`
* `src/stowage/checker/stability_policy.py`
* `src/stowage/checker/stability_policy_contract.json`

No builder or generator changed during the grid. Each start/end manifest covers
all package Python/JSON/TOML, parameter JSON/TOML, runtime versions, relevant
environment and measurement wrapper bytes. Long-lived Python imports are not
hot-reloaded: these are disk-source epochs, not snapshots of imported modules.
The four hashes, exact counts and failed reason IDs remain in `final-audit.json`.

Random feeder_500 medium seed5 fails with `background.finisher.not_found` and
missing finite-CG securing/strength/visibility data. The witness has two APT
masses of about 2.45e-12 kg; `hull_tanks.py:325` reports an invalid liquid-centre
division. A fresh-process replay under a stable disk epoch reproduces the same
failure; it is therefore **not classified as a transient source-edit failure**.
Evidence is `replay-random500-medium-seed5.{jsonl,log}`. The gate correctly
rejects it. The original failed draw is retained in the 36-draw denominator;
the replay is not counted as a second independent draw. Its precise numerical
cause and any subsequent repair belong to the builder/checker owners.

[paired-tiny-small.json](paired-tiny-small.json) compares all 120 tiny/small
draws with the previous final matrix. Pipeline/background acceptance and all
25,800 common numeric state, witness ballast and utilisation comparisons are
bitwise identical, with zero changed metric names. This is a bounded paired
check, not a claim about every condition or every external checker edit.

## Realism and compute

[final-realism-summary.json](final-realism-summary.json) reports box/deck masses
and occupancy. Background TEU is the D42 continuous equivalent representation,
not a count of actual physical containers. Sub-tare equivalent records remain:
medium minimum masses range from 0.460 t to 5.711 t by cell; maximum counts below
the 2.2 t reference range from zero to twelve. These must not be described as
physical ISO-container weights. Medium background occupancy ranges roughly
24–33%; the decision booking accounts for a much larger share than tiny/small.

Two one-thread workers reserved 2 CPU/6 GB and no GPU. Final matrix estimate was
1200 s; actual elapsed to the last persisted row was 539.690 s (8.99 min).
Peak measured **self-process** RSS was 2196.6/1897.8 MiB, excluding reusable
finisher children. Both recorded worker sessions confirmed exit0. The separate
one-CPU fresh replay took 14.618 s generation plus 0.069 s diagnostics against a
15–30 s estimate, with self peak RSS475.4 MiB; it also confirmed exit0.
`compute.json` has exact PIDs/timestamps. The earlier observer-key restart is
fully disclosed in `observer-restart-incident.json`; all affected partial rows
are quarantined outside the final matrix.

## Reproduction

Run with `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1` and
`UV_CACHE_DIR=$PWD/.uv-cache`:

```sh
uv run python experiments/background-1028/followup-medium/measure.py --rungs medium tiny small --vessels feeder_800 feeder_500 feeder_1700 --generators random --out experiments/background-1028/followup-medium/final-random.jsonl
uv run python experiments/background-1028/followup-medium/measure.py --rungs medium tiny small --vessels feeder_1700 feeder_800 feeder_500 --generators simulated --out experiments/background-1028/followup-medium/final-simulated.jsonl
uv run python experiments/background-1028/followup-medium/audit_results.py
uv run python experiments/background-1028/followup-medium/compare_prior.py
uv run python experiments/background-1028/followup-medium/summarise.py --inputs experiments/background-1028/followup-medium/final-random.jsonl experiments/background-1028/followup-medium/final-simulated.jsonl --prefix final-
uv run python experiments/background-1028/derive_realism.py --inputs experiments/background-1028/followup-medium/final-draws.jsonl --prefix followup-medium/final-
uv run python experiments/background-1028/derive_margins.py --draws experiments/background-1028/followup-medium/final-draws.jsonl --out experiments/background-1028/followup-medium/final-state-margins.json
```

Existing output keys are skipped for resumability. Use a new output path for a
new independent experiment rather than appending duplicate seeds.
