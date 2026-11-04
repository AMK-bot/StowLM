# Voyage packet runner

Synthetic ships and cargo, simulated port. This runner assembles a packet, screens
the design envelope, authenticates reader outputs, asks the planner to confirm
each voyage reading, runs Frontier Engine 0.13.0, independently checks the result
and every confirmed instruction, classifies its ladder level, and writes a receipt
and handover package. No network or GPU is needed for recorded playback.

From the repository root, use Python 3.12 through the prescribed environment:

```sh
export UV_CACHE_DIR=$PWD/.uv-cache
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
uv run --offline --no-sync python -m experiments.mvp.pipeline.runner \
  experiments/mvp/pipeline/runner/fixtures/packet.json \
  --out experiments/mvp/pipeline/runner/demo-output --offline --confirm-all
uv run --offline --no-sync pytest experiments/mvp/pipeline/runner
```

Use a fresh output directory. Exit 0 means a full checker pass and every confirmed
instruction passed; other completed statuses return 2. Invalid CLI inputs return
1. `--events` streams events to stderr. stdout contains the JSON outcome.
An exclusive output lock prevents concurrent writers; an existing package is
preserved and the runner asks for a fresh directory. A dead PID in a lock is
reported as a stale lock; choose a fresh directory. The runner never removes
another run's lock or stops another owner's process.
`--confirm-all` accepts all accepted voyage readings for a batch;
refused or malformed readings still stop at `needs_confirmation`.
The signed confirmation step's JSON `notes` record `confirmation_source: batch_confirm_all`;
an ordinary callback records `callback`, and no callback records `none`. These
labels do not claim that a human planner reviewed a batch.
After the confirmation gate, `binding.confirm_brief` authenticates the exact
reader receipt and named host source before execution. The confirmation hash
and source accompany the signed step, engine evidence and checked incumbents.
API callers can pass `confirmation_source='sealed_expected_match'` for the
sealed evaluator's explicit expected-reading match; the name passes unchanged.

```python
from experiments.mvp.pipeline.runner import assemble, run_packet

instance = assemble(packet)  # stowage.model.Instance
outcome = run_packet(packet, parser=parser, out_dir=output_dir,
                     confirm=lambda reading: planner_confirms(reading),
                     earlier=earlier_no_rule_outcome,  # optional previous checked outcome
                     on_event=events.append)
```

The outcome is a JSON dictionary (`PipelineOutcome` is a type alias). The parser
implements `parse(sentence, context) -> (validated_answer, provider_evidence)`.
The public reader API authenticates its output; the engine receives the host-confirmed receipt,
never a separately supplied rules list. With no confirmation callback, accepted
voyage readings stop for confirmation. Standing text is a preference, never a
hard instruction. Nonempty standing text read as another rule, a refusal or a
malformed reading stops at `needs_confirmation` with a question: move it into
`voyage_sentences` and confirm it, or correct/remove it. The recognized
one-port-per-hatch standing reading sets `port_blocked_start` in the reader
receipt, but **nothing under pipeline/ or app/ consumes that flag**. It currently
has no demonstrated search effect; implementing one belongs to its owner.
An empty voyage brief plans normally without voyage confirmation.
An injected GlimmerParser can replace RecordedParser without runner changes.
When a checked no-rule outcome for the exact assembled instance is supplied as
`earlier` (CLI: `--earlier OUTCOME.json`), the runner freshly checks that plan
once and rechecks each newly confirmed limit once, without another search.
`earlier_plan_recheck` reports the violation count and either "The earlier plan
broke this limit in N places" or "The earlier plan already obeyed it". Multiple
limits retain their individual counts. Missing, mismatched, relaxed or previously
constrained plans remain unknown/unavailable; they never become "already obeyed".
This establishes whether the limit mattered to that earlier plan, without a
causal claim about lift differences or all the boxes search rearranged.

`first_checked_wall_s` appears beside `wall_s` in `timing` and the done event,
and both times appear in the status message. They share the runner-start clock,
including reader and admission time. Only a current plan passing the full checker
and every confirmed-limit recheck starts that clock; earlier-plan diagnostics and
relaxed plans do not. Evidence retains `runner_wall_s` beside that timestamp;
the binding's native `wall_s` keeps its original engine-time scope. No checked
plan means `first_checked_wall_s: null`, never zero.
Reader calls have a 30 s deadline per sentence. `reader_timeout_s` overrides it;
the deadline requires the main thread of a dedicated runner process. A host
calling from another thread receives a clear `error` before the reader runs.

