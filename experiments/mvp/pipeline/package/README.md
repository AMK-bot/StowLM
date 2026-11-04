Synthetic ships and cargo, simulated port. Local demo signatures; proposal for planner review.

# Planner handover

`write_package(outcome, out_dir) -> dict` writes an empty output directory.
`verify_package(path, expected_public_key=None) -> dict` reads a directory or its `package.json` and reports
`files`, `chain`, `signature`, `checker`, `rules`, `coverage` and `handover` independently. Both APIs take
and return JSON-able values. Verification always reruns the full checker, including
after a hash failure. It never calls a model, a solver, the network or search.

Run from the repository root (Python 3.12; existing environment, no downloads):

```sh
export UV_CACHE_DIR="$PWD/.uv-cache"
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
uv run --offline --no-sync python -m pytest experiments/mvp/pipeline/package
uv run --offline --no-sync python -m experiments.mvp.pipeline.package verify experiments/mvp/pipeline/package/fixtures/complete
uv run --offline --no-sync python -m experiments.mvp.pipeline.package write experiments/mvp/pipeline/package/fixtures/outcome.json /tmp/stowlm-handover
```

Before time-limited CPU work, wait for fresh fleet admission (`build_fixture.wait_for_admission`).
The four numerical package tests do this themselves; cheap integrity tests explicitly
inject a recorded checker report, and receipt/render/note tests need no numerical rerun.
The CLI above runs real full checks; it does not bypass the checker or use an old verdict.

## Regenerate the actual hero

One command from the repository root, after the leader publishes the pipeline
manifest and its independently trusted SHA256:

```sh
./experiments/mvp/pipeline/package/regenerate.sh --pipeline-manifest experiments/mvp/pipeline/freeze/manifest-pipeline.json --pipeline-sha256 TRUSTED_PIPELINE_SHA256
```

Both arguments are required. The command checks the exact manifest bytes against
the supplied hash, runs the frozen-source guard, and rejects drift before loading
the reader or doing numerical work. Use the published hash; hashing a supplied
file does not independently establish its identity.

When running against the manifest you just built locally, the exact leader
commands from the repository root are below. Choose one regeneration variant:

```sh
pipeline_manifest=experiments/mvp/pipeline/freeze/manifest-pipeline.json
pipeline_sha256=$(shasum -a 256 "$pipeline_manifest" | awk '{print $1}')
./experiments/mvp/pipeline/package/regenerate.sh --pipeline-manifest "$pipeline_manifest" --pipeline-sha256 "$pipeline_sha256" --reader-mode live
# Recorded alternative, instead of the preceding live command:
./experiments/mvp/pipeline/package/regenerate.sh --pipeline-manifest "$pipeline_manifest" --pipeline-sha256 "$pipeline_sha256" --reader-mode recorded
```

Projected regeneration wall time is 6–10 minutes plus fleet-admission waits,
assuming the matching qualified acceleration tables remain available. This is
an estimate based on the earlier 146-frame DG regeneration's measured 346.222 s,
not a new bay-nine measurement. The adopted reader's recorded 200-sentence timing
is 6.797 s cold and 3.437 s warm median. Live Metal is untested in this sandbox.
The command requires both the fresh full checker and `hero-acceptance.json` to
pass before replacing the published package. Afterwards rerun the folder suite,
require its real-artifact gates to pass, and print the resulting bay plan.

The default reader mode is **live**, using the adopted local reader through
`hero_reader.adopted_parser`. Run it in the leader's Metal-capable environment,
outside the Codex sandbox. No Spark substitution is made. The command reads
`lattice/hero.json` and `radio/hero-selection.json`: the selected instruction is
“Nothing goes into bay nine on this call.”, with the canonical `close_bay(9)` rule.
It calls the real `lattice.lookup` (full checker, instruction and every-frame gate),
then calls the real `run_packet` with that stored candidate and its explicit SHA.
It confirms only the exact sentence/reading pair the user authorized, under the
named source `user_authorized_batch:package_regeneration`; this is labelled batch
confirmation, not an invented human identity. There is no live-search fallback.
The package callback captures the original signed runner receipt directly,
publishes its independent hash anchor, writes the hero package and verifies it
again. No runner or lattice source is edited. Numerical caches stay under package/.
Matching existing hull/liquid tables are copied from the lattice cache into this
folder, with their hashes recorded. The native metadata, byte-hash and independent
qualification checks still decide whether they can be used; no physics settings
or acceptance limits are changed. Without qualified tables the direct native
method remains available but is much slower.

