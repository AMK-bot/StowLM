# Sealed pipeline harness

Task1.12 prepares task3.1's single release. Read [PREREG.md](PREREG.md),
[preregistration.json](preregistration.json), and dated [AMENDMENT.md](AMENDMENT.md).
AMENDMENT.sha256 and every frozen identity pin the amendment before opening.
No sealed maps/results are development inputs.

Pre-freeze preparation (2026-11-02): canonical `launch-config.json` now selects
live masked **swa-last-5**, standard, four workers and the 1200s outer wall.
The D276 complete44 timing sample supplies the pinned conditional4.9–8.6h
projection, plus admission; a2x guard-cache sensitivity is11.2h. These are
loaded-machine projections, not measured quiet-machine hours. The incomplete
68-trial development study remains stopped, with lost rows recorded honestly.

Calibration has run before the manifest: fresh independent audits of all20
committed baseline plans, retaining5 no-plan and7 unknown maps, find20/32
breaking the fixed tier cap (17required). This took64.23s at four admitted
workers, without numerical search or retry. See
`measurements/calibration-reaudit-D276-v3/summary.json`. The audit corrects a
vectorized E9 construction-mask false rejection; physical replay/full checker
remain authoritative and mask diagnostics stay visible.

Check all public prerequisites except the not-yet-built manifest:

```sh
sh experiments/mvp/pipeline/sealed/run_sealed.sh --preflight
```

After the leader creates `lattice/hero-coverage/STOP`, use the one command below
from the repository root, outside the sandbox. It prints drain waits until STOP
exists and `progress.json` has zero active rows. Invoking it explicitly attests
that other fleet work has stopped and the GPU is free for the serial live
reader; that operator attestation is recorded as `leader_run_sealed`. The owned
admission bridge preserves the original telemetry timestamp/mask/load; stale
readings, fewer than18 enabled cores or load1 above6 continue waiting. It never
edits shared fleet telemetry. Full-weight freeze verification still refuses
drift before any claim or sealed opening, and all receipts are verified again.

```sh
sh experiments/mvp/pipeline/sealed/run_sealed.sh experiments/mvp/pipeline/freeze/manifest-pipeline.json
```

The authorized-instance assembly seam accepts only the worker's hash-checked,
single-use-authorized plan-phase load. It preserves ordinary runner model
assembly/revalidation and its public sealed-path fence. The frozen inventory
must include the sealed seam, worker, reader factory and runner implementation.

Synthetic ships/cargo, simulated port: **planning step from a clean load list,
templated sentences**, standard profile. Metrics concern **extra crane lifts**.
Keep tiny15,small30,envelope24 separate;500–800TEU and1700TEU apart in testv3;
envelope by ship only. Every pass count includes exact95% binomial interval.

Pipeline and labelled simple two-step (15+15s) run on69maps. Envelope adds
instructed pipeline and secondary two-step300s finisher:186trials. Instruction
primary joins broken baseline counterfactual with instructed fresh-audited L0;
already obeyed, changed box cells, reader/engine failures, signed lift change
and already-obeyed noise floor are explicit. The max-tier cap is physical−3,
release-gated on a strict-majority development binding calibration. DG chooses
most numerous load-list class. The serial prepass constructs the reader once,
reads each instruction once, records timing and hash-bound reading; workers
replay only. standing is empty; confirmation source sealed_expected_match.

Lift primary is unweighted total, envelope24 if>=8mutual passes; else explicitly
small, predeclared500–800TEU population. Inconclusive envelope primary does not
switch cohorts. Quote only paired95% interval excluding0 in saving direction,
with current-port interval beside it. Otherwise no lift-saving claim. Current
and projected D15 components, passes,2x2 table and sensitivity are separate.
Weight-tolerance sensitivity is waived in the amendment; relocation-policy
confound remains explicit beside comparisons. Experienced planners may block
by port already. Means use passing maps only, never compare different cells.

From repository root, Python3.12:

