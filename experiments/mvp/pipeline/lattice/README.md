# Stored what-if plans

Synthetic development voyages only. Plans are computed with Frontier Engine
0.13.0 and labelled **Stored plan, computed on <date>, rechecked at <time>**.
Only a freshly executed gate may say “rechecked now”; memo hits name the actual recheck time.
This is a development demonstration, not a sealed result or a live-search speed claim.

Every frame claim below means **crane frames walked on this plan**. Both frozen
base plans carry **crane sequence repaired** provenance; their passing frames
belong to those repaired derivatives. Instruction plans have their own sequence
labels in the tables and returned API payloads. A repaired comparison base does
not imply that a native instruction replan was repaired. The planner replans
from scratch; minimizing changes from the base is not implemented.

## Hero and progress

The detached instruction batch belongs to `current-v6`; its state and owned
process IDs are in `screening/current-v6/state.json`, with its active manifest
and graceful transition audit beside it. Four explicitly labelled standard-plan
portfolio selections get fresh full gates first, followed by the remaining
supplemental and original requests. The four draining v4/v5 workers retain
their exact reserved slots.
The primary is the 87-box
C6 feeder_500 low-weight voyage: 66 extra lifts, two reefers, two discharge ports
and all 144 crane frames passed; **crane sequence repaired**. This primary is frozen so subsequent stored
instruction results continue to use the same base. The preferred feeder_800
was screened and is now the distinct backup: 163 boxes, 115 extra lifts and all
263 frames passed in a fresh native sweep; **crane sequence repaired**. Screening has finished; the batch
continues with checkpointed results and fleet admission.

Both original C6 sequences failed real intermediate heel checks: the 87-box
plan at frame 76 (2.00305°), and the 163-box plan at frame 183 (−2.02147°).
Their accepted derivatives retain explicit lattice postprocessing labels,
original engine plans and transformation proofs. Failed sequences and partial
trials remain historical evidence; acceptance requires the complete fresh gate.
All native jobs remain within four outer slots while reservations are active.
`progress.json` counts stored bases and instructions; completion requires a
gated base plus ten gated instructions.

<!-- HERO_STATUS_START -->
Hero: **D192.medium-feeder_500-f60-low-s192200002**, 87 boxes, 66 extra lifts including projected later-port lifts; 144 crane frames walked on this plan and passed; crane sequence repaired. Computed 2026-11-02T02:50:09.142520+00:00.

Source: `experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-low/seed-192200002.json`. Screened in-envelope development candidate with a full original-instance checker pass and every crane frame inside limits; synthetic DG cargo, reefers and 2 discharge ports, with 40.42% of section capacity open.

Backup: **D192.medium-feeder_800-f60-low-s192200100**, 163 boxes, 115 extra lifts, 263 crane frames walked on this plan and passed; crane sequence repaired.

`hero.json` pins the sources and selection evidence. `fixtures/hero-instruction.json` and `fixtures/hero-replan.json` identify the selected fully gated DG rule that visibly changes this base.
<!-- HERO_STATUS_END -->

The first stored DG instruction moves class 2.2 container `QZXU0000288` away
from the actual accommodation, from slot `0220302` to `0020584`. Its plan has
70 extra lifts against the base's 66, changes 83 placements and passed all 146
native crane frames; **crane sequence not repaired** for this instruction output.

The class 2.2 distance-one, distance-two and distance-three searches produced
the same 70-lift plan (`359e8fd8273c1b493b84d0ad424d4c235607c86962db69a95a7218055d2ef0ee`).
They were independently computed at the recorded standard budget and have
distinct canonical rule keys. Each changes the frozen base; they do not
demonstrate three different placements from one another.

The primary had a 900-second standard search allowance; its historical search
elapsed time was not measured. Its sequence repair took 598.876 seconds; that repair includes its 351.321-second fresh
release sweep. Do not add that sweep a second time. The
measured source is `screening/current-v3/cabff3d68d56e64b.repaired.json`.
The repaired sequence's largest absolute heel is 1.99536°, maximum shear ratio
0.20378 and maximum bending ratio 0.16637, from its complete native frame
checkpoint in `cabff3d68d56e64b.repaired.repair/attempt-5.native-frames.json`.

`runs/*.json` checkpoint every completed request; `*.running.json` and
`*.incumbent.json` record active work and checked incumbents. Run the progress
command below to refresh the measured table in [RULES.md](RULES.md).
Pending cells are unmeasured. A budget failure does not prove impossibility.

## Frozen demo snapshot

[20261102T051624.103855Z](releases/20261102T051624.103855Z/release.json), created 2026-11-02T05:16:24.103855+00:00, contains **10 primary instruction plans, two bases and one proved refusal**. All twelve plans passed their recorded original-instance checker and every crane frame; all twelve also passed native runner authentication. The two bases are labelled **crane sequence repaired**; the ten instruction outputs are **crane sequence not repaired**. The ten instructions have 6 distinct plan trajectories, each changing the base. Two keys (`dg_keep_away-12`, `max_tiers-08`) explicitly select independently gated standard-budget portfolio donors; both requested searches and donor histories are retained.

[LATEST_RELEASE.json](LATEST_RELEASE.json) pins the full catalogue and three-entry demo subset independently. The immutable release remains stable while the mutable detached batch continues. Run `lattice.regate` at launch in the persistent demo process before playback; snapshot authentication is not a new physics run. [completion.json](completion.json) records the accepted frame counts, 305 passing offline tests, remaining PID51958, and assumptions. The three off-lattice keys are verified absent from this snapshot.

## Python API

```python
from experiments.mvp.pipeline import lattice

# Pin this catalogue digest independently in the demo's release configuration.
summary = lattice.regate(expected_catalogue_sha256=pinned_catalogue_sha256)
candidate = lattice.lookup(instance_sha256, canonical_rules)
trusted_candidate_sha256 = lattice.candidate_sha256(instance_sha256, canonical_rules)
# runner.run_packet(..., stored=candidate,
#                   stored_sha256=trusted_candidate_sha256)
proof = lattice.refusal_lookup(instance_sha256, canonical_rules)
```