`assemble` checks the base file's byte SHA256, replaces supplied cargo rows and
yard state, checks identities, and revalidates the whole Instance. Missing or
conflicting identities become planner questions. `outcome.assembly.hash_equal`
compares canonical validated base and assembled hashes: exact intake preserves
the hash. A replacement that intentionally changes attributes need not preserve it.
Sealed paths and symlinks resolving to sealed inputs are refused before reading.

The pure envelope gate measures physical cells as C6 does: 20 ft boxes consume
one cell; 40/45 ft boxes consume two. Fill is load-list demand / vessel cell count.
It limits load-list size to 195 boxes and fill to 70% of the planned section;
medium means a half-ship section of feeder_500 or feeder_800. Tiny/small tested
sections are allowed. Unsliced ships cannot bypass the ship test by declaring
their size small, and empty load lists/empty intake rows cannot pass. Arrival has zero
placed boxes and zero discharges, stage A, and one loading call. Fixed synthetic
background is excluded from arrival box count. Every refusal gives measured
values and limits. The gate is a design screen, not a feasibility proof.

Every event uses the shared `stowlm.events/1` shape and appears in `events.jsonl`
and the callback. An incumbent carries a plan hash, lifts, lane, and `checked:true`
only after a fresh full check and all instruction rechecks. Only strict
improvements stream. The anytime demo shows the first full checker pass as soon
as every confirmed instruction recheck also passes, then keeps that plan visible
while the engine continues to improve it. Suggested status: "First checked plan
available. Continuing to improve." Before that event, say "Searching for a checked
plan"; there is no promised arrival time. The best checked incumbent survives a later failed lane,
and is rechecked before final release. Relaxed plans never count as passes and
name broken rules and boxes ashore; T0 and confirmed instructions cannot be peeled.

`OUT/outcome.json` contains the packet, assembled instance, exact readings,
independent checker evidence, classification, plan, signed receipt, and events.
When the package writer is present, `OUT/package/` is the separate immutable
handover folder; verify it with `package.verify_package(path)`. Keeping events
outside it satisfies that writer's empty-directory and signed-file-manifest
requirements. With no writer, outcome.json remains the explicit seam. Nonplan
outcomes still have their events, clear message and, after assembly, a receipt.
The writer builds under a private `.package-*` staging folder. Only after a
complete outcome JSON has been atomically written is the package renamed into
place. A failed write or rename clears the returned plan/package and removes the
staging verification index before cleanup. If the disk cannot hold even an error
file, the API still returns the status and corrective message in memory.
Receipt steps use the shared hash chain. Ed25519 public keys/signatures are base64;
the runner's receipt uses a per-run local key. Package signing uses an application
key stored under runner/.keys. Neither signature implies external approval.

Stored plan input is an application-supplied mapping (or callable receiving the
instance hash and canonical rules). It must contain `instance_sha256`,
`canonical_rules` (the exact ordered voyage envelopes), `computed_utc`,
`engine_version`, `engine_manifest_sha256`, and `plan`. It must match the packet,
then pass a fresh full checker and every instruction recheck. It carries the
"Stored plan, computed on <date>, rechecked now" label. The application owns the
stored source's provenance: `stored_sha256` is its independently trusted canonical
payload SHA256. Without that argument only the bundled pinned payload is
accepted. Engine version and manifest hash must equal the current
`binding.adapter.verify_manifest()` result; a failed manifest or active checker
stubs blocks release. Both the payload (including its recorded date) and current
engine provenance are checked, followed by fresh checker/instruction gates.
An authenticated stored `status: relaxed_plan` is supported; fresh classification
must still identify L1–L5 and every confirmed instruction must pass.

`--offline` supplies `fixtures/readings.json` and `fixtures/stored.json` to
`run_packet(..., stored=...)`. Override them with `--recorded` and `--stored`.
Every custom recorded file requires an independent `--recorded-sha256`, including
live search with recorded readings. Custom offline files also require
`--stored-sha256` trust anchors. The bundled byte hashes and canonical plan hash
are pinned in `stored.py`. A hash supplied by the untrusted input itself is not
an independent trust anchor. The Python helper accepts `stored_file_sha256` and
`readings_file_sha256`; file authentication covers the exact bytes parsed.
The Python equivalent is `offline.run_recorded_packet(packet, out_dir=...,
confirm=...)`. It has no live fallback: missing recordings, changed rules or a
mismatched instance stop the run. The plan keeps its `stored` label, reader rows
say `recorded`, and both outcome JSON and the returned mapping carry
`execution_mode: recorded_offline` and the explicit offline message. Replay runs
the full checker and confirmed-instruction rechecks; it calls no network, GPU or
live search. The integration test blocks sockets, GPU initialization and search.