```sh
export UV_CACHE_DIR=$PWD/.uv-cache
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
uv run --offline --no-sync pytest experiments/mvp/pipeline/sealed
uv run --offline --no-sync python -m experiments.mvp.pipeline.sealed.dry_run \
  --out-dir experiments/mvp/pipeline/sealed/fixtures/development-amended \
  --fixture-admission --workers 1
```

Use `--workers 1` (separate arguments). Dry fixtures use dev.txt when available,
otherwise18quick tiny/small maps. Actual runner assembly/envelope with no-plan
engine/two-step, FixtureParser, in-process deadline/cache and unavailable fresh
audit are explicit surrogates. Fixture admission exists only for this helper,
where no search or timed numerical work runs. No native performance is measured.
Historical fixtures retain their original source epochs; do not reinterpret them
under the amendment. Use a fresh development destination.

`report.write_report(rows,out_dir,metadata=None)` writes report.json,REPORT.md,
per-map.csv without opening maps. Rows preserve hashes/status/level/checks,
reader/counterfactual/audit evidence,final_cells,lift components,time. Metadata
expected_maps identifies cohort,map_id,ship_id; sensitivity_arms=['twostep_300']
fixes complete expected accounting. Errors retain all identities, provisional
status and partial evidence. A timeout/crash/audit mismatch does not stop later
trials. Finally publishes reports; dirty source stops new openings. Failed
trials are consumed once; saved worker commands cannot retry, phase-substitute
or run without the controller. No unverified row passes.

Sealed config requires mode sealed, reader_factory module:callable, optional
parser_options, pipeline_manifest, runtime_measurement and cap_calibration_receipt.
Task2.1manifest schema: task2.1,status frozen,files path→SHA256 (or path/sha list),
standard_profile equal JSON-normalized measured runtime profile,artifacts with
engine,adapter,prompt paths covered byfiles. Runner must expose approved
supports_sealed_evaluation seam. This folder cannot authorize sealed loader changes.

Runtime receipt sealed-runtime-budget/1 must record real development batches,
split development,fixture_surrogate false,workers,gpu_running,cpu_cores,
admission_load_limit workers+2,no_other_fleet_work true,includes_serial_readings
true,includes_admission_and_audits true,sensitivity_finalize_s300,
profile_sha256,binding_sha256,twostep_s15,twostep_finalize_s15. Each cohort
records maps,elapsed_s,all_registered_arms true. Scale batch elapsed by mapcount;
never divide concurrent wall time by workers again.

Calibration receipt sealed-tier-calibration/1 fixes cap_reduction3,
instructions_sha256,development_list_sha256,profile_sha256,binding_sha256,
split development,fixture_surrogate
false and evidence for everydev map:map_path,map_sha256,audit_row,audit_sha256,
rule_sha256,cap_reduction3. Fresh-audited no-rule L0 rows carry counterfactual_rule,
its hash and recheck. Validator recomputes the rule from public dev facts and
requires broken rules on a strict majority of all32maps, not selected successes.
Native calibration and timings are emitted by the detached development study
below; a receipt is usable only when its checks pass.

Machine18cores,12whileGPU runs:workers<=enabled−2 (max16or10respectively).
Final workers are fixed at4. Admission waits for fresh mask/load telemetry
with popcount>=workers+2,load1<=workers+2 and explicit other_fleet_work:[];
mask/load alone does not prove fleet exclusivity. Leader must publish that
additional field; missing/stale/busy evidence waits. No other fleet work may run.
Measured wall hours come from the native study below. There is no duration target.

After every release prerequisite is met, leader invokes exactly once:

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.sealed.release \
  --config frozen-config.json --out-dir experiments/mvp/pipeline/sealed/runs/final
```

Global one-run claim precedes sealed access; manifest/source/amendment hashes
are verified before dispatch, each map read is logged, and phase capabilities
are single-use. No sealed pilot, resume, retry, substitution or alternate-directory
bypass. Real ships, human planners, industry performance, final-reader accuracy,
intake/voice/free wording and live-profile behaviour remain outside this test.

Before the development list is published, inspect the growing development
directory and existing baseline plans without running a planner:

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.sealed.dev_cap_check \
  --out-dir experiments/mvp/pipeline/sealed/fixtures/dev-cap-check
```

