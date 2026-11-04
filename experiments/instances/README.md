# Random yard instances (task 3.5)

Run `uv run python -m stowage.generate.random_yard --size all --count 1 --seed 1`.
The CLI uses `stowage.io.save_instance`, rejects stub-tainted output, and writes
one resumable JSON shard plus its audit sidecar per seed under each rung. Existing
shards must match the full family configuration before resumption. Missing audit
sidecars are regenerated deterministically. Total shards per invocation are
capped at 200; larger campaigns require compute admission outside this CLI.

The active generator is **1.4.0** (loaded full exports; 4.3 adapters **2.4.0**).
Full-rung defaults and bounded initial ballast now target the design band;
see [full-export measurements](full-export.md). Tiny/small/medium retain D29's
load sizing and background condition. Small/medium demand derives from half of
the actual vessel-region TEU and compatible per-length slots, using task 2.5's
expected equipment mix. The realised draw must also respect those budgets and
pass depth-0 C1 completion checks. C2 is off. `SizeRung.boxes` values
6/36/150/2000 are **maximum ceilings**, retained for task 4.3 compatibility;
they are no longer fixed default load counts. See [D27 capacity sizing](d27-capacity.md).

| Rung | feeder_500 decision region | Targets | Yard blocks × bays × rows | Available PODs |
|---|---|---:|---|---:|
| tiny | Shared tiny preset restricted to 4 hold + 2 deck cells, one hatch | 6 | 1 × 16 × 1 | 2 |
| small | Shared small preset; 44 physical cells | 13 | 1 × 24 × 4 | 2 |
| medium | Shared medium preset; 180 physical cells | 53 | 3 × 24 × 6 | 2 |
| full | Whole vessel; 372 physical cells | 189 | demand-sized, at least 4 × 24 × 6 | 2 |

Bays are 20-foot ground units. D27 uses task 2.2's current `slice_preset`
regions directly; the earlier D20-specific region selection is historical.
Shared task 2.2 slice accounting preserves the whole lightship, hull,
tanks, fixed loads and applicable DG Document of Compliance areas. Tiny's DoC
stack references are clipped to its retained stacks. Screening still does not
certify a feasible stowage plan. The previous [KCS migration report](kcs-migration.md)
records generator 1.1.0, not the current ladder.

Yard storage/equipment heights, reefer rows and DG block capability come from
2.6. Each target has a separate randomly selected stack; two ground bays per
stack avoid overlapping long footprints. **Blocking ratio** is the fraction of
required boxes with a later retrieval cohort above: required cargo leaves in
cohort 0 and non-load background in cohort 1. POD describes the ship's later
destination, not yard retrieval time. Same-category identities interchange
within a cohort; background cannot substitute for a current target. Target ID
order creates no blockers. Exactly floor(ratio × n + 0.5) target stacks receive
one background box, giving error at most **0.5/n**, including endpoints 0 and 1.
This follows retrieval priorities in Tanaka & Voss (2019),
doi:10.1016/j.ejor.2019.06.014, on the block relocation problem.

APPROXIMATION: these controlled two-cohort layouts are stress instances, not
calibrated terminal traffic. Background is empty dry equipment with the target's
length and tare, classified by the D25 catalogue weight classes. Targets come
from task 2.5, including its D25 weight/category semantics. Tiny conditions only
equipment to dry20: it retains the source loaded/empty mix, exact masses and
DG sampling. It does **not** force empty cargo or disable DG. Larger rungs retain
the full equipment mix. All physical and cargo constraints remain the checker's
responsibility.

Tiny has 6! = 720 identity-to-cell assignments and at most 6! loading orders:
**518,400 canonical discrete assignment/order pairs**. Hatch access imposes
hold-before-deck loading. Each independent blocker needs one relocation; two
reserved empty stacks can hold all six, attaining that yard lower bound.
This counts canonical choices, not cyclic move sequences. Ballast remains a
continuous feasibility problem for each choice; exact solver/checker validation
is still required. D29 uses `recondition_slice` on the final selected geometry
for every candidate's actual load-list mass, before screening. Yard blockers
are excluded. The returned background cargo includes deck silhouettes.
`departure_tank_masses` initializes consumables at 97% and adjustable ballast
at zero; consumables remain live tanks and are not added to fixed loads.
Each retry starts from the immutable geometry template, preventing accumulated
background cargo or source records. This is the shared synthetic C3 loading
condition documented in `stowage.vessel.fleet_conditions`, not a loading manual.

Cheap necessary-condition screens reject TEU overflow, individual-length slot
count overflow (disjoint 40/45 pairs), excess powered reefers and aggregate stack
mass. They do not certify mixed packing, local heights/masses, DG, strength or
stability. Depth-0 `completion_check` additionally requires every C1 bound to
pass (`lookahead=0`, `global_screen=0`); no action search or C2 screen is used.
APPROXIMATION: after a C1a failure, individually impossible DG shipments are
identified with single-target probes and resampled from task 2.5. Other bookings
are retained; no DG facts are relaxed. Every resulting whole candidate must
pass all capacity budgets and C1 again. This sequential conditioning changes the
accepted distribution and is counted by `shipment_redraws`.
Rejections carry catalogue rule IDs; unsupported DGL draws are explicit.
Audit screening rate is rejected draws / all draws. Family identity keys stay
unchanged: vessel, port, size, requested ratio, seed and generator. Provenance
records `random_yard=1.4.0`; SplitMembership's canonical key selection is unchanged.
Cold vessel physics is cached once per vessel/process; each instance gets an
independent decision region. Shard directories are locally git-ignored.