## Profiles selected from the stopped development study

The user selected the exact `120` arm for live and `300` arm for standard from a
**stopped partial study: 68 measured rows of 256 planned jobs**. This is an explicit
selection from measured development results, not a completed study or a sealed
result. The selection is recorded in `fixtures/profile-selection.json`; its source
is the [68-row summary](measurements/profile-study-20261101/summary.json).
No 60 s first-plan or 180 s whole-replan guarantee follows from this selection.

| Setting | live | standard |
|---|---:|---:|
| Selected study arm | 120 | 300 |
| Engine deadline after admission (configured) | 720 s | 1,800 s |
| Extra search / finalizer | 120 / 40 s | 300 / 100 s |
| Assignment / finalizer | 120 / 40 s | 300 / 100 s |
| Assignment tries | 1 | 1 |
| Lift polish | 10 s | 25 s |
| Peel proposal / finalizer | 40 / 20 s | 100 / 50 s |
| Peel candidates | 1 | 1 |
| Warm polish / work-count search | off / off | off / off |

On the medium **no-rule development arm**, standard passed 9 of 9 maps and lost
no full pass on the 8 maps paired with the default reference. Its measured call
time had median 691 s and maximum 1,715 s. Live passed 7 of 9 maps; among those
passes, first-legal time had median 133 s and maximum 1,023 s. These call times
exclude assembly, model inference and handover rendering. They do not measure
the pass rate under the new runtime hard caps: in particular, a 720 s live cap
can stop before a later passing plan appears. It can return the best checked
incumbent already found, after fresh release checks; without one it returns no
checked plan. A 1,023 s first-legal observation is not a total-time figure.

GPU training and fleet work were active. These two selected arms started at
load1 21.42–44.78 with 10–18 enabled cores; ending load reached 87.09. The leader
uses the loaded-machine timings as conservative planning upper bounds, not
statistical bounds or guarantees. A quiet-machine rerun is optional; no quiet
machine speedup has been measured.

The selected configurations reproduce the study arms exactly, with no unmeasured
peel override and no pending-selection flag. The earlier provisional 70 s live
peel adjustment is superseded. Live's selected 40 s allowance can leave the L3
partial branch without a proposal iteration; the evidence below reports actual
work. Frozen study configurations and completed row bytes remain unchanged.

Ladder evidence counts actual `proposal.trials`, rather than controller level
visits. Empty loops report `ladder_work: none` and remove the misleading "within
this work" text. Evidence, ladder events and the status message name skipped
levels. When live skipped L1–L3, the message is: "L1-L3 weren't searched on the
live profile. This is the best candidate already found." A checked L0 plan
needs no tier search; absent trial evidence remains unknown.
For a hatch-mapping portfolio, global work covers every tried mapping once,
with separate per-mapping diagnostics. Another mapping's real search prevents
the selected candidate's empty loop from becoming a false global no-search claim.