The result is `fixtures/hero-run/package/`; `outcome.json`, `stored-candidate.json`,
`verification.json`, `native-frames.json` and `regeneration.json` preserve the
complete run, every native frame and timings. The pipeline hash is bound into
the final package receipt and regeneration evidence. The original runner receipt
is preserved exactly; its API has no pipeline-hash input, so that original field
remains null. The package attests the frozen pipeline used for the run.
Successful replacement archives the old output under `fixtures/history/` and
keeps its receipt anchor trusted. Failures retain diagnostic output separately
and restore the previous anchor. The historical 190-box fixture remains separate.

An explicit `--instruction PATH` can provide matching instruction metadata. Its
sentence and rule must still equal the adopted bay-nine selection, and its
instance hash must identify the selected hull. A supplied stored-plan hash must
match the lattice candidate. An old DG instruction is rejected; there is no
fallback when the bay-nine candidate is unavailable.

For offline replay, explicitly select **recorded** mode. The helper pins the
adopted-reader answers in `reader_final/readings/hero-candidates.json` to their
published hash and exact 87-box ship context:

```sh
./experiments/mvp/pipeline/package/regenerate.sh --pipeline-manifest experiments/mvp/pipeline/freeze/manifest-pipeline.json --pipeline-sha256 TRUSTED_PIPELINE_SHA256 --reader-mode recorded
```

Model, adapter, prompt and source hashes stay in the real runner stages.
Recorded replay uses no model, GPU or network and spends $0. The earlier
DG/blank Spark recordings cost $0.00335175; they remain historical evidence and
are not the adopted bay-nine reader. Recorded backup-hull inference is unavailable:
the existing recording is bound to the primary ship context. Reader factories
and custom readings are not an identity bypass; incomplete adopted-reader
metadata is rejected.