Keys contain the canonical original instance SHA256 and a sorted, duplicate-free
set of strict reader envelopes. Sentences are captions, never keys. Lookup
returns JSON-able data or `None`, preserves the caller's confirmed rule order
for the runner, and never starts a search. The first lookup regates if launch
regating has not already done so. Repeated lookups use the current-process gate
only while the stored bytes, engine identity and physics epoch stay unchanged.

The catalogue authenticates records against a launch snapshot. A release must
pin its digest outside the input store: accepting a replacement catalogue from
an untrusted input would not provide independent provenance. The runner needs
the explicit trusted candidate digest because its bundled fixture has a
different application pin. This folder does not modify the runner or app.

## Freeze a demo release

After at least ten primary-hero instructions have complete recorded gates and
`fixtures/shot6-backup.json` contains its stored max_tiers fallback, freeze the
current catalogue:

```sh
UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
uv run --offline --no-sync python -m experiments.mvp.pipeline.lattice.release
```

The CLI prints the new timestamped directory and catalogue pins. It holds the
source catalogue lock, authenticates every entry, copies exact bytes, then
publishes the staged directory in one rename. Existing releases are never
replaced. `release.json` records the source pin, entry counts and copied keys
outside the input stores. This verifies an authenticated copy only; it performs
no native checker, physics or search work. Historical computed dates and
compatibility proofs remain in the copied bytes.

Each release contains the complete `store/` and a separately copied and pinned
`demo-store/` with the hero base, hero replan and shot-6 fallback. Continued
batch writes cannot change either copy. `--no-demo-store` omits the small copy;
the ten-instruction and contained shot-6 guards still apply. Run the demo
against the release path and pin its catalogue digest independently in the
demo configuration:

```python
from pathlib import Path
from experiments.mvp.pipeline import lattice

frozen_store = Path("experiments/mvp/pipeline/lattice/releases/<timestamp>/store")
pinned_catalogue_sha256 = "<catalogue_sha256 printed by freeze_release>"
summary = lattice.regate(store_dir=frozen_store,
                        expected_catalogue_sha256=pinned_catalogue_sha256)
candidate = lattice.lookup(instance_sha256, canonical_rules, store_dir=frozen_store)
trusted_sha256 = lattice.candidate_sha256(instance_sha256, canonical_rules,
                                        store_dir=frozen_store)
# runner.run_packet(..., stored=candidate, stored_sha256=trusted_sha256)
```

Launch regating remains required. For the three-plan subset, use `demo-store/`
with its separate `demo_catalogue_sha256`. Default lookup continues to use the
mutable main store.

Every released plan passes the original-instance full checker with no active
stubs, every confirmed instruction, and `app.stage_physics` at **every** integer
crane step, including arrival and departure. A missing or failed gauge withholds
the plan. Intermediate physics retains the app's documented arrival-ballast
approximation; it does not model a ballast-transfer programme. Lashing uses the
checker's approximation and synthetic gear. Lift comparisons use the engine's
`weighted_nonproductive` objective, including projected later-port lifts;
current physical lifts are reported separately as `total_lifts_now`.

## Batch and playback

`standard_plan_pool.compute(job)` is an explicit fallback after a requested
instruction had its own standard-profile search and its candidate failed a
physical crane-frame gate. It selects a different, already checked plan from
another standard search on the exact same original instance, then runs the
complete native gate again for every requested instruction and every crane
frame. Live-profile plans and requests the base already obeys are excluded.
No binding search or sequence repair is performed by this fallback.

This assumes portfolio selection across standard searches is an acceptable
fallback after the requested search has run. It does **not** claim that the
requested binding search produced the selected plan, or that its stowage or
move multiset stayed unchanged. Outputs say `plan_producer:
lattice.standard_plan_pool` and `native_engine_output: false`. They preserve
both exact source byte snapshots, SHA256 pins, complete original records,
rules, dates, profiles, engine identities and compatibility proofs. The plan's
`computed_utc` remains the donor's date; `requested_computed_utc` and
`pool_selected_utc` distinguish the requested search and new selection. A
row retains the requested run's `engine_wall_s`, `wall_s` and `total_wall_s`;
new runs separate `preflight_wall_s`, `initial_admission_wall_s`, `native_gate_wall_s`
and `release_wall_s` (work after admission). Historical portfolio retry totals
included admission and are shown as unknown in the comparable retry column;
the original raw numbers remain preserved. `portfolio_native_search_s` is
zero. These separate clocks are not added or counted as another search. A
failed fresh gate checkpoints separately and cannot replace the canonical run
or stored plan. Batch specs must pin both source paths and byte digests using
`standard_plan_pool_{requested,donor}_{path,sha256}`.

Run from the repository root:

```sh
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export UV_CACHE_DIR=$PWD/.uv-cache
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
uv run --offline --no-sync python -m pytest experiments/mvp/pipeline/lattice -q
uv run --offline --no-sync python -m experiments.mvp.pipeline.lattice.report
nohup uv run --offline --no-sync python -m experiments.mvp.pipeline.lattice.current_campaign \
  --campaign current-v3 \
  > experiments/mvp/pipeline/lattice/current-campaign.log 2>&1 < /dev/null &
# Use the batch command below only after the campaign's supervisor has finished.
nohup uv run --offline --no-sync python -m experiments.mvp.pipeline.lattice.batch \
  experiments/mvp/pipeline/lattice/screening/current-v6/manifest.json --workers 4 \
  --campaign-control experiments/mvp/pipeline/lattice/campaign-control.json \
  > experiments/mvp/pipeline/lattice/screening/current-v6/batch.log 2>&1 < /dev/null &
```

The standard profile is imported from `runner.profiles`, recorded per result,
and limited to its 900-second engine allowance. Admission waits precede that
allowance. A timeout retains any checked incumbent and independently regates it.
Four independent process slots prevent one slow instruction blocking the rest.
Six slots are available only after load stays below 30 for 120 seconds and no
reserved screen work remains. `reserved_screen_paths` prevents an active screen
and batch together exceeding four outer worker slots. Every
new job waits for fresh fleet telemetry: at least eight enabled cores, load1 at
most 45, age under 120 seconds. Missing/stale telemetry waits without raising.
Only owned process IDs are stopped at their caps.