`budget_s` buys additive engine work; it is not elapsed time from packet submission
and is not a wall-clock cap. Assignment, finishing, polishing and peel work have
their own allowances, and native historical floors still apply. A runner-owned subprocess gives every
engine call a deadline of 720 s (`live`) or 1,800 s (`standard`); `engine_timeout_s` can
override the cap. These are the user-selected engine allowances, not whole-run
guarantees. The recorded tiny demo's 547.552 s engine step / 623.410 s whole run
(`fixtures/cli-measurement.json`) remains historical evidence from its earlier
configuration; it does not measure either selected profile.
On timeout, a previously checked incumbent goes through the final checker and
instruction audit before release. Without one, the status is `no_checked_plan`.
`evidence.runner_deadline` records the actual cap, including an API override,
and whether the runner's own timer expired; `timed_out` also covers a timeout
reported by the engine itself. Neither is inferred from `budget_s`.
The D261 subprocess regressions passed for both profiles with 10,000 s internal
work knobs and a 0.5 s runner deadline: calls including cleanup took 0.558–0.684 s.
These are synthetic hanging-binding checks, not engine performance measurements
([test evidence](fixtures/d261-deadline-pytest.xml)). They verify best-incumbent
rechecking, clean no-plan timeouts, reaped workers and rejection of buffered
results when a parent callback has consumed the remaining deadline.
The real native-engine short probe also dispatches both selected profiles with
every Config field unchanged: live 120/40 s, standard 300/100 s, configured caps
720/1,800 s. An explicit **2 s cap override** stops each real call with
`runner_deadline.expired: true`, `no_checked_plan`, no package, and its recorded
worker PID reaped. Engine-stage times including setup/cleanup were 2.551/2.405 s;
whole-run times were 4.457/4.192 s, at load1 7.09 with 18 enabled cores.
See `fixtures/profile-short/summary.json` and `profile-short-probe.py`. These
short runs verify dispatch and wall enforcement, not quality or completion at
the default caps; no 720 s or 1,800 s job was launched.
Every attempted engine step records the pinned version and manifest, including
timeouts and failures. An engine/worker death returns `error`. Only the exact
owned process and its own multiprocessing children are stopped on timeout.
An engine killed with SIGKILL cannot clean up its descendants; the runner reports
the failure without signalling unrelated jobs. The parent runner itself cannot
return a message after SIGKILL; its next invocation identifies the stale lock.
Independent final checking and package writing happen after the engine deadline,
and synchronous incumbent checks/worker cleanup can delay reporting the timeout,
so these deadlines are **not whole-run timing guarantees**. Other settings are exposed by
`profiles.get_profile` and recorded in engine evidence. The runner doesn't change
frozen engine source. Optional runtime cache writes stay in runner/cache.
Runner admission happens **before the deadline starts**: at least eight enabled
cores, load1 <= 45, telemetry younger than 120 s. Missing, unreadable, busy,
malformed and stale readings all wait; admission never fabricates capacity or
fails because telemetry is temporarily unavailable. A judge's clone needs no
monitor for offline replay. Binding reuses the accepted reading inside the worker.

### Anytime observation from the engine handover

The D261 engine handover reports **103–202 s for the LP lane's first full checker
pass** on eight in-envelope maps under heavy shared load. The 103 s endpoint is
from the **user handover**; [docs/evaluation.md](../../../../docs/evaluation.md)
records a mean of 139 s, median 131 s and maximum 202 s. This is a historical
LP-stage observation, not packet-start latency, a deadline, a guarantee, a new
runner measurement or a demo speed claim. The machine shared reader GPU training
and fleet work: reported load was 30–80, with spikes to 90. The observation does
not establish a reader/instruction pipeline latency or a less-than-60-second plan.

The same evaluation reports 1,511–7,830 s total engine wall time despite nominal
900 s + 600 s work budgets. These totals diagnose where work accumulated;
`starts` dominated the stage table (mean 3,007 s, maximum 7,251 s). They are not
engine speed figures, and D261 leaves the overrun cause uninvestigated. See
[D261 in docs/decisions.md](../../../../docs/decisions.md) and the documentation
note [fixtures/anytime-handover.json](fixtures/anytime-handover.json).
The `engine` start event and `outcome.anytime` carry this historical guidance.
Only a new full pass emits “First legal plan available. Continuing to improve.”
The completion message shows the measured first-legal time beside total wall time.

For the current runner, admission completes **before** its owned engine deadline
starts. The first accepted full pass streams while work continues; the independent
final checker, confirmed-instruction rechecks and package writing follow the
deadline-controlled engine stage and are outside its cap. Total packet wall time
can therefore exceed 720 s (`live`) or 1,800 s
(`standard`). No new measurement here replaces the tiny recorded run's historical
536.125 s first checked incumbent or establishes that either live target was met.

### Archived development study and optional rerun

The user stopped `measurements/profile-study-20261101` after the 68-row summary
used for selection. Its measured results and source epochs remain historical
evidence; the 256-job grid was not completed. A future quiet-machine rerun is
optional and is not needed to use the selected profiles. The command below
creates a fresh timestamped study, uses `nohup`, and records its owned PIDs:

```sh
experiments/mvp/pipeline/runner/run_profile_study.sh
```