The check reads only the 32 development paths declared in the public
`maps/requests.json`, plus final baseline snapshots in binding measurements and
lattice screening/store. Every missing map and baseline stays explicit. Source
and input hashes are checked where available. Every eligible baseline is kept;
lift values and cap violations never select a preferred plan. While generation
is incomplete, the checker reports all physical bays and withholds the assigned
bay because missing public facts can change the final rank. Bay checks on one
plan are correlated, not additional map trials.

This is a development diagnostic, not a release calibration receipt: it runs
fresh intended-rule checks but no native full checker, completion mask or search.
Release still needs the full 32-map cohort, the fixed complete-population ranks,
matching frozen-standard no-rule plans, fresh subprocess audits, and at least
17 binding maps out of 32, through `calibration.verify_calibration`. Existing
plan snapshots cannot satisfy that gate merely by claiming they passed.

The runner now calls `binding.confirm_brief(receipt, source)` before
`execute_brief`. The sealed wrapper passes `sealed_expected_match`; integration
tests verify authenticated confirmation on the actual runner/binding path and
that unconfirmed calls and wrong readings never reach planning.


The native study is launched with:

```sh
sh experiments/mvp/pipeline/sealed/run_real_dry.sh
```

It detaches into its own session, records the launcher PID and checkpoints under
`measurements/native-development-*`, and survives the calling turn. Four workers
execute the real runner/binding and real two-step. Only the serial reader uses
explicit expected-answer recordings because the GPU is busy; these are not
represented as adapter-v2 inference. Physical acceleration is copied byte-for-byte
into `sealed/runtime-cache`; planner/checker functions are unchanged.

All32dev maps establish public-facts ranks before the sample: r0 in each of8cells
gets both arms plus instruction/sensitivity;6quick tiny/small sections cover all
three ships. Another24 no-rule baseline trials complete the full development
cap survey. There are68native trials, no repeated spent trial. Native process
and independent-audit elapsed times and real admission snapshots are recorded.
The hard wall deadlines are1200s pipeline/instruction,180s two-step,420s sensitivity,
300s independent audit and1800s prepass. Owned session groups receive bounded
TERM/KILL cleanup; PermissionError and ProcessLookupError are caught.

`FINAL.json`, `RESULTS.md`, `runtime-projection.json`, `runtime-budget.json`,
`cap-calibration.json` and `launch-config.json` are produced automatically.
The tier reduction is now3, selected using the preserved physical-minus2
reference study before any sealed access. The strict17of32 fresh-audited
binding requirement remains. Failure produces a blocked calibration result.
Shared-machine development timing is a projection for an idle machine, with
one maximum job as scheduling reserve and1800s for actual reader inference;
admission waiting is additional and unbounded. No clean-machine time is claimed.

The leader's one-line3.1 launch, outside the sandbox, is:

```sh
sh experiments/mvp/pipeline/sealed/run_sealed.sh "$PIPELINE_MANIFEST"
```

Set PIPELINE_MANIFEST to the actual frozen task2.1manifest. Missing manifest,
profile/recipe mismatch, unfinished study, failed cap calibration, or unavailable
approved sealed loader blocks before the global claim. The current ordinary
runner still fences sealed paths; its owner must deliver the approved seam.
Actual3.1 uses adopted adapter-v2 in the one serial GPU prepass. Final planning
uses four workers and waits for18enabled cores, load1<=6, fresh telemetry and
explicit other_fleet_work:[]; the fleet/GPU must be idle. The runtime receipt
schema2 keeps the recorded-reader limitation and reader time reserve visible.

D275: run_sealed.sh defaults to freeze/manifest-pipeline.json and delegates
inventory, public runs/ evidence and full weight hashing to p21 require_frozen.
The exact registered standard recipe is300/100,seed0,1800s engine wall, with
its full config digest; the outer1200s evaluation child deadline is separate.
A clean planning deadline is deadline_exceeded, never proved infeasible or
error. Before-wall published incumbents require fresh independent audit.
Staged D275 fixes install as one guarded bundle after the development study
and its recorded process groups exit, before the pipeline freeze is generated.