A failed crane sequence may undergo an explicitly labelled lattice
postprocessor (`plan_producer="lattice.sequence"`). It swaps only independent
adjacent loads, or a load and ship-neutral yard relocation. The public move
engine must accept both orders and reach the same normalized world state;
stowage, moves and lift objective remain identical. Original plans, source
hashes and each transformation are retained. Trial frame reuse only guides the
repair. Release always performs a fresh full checker, rule recheck and native
every-frame sweep. These plans carry a deterministic repair label and the original engine plan.
A bounded alternative (`plan_producer="lattice.sequence_blocks"`) permits at
most 32 loads, ship-neutral yard relocations and hatch-cover on/off moves, with
up to four preceding moves included before the first failed frame.
It uses a cargo-only moment proxy for ranking; covers make no change to that
proxy. At each beam depth, the best legal representative of each exact
load/cover event history gets one slot; ship-neutral yard-order variants share
a slot. The diversity policy and physical-trajectory hashes are recorded.
Every candidate move must pass public replay, and the complete block
must reach the identical world state, including every cover location. The
exact move multiset, stowage and lift objective stay unchanged. The beam stays
at most four. Trial-only composition may repair up to eight separated blocks,
with at most twelve candidates total and one global 180-second trial allowance.
Older callers may request an initial eight-move width; if every beam trial
fails inside that window, it widens to 16 and then 32 under the same global
limits. Requested initial width, adaptive cap and each actual proof width are
recorded separately. Historical resume keeps its initial width unless an
adaptive cap is explicitly supplied.
A local repair is retained only when its block is fully green and the next
failure lies strictly after the proven unchanged-state boundary. Chained plan
hashes and exact frame envelopes remain guidance; release still requires one
fresh full checker, all rules and every native frame for the final plan.

The backup uses `plan_producer="lattice.recover_candidate"`: it reconstructs
the exact recorded block permutation, verifies the candidate hash, public replay
and unchanged lift count, then recomputes all 263 native frames under the
current physics epoch. Historical trial frames provide no acceptance verdict.

The selection marks whether the base already obeyed each instruction. Such a
result demonstrates rechecking, not an instruction-driven plan change. Pairs
are separately confirmed reader sentences. Necessary-condition refusals retain
the screen proof and arithmetic, and never receive a checker tick. Allocation
UNKNOWN and exhausted search budgets are not called impossible.

Three separate canonical rule sets were reserved for the original live-replan
demonstration and are absent from its frozen snapshot. The new
[hero coverage campaign](hero-coverage/README.md) explicitly includes these
single-rule keys, superseding that reservation for the expanded catalogue.
Measured on this machine on
2026-11-02, with the 720-second live engine cap; all three recovered incumbents
passed the full checker, rules and all 146 native crane frames; these live
instruction outputs are **crane sequence not repaired**:

| Deliberately unstored sentence | Engine seconds | Total seconds | Admission load1 |
|---|---:|---:|---:|
| In bay 09, use at most 3 occupied tiers per deck stack. | 720.11 | 1001.92 | 38.87 |
| In bay 09, use at most 2 occupied tiers per hold stack. | 720.10 | 999.53 | 38.87 |
| In every bay, use at most 2 occupied tiers per hold stack. | 720.13 | 999.44 | 38.83 |

Sources are the `live-1`, `live-2` and `live-3` records in `runs/`, indexed in
`RULES.md`. Total time includes release admission and native gates, excluding
initial admission. These were approximately 17-minute computations, unsuitable
for an uninterrupted short camera take. Filming a future run live requires
computing it on camera. `fixtures/shot6-backup.json` is a self-contained
max_tiers fallback, independent of the catalogue, once successfully gated.

Numerical acceleration caches are isolated under this folder and ignored by
git. `cache-seeding.json` records immutable physical artifacts copied with their
timestamps preserved; cargo plans were not copied as planner starts. Binding
provenance tracks the active planning runtime closure, including the reviewed
`review3` API and hatch implementation. Measurement-only launchers and reporting
helpers are excluded from that closure. Unreviewed changes withhold affected
results until they are rerun.
No network, GPU, model call or API spend is required by this folder.

`binding-change-audit.json` records the runtime changes observed during the
first screen. A narrow, official review-three migration also permits the
unchanged non-hatch planning core: `binding_compat.py` pins
`../binding/measurements/logic3-before.json`, the exact approved
`../binding/review3/init-export-delta.json` append, reviewed API/package bytes and
native provenance pins. It preserves historical source hashes and planning
origin; it does not replace the full checker, instruction checks or every-frame
gate. This exception covers the base and `max_tiers`, `dg_keep_away` and
`close_bay` rules only. `one_port_per_hatch` uses the new hatch portfolio and
must be recomputed; the non-hatch migration never qualifies an old hatch result.

The first 82-box incumbent passed the full checker but failed the display gate
at frame 10 (heel 2.06485 degrees); it is retained only as the labelled rejection
fixture `fixtures/rejected-heel-frame10.json`. Later incumbents have different
sequences and need their own sweeps. This is not an impossibility finding.

For hatch-off frames, a scoped lattice wrapper skips only the GZ plot that the
app subsequently discards. Native stability and strength parts still run.
Tests compare the entire emitted frame before/after this optimization and verify
closed-hull curves use the original function. No app or native sources are edited.

<!-- SUPPLEMENTAL_DG_AUDIT_START -->
## Supplemental DG pair feasibility audit

`supplemental-dg-pairs.audit.json` records six proposed pairs: accommodation
clearance for class 2.2 at 1, 2 or 3 bays, combined with class 3 at 1 or 2 bays.
All six keys are new relative to the original 25 and three live requests. Both
rules of every pair reject the base and fit the existing safe plan `359e8fd8…`
(its recorded result is 70 lifts and 146 passing frames; **crane sequence not repaired**). The cargo story is
real within this synthetic voyage: QZXU0000288 moves from 0220302 to 0020584,
and QZXU0000328 from 0230184 to 0090106, away from the vessel's accommodation.

These requests are queued for independent standard solves and strict
gates. The feasibility audit itself performed no engine searches or native
physics sweeps and published no plans. Each pair requires its own completed
solve and strict gate before storage; distinct rule pairs may share the same
feasible stowage.
<!-- SUPPLEMENTAL_DG_AUDIT_END -->

<!-- RULE_STATUS_START -->
## Current rule status

As of 2026-11-02T07:04:50.636255+00:00: 15 stored instructions, 2 stored bases, 1 proved refusals and 3 measured live requests.