The harness globs only `maps/dev/*.json`, records immutable source/cohort hashes,
and adds maps arriving while it runs. `dev.txt` is unnecessary. One process owns
each map × rule × budget cell. Four workers run initially; six are allowed only
after three distinct fresh load readings below 30 spanning at least 60 s. Existing
workers are allowed to finish when load rises. Each new run still waits for the
normal eight-core/load-45 admission rule. GPU/model APIs are never used; the GPU
job used six cores during the recorded run and concurrent engine work shared the machine.
Timings may improve on a quiet machine; no quiet-machine speedup is measured yet.

| Study arm | Extra search | Finalizer | Assignment | Assignment finalizer | Warm polish / work-count search |
|---|---:|---:|---:|---:|---|
| 60 | 60 s | 20 s | 60 s | 20 s | off / off |
| 120 | 120 s | 40 s | 120 s | 40 s | off / off |
| 300 | 300 s | 100 s | 300 s | 100 s | off / off |
| default reference | native default | native default | 300 s, two tries | 180 s | on / on |

Short-arm lift polish is budget/12; peel proposal budget/3, peel finalizer budget/6,
one candidate; proof and area work budget/6, post-pass exchange budget/12. Every
switch is recorded. Removing native warm-polish/work-count floors is an explicit
Config ablation, so this compares complete profiles, not a pure budget ablation.
The default reference passes `None` for budget, finalizer and Config, retaining
all engine defaults. Its 7,200 s emergency ceiling is recorded as censored if
reached; an interrupted reference is never counted as a failed plan.

Both arms use the authenticated public reader route with an explicitly synthetic
exact-rule parser, zero model inference and batch confirmation. The constrained
arm enforces one port per physical hatch cover. Each passing no-rule plan is
audited against that rule; baseline-obeyed and actually binding cases are separate.
The confirmation source is `profile_study_batch_confirm_all`; the older benchmark
uses `benchmark_batch_confirm_all`. Both authenticate the accepted reader receipt
with `binding.confirm_brief` before execution, including the no-rule arm.
No borrowed cargo plan is supplied to the engine. Only hash-pinned numerical
physics caches are copied from `maps/runtime`, preserving their dispatch mtimes.

`rows/*.json` checkpoints every completed cell; `incumbents/*.json` preserves each
first legal plan while work continues. Every row retains the native plan/checker
evidence, exact settings, first legal time, total time, engine/recheck time and
load1/enabled cores at both start and end. Total excludes assembly, model inference
and package rendering; it includes synthetic reading, binding and independent
release checks. These are profile measurements, not whole-run timing guarantees.
`measurements.json` adds matched baseline audits; `summary.json` reports passing-only
lift means and map-paired pass/lift costs against default. Lifts include projected
later-port lifts (D15). Censored outcomes stay unknown and relaxed plans stay out
of pass rates.

The study's recovery handled only the old subsecond unconfirmed-binding failure
signature, retrying those cells with fresh reader authentication; original error
bytes are retained under `archive/unconfirmed-auth`.
Finished passing and relaxed measurements remain unchanged. New rows record their
actual source epoch and original job source. `source-epochs.json` documents the
reviewed binding hardening and partial-hatch correction during the transition;
cross-epoch timing and search differences remain a comparability limitation.
Unexpected source changes wait for an explicitly reviewed migration rather than
launching an optimizer or discarding a row. A tail repair mode waits for the
original coordinator and owned workers to end before recovering any missed cells.
Automatic complete-grid publication rejects obsolete authentication failures.
The explicit partial-study selection retains the source epochs and their caveat.
The newer hatch portfolio is a different numerical policy from the original
single-mapping arm. Mixed-policy hatch measurements cannot publish calibrated
profiles; use the fresh-study command above for a comparable cohort. Exact
reviewed policy dependencies are recorded separately, without importing the
binding campaign activation. A legacy worker admission bridge archives the exact
old job manifest and changes only its admission source baseline: each frozen job,
map, budget and completed row stays unchanged. Bridge and old-helper caveats
remain in the provenance; a new baseline does not relabel historical results.

The generic `profile_finalize` route requires every expected paired cell, an
independently demonstrated binding-rule pass and a matching pinned engine
manifest. That complete-grid criterion was not met here. The current
`fixtures/profile-selection.json` instead records the user's explicit selection
from the stopped 68-row snapshot, the exact study settings, provenance and
configured 720/1,800 s caps. It does not relabel the incomplete 256-job grid as
complete or apply an unmeasured ladder override. `profiles.get_profile` uses this
selection; a later optional rerun must retain its own results and provenance.