After regeneration, the leader can check the signed bay-nine handover and print
the exact resulting HTML outside the sandbox:

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.package.hero_acceptance experiments/mvp/pipeline/package/fixtures/hero-run --pipeline-sha256 TRUSTED_PIPELINE_SHA256
uv run --offline --no-sync python -m experiments.mvp.pipeline.package.print_check experiments/mvp/pipeline/package/fixtures/hero-run/package/bay-plan.html --pdf /tmp/stowlm-hero-bay-plan.pdf
```

The acceptance command audits recorded provenance, hashes, signature, reader
identity, selected rule, confirmation and every-frame checkpoint without a model
or numerical rerun. Regeneration separately runs the fresh full checker.

`test_hero_regeneration.py` requires the real published runner artifacts; it does
not substitute a mocked checker or invented stages for the hero acceptance test.
Print checking is performed by the leader outside the sandbox.
`test_bay9_regeneration.py` covers manifest preflight, radio selection and reader
mode routing with mocked freeze verification; it never loads Metal or runs physics.
Its 20 offline cases pass. The frozen-hero acceptance test explicitly skips the
retained pre-freeze fixture and becomes mandatory when the new artifact carries
a pipeline hash.
The previous complete folder suite passed **127 offline tests in 138.863 s**, including
standalone full verification of the actual hero outside the regeneration cache
scope and the four historical 190-box checker/tamper cases. Measurements are in
`fixtures/hero-tests.json` and `hero-pytest.xml`. Static inspection of the hero
HTML found six bay pages, 143 cell labels and no external assets. These are
measurements of the stored DG package, not the pending bay-nine regeneration.

The output contains a self-contained `bay-plan.html` (A3 landscape, one page per
planned physical bay), `crane-sequence.csv`, `yard-sequence.csv`, `manifest.csv`,
`weight-by-port.csv`, `restows.csv`, `tank-plan.csv`, `ballast-plan.csv`,
`handover-scope.json`, `cargo-work.json`, `not-included.html` (A4, one-page scope),
`stability.json`, `stability-summary.md`, `note-to-master.md`,
`note-evidence.json`, `instance.json`, `plan.json`, `checker-report.json`,
`receipt.json`, `packet.json`, `engine-manifest.json`, `rule-coverage.json` and the index.
Provenance is included when supplied. Box labels show identity, POD colour, gross tonnes,
slot, length, DG class and UN number; the legend explains repeated long boxes.
The bay section looks forward; a schematic side elevation shows all 14 bays,
including six hatched bays of fixed synthetic background cargo. Every human-facing
file starts with the half-ship banner. Native strict JSON inputs and the pinned
engine manifest retain their exact contract shapes/bytes; their common scope is
bound by the signed index banner and `handover-scope.json`, rather than inserting
unknown fields that would invalidate their schemas or source hashes.
The fixture is an empty section, on a ship whose other bays are a fixed synthetic
load: 190 planned boxes in eight bays plus 268 fixed background boxes (3,856.193 t).
The weight table closes to checker displacement (13,564.284 t): lightship,
section cargo, background cargo, ballast and other tank contents are all shown.
The physics summary extracts GM, trim, draughts, strength margins and securing
state from checker parts and imports `app.stage_physics` for limits and input identity.
Missing securing evidence remains unavailable. This is not a vessel-specific Cargo
Securing Manual or a whole-voyage assessment.

Relaxed packages put `RELAXED PLAN`, level, broken rules and boxes ashore first
in each human-facing file (HTML comment first, visible warning on every page;
CSV comment before the header; Markdown first line). JSON is machine-facing and
carries status/banner fields. Every CSV starts with the provenance banner; readers must skip its initial
`# ` comment. Relaxed integrity may pass while `checker.passed` and overall
`passed` are false. Instructions are rechecked from their canonical readings and
are never relaxed. Unknown readings fail closed.

## Outcome mapping