Stored entries retain their recorded computation timestamps. Engine seconds cover the owned engine call; retry gate seconds are additional fresh release work. Load1 is sampled at admission. Allowances are configured limits, not measurements; historical timeout durations and mixed-scope retry times are unknown (—). Crane sequence labels describe the selected plan; the baseline column describes only its comparison base. Portfolio selections inherit the donor sequence's repair status. Frame counts are crane frames walked on this plan, not a fleet pass rate. Individual checkpoints preserve the complete provenance and gate evidence.

| Instruction | Status | Lifts | Change | Engine seconds | Original total seconds | Retry gate seconds | Allowance seconds | Admission load1 | Base obeys | Computed UTC | Crane sequence | Baseline crane sequence | Crane frames walked on this plan |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---:|
| Keep all IMDG class 2.2 containers at least 2 20-foot bays away from the accommodation. | checked_plan | 70.00 | 4.00 | 900.12 | 1232.54 | — | 900.00 | 35.93 | [False] | 2026-11-02T03:34:08.022638+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| In bay 23, use at most 1 occupied tiers per hold stack. | gate_failed | 70.00 | 4.00 | 900.12 | 1151.47 | — | 900.00 | 33.82 | [False] | 2026-11-02T03:58:26.322961+00:00 | crane sequence not repaired | crane sequence repaired | 117 |
| In bay 09, use at most 2 occupied tiers per hold stack. | checked_live | 70.00 | 4.00 | 720.10 | 999.53 | — | 720.00 | 38.87 | [False] | 2026-11-02T03:17:23.223132+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| In bay 09, use at most 3 occupied tiers per deck stack. | checked_live | 70.00 | 4.00 | 720.11 | 1001.92 | — | 720.00 | 38.87 | [False] | 2026-11-02T03:17:23.231658+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| In bay 21, use at most 1 occupied tiers per hold stack. | gate_failed | 66.00 | 0.00 | 900.12 | 1169.92 | — | 900.00 | 44.63 | [False] | 2026-11-02T03:29:04.991726+00:00 | crane sequence not repaired | crane sequence repaired | 114 |
| In every bay, use at most 0 occupied tiers per hold stack.; In every bay, use at most 0 occupied tiers per deck stack. | proved_refusal | — | — | 1.02 | 1.05 | — | 900.00 | 26.51 | False | 2026-11-02T04:56:03.165294+00:00 | crane sequence not repaired | crane sequence not repaired | — |
| Keep one discharge port per physical hatch cover, across hold and deck. | gate_failed | 66.00 | 0.00 | 900.13 | 1001.54 | — | 900.00 | 44.78 | [True] | 2026-11-02T03:54:58.769004+00:00 | crane sequence not repaired | crane sequence repaired | 36 |
| Keep all IMDG class 2.2 containers at least 3 20-foot bays away from the accommodation.; Keep all IMDG class 3 containers at least 1 20-foot bays away from the accommodation. | gate_failed | 66.00 | 0.00 | 900.15 | 1002.19 | — | 900.00 | 44.05 | [False, False] | 2026-11-02T05:20:03.978252+00:00 | crane sequence not repaired | crane sequence repaired | 21 |
| Keep all IMDG class 3 containers at least 1 20-foot bays away from the accommodation. | checked_plan | 70.00 | 4.00 | 900.15 | 1185.16 | — | 900.00 | 40.09 | [False] | 2026-11-02T03:48:39.429777+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Do not load any containers in bay 03. | gate_failed | 70.00 | 4.00 | 900.14 | 1413.94 | 231.52 | 900.00 | 37.20 | [False] | 2026-11-02T02:53:33.990922+00:00 | crane sequence not repaired | crane sequence repaired | 85 |
| In bay 03, use at most 3 occupied tiers per deck stack. | checked_plan | 70.00 | 4.00 | 900.12 | 1165.91 | — | 900.00 | 28.01 | [False] | 2026-11-02T04:30:47.251638+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Do not load any containers in bay 11. | gate_failed | 82.00 | 16.00 | 900.17 | 1039.87 | 159.06 | 900.00 | 33.43 | [False] | 2026-11-02T04:27:53.636415+00:00 | crane sequence not repaired | crane sequence repaired | 56 |
| In bay 01, use at most 1 occupied tiers per hold stack.; Do not load any containers in bay 03. | gate_failed | 70.00 | 4.00 | 900.10 | 1058.74 | — | 900.00 | 39.03 | [False, False] | 2026-11-02T05:35:13.723224+00:00 | crane sequence not repaired | crane sequence repaired | 85 |
| Do not load any containers in bay 09. | checked_plan | 82.00 | 16.00 | 900.12 | 1159.25 | — | 900.00 | 41.92 | [False] | 2026-11-02T04:36:38.695362+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Keep all IMDG class 2.2 containers at least 3 20-foot bays away from the accommodation.; Keep all IMDG class 3 containers at least 2 20-foot bays away from the accommodation. | checked_plan | 70.00 | 4.00 | 900.13 | 1116.56 | — | 900.00 | 44.05 | [False, False] | 2026-11-02T05:20:03.709748+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Do not load any containers in bay 01. | gate_failed | 70.00 | 4.00 | 900.12 | 1161.44 | — | 900.00 | 35.93 | [False] | 2026-11-02T03:34:09.799586+00:00 | crane sequence not repaired | crane sequence repaired | 99 |
| In bay 01, use at most 3 occupied tiers per deck stack. | checked_plan | 70.00 | 4.00 | 900.12 | 1108.37 | — | 900.00 | 22.97 | [False] | 2026-11-02T04:30:47.251638+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Keep all IMDG class 2.2 containers at least 3 20-foot bays away from the accommodation. | checked_plan | 70.00 | 4.00 | 900.13 | 1159.26 | — | 900.00 | 32.53 | [False] | 2026-11-02T03:48:39.429777+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Do not load any containers in bay 21. | gate_failed | 70.00 | 4.00 | 900.12 | 1140.91 | — | 900.00 | 33.82 | [False] | 2026-11-02T03:58:25.461534+00:00 | crane sequence not repaired | crane sequence repaired | 117 |
| Keep all IMDG class 2.2 containers at least 1 20-foot bays away from the accommodation.; Keep all IMDG class 3 containers at least 2 20-foot bays away from the accommodation. | checked_plan | 70.00 | 4.00 | 900.12 | 1157.69 | — | 900.00 | 28.29 | [False, False] | 2026-11-02T04:56:35.382786+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| base | checked_plan | 115.00 | 0.00 | — | — | — | 900.00 | 39.82 | None | 2026-11-02T03:27:52.932841+00:00 | crane sequence repaired | crane sequence not repaired | 263 |
| In bay 21, use at most 3 occupied tiers per deck stack. | checked_plan | 70.00 | 4.00 | 900.10 | 1127.35 | — | 900.00 | 39.69 | [False] | 2026-11-02T04:17:30.273535+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Keep all IMDG class 2.2 containers at least 2 20-foot bays away from the accommodation.; Keep all IMDG class 3 containers at least 1 20-foot bays away from the accommodation. | gate_failed | 66.00 | 0.00 | 900.14 | 1002.24 | — | 900.00 | 33.79 | [False, False] | 2026-11-02T05:15:21.016546+00:00 | crane sequence not repaired | crane sequence repaired | 21 |
| In bay 01, use at most 1 occupied tiers per hold stack. | gate_failed | 70.00 | 4.00 | 900.16 | 1488.64 | 267.27 | 900.00 | 41.09 | [False] | 2026-11-02T02:52:20.659599+00:00 | crane sequence not repaired | crane sequence repaired | 85 |
| In bay 23, use at most 3 occupied tiers per deck stack. | gate_failed | 70.00 | 4.00 | 900.12 | 992.03 | — | 900.00 | 32.89 | [False] | 2026-11-02T05:32:08.853339+00:00 | crane sequence not repaired | crane sequence repaired | 69 |
| Keep all IMDG class 2.2 containers at least 2 20-foot bays away from the accommodation.; Keep all IMDG class 3 containers at least 2 20-foot bays away from the accommodation. | checked_plan | 70.00 | 4.00 | 900.08 | 1147.69 | — | 900.00 | 33.74 | [False, False] | 2026-11-02T05:15:57.827144+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| In bay 11, use at most 2 occupied tiers per hold stack. | gate_failed | 70.00 | 4.00 | 900.13 | 1520.98 | — | 900.00 | 37.41 | [False] | 2026-11-02T04:45:17.695968+00:00 | crane sequence not repaired | crane sequence repaired | 123 |
| In bay 11, use at most 3 occupied tiers per deck stack. | checked_plan | 70.00 | 4.00 | 900.12 | 1170.43 | — | 900.00 | 38.84 | [False] | 2026-11-02T04:50:17.793946+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Keep all IMDG class 2.2 containers at least 1 20-foot bays away from the accommodation. | checked_plan | 70.00 | 4.00 | 900.08 | 1578.09 | — | 900.00 | 41.09 | [False] | 2026-11-02T02:52:29.217264+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Keep all IMDG class 3 containers at least 2 20-foot bays away from the accommodation. | checked_plan | 70.00 | 4.00 | 900.16 | 1132.01 | — | 900.00 | 32.67 | [False] | 2026-11-02T03:48:39.429777+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| base | checked_plan | 66.00 | 0.00 | — | — | — | 900.00 | 39.82 | None | 2026-11-02T02:50:09.142520+00:00 | crane sequence repaired | crane sequence not repaired | 144 |
| In bay 01, use at most 1 occupied tiers per hold stack.; Keep all IMDG class 2.2 containers at least 1 20-foot bays away from the accommodation. | checked_plan | 70.00 | 4.00 | 900.15 | 1100.04 | — | 900.00 | 38.15 | [False, False] | 2026-11-02T05:36:55.916545+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| Keep all IMDG class 2.2 containers at least 1 20-foot bays away from the accommodation.; Keep all IMDG class 3 containers at least 1 20-foot bays away from the accommodation. | gate_failed | 66.00 | 0.00 | 900.13 | 989.84 | — | 900.00 | 26.51 | [False, False] | 2026-11-02T04:56:11.124055+00:00 | crane sequence not repaired | crane sequence repaired | 21 |
| Do not load any containers in bay 23. | gate_failed | 70.00 | 4.00 | 900.13 | 1139.21 | — | 900.00 | 34.52 | [False] | 2026-11-02T04:11:43.475451+00:00 | crane sequence not repaired | crane sequence repaired | 104 |
| In bay 03, use at most 1 occupied tiers per hold stack. | checked_plan | 70.00 | 4.00 | 900.17 | 1224.37 | — | 900.00 | 25.75 | [False] | 2026-11-02T04:17:30.273535+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
| In every bay, use at most 2 occupied tiers per hold stack. | checked_live | 70.00 | 4.00 | 720.13 | 999.44 | — | 720.00 | 38.83 | [False] | 2026-11-02T03:18:52.291874+00:00 | crane sequence not repaired | crane sequence repaired | 146 |
<!-- RULE_STATUS_END -->