The development-only study harness is ready:

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.runner.benchmark \
  --rules rules.json --recorded recorded-readings.json --recorded-sha256 TRUSTED_SHA256 \
  --budgets 60 180 900 \
  --out experiments/mvp/pipeline/runner/measurements
```

`rules.json` is a list of synthetic sentences; running the study confirms them.
Supply recordings valid on every development map or an injected parser. It reads
only maps/dev.txt and paths resolving under maps/dev; it never opens sealed maps.
Results checkpoint after each run and report admission load, actual wall time,
first checked incumbent time, passes, relaxed plans, and shorter-budget costs.
Relaxed results split by L1–L5, completed/partial counts and percentage aboard;
none is added to a full-pass count. The runner retains `best_safe_partial` from
nested engine evidence in the outcome and signed receipt checker summary.
Partial relaxed messages explicitly say "partial" with the percentage aboard.
Nonfinite diagnostic ranks become `null` with exact `nonfinite_values` path/kind
entries before shared `sha256_json` hashing. Unknown values never become zero
or a fabricated finite breach; the shared strict JSON contract is unchanged.
Lift differences are paired only across maps passing both budgets. Medium means
use passing maps only. Lifts include projected later-port lifts (D15), and means
covering different passing subsets are not directly comparable.

## Earlier fixtures and limits

The initial runner suite passed 69 tests; see fixtures/pytest.xml and
fixtures/verification.json for that earlier run. On the three-box fixture, 1,000 pure gate calls
averaged 2.90 microseconds (maximum 36.6 microseconds), at fleet load1 54.37;
fixtures/envelope-measurement.json records the map hash and telemetry. This is a
tiny-map gate measurement, not an engine or medium-map timing claim.

The real recorded-reader CLI produced a full-checker passing plan with 8 weighted
nonproductive lifts (including projected later-port lifts), first incumbent at
536.1 s and whole run 623.4 s. Admission load1 was 40.52, with 12 enabled cores.
Two verification invocations overlapped on a shared machine, and fleet load1
was observed at 75.11. These timings do not calibrate either profile and do not
meet the timing targets. fixtures/cli-measurement.json names the successful
outcome hash. The original interleaved event file is retained separately; the
demo event stream was restored from that successful outcome's own events after
both invocations ended. Output locking now prevents this collision.
The signed package independently verifies all five checks (files, stage chain,
signature, full checker, instructions); verification took 76.0 s at admission
load1 36.89. See fixtures/package-verification.json. fixtures/stored.json provides
the exact plan and rule key for API replay with fresh gates and the stored label.

fixtures/packet.json is a tiny development map packet. fixtures/readings.json
copies real existing Spark answers with their exact sentences, source hashes,
provider metadata and original context restrictions. Historical reader cost is
provenance only; replay incurs zero cost and its StageRecord cost is zero.
The standing recording is labelled as a recognized preference with no currently
consumed search effect; the voyage recording
is independently confirmed and enforced. Recorded parser files have
`{"readings":[{"sentence":...,"envelope":...,"evidence":...,"ship":...,"context":...}]}`.
`--recorded FILE --recorded-sha256 TRUSTED_SHA256` selects another such file;
the default is this independently pinned fixture.

Tests use stub binding and explicit checker seams for orchestration cases. These
tests are not engine pass-rate evidence. The profiles use the explicitly selected
68-row partial development study; no complete-grid or sealed result or production/industry claim is made here. No paid
call or GPU run is required or authorized by this runner.

## Failure cases

Real-binding refusal refresh: **41 failure tests passed in 9.12 s**, at admission
load1 7.09 with 18 enabled cores (`fixtures/failures/real-binding-pytest.xml`).
Six additional refusal-message and exact-profile checks passed in 2.13 s
(`fixtures/refusal-policy-pytest.xml`). These focused checks replace the old
impossible-instruction stub and preserve the refused-reading gate.

Logic3 B2/B6 regressions cover empty ladder loops and the live allowance,
preserved measured selections, earlier-plan violation counts without extra
search, invalid/missing comparisons, and first-checked timing. Commands, results,
the historical zero-work ladder excerpt, source hashes and the unchanged finished
study checkpoints are recorded in `fixtures/logic3-verification.json` and
`fixtures/logic3-ladder-excerpt.json`.

Authenticated-confirmation verification: 241 runner tests passed in 211.528 s,
followed by 39 targeted study/finalizer tests. See
`fixtures/confirmation-pytest.xml` and `fixtures/confirmation-verification.json`.
Regressions cover true-only approval before sealing, unchanged named sealed
sources, authoritative confirmation metadata, pinned recording bytes, and
recovery/publication guards. The detached recovery waits for admission and
preserves all earlier completed measurements.

Logic-review verification: **183 passed, 1 deselected**, no failures, in 238.31 s
at admission load1 42.65/12 enabled cores. See `fixtures/logic-review-pytest.xml`
and `fixtures/logic-review-verification.json` for final source hashes, admission,
regressions, stored-input trust policy and the genuine relaxed result. This run
also verifies parent/fork/spawn cache containment without an optimizer call.

Earlier hardening verification, before the logic-review fixes: **143 passed, 1 deselected**, no failures, in
206.98 s at admission load1 42.88 with 12 enabled cores. See
`fixtures/hardening-pytest.xml` and `fixtures/hardening-verification.json` for the
command, environment, source hashes and limits. There are 41 failure tests and
40 recorded outcome observations covering the 28 named failure cases.

`test_failures.py` replays synthetic failures without a model or network. Inputs
and the case manifest live in `fixtures/failures/`; `observed.jsonl` records the
actual returned statuses/messages. The table summarizes those messages; paths,
hashes and additional envelope reasons are omitted for readability.

| Case | Status | Message |
|---|---|---|
| Malformed JSON packet | `error` | Correct the JSON file and retry. |
| Schema-invalid packet | `error` | Correct the named packet field and schema. |
| Missing base instance | `error` | The base instance cannot be read; supply a readable file. |
| Base hash mismatch | `error` | The measured base hash differs from the expected hash; select the matching file. |
| Instance fails validation | `error` | Correct the base instance field identified by validation. |
| Unknown intake id | `needs_answers` | Resolve the load-list and yard identity questions before planning. |
| Reader unavailable | `error` | Restore the reader or use recorded answers and retry. |
| Reader timeout | `error` | Retry the reader or use recorded answers for this packet. |
| Malformed reader output | `needs_confirmation` | Correct or remove unresolved voyage sentences, then rerun. |
| Network down during Spark | `error` | Restore the service or use recorded answers/a recorded note. |
| Network down during voice | `needs_answers` | Type the instruction or use a recorded transcript and retry. |
| Engine exceeds budget | `no_checked_plan` | Retry with the standard profile or reduce the load. |
| Engine killed | `error` | Restart the worker and rerun in a fresh output directory. |
| Worker killed | `error` | Restart the worker and rerun in a fresh output directory. |
| Checker raises | `error` | Checker could not complete; check the input or service and retry. |
| Disk full | `error` | Free disk space or choose a writable output directory and rerun. |
| Unwritable output directory | `error` | Choose a writable output directory and rerun. |
| Existing package | `error` | This output directory already contains a package. Choose a fresh output directory. |
| Out-of-envelope ship | `out_of_envelope` | Medium ship feeder_1700; limit is feeder_500 or feeder_800 (500–800 TEU). |
| Too many boxes | `out_of_envelope` | 196 boxes to load; limit 195 boxes. |
| Too full | `out_of_envelope` | Physical fill 83.333% (5/6 cells); limit 70%. |
| Layover | `out_of_envelope` | Two boxes onboard on arrival; limit zero. Stage B exceeds the stage A limit. |
| Two loading calls | `out_of_envelope` | Two loading calls; limit one. |
| Impossible instruction | `constraint_unavailable` (`proved_full_load`) | With every box aboard this limit can't be met: 143 TEU needed, 60 open. A partial plan wasn't tried. Correct the instruction or reduce cargo, then reconfirm. |
| Every instruction refused | `needs_confirmation` | Correct or remove unresolved voyage sentences, then rerun. |
| Empty brief | `no_checked_plan` in the failure fixture | No voyage instructions; planning normally. The injected search returns no checked plan; retry with the standard profile or less cargo. |
| Stale killed-process lock | `error` | A stopped runner left a stale lock. Choose a fresh output directory and rerun. |
| Confirmation callback raises | `error` | Confirmation could not complete; check the confirmation service and retry. |

The set also injects ENOSPC while writing events/outcome, EACCES during package
rename, a writer that fails after writing an index, a real 30 ms reader/engine
deadline, and SIGKILL of an owned subprocess. Failed commits leave no public
package index or staging folder. An existing package is deliberately preserved.
Voice is a pre-packet step: `preflight.prepare_voice(path, transcriber=...)` maps
the existing VoiceFlow result to contract statuses and keeps transcript review
separate from reading and confirming an instruction. Spark/voice network errors
are injected at service seams; no cloud outage or live provider was required.
The impossible-instruction case now reruns an authenticated stored refusal with
`lattice.refusal_lookup`, then runs the same confirmed rules through the real
binding. Both reproduce the necessary full-load capacity proof (143 TEU needed,
60 open); the real binding returns `refusal_kind: proved_full_load`. This proves
the stated limit cannot hold with every box aboard; no partial plan was tried.
Every-instruction-refused exercises both the runner and the real binding's
unresolved-reading gate, including a labelled host approval that cannot resolve
refusals. Neither case releases a plan or package. Exact evidence and source
pins are in `fixtures/failures/impossible-instruction-real.json`,
`every-instruction-refused-real.json` and `real-refusal-source.json`. Reader
answers remain recorded synthetic inputs; binding/proof implementations are real.
Envelope demand/capacity edge cases use an assembled-model seam and the real gate. These are failure-handling
checks, not engine accuracy or medium-envelope pass-rate evidence.

To refresh exact failure observations after a code change:

```sh
RUNNER_RECORD_FAILURES=1 uv run --offline --no-sync pytest \
  experiments/mvp/pipeline/runner/test_failures.py