## Historical D29 verification (1.3.0)

D29 command:
`UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run pytest -q tests/generate -o addopts=--import-mode=importlib`
— **207 passed in 23.32 s**. Random-yard tests alone: **40 passed**.
Coverage includes actual booking mass excluding blockers, live 97% consumables,
zero initial ballast, background silhouettes, fresh-template reconditioning on
rejection, deterministic output, round-trips and unchanged split keys.

Full-physics feeder_500 seed-1 smoke cases (blocking ratio 1) all round-tripped
with unchanged split keys. Tiny/small/medium booking masses were
79,232.95 / 189,979.73 / 809,540.66 kg; each shared helper reported its design
band achieved, with 66 / 60 / 36 background deck silhouette blocks. The full
111-box booking weighed 1,696,553.31 kg and reported `achieved_design_band=False`:
the full decision region has no omitted stacks available for fixed background.
The generator preserves this truthful helper result rather than inventing cargo
in decision cells. Cached smoke times were 0.440 / 0.067 / 0.075 / 0.278 s.
These checks do not certify departure plans; the full checker still decides.
No split-key fields or load-size policy changed in D29; only the producer
version and initial physical condition changed.

## Historical D27 verification (1.2.0)

Geometry-only seed-1 diagnostics completed for all three fleet vessels and four
rungs. The largest full case took 2.914 s; other cases took 0.014–0.427 s.
Target counts, candidate attempts and shipment redraws are recorded in
[d27-capacity.md](d27-capacity.md). These are not full-physics/checker acceptance
measurements. Subsequent feeder_500 full-physics seed-1 shards passed clean
save/load round-trips and C1 at realised TEU 6/20.25/88.25/183.0; cached times
were 0.309/0.058/0.072/0.311 s. Candidate attempts were 1/1/1/4.
The requested generate+yard suite initially had one stale task 4.3 assertion
requiring `attempts == 1`; C1 now correctly rejects its first candidate and
accepts its second, with determinism/restoration passing. Final requested suite:
**197 passed, 1 failed in 36.46 s**, with that same assertion the sole failure.
Targeted random-yard + yard tests: **47 passed in 9.07 s**; `compileall` passed.
Earlier counts below belong to previous versions.

**Task 4.3 owner action:** besides the stale test, update `instances.py` /
`instances_v2_random` to treat `SizeRung.boxes` as a ceiling and use
`capacity_load_size` or the actual count before tagging explicit `n_boxes`.
Otherwise old specifications can mislabel output quantities. Task 5.3 canonical
split keys remain unchanged. Its `GenerationReport` must also propagate raw
`completion_rejections`, `shipment_redraws` and `c2_screened`, which it currently
drops. See [D27 details](d27-capacity.md).

## Historical D20 / generator 1.1.0 verification

The executive reports **151 tests passed**, including structural and split-identity
checks for all three fleet vessels across all four rungs, with this command:

`UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run pytest -q tests/generate tests/yard`

Full-physics seed-1 generation and clean save/load round-trips passed for all
four rungs; cached times were 0.068, 0.053, 0.267 and 0.073 seconds. Medium
required 34 candidate draws; the other rungs accepted their first draw. Details,
including the outstanding task 4.3 adapter warning, are in
[kcs-migration.md](kcs-migration.md). No checker-approved plan is claimed.
An additional geometry diagnostic over seeds 0–9 per rung had no generation
failures: screening rates were 0%, 0%, 87.65% and 16.67%, taking 1.215 s total.

## Historical pre-D20 verification

The following reports describe **generator 1.0.0 and pre-D20 geometry**, not the
active ladder or cargo conditioning. `verification.json`,
`geometry-diagnostic.json` and old local shard audit sidecars are historical.
In particular, their 504-cell full vessel, 280 full targets and empty-only tiny
cargo must not be used as current acceptance evidence.

- Original generator tests: 17 passed, using explicitly geometry-only unit fixtures.
- Four full-physics seed-1 shards round-tripped: cold setup 55.169 s; cached
  small/medium/full 0.059/0.102/0.142 s. All accepted their first cargo draw.
- Forty geometry diagnostics, seeds 0–9 per rung: screening rates 0%, 0%,
  33.3%, 0%; medium had five TEU rejections in 15 draws. Warm total 1.178 s;
  cold geometry 0.091 s. These were small-sample necessary-condition rates.

Historical compute used one core, BLAS/OpenMP pinned to 1, no GPU, memory
estimated below 1 GB (peak unmeasured). The four-shard run took about 56 s;
the sandbox refused its requested `nice` adjustment. Earlier exploratory calls
into an in-progress hull builder were interrupted after more than six minutes.