## Code review 6 corrections

Final native audit: **17 current plans, covering all 12 frozen plans, remain accepted; no verdict changed**. All 2,597 fresh crane frames passed: the two bases are **crane sequence repaired**, and the fifteen instruction outputs are **crane sequence not repaired**. The hero hashes, frozen catalogue and independent shot-6 payload are unchanged. PID51958 received no signal or control change and completed its remaining requests normally. [code6-verification.json](code6-verification.json) records the external-temp tests, source identity and audit links.

The external-temp offline run is recorded in [code6-verification.json](code6-verification.json): 374 tests pass with no network or GPU. Native acceptance is independently rechecked in [audits/code6-regate](audits/code6-regate); its immutable input copies preserve the original current and frozen catalogues. No stored record, release pin, hero file or batch control is rewritten by this audit.

| Finding | Correction and regression |
|---|---|
| 1: foreign screening frames | All input digests, epoch and frame structure must match; source and promoted gate identities must bind. `screening/test_screen.py::test_sweep_foreign_or_malformed_frames_are_never_resumed` and promotion cases. |
| 2: repair instructions omitted | All three CLIs forward confirmed rules and baseline; source identity and exact returned audits must pass. `test_repair_cli.py::test_cli_forwards_all_confirmed_rules_and_baseline` and negative output cases. |
| 3: concurrent catalogue pins | Immutable local snapshots bind every read and cache publication; superseded launches withhold results. `test_store.py::test_snapshot_is_immutable_and_concurrent_launch_never_reads_replacement_under_old_pin`. |
| 4: memo freshness/source drift | Fresh/memo fields name the actual recheck time; reader source, loaded bytecode, schema and constants form the gate identity. `test_store.py` exercises memo labels, identity drift and exact candidate handoff hashes. |
| 5: stale recorded evidence | Instance body/hash, plan hash and exact canonical audit envelopes must match before release counts. `test_release.py::test_stale_identity_or_rule_evidence_cannot_count_or_freeze`. |
| 6: nominal timeout duration | New screens separately measure the owned call and shutdown. The two historical baseline search durations are unknown; 900 seconds is their configured allowance. `screening/test_screen.py::test_screen_timeout_persists_measured_elapsed_not_nominal_allowance` and `test_report.py::test_historical_timeout_allowance_is_not_a_measured_search_duration`. |
| 7: retry clock scope | New portfolio runs measure preflight, admission and native gate separately; retry work begins after initial admission. Historical mixed-scope retry totals remain raw evidence and are unknown in the comparable column. `test_standard_plan_pool.py::test_retry_work_clock_excludes_preflight_and_initial_admission` advances a fake admission clock by 120 seconds. |
| 8: external temporary paths | Checkpoints outside the repository retain absolute paths. `screening/test_screen.py::test_sweep_exact_valid_resume_and_external_checkpoint_path` plus both failed-frame cases run with an external basetemp. |

