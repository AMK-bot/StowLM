# Synthetic port-state simulation (task 3.4)

These are public synthetic scenarios, not measured terminal data or approved
departure plans. The move engine validates each recorded lift; the full checker
remains responsible for certifying departures.

Run from the repository root:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --offline --no-sync python experiments/yard-sim/run.py
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --offline --no-sync pytest -q -p no:cacheprovider tests/yard/test_sensitivity.py
```

`metrics.json` records simulator version 1.1.0, fixed workload seed 41, placement
seeds 2026/2027/2028 and noise values 0/0.2/1. Each noise value overrides both
independent placement error probabilities. Workload is sampled once and shared;
import dwell uses a per-container stream shared between noise settings within
each seed. The original top-level `runs` contains the three seed-2026 settings;
`controlled_comparison.runs` contains all nine, including per-call occupancy,
height statistics, nonempty-stack height histograms, requested/present/blocked
target counts, queue outcomes and excluded call IDs.

The model fixture provides the whole synthetic hull and small ship layout.
The Laem Chabang-like profile is scaled to one block × 12 bays × two rows ×
five tiers = 120 TEU storage positions, with traffic at 4%. Ship cargo is capped
by capacity and accessible discharge slots; conditioning is reported. The fixed
14-day workload has 20 calls, including a pre-origin service cycle, and 14
loading snapshots after the four-day warm-up cutoff. Sampled demand was 350
loads / 327 discharges; conditioned local flows were 177 exports / 51 imports.
The 36 transfer bookings and 35 inbound transfers remain explicitly unsupported.
This is a local-flow diagnostic and cannot substantiate transfer-hub behavior.

## Controlled sensitivity

All comparisons below use the **same two calls** (`regional_feeder-4` and
`regional_feeder-5`) with nonempty target lists and **100% target presence in
every one of the nine runs**. Each row averages six observations (two calls ×
three seeds). Missing-target calls remain in the all-run audit but cannot alter
the conditional blocked-share denominator in this comparison.

| Noise | TEU occupancy | Mean nonempty stack height | Physically blocked targets | Matched presence | All-run mean presence |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0.0 | 52.64% | 2.679 | 72.22% | 100% | 87.52% |
| 0.2 | 52.64% | 2.522 | 83.33% | 100% | 84.53% |
| 1.0 | 55.42% | 2.571 | 68.52% | 100% | 78.00% |

Noise changes occupancy, height and blocking on this matched cohort. No monotonic
direction is assumed: clustering can itself block same-call boxes, and placement
can change residual inventory. The two-call intersection is small and selected
for complete presence; it establishes a deterministic sensitivity regression,
not a population estimate or an efficiency improvement. All-run presence
measures service outcomes separately. All observed stacks stay within five tiers.
The test uses three seeds at noise 0 and 1 and requires a paired effect in each
of the three statistics. Separate regressions prove that incomplete populations
are excluded from every setting and that an empty matched cohort reports `null`,
not a fabricated zero effect.

Occupancy counts twenty-foot storage positions: long boxes occupy two; 45-foot
boxes use the 40-foot yard footprint. Heights count physical boxes in nonempty
stacks. Physically blocked share counts any box above each present target. This
differs from the random-yard generator's narrower out-of-order definition, which
discounts interchangeable target boxes. Means are per-snapshot means, rather
than a pooled per-container blocking probability.

## Plausibility is still uncalibrated

Task 1.5 supplies an assumed **60% initial occupancy**, five stored tiers and
placement probabilities 0.15/0.20. It supplies no numerical reference for the
height distribution or blocked-target share. This experiment starts empty and
collects after four days (the longest gate window and mean import dwell); gamma
tails persist, and four days does not prove stationarity. Its warmed-up occupancy
must not be compared to the 60% startup target as calibration evidence. The audit
records zero initial inventory and explicitly marks plausibility uncalibrated.

Closing the done criterion needs justified reference ranges/distributions for
occupied TEU fraction, stack heights (including whether empty stacks count) and
blocked share with the same target/blocking definition. It also needs documented
cargo mix, layout/equipment, initial inventory, traffic/dwell and observation
phase so reference and simulation represent comparable operating conditions.
Task 1.5/X1 must supply or approve that evidence; then create legal synthetic
initial inventory with future exits/calls at the agreed occupancy, choose and
justify a dwell-aware warm-up/measurement window, rerun multiple seeds and
compare distributions with the approved bounds. A tier ceiling proves legality,
not plausibility. No empirical acceptance interval has been invented here.

## Mechanics approximations needing the leader's decision entry

- APPROXIMATION: fixed per-call berth service duration; lifts occur at loading
  start without timed equipment service, crane routes or travel. Equipment and
  tier limits still bind, but outputs do not measure crane throughput.
- APPROXIMATION: explicit load order with greedy length-compatible ship slots,
  preserving intact long-box pairs and preferring hold before deck; this is not
  a packing optimizer or departure-feasibility certificate.
- APPROXIMATION: nearest legal stack for each blocker, without future lookahead;
  squared Euclidean distance uses block origins and ISO 6.058 m / 2.438 m pitches,
  without lanes, crane reach paths or calibrated travel time.
- APPROXIMATION: explicit DG blocks and powered reefer rows are enforced, while
  detailed yard DG pair segregation and OOG clearance geometry are omitted.
  Departure checking does not establish safety of these intermediate yard states;
  the synthetic simulator makes no such safety claim. Operational use needs
  detailed yard rules and validation, not a relaxed departure checker.
- APPROXIMATION: synthetic negative-binomial calls, gamma dwell, independent
  placement errors and capacity spillover model scenario variety, not fitted
  terminal operations (profile assumptions in D17).

No physics or hard check is relaxed. Actual transshipment needs the model/move
owners to authorize intermediate discharge while preserving final POD, then
retained inventory, dwell readiness, compatible connections and outstanding
queues/warm-up inventory. The present final-POD discharge contract cannot
implement that safely by relabelling transfer cargo.

Fresh sensitivity verification: three tests pass; six-run regression 1.96 s in
the pilot. The final nine-run audit took **2.78 s**, versus ~3 s estimated from
the pilot, one CPU core, no GPU, estimated <1 GB RAM (peak not measured). No
heavy job, cloud call or cache-physics change was needed. See the task fix log
for full yard/dependent verification.

Sources: `params/ports/README.md`, D17/D18 and the repository's model/move
contracts. Tanaka & Voss (2019), DOI: 10.1016/j.ejor.2019.06.014, describes the
block relocation problem: moving boxes above a requested box to reach it. It is
not used as an empirical reference for these synthetic yard statistics.