The runner's `PipelineOutcome` is a plain dictionary. Required fields are
`status` (`checked_plan` or `relaxed_plan`), complete `instance` and `plan` model
dumps, plus the complete `packet`. A hash alone cannot prove instruction coverage.
`label` is live/stored/recorded/generated.
Engine identity, `rules`, `lifts`, and stage rows come from top-level fields,
`evidence`, or the runner's shared `Receipt`. Explicit `records`/`steps` are also
accepted. An explicit `upstream_records` list prepends intake, yard and voice
`StageRecord` dumps, retaining model, adapter, prompt, input/output hashes, mode,
timing and original cost. The runner can pass `intake.records + yard.records + voice_records`
in that slot; missing stages are never invented. Rules contain `reading` (or binding's `canonical_envelope`); the same
deterministic reader constraint audit runs again during verification, with no
language parsing or allocation search. Packet sentences must match rule sentences
in exact order, including duplicates. Conflicting rule sources and explicit
unconfirmed readings fail closed. The fresh checker and deterministic ladder
classification supply lifts, level, broken rules, completion and boxes ashore;
contradictory caller metadata is rejected and every signed fact is rechecked.

An optional `note_selection` is a recorded Spark JSON selection over the exact
checker statement allow-list. `note_evidence` preserves its measured wall time
and original paid cost. Without it, the note is the labelled deterministic
template using the selection validator from `src/llm/spark/explain.py`; no network is attempted. Rejected IDs
fall back to that template with a recorded rejection. `engine_manifest`, when
provided, must match its stated file SHA and version. Otherwise the exact pinned
binding manifest is copied only when its hash matches. No manifest is fabricated.
`pipeline_sha256` displays **not frozen yet** in the index/banner and
`checker.pipeline_freeze` until the 2.1 hash exists; the shared receipt's hash stays null.
`computed_utc` or provenance records
identify a stored run; an absent computation date is explicitly unrecorded.

The master's allow-list contains departure GM, draught, trim direction, heel,
displacement and limits; special-cargo counts and slots; the three tightest
fractional margins across all checker margins; confirmed instructions and their
recorded confirmation source; and the exact inactive-rule/lashing caveat. It has
no yard box list or project labels. All statements remain printed even if Spark
does not select them. The previous paid recording uses the retired menu and now
falls back explicitly to the deterministic template; no replacement call was made.

`crane-sequence.csv` contains quay work; `yard-sequence.csv` contains the 92 yard
relocations, with original source move indices retained across both. Suggested
serial crane assignment, gross tonnes and POD are explicit. `list_deg_est` is a
labelled approximation using native mass/transverse moment and constant departure
GM, not a cargo-work hydrostatic verdict. On the historical fixture its peak is
2.830 degrees; 137 of 206 quay lifts exceed the 1-degree flag threshold. The plan
is unchanged and replays natively. **HEEL-BALLAST-01 is an unresolved operational
hold** before flagged work, requiring a chief officer's heeling-tank programme and
safe intermediate conditions. No invented transfer is credited against the estimate.
Arrival ballast is unchanged (1,551.754 t; human text rounds to 1,552 t); tank files
retain exact masses. Stability during loading is not checked; the in-port rule is off.

The flat manifest includes section cargo and individual fixed background geometry,
DG PSN/packing group/segregation, reefer settings, OOG dimensions and named projected
restows. Native data contain no EmS codes: the manifest explicitly requests the DG
declaration instead of inventing them. Background booking/POD/DG declarations are
also explicitly unavailable. Weight by port separates that undeclared background.
The two projected restow boxes and their slots/calls are listed in `restows.csv`;
these are discharge-access projections, not an executable future-port programme.

Set `presentation='hero'` only on the actual `run_packet` outcome. This fails closed
until its exact canonical runner receipt SHA256 is independently published in
`package/hero-run-anchor.json` after the leader hands over the real hero run.
That anchor is outside the package being verified. The runner's ephemeral,
self-supplied signing key is not trusted as proof of origin. The original runner
receipt must also bind the packet, instance and plan, identify
the pinned Engine 0.13.0 manifest, contains an explicitly confirmed instruction
and named authenticated host confirmation, and has the ordered source-hashed
assemble/reader/confirmation/engine/checker/ladder/receipt chain. The original
runner receipt and confirmation proof are preserved and re-audited during verify.
The application's published package key attests this evidence; source hashes are
recorded application provenance, not independent proof of process or human identity.
The acceptance test rejects the historical fixture as hero. The earlier provisional
87-box DG handover remains in `fixtures/hero-run/package/`: it came through `run_packet`
with a genuine recorded Spark reading, named confirmation, stored lattice candidate
and Engine 0.13.0. All 146 native frames passed. Regeneration took 346.222 s on the
shared machine; its fresh package verification took 3.686 s using the regeneration
cache. It predates current runner sources and the pipeline freeze, so it currently
fails the source-provenance release gate. The leader must regenerate the adopted
bay-nine handover after freezing. No old artifact is relabelled as that run.

## Receipt and trust boundary

The shared contract's canonical JSON and stage hash chain are imported directly.
Ed25519 signs the canonical receipt excluding only `signature`; `public_key` is
covered. The signed checker block binds every payload file hash and the index
metadata. The index hashes every other package file, including the signed
receipt. The index does not hash itself: that would create a circular hash.
Changing its file list or metadata still conflicts with the signed receipt.
Whitespace-only index reformatting preserves its semantic identity.

The application generates `.keys/application-ed25519.pem` at runtime (private,
0600); `.keys/` is ignored and is never in the package. Its README labels the
key as a demo key. A receipt includes only the public key. This proves integrity
under that key, not carrier, class or statutory approval. Verification defaults
to [our published public key](published-public-key.pem), independently of the receipt:

```text
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAAbQkuArnzAXpOZdVvzsJZPOOru0dtqUhpP0+g5q+Pfo=
-----END PUBLIC KEY-----
```

A valid signature by another key fails as **untrusted public key**. An explicit
`expected_public_key` accepts a trusted PEM path/text, raw public bytes or base64;
obtain that anchor outside the package being checked. The receipt's own key is
never selected as the default anchor. Signed `checker.chain_head` also detects
an edited or dropped final stage. The fixture contains no private key.
After setup: verify against our published key, with no model and no network.
A fresh installation generates its own private application key; publish and
explicitly trust its public half separately for new runs. Never replace the
published trust file automatically. Package writing always uses this service's
key under `package/.keys`; a runner's temporary `KEY_DIR` override cannot change
the package signer. Direct `sign_receipt` callers may choose a separate key,
whose public half must then be independently trusted explicitly.

## Active checker rules

The code-derived inventory contains **54 checker-active rules and 8 inactive
catalogue entries**: six contracts are disabled by `rule_freeze_v1=false`; two
soft objectives belong to the solver and are not checker-enforced. The flag is
unchanged. `checker.active_rules`, `inactive_rules`, counts, reasons and
`catalogue_sha256` are signed; `rule-coverage.json` also records runtime gate
locations and checker/move source hashes. Verification compares the inventory
against current code. AST gate checks fail closed on drift.
Active means enabled for checker enforcement, not exercised on every cargo packet.
Legacy companion behavior and cargo/port applicability remain explicit in the inventory.

`lifts` retains the pipeline's weighted nonproductive objective, which includes
projected later-port lifts; the CSV lists actual ordered moves. Future moves are
projections, not executed operations. See the checker count for the breakdown.

## Recorded evidence and limits

`fixtures/outcome.json` contains a real checked 190-box synthetic development
TRAIN plan from c6, with zero arrival placements. It is historical, **not** a
frozen Engine 0.13.0 result. `fixtures/historical-engine-manifest.json` is an
explicitly generated provenance manifest of its recorded source/configuration,
not a retrospective frozen-engine attestation. The original computation date
was not recorded. `fixtures/complete/` is the complete signed handover.
`build_fixture.py` reproduces the input adapter from the named unsealed TRAIN
artifact; it does not discover maps or search for a plan.

The initial fresh full checker took **66.144 s**, measured on this shared machine.
After the chief-officer review, all **116 offline tests passed in 470.63 s** on
the shared machine, including fleet admission waits and four real checker reruns.
The complete 190-box verification test took **56.833 s**, including setup/copy
and admission, rather than an isolated API timer. The fresh signed package write
took **65.357 s**. `fixtures/pytest-results.xml` and `verification-summary.json`
record these results. Before these revisions the isolated verification API took
42.183 s (checker phase 42.139 s); that earlier measurement is retained separately.
The retired Spark recording cost **$0.00336025** (1165 input, 448 output tokens);
`fixtures/spark-selection.json` records selection, model, hashes and usage.
Ordinary package writing and replay spend $0. Optional `build_fixture --spark`
uses the existing shared Spark ledger and a restart-safe $1 allocation with a
bounded request, and reuses its recorded answer thereafter.

Required tamper tests edit a move, a receipt row and an index hash and name each
failure. Full-suite/verification measurements are recorded in `fixtures/verification-summary.json`.
No sealed set, GPU, company data or live model is needed by tests.

BAPLIE was researched and explicitly cut; no EDI file is emitted. Public guide
fetches and complete profile mapping/independent validation were insufficient
to establish a correct exporter. [BAPLIE.md](BAPLIE.md) records sources and gaps.

`fixtures/print-check.json` records eight bay sections, 316 physical cell labels
(long boxes repeated), no external assets, and print CSS/page breaks. The leader
printed the earlier bay plan outside the sandbox: **8 pages, no errors, looks good**
(user-reported verification, retained with its original HTML hash). The revised
14-bay elevation and weight-table layout passes current structural checks; it
still needs an external print recheck. The earlier print is not evidence for the
changed layout. Never launch Chrome or another GUI application
from a Codex sandbox, including headless mode. The reusable screenshot-free
check opens local HTML with remote URLs blocked, inspects overflow, prints a
PDF, and compares actual PDF pages to bay sections:

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.package.print_check experiments/mvp/pipeline/package/fixtures/complete/bay-plan.html --pdf /tmp/stowlm-bay-plan.pdf
```

Run that optional command only outside the sandbox. It requires installed Chrome
and Node 22+ and terminates only its own child process. Structural tests run offline.