<!-- FILM_ANALYSIS_START -->
## Film instruction audit

Authenticated checkpoint snapshot at 2026-11-02T07:46:11.178635+00:00. Full details and source digests: [film-analysis.json](film-analysis.json).
Counts cover the current catalogues, including the isolated 163-box campaign; frozen copies of the same keys are not additional plans. Refusals have no plan and are excluded.

| Ship | Stored instruction keys | Distinct instruction plans / crane sequences / final placements | Distinct including base | Base frames and provenance |
|---|---:|---|---:|---|
| 87 boxes | 15 | 7 / 7 / 7 | 8 | 144 passed; crane sequence repaired |
| 163 boxes | 9 | 9 / 9 / 9 | 10 | 263 passed; crane sequence repaired |

Equal lift totals do not imply equal plans. Full hashes cover canonical plan JSON; crane-sequence hashes remove annotation IDs and metadata; placement hashes cover final box-to-slot mappings. Parenthesized group sizes below count matching stored records.

Recorded, source-bound gate verdicts only: **crane frames walked on this plan**. No new native sweep was run for this analysis. Directly forced boxes occupied baseline footprints forbidden by the rule; other changed boxes may reflect search and coupled constraints. This is not a minimal-change certificate. Long boxes count once even when two physical stacks shrink.