```

The recorder replaces its observations for the run. Always wait for fleet
admission before timed CPU work. No API credits were spent on hardening.

## Measured cold process start

`fixtures/cold-start.json` records three fresh Python 3.12 processes on the
bundled synthetic tiny packet (three load boxes), admitted at load1 38.16–43.98
with 12 enabled cores. Median launch-to-first-envelope-verdict was **5.509 s**;
launch-to-first `parser.parse` call was **10.957 s**. Individual verdicts ranged
5.342–6.122 s; first reader calls ranged 10.481–12.014 s. These include interpreter
startup, imports, assembly and emitted verdict hashing, but exclude uv bootstrap,
reader inference, engine search and full checking. Filesystem and vessel caches
were left warm: this is process cold start, not a cold-disk or cold-model claim.

The median milestone intervals were startup/imports 0.953 s, assembly/setup
4.258 s, gate/verdict publication 0.298 s and reader preparation 5.448 s.
Reader preparation dominates because the shared reader API imports its historical
planning dependencies before `parse`; moving that import needs its owner outside
this folder. Cheap runner fixes defer binding, checker, ladder, reader, profile
Config and cache setup until their stages. The initial observation is retained as
`cold-start-initial.json`; concurrent edits prevent a controlled speedup claim.
The final fixture records source hashes and notes later non-startup edits during
measurement. No model, network, GPU or search ran in these probes.

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.runner.cold_start \
  --samples 3 --out experiments/mvp/pipeline/runner/fixtures/cold-start.json
```

