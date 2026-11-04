# Frozen-source v2 regeneration and G1 drivers

**No new split release.** Existing `experiments/splits/v2/manifest.json` and its
trust pin remain unchanged. The 9,400 specs retain archived `feeder_1000` labels;
physical generation resolves them to `feeder_800` through D33.

## How to read these numbers

> Solve rate is plans returned / all attempted inputs, including timeouts; a returned plan may still fail checking.
> Checker passed / attempted includes every attempt; pass among returned separately measures agreement on returned plans, including unchecked returns in its denominator.
> Gold is conditional on solver success: selection can favor easier inputs ([Hernán & Robins, *What If*, ch. 8](https://miguelhernan.org/whatifbook)).
> Claim “fewer moves” only from paired inputs, showing n pairs / n excluded; bootstrap whole pairs ([Efron & Tibshirani](https://www.routledge.com/An-Introduction-to-the-Bootstrap/Efron-Tibshirani/p/book/9780429246593)).
> Held-out families test unseen combinations, port or noise settings; the vessel panel is an unseen ship/slice combination, not a wholly unseen ship, and is never used for tuning.

The leader edits [REQUIRED_RULES.json](REQUIRED_RULES.json) before freezing.
Freeze/check, gold and production SFT enforce its rule IDs and minimum schema;
changing it afterwards requires a new epoch. `selection-audit.json` reports
accepted / allocated / rejected / not-generated counts and emitted booking mix,
with warnings for held-out panels below ten. Raw pre-screen booking draws are
not retained by the generators: requested mass and DG mix are explicitly
unavailable beside the pinned profile recipe. No v2 backfill is performed.

Medium regeneration runs C2 on. Post-regeneration admission requires current
own generator/condition/campaign versions, gross masses ≤30,500 kg and fresh
background certificates. Catalogue-window physical diagnostics (after 18:20
through 18:49:45 EDT on 28 October) are invalid evidence; rerun qualification,
the frozen-rule background matrix and first-shard calibration before bulk work.

The 5.4 JSON retains solver-stage counts per rung and per ship × generator ×
port × noise family in `solver_yield`. Its scalar `solve_yield[rung]` is the
minimum return rate across required solver stages (4.6 for tiny/small, plus
4.7 and 4.8), and remains unknown if a required stage has no observations.
The leader must fill the per-rung `min_yield` values in the same-epoch G1
approval; synthesis leaves them null and `g1_met=false`. Yield requirements
supplement the original full-checker agreement gate.

Exact-versus-scalable gaps are labelled **on proved-optimal small instances
only**, with the matched count. Unproved small attempts have their own
scalable-versus-lower-bound population and count; its normalization is
`(objective - lower bound) / objective`. Per-method move means use that
method's approved plans and cannot establish cross-method move savings.

## Evidence integrity after driver review

The 18:36 round-3 audit routes maintained campaign/gold/eval readers through
`eval.snapshot_io.read_snapshot`: parsing, validation and SHA-256 all refer to
one immutable byte snapshot. Resume returns the checked row. Final gold
publication preserves the validated envelope digest and refuses later drift.
[SNAPSHOT_AUDIT.md](SNAPSHOT_AUDIT.md) lists every converted site and exclusions.
The default ARG_MAX child-launch test uses a padded synthetic inventory and
works without local caches. The real-inventory transport test remains an
optional diagnostic (`pytest .../test_pin_transport.py -m slow`).
Previous logic-review verification: **678 passed, one skipped**, plus **19 passing
training-lineage tests** after the final admission guard. REPORT.md records
the commands and the superseded E6 catalogue/engine integration issue.

The 18:00 re-review fixes replace inventory environment transport with
content-addressed files in the run directory. Children receive only
`CAMPAIGN_NUMERICAL_CACHE_PIN_PATH` and `CAMPAIGN_NUMERICAL_CACHE_PIN_SHA256`,
verify the file hash, then parse those same bytes. Legacy inventory-valued
environment variables are stripped/rejected. Regeneration spec markers carry
only the pin-file reference, avoiding a full inventory copy per spec.

Official harness publication rechecks inputs, source, cache bytes/routes and
component versions after planning/checking/lower-bound work, before each record
and final summary. Input hashing/parsing uses one byte read, including gzip.
G1 decisions, freeze, split and instance manifests retain the digest of their
validated bytes. Gold calibration resume restores committed publication state.

Transport-only real diagnostic (explicit synthetic tiny input, no evidence):

```sh
uv run --offline --no-sync python experiments/campaign-g1/diagnostic_transport.py \
  --input PATH/TO/SYNTHETIC-TINY.json \
  --output experiments/campaign-g1/diagnostic-transport-NEW --timeout-s 180
uv run --offline --no-sync pytest experiments/campaign-g1 tests/eval tests/experiments
```

The diagnostic hashes the actual current inventory, runs the existing stage
worker, records its owned PID and uses a bounded timeout. It records transport
success separately from stage completion, always with `admitted=false` and
`evidence_eligible=false`. Prior round-2 full-suite verification: **481 passed, one
skipped**, exit 0. Exact artifacts and cold-start limits are in REPORT/COMPUTE.
The real worker subsequently completed against **133,438 actual-hash files**:
24,634,747-byte pin file, 5,320-byte environment, worker exit 0 in 107.123 s.
The mask outcome was infrastructure-blocked on stale linear qualification;
this demonstrates launch/cache plumbing and supplies no production evidence.
The initial cold attempt hit its 120 s limit; its failure is preserved.

Create a **fresh freeze** after these fixes. Schema-2 freezes bind the exact
stability, vessel and linear cache bytes, including an environment-selected
vessel cache, into the epoch. Old freezes without those pins are rejected.
Every reuse verifies the pinned originals and immutable local base. Workers and
spawned children receive fresh working copies; generated additions are never
promoted into reusable frozen caches. Hashing adds I/O overhead; the historical
diagnostic timings below do not measure the new verification overhead.

Production qualification rejects diagnostic, allow-drift, tiny-run, unofficial
or explicitly ineligible lineage at every nesting depth. The original report
and its exact-byte hash are retained. Official harness references require
configuration-bound official status and matching epoch, source, instance
manifest and current component versions. Historical references missing that
provenance must be regenerated. Generic official harness runs require complete
prewarmed caches at the frozen reader paths; route changes or cache additions
fail closed, and prior numerical memoization is cleared before evaluation.

Retries quarantine old outputs and require the child to echo this attempt's
identifier. Nonzero or timed-out children cannot retain approval. New done
markers inventory every returned artifact and checksum. Missing or corrupt
plans remain failed/unknown replay obligations. Production synthesis and gold
publication require verified checkpoints; legacy result-only markers cannot
resume. Restore an inventoried artifact to its recorded bytes, or choose a
fresh output, rather than replacing a committed shard and losing its denominator.

## Commands and order

The leader finishes linear policy requalification **before source freeze**.
Requalification may write `src/stowage/linear/margin_policy_data.json`; regen
never does. Central cache qualification must also complete before regen starts.

```sh
export PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export UV_CACHE_DIR=/private/tmp/stowage-w2-freeze-uv

# After the leader's source-changing requalification:
uv run --offline --no-sync python -m stowage.cache_warm --workers 1
uv run --offline --no-sync python experiments/campaign-g1/freeze.py
uv run --offline --no-sync python experiments/campaign-g1/freeze.py --check

# Example cumulative tranche (counts are train totals per rung):
uv run --offline --no-sync python experiments/campaign-g1/regen.py \
  --workers 1 --tranche first --train-per-rung tiny=200,small=100,medium=25,full=0 \
  --calibrate-first-shard --calibration-rung tiny

# Resume the same named request, then start/resume B on available instances:
uv run --offline --no-sync python experiments/campaign-g1/regen.py --workers 1 \
  --tranche first --train-per-rung tiny=200,small=100,medium=25,full=0
uv run --offline --no-sync python experiments/campaign-g1/campaign.py \
  --workers 1 --count 2 --rungs tiny small medium

# Later cumulative target: the same root grows; completed requests are reused.
uv run --offline --no-sync python experiments/campaign-g1/regen.py --workers 1 \
  --tranche second --train-per-rung tiny=400,small=200,medium=50,full=10
```

These production counts are command examples, not a compute reservation. Every
real tranche includes all validation/test specs, including full, even when
`full=0` excludes full training specs. Measure/admit those full requests first.

Check fleet admission before increasing workers. This follow-up used one
computational CPU per job. The shared ledger reserved 13 CPUs, leaving three
free. Twelve-worker packing is for a later reservation with four CPUs busy
elsewhere; it is not permission to launch twelve now.

Preflight refuses incomplete qualification **even with --diagnostic --allow-drift**.
It requires central complete=true, every production ship/linear geometry,
empirical measured-policy status, valid policy SHA and the mandatory checker.
The report is hashed; source/epoch fields are checked when provided. Current
upstream reports lack source fingerprints, so the leader must supply a freshly
completed report for frozen physics; that limitation is recorded in provenance.

## New epoch content, existing allocation

Output is staged inside scope at:

`<epoch>/regen/release/experiments/instances-e<epoch>/`

Neither `instances-v2/` nor `instances-v3/` is overwritten. No splits v3 is
created. The leader may publish this entirely new root with its manifest. That
manifest embeds source/version provenance and binds each raw v2 spec to exact
instance bytes. No later source/trust edit is required to select an epoch root.

The production generator's immutable resume files live in `.producer/`.
Published copies add validated `family.split_vessel` for the archived requested
label while `family.vessel` remains canonical. An arbitrary different-ship label
fails closed. Original producer bytes/audits and annotated publication bytes are
independently pinned. This preserves v2 keys for gold/SFT identity checks without
invalidating generator resume. Rejected requests stay visible with hashes and
denominators; no alternative spec is substituted.

Reuse the same command/configuration to resume. Source, drivers, inputs, report
or mode drift stops admission. `--calibrate-first-shard --calibration-rung RUNG`
measures one pending request and stops after checkpointing it; omit the flag to
continue. D58 runs unchanged and can increase slice generation time.
[SCHEDULE.md](SCHEDULE.md) leaves unmeasured rungs and total wall time unknown;
it never projects medium/full from tiny.

## Tranche semantics

`--train-per-rung` gives cumulative totals across train families in each rung;
omitted rungs are zero. Named requests are immutable: use a new `--tranche` name
to raise the targets. Selection is a stable balanced prefix, round-robin across
families and ship × generator × port × noise cells, with seeds ascending within
each cell. The archived v2 specs and trust pin are unchanged. Without tranche
arguments, the previous full-allocation command remains available.

Each spec has a stable done-marker independent of tranche name or worker count.
Accepted and genuinely rejected requests both resume without rerunning. The
epoch manifest checkpoints after every completed shard and retains the union
of earlier tranches; `tranches`, `spec_tranches`, `pending_spec_keys` and
`not_generated_spec_keys` record membership and coverage. Missing requests mean
**not generated yet**, never a synthetic rejection. Only accepted published
instances appear in consumer groups. A tranche may complete while the complete
9,400-spec allocation remains unfinished.

G1 and `experiments/gold-plans/campaign/gold.py` select only those accepted
groups and validate their files. An extended manifest changes campaign resume
identity: finish the current campaign before extending, or select a fresh
`--output experiments/campaign-g1/EPOCH/campaign-TRANCHE` for the new inventory.
Changing inventory during a campaign fails closed.

Normal regeneration prints a measured remaining-request schedule after the
first completed shard of each new rung. The explicit calibration hook can stop
there. Only matching generator/rung/ship cells are projected; missing cells
leave the total unknown. Full is never inferred from tiny or medium.

## Explicit consumer roots

Harness APIs require explicit `instances_dir`. Its CLI requires one of
`--instances-dir`, `--instances` or `--instance-root`:

```sh
uv run python -m eval.harness --instances-dir PATH/instances-eEPOCH \
  --regen-manifest PATH/instances-eEPOCH/manifest.json --run-dir PATH/RUN
```

The manifest argument defaults to the selected root's `manifest.json`. Epoch
validation checks trusted v2 membership, freeze/source/version provenance,
spec-to-content bindings, selected SHA and alias-aware identity before planning.
Official evaluation rejects diagnostic instances. Root, epoch, source hash and
manifest hash enter resume config/results. Explicit legacy roots remain readable;
no default silently selects `instances-v2`.

Shared APIs: `eval.splits_epoch.validate_epoch_manifest`,
`validate_epoch_instance`, `instance_matches_spec`. No hardcoded instance-root
default was found in `src/llm/stowlm`; SFT consumes explicit inputs. Gold's
out-of-fence `experiments/gold-plans/tiny/driver.py:590` and `status.py:12` still
default to `instances-v3`; existing `--instances` selects another root. That
driver does not yet call the shared epoch validator; its owner should wire it in
before epoch gold production. Those files were not edited.

## B and diagnostic

B interleaves 4.2/4.6/4.7/4.8 in one bounded pool, then 5.1/replay, then 5.4.
Existing experiment functions remain in use. Evidence requires completed
same-epoch generation/bindings, prior cache qualification and exact v2 members.
All returned solver candidates get ballast, full checking and production mask
replay. Missing plans are unknown denominators; gold affordability counts unique
approved physical plans. Expert-rule coverage still needs its own audit for G1.

Requested tranche diagnostic, one computational core, one generator attempt per
request and a 120-second owned-process limit per request:

```sh
uv run --offline --no-sync python experiments/campaign-g1/diagnostic_v2.py \
  --manifest experiments/campaign-g1/diagnostic-tranches-freeze.json --probe \
  --tranche pilot --train-per-rung tiny=2,small=2,medium=1,full=0 \
  --diagnostic-eval-per-rung 0 --max-attempts 1 --timeout-s 120
# Run that exact command again to verify resume, then extend:
uv run --offline --no-sync python experiments/campaign-g1/diagnostic_v2.py \
  --manifest experiments/campaign-g1/diagnostic-tranches-freeze.json --probe \
  --tranche extension --train-per-rung tiny=3,small=2,medium=1,full=0 \
  --diagnostic-eval-per-rung 0 --max-attempts 1 --timeout-s 120
uv run --offline --no-sync pytest experiments/campaign-g1 tests/eval
```

For a qualified diagnostic front door, use `regen.py --manifest ...
--diagnostic --allow-drift` with the same tranche/count/eval arguments.
`--diagnostic-eval-per-rung N` caps **each** val/test split per rung and is
illegal for real runs. The zero cap above explicitly skips diagnostic eval
requests; production always includes all 400 eval requests. The independent
helper probe does not waive cache qualification: if admission fails, its
artifacts remain `admitted=false`, `complete=false`, diagnostic, not evidence.
It exercises real generator physics, tranche membership and done-marker resume.

Current tranche probe: **five requests in 67.631 s**, two generated / three
rejected, zero helper errors. Same-command resume: **0.887 s**, five reused.
Extension: **16.860 s**, five reused and exactly one new request. Six cumulative
requests leave 9,394 not generated, separately from four actual rejections.
Combined suite: **235 passed, one skipped**. Details and measurements are in
[REPORT.md](REPORT.md).

Prior v2-only probe (historical):
```sh
uv run --offline --no-sync python experiments/campaign-g1/diagnostic_v2.py \
  --manifest experiments/campaign-g1/diagnostic-v2-freeze.json --probe
uv run --offline --no-sync pytest tests/eval experiments/campaign-g1
uv run --offline --no-sync python experiments/campaign-g1/schedule.py \
  --calibration experiments/campaign-g1/diagnostic-20261028-a9490eb84562/v2-probe/calibration.json
```

The strict CLI refused the current complete=false cache report. The separate
explicit helper probe used four v2 tiny requests, one candidate attempt each,
actual D58 acceptance, full physics and a timestamped diagnostic source copy:
**one generated, three rejected, zero helper errors, 63.524 s**. Both generators
and the middle-ship alias were exercised. The accepted binding was checked with
the shared eval validator. This is **diagnostic, not evidence**: no regeneration
was admitted, result.complete=false, and no fake qualification was supplied.

Artifacts: `<epoch>/v2-probe/{preflight.json,result.json,calibration.json}`.
Historical v3 staging from the initial order remains diagnostic-only and is
superseded. Previous follow-up suite: **222 passed, 1 skipped**; current tranche
suite: **235 passed, 1 skipped**. No real campaign, GPU,
cloud, commit or push was performed. See [REPORT.md](REPORT.md) and
[COMPUTE.md](COMPUTE.md).


Rule migration readiness (L1 checklist)
--------------------------------------
`uv run python experiments/campaign-g1/freeze.py --readiness` prints every
static and canary blocker and exits 1 while any is missing. It performs no
solver run and creates no source freeze. `--canary-report PATH` selects an
explicit proof journal; absent, stale, diagnostic, incomplete or cache-mismatched
proofs refuse admission. Operational profiles and later-call resolution are
checked alongside the **effective loaded** catalogue, not inactive TOML history.

A production `freeze.py` call now requires this gate to pass, pins its proof
artifacts, implementation hashes and loaded prompt/checker/mask/solver epoch,
and repeats those checks on `--check`. A diagnostic freeze records the full
missing list and remains ineligible. Old manifests need a new freeze.

After regeneration, the G1 precondition consumes **all 5.4 solver plans**, exactly
once and nonempty in every evaluated rung. `5.4/solver-plans.json` binds originals,
ballast-finalized plans, primary/replay done markers and the campaign population.
Stored gold is produced after G1 and is not a precondition.

To obtain the live D62 table for the leader's review before approval, run:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  uv run python experiments/campaign-g1/migration_evidence.py \
  --freeze-manifest "$FREEZE_MANIFEST" --instance-manifest "$INSTANCE_MANIFEST" \
  --campaign-manifest "$SOLVER_PLANS" --summary "$G1_SUMMARY" \
  --cache-work experiments/campaign-g1/EPOCH/migration-measure-01 \
  > experiments/campaign-g1/EPOCH/migration-measure.json
```

This executes all returned plans. An unfilled approval table causes exit 1 while
the report still supplies measured counts and exact refusal reasons. The leader
reviews those counts and fills the summary; local signed admission then repeats
the live verification against the completed approval. Each attempt inventories
every returned candidate exactly once; it cannot substitute caller-written counts.

Fill `min_yield`, `g1_met`, and explicit zero acknowledgements in the leader's
summary. Its `migration_evidence: {path, sha256}` names a declaration containing
`epoch_id`, `diagnostic: false`, `evidence_eligible: true`, and immutable
`{path, sha256}` entries `freeze_manifest`, `instance_manifest`, `campaign_manifest`.
Then execute local admission once on the frozen machine:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  uv run python experiments/campaign-g1/rule_policy.py \
  --admit-summary "$G1_SUMMARY" --output experiments/campaign-g1/EPOCH/admission-01
```

Admission executes the full checker and default production mask on every returned
plan. It derives D62 counts from live, epoch/input/plan-bound observations over
the exact campaign population; unknowns refuse. Counts also separate port × ship.
Each observed-zero panel needs `d62_zero_acknowledgements["rung|port|ship"] =
{"epoch_id": EPOCH, "acknowledged_by": LEADER}`. See
[D62_OBSERVATION_CONTRACT.md](D62_OBSERVATION_CONTRACT.md).

Use the resulting `approved-summary.json` for gold/SFT consumers. It includes a
portable Ed25519-signed admission capsule bound to that exact approval, source,
loaded catalogue/components, gate implementation and artifact inventories. Raw
checker/mask JSON never proves execution. Upload the public `admission-trust.json`
with the capsule and code; **never upload `.admission-private/`**. Private signing
material is mode 0600 and ignored. Remote consumers verify the trusted public key,
loaded versions and dataset content without re-running physics or accessing the
original absolute cache paths. A different policy, approval or gate revision
requires a new local admission.

Run paired canaries after cache_warm and before freeze. The next diagnostic waits
for D74 and D75 to land; follow [the next-run procedure](D74-D75-PROCEDURE.md).
It keeps the raw rejection pairs separate from the D75 pipeline comparison and
adds D74 rate-control cases, whose mask need not screen pumping rates.
The next round uses the [landed DG contexts](canary-inputs/dg-contexts/README.md)
and compares checker/mask verdicts on identical expanded moves. A manually edited
truck endpoint is not the canonical yard `Place` expansion; that mismatch is not
a mask admission bug. Context inputs still need real-slice qualification.

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  uv run python experiments/campaign-g1/migration_canaries.py \
  --execute --report experiments/campaign-g1/migration-canaries.json
```

For the pre-flip diagnostic, add `--diagnostic` and choose a fresh report path;
its receipts remain ineligible. Builders use a generated production feeder slice,
never the placeholder fixture. Clean twins must fully pass; defect twins isolate
the declared primary rule and require its independently declared production-mask
reason from the engine. Refusal exercises allow only their explicitly declared
`objective.all_loaded` checker fallout; this is never a mask denial reason. E1/E2
are the catalogue's two IDs for the same kg predicate: both and no unrelated
violation are required. Accept cases cover equal masses, E6 soft heavy-on-deck,
empty OOG killed slots and D69 yard crossing with a counted relocation.

Contract A8 (29 Oct): yard crossing blocks at the effective yard limit, not quay
H. Both yard cases use the actual Laem Chabang-like profile: yard 5, quay H=8;
the D69 accept case uses five blocking boxes and requires the counted rehandle.
Ship crossing continues to use its current port's quay H. The historical
`yard_crossing_at_h` journal name is retained; it no longer denotes a yard H cap.

The concrete 4.1-owned physical witness request is
[INPORT_CANARY_REQUEST.json](INPORT_CANARY_REQUEST.json), with validation rules in
[INPORT_CANARY_CONTRACT.md](INPORT_CANARY_CONTRACT.md). No waiver exists. Current
fleet DoC/action-grammar blockers are in [DOC_CANARY_SOURCE_GAP.md](DOC_CANARY_SOURCE_GAP.md).
One paired witness tests wiring, not exhaustive rule coverage. Missing physical
qualification, non-isolated defects and unavailable mask reasons refuse the gate.

The measured review diagnostic command was:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  uv run python experiments/campaign-g1/migration_canaries.py --execute --diagnostic \
  --diagnostic-cache-root experiments/campaign-g1/canary-diagnostic-v2/worker/cache \
  --report experiments/campaign-g1/canary-diagnostic-owned-v3.json
```

Use a new report name to repeat it. The alternate owned cache root is diagnostic
only; production refuses that option. Baseline physical fields must match the
original pinned full-vessel cache; a missing current canonical entry requires
cache_warm, and mutable attempt additions cannot establish canonical authority.
Production automatically regenerates a
missing/stale baseline in the attempt's private cache and refuses screening
failure. This run visited all fourteen cases but qualified zero pairs after fifty
baseline rejections. See [MIGRATION_REPORT.md](MIGRATION_REPORT.md) for timings and
the exact remaining source/input dependencies. It is not physical rule evidence.