| Hero / instruction | Key | Full / moves / placement groups (all record members) | Unique instruction placement / changed from base | Directly forced / additional changed boxes | Stack occupied tiers before → after | Extra lifts | Frames | Sequence provenance |
|---|---|---|---|---|---|---:|---:|---|
| 87 boxes / dg_keep_away-06: Keep all IMDG class 2.2 containers at least 2 20-foot bays away from the accommodation. | 0853a6cf7cc1 | 359e8fd827 (5) / 8fc88bbf20 (5) / 18a1a47cdb (5) | no / yes | 1 / 82 | — | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / dg_keep_away-12: Keep all IMDG class 3 containers at least 1 20-foot bays away from the accommodation. | 4c77dbdc69ce | 359e8fd827 (5) / 8fc88bbf20 (5) / 18a1a47cdb (5) | no / yes | 1 / 82 | — | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / max_tiers-17: In bay 03, use at most 3 occupied tiers per deck stack. | 55c3578f72d2 | 5039b77446 (2) / 0faad9842d (2) / bc4d409030 (2) | no / yes | 1 / 84 | 00301D: 1 → 3; 00302D: 0 → 1; 00304D: 4 → 3; 00305D: 3 → 0 | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / close_bay-18: Do not load any containers in bay 09. | 72ccfc883f3d | f66c8301b6 (1) / 2e4bf07c14 (1) / bb5e32d0df (1) | yes / yes | 36 / 48 | 00901D: 3 → 0; 00901H: 3 → 0; 00902D: 4 → 0; 00902H: 2 → 0; 00903D: 4 → 0; 00903H: 2 → 0; 00904D: 3 → 0; 00904H: 3 → 0; 00905D: 3 → 0; 00905H: 3 → 0; 00906D: 3 → 0; 00906H: 3 → 0 | 82.0 | 146 | crane sequence not repaired |
| 87 boxes / supplement-dg-pair-6: Keep all IMDG class 2.2 containers at least 3 20-foot bays away from the accommodation.; Keep all IMDG class 3 containers at least 2 20-foot bays away from the accommodation. | 734ce36bef8a | f34250788a (3) / 5991118279 (3) / 12e0877bfe (3) | no / yes | 2 / 82 | — | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / max_tiers-19: In bay 01, use at most 3 occupied tiers per deck stack. | 8811330b257a | 5039b77446 (2) / 0faad9842d (2) / bc4d409030 (2) | no / yes | 2 / 83 | 00101D: 4 → 3; 00102D: 0 → 1; 00104D: 4 → 3; 00105D: 3 → 0 | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / dg_keep_away-09: Keep all IMDG class 2.2 containers at least 3 20-foot bays away from the accommodation. | 9381a5a54b03 | 359e8fd827 (5) / 8fc88bbf20 (5) / 18a1a47cdb (5) | no / yes | 1 / 82 | — | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / supplement-dg-pair-2: Keep all IMDG class 2.2 containers at least 1 20-foot bays away from the accommodation.; Keep all IMDG class 3 containers at least 2 20-foot bays away from the accommodation. | a105bbaf39d1 | f34250788a (3) / 5991118279 (3) / 12e0877bfe (3) | no / yes | 2 / 82 | — | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / max_tiers-08: In bay 21, use at most 3 occupied tiers per deck stack. | a938b961f670 | cbb5b9b98d (2) / 49f2c7752e (2) / d49ad4ddd5 (2) | no / yes | 1 / 82 | 02102D: 4 → 3; 02104D: 2 → 3; 02105D: 3 → 2 | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / supplement-dg-pair-4: Keep all IMDG class 2.2 containers at least 2 20-foot bays away from the accommodation.; Keep all IMDG class 3 containers at least 2 20-foot bays away from the accommodation. | cfdc9d7f890b | f34250788a (3) / 5991118279 (3) / 12e0877bfe (3) | no / yes | 2 / 82 | — | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / max_tiers-21: In bay 11, use at most 3 occupied tiers per deck stack. | e0c1c52d80f8 | c8f972a1f4 (1) / e7fb9de889 (1) / 586b1d4dbf (1) | yes / yes | 2 / 84 | 01101D: 3 → 2; 01102D: 4 → 2; 01103D: 4 → 2; 01105D: 3 → 2; 01106D: 3 → 2 | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / dg_keep_away-02: Keep all IMDG class 2.2 containers at least 1 20-foot bays away from the accommodation. | e17e0ce08804 | 359e8fd827 (5) / 8fc88bbf20 (5) / 18a1a47cdb (5) | no / yes | 1 / 82 | — | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / dg_keep_away-15: Keep all IMDG class 3 containers at least 2 20-foot bays away from the accommodation. | e1dbb1e51637 | 359e8fd827 (5) / 8fc88bbf20 (5) / 18a1a47cdb (5) | no / yes | 1 / 82 | — | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / pair-24: In bay 01, use at most 1 occupied tiers per hold stack.; Keep all IMDG class 2.2 containers at least 1 20-foot bays away from the accommodation. | ec3960d32ba1 | a99b0bc610 (1) / 749c616292 (1) / dbe155e492 (1) | yes / yes | 2 / 84 | 00101H: 0 → 1; 00102H: 2 → 1 | 70.0 | 146 | crane sequence not repaired |
| 87 boxes / max_tiers-14: In bay 03, use at most 1 occupied tiers per hold stack. | fc6e93b2a31d | cbb5b9b98d (2) / 49f2c7752e (2) / d49ad4ddd5 (2) | no / yes | 1 / 82 | 00301H: 0 → 1; 00302H: 2 → 1 | 70.0 | 146 | crane sequence not repaired |
| 163 boxes / backup-hatch-06: Keep one discharge port per physical hatch cover, across hold and deck. | 0db9e2c5f7b4 | 3d1ce9534b (1) / 3c609313a2 (1) / 1904ecb49f (1) | yes / yes | ≥5 (IDs undetermined) / ≤156 | — | 107.0 | 259 | crane sequence repaired |
| 163 boxes / backup-dg-03: Keep all IMDG class 3 containers at least 1 20-foot bays away from bay 11. | 52e5de81aa26 | 75d66e6e10 (1) / ac3f7b11db (1) / f50cc3d0b4 (1) | yes / yes | 1 / 153 | — | 115.0 | 263 | crane sequence not repaired |
| 163 boxes / backup-max-09: In bay 27, use at most 2 occupied tiers per hold stack. | 5f2dac94244e | 37c305eb02 (1) / 2b2701f514 (1) / 501816492c (1) | yes / yes | 2 / 151 | 02701H: 3 → 2; 02702H: 3 → 2 | 115.0 | 263 | crane sequence not repaired |
| 163 boxes / backup-max-07: In bay 25, use at most 2 occupied tiers per hold stack. | 8376e2a0e3de | 947f8b304d (1) / 67119eaf71 (1) / a5c3684a2e (1) | yes / yes | 2 / 150 | 02501H: 3 → 2; 02502H: 3 → 2; 02506H: 1 → 0 | 131.0 | 263 | crane sequence not repaired |
| 163 boxes / backup-close-08: Do not load any containers in bay 03. | bcbaad937709 | deba5fe2e3 (1) / 37d03250c1 (1) / c1997dce7c (1) | yes / yes | 27 / 122 | 00301D: 3 → 0; 00301H: 3 → 0; 00302D: 3 → 0; 00302H: 3 → 0; 00303D: 3 → 0; 00304D: 3 → 0; 00305D: 2 → 0; 00306D: 2 → 0; 00307D: 3 → 0; 00308D: 2 → 0 | 115.0 | 263 | crane sequence not repaired |
| 163 boxes / backup-pair-11: In bay 25, use at most 2 occupied tiers per hold stack.; Keep all IMDG class 8 containers at least 1 20-foot bays away from the accommodation. | caededec8b23 | 6bce0d8156 (1) / f2bd566bda (1) / 3374e14795 (1) | yes / yes | 3 / 146 | 02501H: 3 → 2; 02502H: 3 → 2; 02506H: 1 → 0 | 139.0 | 263 | crane sequence not repaired |
| 163 boxes / backup-pair-10: In bay 01, use at most 2 occupied tiers per hold stack.; Keep all IMDG class 3 containers at least 1 20-foot bays away from the accommodation. | faefb906a431 | c19a45f091 (1) / 0bea9cf12e (1) / 97f6f0746d (1) | yes / yes | 3 / 156 | 00101H: 3 → 2; 00102H: 3 → 2 | 117.0 | 263 | crane sequence not repaired |
| 163 boxes / backup-close-05: Do not load any containers in bay 01. | faf487096a4d | b65685221b (1) / 97693607e7 (1) / f36a2d0369 (1) | yes / yes | 26 / 125 | 00101D: 2 → 0; 00101H: 3 → 0; 00102D: 3 → 0; 00102H: 3 → 0; 00103D: 3 → 0; 00104D: 3 → 0; 00105D: 2 → 0; 00106D: 2 → 0; 00107D: 3 → 0; 00108D: 2 → 0 | 141.0 | 263 | crane sequence not repaired |
| 163 boxes / backup-dg-02: Keep all IMDG class 8 containers at least 1 20-foot bays away from the accommodation. | fb3a9cac5ec4 | eff80f7032 (1) / 4fb83bfd9e (1) / f77615e66f (1) | yes / yes | 1 / 126 | — | 115.0 | 263 | crane sequence not repaired |

Refresh once with `python -m experiments.mvp.pipeline.lattice.film_report` using the offline environment below. A separate read-only observer watches accepted catalogue checkpoints and refreshes this section and the candidate JSON; [film-observer/state.json](film-observer/state.json) records its PID and last refresh. It never signals the solver batch, changes a plan, or grants a gate. Demo launch still regates the selected stored plan.