Each sample waits for fresh fleet admission and stops on the first reader call.

## Logic-review relaxed-path record

`fixtures/relaxed-review/summary.json` records an actual runner → binding →
Engine 0.13.0 → native checker → package invocation: **L3 relaxed plan, 4/4 boxes
aboard, 7 weighted nonproductive lifts, full checker pass false**. The broken
rule is `expert.e8.away_from_heat`. The engine manifest was verified after search.
The package was written only after independent checker and instruction gates.

This is an explicitly synthetic routing fixture based on approved validation
seed `020000040`: only `size_class` was changed to exercise the frozen ladder;
physical geometry remains tiny, and ordinary L0 lanes were disabled by Config.
It is **not medium-envelope or medium-performance evidence**. It took 694.382 s
at admission load1 34.51/13 enabled cores, with much higher load later; this
single heavily shared run calibrates no profile. The initial short proposal
attempt returned no plan and is retained separately. The successful process's
summary was recovered from its persisted outcome after correcting the
postprocessor's explicit tier-table path, without another search.

The actual run's rank was finite. A separate regression uses the native ranker
on a qualitative breach to produce infinity, proves strict canonical hashing
rejects it, then hashes and writes the explicit unknown-value representation.
The stored plan, reader recordings, checker results, ranks and timings are all
synthetic or recorded as labelled; no network, GPU or paid call ran.

The first probe inherited binding's previous runtime-cache paths. Future runner
calls use a process-local scope and spawned-worker trampoline to keep derived
caches and audit files in `runner/cache`; existing binding artifacts are neither
moved nor deleted.
