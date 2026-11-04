# V3 tiny rejection: qualified C2 draught/trim conflict, not occupied cells

27 October 2026, reproduced at 18:11–18:15 EDT, ahead of the 19:10 deadline.
No production code, tiny specification, split key, cargo fact, C2 constraint or
margin was changed. The fault is not demonstrated in generator capacity sizing
or in 2.2's exclusion of decision cells. Route the numerical-model diagnosis to
the 3.7/4.4 owners, with 2.2 involved in choosing any revised operating condition.

## Exact reproduction

Single-thread commands (also set `UV_CACHE_DIR=$PWD/.uv-cache`):

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run python experiments/instances/diagnose_v3_tiny.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/instances/diagnose_v3_lp.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/instances/diagnose_v3_tiny.py --boxes 5 --attempts 1 --prefix v3-tiny-five
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/instances/diagnose_v3_tiny.py --generator simulated --size small --seed 110100000 --attempts 5 --prefix v3-sim-small
```

The default replays random-yard tiny seed 100000000, feeder_500,
laem_chabang_like, ratio/noise zero, six targets, DG on, max20 attempts, and
the campaign's C1-then-C2 ordering (C2 omitted only for individual DG probes,
exactly as `instances_v3._completion_screen`). It reproduces the audit exactly:
20 slot-occupancy aggregate tags, C2=16 and C1a/C1g/C1h=4 each, five shipment
redraws. Elapsed 21.67 s. Saved instances are explicitly REJECTED diagnostics,
not canonical shards or approved plans. Source campaign's admitted epoch is
3.1.0, condition `c3-fixed-background-5`, linear model3.4, fingerprint
`2497bc54df98534644eee1b1e3da469bdda74068599f89ef076b4563354ab7ae`.

Evidence files: `v3-tiny-diagnostic.json`, `v3-tiny-c2-rejected.json`,
`v3-tiny-c2-lp.npz`, `v3-tiny-lp-diagnosis.json`; separate five-box and
simulated-small files use the command's prefix. These synthetic measurements
are not git-ignored. No canonical campaign shard was overwritten.

## Hypothesis tests

1. **No background overlap.** `fleet_conditions.background_condition` excludes
   every selected stack and deck stacks on active hatch covers before balancing.
   `fleet_balance.balance_background` independently repeats those exclusions.
   Restricting tiny to two hold tiers and one deck tier does not reopen omitted
   tiers of the selected stacks. The captured instance has six model cells,
   zero onboard placements and 204 fixed loads. All six engine cell flags are
   free and all fixed **cell** masses are zero. Fixed-load mass remains correctly
   present in the physical constants. Geometric checks found zero fixed-load
   centroids inside selected cell boxes and zero positive-volume intersections
   between background silhouettes and those boxes. FixedLoads are not placements.
2. **100% tiny fill is not this failure.** The first six-box candidate has no DG
   and passes every C1 key. Reducing to five targets on the same six cells,
   reconditioning against the actual five-box mass, still passes all C1 and
   fails C2. The one-attempt five-box diagnostic took 8.14 s. Reducing the rung
   would change its specification without resolving this captured failure.
3. **Simulated small has the same screen-stage symptom.** Seed110100000 has
   20 targets on68 cells and zero placements. Its sole candidate reaching C2
   passes every C1 key, then fails C2. Five attempts take14.41 s; other attempts
   are rejected earlier by size/IMDG screens. Its D29 upright mean draught is
   6.626318 m. Its full LP is infeasible and becomes feasible when all draught
   rows are removed; unlike tiny, the trim/aft pair alone is feasible, so do
   not assign tiny's exact two-row contradiction to the larger slice. This is
   not a100%-fill case. Five archived simulated-tiny
   requests also report C2 failures, but were not all rerun here.

## Exact C2 contradiction

The first captured tiny C1-passing candidate has95,327.517992 kg of cargo.
The saved NPZ contains the actual C2 arrays, bounds and constraint names; it
does not regenerate or edit a margin bank. Zero-objective feasibility LPs use
the engine's kg variables, free-cell upper bound27,317.387415 kg, remaining
cargo equality, 18 adjustable-tank bounds and one HiGHS thread.

| Quantity | Value |
|---|---:|
| Required affine trim lower bound | +0.008966988435 m |
| Required affine aft-draught upper bound | 6.832068567423 m |
| Minimum attainable aft draught subject to trim bound | 7.060497428830 m |
| Joint-bound infeasibility gap | 0.228428861407 m |
| Maximum attainable trim subject to aft bound | -0.589387000992 m |

The trim/aft pair alone is infeasible with every other inequality removed;
other draught rows create further conflicts. Removing **all** draught rows
makes the remaining full LP feasible. This deletion is diagnostic only and
must never be used to accept an instance. A simpler zero-objective LP also
fails, so the reference-centering objective is not the cause.

At the identical uniform booking centroid used by the background initializer,
with zero ballast, D29's nonlinear upright audit reports draught6.852602135909 m,
trim0 and solidGM1.8 m. The affine prediction is mean draught6.971609134459 m,
trim0.815328797554 m and aft draught7.379273533236 m. The ship's draught limit
is7.05 m. Qualified directional reserves tighten the affine aft upper bound
by0.217931432577 m and forward upper bound by0.409379602992 m. The lower trim
reserve is1.086966988435 m, exceeding the1.078 m absolute trim limit and hence
forcing the affine trim positive. These are conservative linear-screen facts;
the D29 centroid audit does not establish a checker-valid discrete loading.

This independently confirms the earlier campaign-owner observation in
`experiments/instances-v3/C2-YIELD.md`, and adds a reproducible contradictory
row pair, exact numerical gap, occupancy check and reduced-fill experiment.

## Proposed owner patch, not applied or qualified

**3.7 primary / 4.4 review:** retain C2 fail-closed. Inspect the response/reference
transfer for this saved Instance in `linear/bands.py:band_for_instance`,
`linear/operations.py:constraints` (trim and endpoint draught rows), and
the directional policy in `linear/margin_policy.py`. Recenter/refit trim and
draught around the actual fixed-background condition; qualify any genuinely
narrower validity domain/ballast windows against independent nonlinear holdouts.
Recompute and repin directional evidence before using new rows. Do not set
`global_screen=0`, delete endpoint rows, reduce reserves without evidence, or
accept the background's solid-GM audit as a C2/full-checker certificate.

**2.2 secondary:** there is no evidenced overlap patch to apply. Add explicit
`reserved_cell_ids`, `background_cell_ids` and an empty intersection to the
condition audit before FixedLoad aggregation, asserting the existing whole-stack
and active-cover exclusions. If the current condition still has no qualified
C2 completion after the linear review, expose an owner-controlled lower
departure target and continue the existing deterministic1%-design target search
until both balancing and the approved completion screen succeed. Keep all
actual mass, moment, stack, visibility and design-band constraints. This needs
a reviewed generator/fleet handoff because 2.2's current helper has a Vessel,
not a complete Instance; do not introduce a hidden cross-layer C2 dependency.
No lower target has been verified here and no public signature was changed.

**Generator action:** preserve current tiny cells/count/fill and split identity.
Do not resume hundreds of tiny requests until an unchanged-seed C1+C2 pilot
has nonzero accepted yield under the new pinned epoch. Production `random*`
files remain unchanged because capacity is not the demonstrated defect.

## Compute and verification

Reserved2CPU/4GB/noGPU, <=20min; measured baseline16.34s from supplied audit.
Root used one pinned native thread; independent read-only LP review used one
thread for about5s. Three bounded replays took21.67+8.14+14.41s; the saved-LP
verification took under1s. Memory was not instrumented. Live admission showed
13.90 load,95% memory free and13 other reserved cores; ours fit at15/16.
`ps` is sandbox-restricted. No paid service, unbounded simulation or campaign
was launched; only owned sessions were polled. Ledger is released at completion.

Final verification: `UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run pytest -q tests/generate`
completed with exit0: **257 tests passed** (default collection independently
confirmed257 tests). No production code was changed. A final one-attempt
occupancy replay took8.07s and explicitly records `free_cells=6` and
`fixed_cell_mass_kg=0` in `v3-tiny-occupancy-diagnostic.json`.
Total reservation elapsed about7min versus <=20min estimate; maximum2CPU,
RAM not measured, noGPU. Completed and reported before19:10EDT.