<!-- FILM_ANALYSIS_END -->

<!-- FILM_CANDIDATES_START -->
### Film instruction candidates

Recorded, authenticated comparison: [candidate details and spoken variants](hero-instruction-candidates.json). These are recorded gates, not a fresh native sweep. Three authored wordings share each canonical target; reader-model accuracy is unmeasured and every reading needs confirmation.

| Voyage | Recommendation | Forced / other changed boxes | Evidence |
| --- | --- | --- | --- |
| 87 boxes | close_bay-18: Don't load any containers into bay nine. | 36 / 48 | 146 recorded frames; unique placement; 12 target stacks shrink; selected: crane sequence not repaired; baseline: crane sequence repaired |
| 163 boxes | backup-close-08: Don't load any containers into bay three. | 27 / 122 | 263 recorded frames; unique placement; 10 target stacks shrink; selected: crane sequence not repaired; baseline: crane sequence repaired |

**87-box choice: close_bay-18.**

- Don't load any containers into bay nine.

- Keep bay nine out of this container loading plan.

- No container loading in bay nine, please.

Recorded selected-plan sequence: crane sequence not repaired; baseline: crane sequence repaired. Crane frames walked on this plan; retain the stored producer credit.

**87-box stack-focused alternative: max_tiers-21.**

- In bay eleven, keep each deck stack to three tiers.

- For bay eleven, cap the deck stacks at three occupied tiers.

- Don't stack deck cargo more than three tiers high in bay eleven.

Recorded selected-plan sequence: crane sequence not repaired; baseline: crane sequence repaired. Crane frames walked on this plan; retain the stored producer credit.

**163-box choice: backup-close-08.**

- Don't load any containers into bay three.

- Keep bay three out of this container loading plan.

- No container loading in bay three, please.

Recorded selected-plan sequence: crane sequence not repaired; baseline: crane sequence repaired. Crane frames walked on this plan; retain the stored producer credit.

**163-box stack-focused alternative: backup-max-07.**

- In bay twenty five, keep each hold stack to two tiers.

- For bay twenty five, cap the hold stacks at two occupied tiers.

- Don't stack hold cargo more than two tiers high in bay twenty five.

Recorded selected-plan sequence: crane sequence not repaired; baseline: crane sequence repaired. Crane frames walked on this plan; retain the stored producer credit.

Pointwise forced boxes cannot retain their baseline slots under the rule. Other changes may reflect search or coupled constraints; this is not a minimal-change certificate. Physical stack counts include both halves of long containers. An observed tier drop beyond the cap requirement is not wholly attributed to that cap.

It replans from scratch; keeping changes small isn't built yet.
<!-- FILM_CANDIDATES_END -->

F8 verification: [f8-verification.json](f8-verification.json) records 429 passing offline tests with an external temporary directory and 107 passing follow-up tests for the final presentation change. Acceptance logic and frozen plan/release bytes are unchanged. No new physics acceptance is claimed; source-bound recorded gates underpin the comparison. The existing 163-box supervisor PID 75434 received no control action. Detached observer PID 79552 (parent 1) refreshes analysis artifacts from accepted checkpoints and exits when that batch completes.

## Expanded hero coverage

Launched 2026-11-02T08:50:48.237430+00:00: **143 missing single-rule keys**, out of 154 requested; 11 existing singles and all 18 existing catalogue entries are preserved. Queue: eight closures, 36 deck caps, one hatch rule, 85 DG rules and thirteen hold caps. The pinned hero and D272 film sentence are unchanged.

Supervisor **PID 14953**, parent 1, uses at most six workers and the selected standard 300/100 profile with a separate 1,800-second engine wall. Fleet admission waits; quiet.flag pauses new rows and active rows finish. At six workers, projected time is **4.41 hours**, based on 400 seconds of search/finalize plus the measured historical median gate of 265.46 seconds per row. Using the full engine wall gives 13.67 hours before quiet time, capacity waits and repairs; neither figure is a measured completion time.

[Campaign instructions](hero-coverage/README.md), [progress](hero-coverage/progress.json), [rules](hero-coverage/RULES.md) and [startup verification](hero-coverage/verification.json). The offline suite passed **490 tests**. New entries are independently gated in staging and appended to the main catalogue without replacing existing bytes. Every new entry records engine/native, binding and adapter-free source identities for the later freeze; it makes no premature frozen-code claim.

Stop admissions and drain running rows with:

```sh
touch /Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/pipeline/lattice/hero-coverage/STOP
```

Before the sealed run the leader creates that STOP marker and lets the supervisor drain; after it reports `stopped`, delete the marker and restart using the exact [resume command](hero-coverage/README.md). This documentation change sends no control action to the running campaign.

## Script facts for shots 7 and 9

[hero-facts-close-bay-9.json](hero-facts-close-bay-9.json) contains the selected
closure's counts, forced box IDs, lift comparison, recorded peak heel and
frame, computation date, producer lineage and authenticated entry sources.
[hero-impossible.json](hero-impossible.json) contains the selected stored
capacity refusal, exact arithmetic source fields and possible simpler queued
replacements. Both are recorded facts; extraction performs no new native gate.

Refresh the refusal after coverage checkpoints new entries:

```sh
PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab uv run --offline --no-sync python -m experiments.mvp.pipeline.lattice.film_refusal_facts
```

The refresher prefers a published single-rule refusal, especially a global
one-tier deck cap. Queued arithmetic candidates remain labelled candidates
until their proved refusals actually enter the authenticated store. The
original zero-deck/zero-hold pair's recorded 143-versus-60 calculation is a
sufficient **deck-zero subset** proof; applying both rules leaves zero cells.
The facts file distinguishes these two capacities.

The two currently queued arithmetic candidates are `hero-coverage-deck-4313cb975de6`
(one deck tier in every bay) and `hero-coverage-deck-a522ea5a2b39` (two deck tiers
in every bay). Their exact keys, counts and sources are in the JSON; neither
is presented as stored until published. The fact extractors and existing
lattice suite pass 518 offline tests; [film-facts-pytest.xml](film-facts-pytest.xml)
records the run with an external temporary directory.
