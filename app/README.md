# StowLM apps

StowLM is an AI pipeline around a ship-planning engine, delivered as an app: Muse Spark reads the cargo papers, Muse
Voice Transcribe hears the planner, StowLM Reader (Muse Glimmer 30B fine-tuned by us with LoRA, on this laptop) turns each
instruction into a strict rule, the planner confirms it, the engine plans, the full checker decides, the rule-tier ladder
names any rule a relaxed plan breaks, and a signed handover package comes out. The engine sits in one slot: Frontier
Engine 0.13.0 is the reference engine, and the design lets a firm put its own planning software there. Only Engine 0.13.0
is wired into these apps; no third-party engine has been ported (see "The engine slot" in the repository README).

There are two apps on one planner page. They look the same; what differs is where the answers come from.

| | **Demo app** | **Functional app** |
|---|---|---|
| Open | `/planner?demo=1` | `/planner?live=1` |
| Launch | `uv run python -m app --v6` | `uv run python -m app --functional` |
| Ship and cargo | the film's call (Laem Chabang, call 412) | a fresh synthetic call from a new seed, generated when you press Create call |
| Cargo papers | a recorded Muse Spark reading of a 12-box demo voyage | your papers, or the call's generated synthetic papers, read now by Muse Spark |
| Instruction | a recorded radio call and a recorded reading | your typed sentence or WAV, transcribed now by Muse Voice Transcribe and read now by the StowLM Reader on this laptop |
| Plan | the stored Engine 0.13.0 plan, computed 2 Nov 2026; its recheck status is shown with the plan | planned now by Engine 0.13.0 and checked now by the full checker |
| Meta Model API key | none needed | your own key, for Muse Spark and Muse Voice Transcribe; none ships with StowLM, and the reader, engine and checker need none |
| Badge | "Demo app" | "Functional app" |

Both apps from one server: `uv run python -m app --v6 --functional`, then open both URLs. Everything is synthetic: ships,
cargo, papers and port are generated or invented, never company data.

## Functional app

The take-4 planner, running the pipeline live (D353). Every number on the page comes from the call you just made.

1. **Install** (once): `uv sync --python 3.12 --locked` and `uv run python -m stowage.data_setup` (hull data for the
   checker).
2. **The StowLM Reader** (optional; Apple silicon): base weights in `models/glimmer-30b-4bit/` and the reader adapter, as
   in the repository README's "Live StowLM Reader on Apple silicon". The app finds the adopted adapter by its recorded
   path, or take `--reader-adapter DIR`. Without them the app still runs: the reader shows "unavailable" and why, and you
   can plan with no instruction.
3. **Your Meta Model API key** (for Muse Spark and Muse Voice Transcribe). Either enter it in the app (the key chip in
   the header: it stays in this local server's memory and goes only to Meta's API for the calls you run), or copy
   `.env.example` to `.env` and fill in `MODEL_API_KEY` (and `STOWLM_ACCEPT_PAID_CALLS=1`, or accept paid calls in the
   app). `.env` is git-ignored. No key ships with StowLM. Without a key, the generator, the engine and the checker still
   run, and the page says which steps need one.
4. **Run:** `uv run python -m app --functional --port 8793` and open http://127.0.0.1:8793/planner?live=1.

**On the page:** 1 · Ship & call (ship, section size, fill, generator, port, seed) → Create call generates the ship and its
cargo and runs the envelope gate. 2 · Cargo → "Read these papers with Muse Spark" reads the call's synthetic papers one
booking (at most 6 boxes) per call, or upload or paste your own with a role you choose; answer any questions it raises;
or choose "Use the generated load list, not read by a model". 3 · Instructions → type a sentence, or upload a 16 kHz mono
WAV (or record one in the browser) for Muse Voice Transcribe, edit the transcript, "Read with StowLM Reader", then confirm the reading (or plan with no
instruction). Generate plan → Engine 0.13.0 searches (standard profile by default, engine deadline 1,800 s; live 720 s),
the full checker decides, and the D241 ladder names any relaxed rule. The provenance under Checks lists each stage with
who ran it, where, its measured wall time and its cost. After any run, "Plan again" lets you change the instruction or the
profile on the same call; earlier attempts stay listed. If Muse Spark's reading leaves a box the intake can't read by
design (an unsupported equipment type such as a 45P3 platform, or OOG dimensions), you can take the generator's record
for just that box; its row says "generator, not read by a model" and the rest stays Spark's.

**What it is and isn't.** Inside the design envelope only (up to 195 boxes in a half-ship section of a 500–800 TEU
feeder, up to 70 % fill, an empty section on arrival and one loading call); outside it the call stops and says why. A plan
is drawn only when it passes the full checker and every confirmed instruction, or as a relaxed plan that names its level
and every rule it breaks. A search that ends without one says so: that isn't a proof that no plan exists. Medium searches
with an instruction can run to the engine deadline. Stress test and Ask copilot aren't live yet, and the page says so;
nothing recorded is ever shown in their place. Spend: a per-launch cap (default $10, D359); each Muse Spark call holds
about $1.38 until it settles (its worst case), and on 3 Nov 2026 an 86-box call's 15 bookings read for $0.77 and a
24-box call's 4 bookings for $0.34 (measured on this machine). Live runs, including the ones that didn't pass, are
recorded in `experiments/mvp/ui/functional/verification/VERIFICATION.md`.

Developer notes, routes and file layout: `experiments/mvp/ui/functional/README.md`. A recorded run of this app on fresh
synthetic inputs, with no API calls, is on the project site's play page (https://amk-bot.github.io/StowLM/play/).

## Demo app

The take-4 planner as filmed (D345–D352): `uv run python -m app --v6`, then open
**http://127.0.0.1:8000/planner?demo=1**. It replays the stored plan and recorded answers and carries the "Demo app"
badge; its behaviour is unchanged from the recording.

# Earlier pages (retired; kept for the record)

Everything below describes pages built before the two apps: the v6 stage, the v3 replay dashboard and the stage page.
They still load, but they are not the product.

From the repository root, the retired v6 stage (the earlier film flow) launches with Python 3.12 through uv at
**http://127.0.0.1:8000/stage?v6=1**:

```sh
uv run python -m app --v6
```

The first page load can take minutes while the hero plan is regated; the terminal says when it's warm. Add
`--manifest PATH` (or set `PIPELINE_MANIFEST`) to run against a frozen pipeline: the launcher verifies the tree against
that manifest first and refuses to start on any drift, listing each one. Receipts then carry `pipeline_sha256` and the
pages show its first 12 characters. Without a manifest the stage page and every receipt say "Development build, not
frozen". The 25 pinned break-it drags also replay with no server at all: open
`experiments/mvp/ui/replay/index.html` (rebuild it with `uv run python experiments/mvp/ui/replay/build.py`).

The older v3 flow below starts with no flag:

```sh
uv run python -m app
```

Open **http://127.0.0.1:8000**. The server binds localhost only. All assets are
local: no JavaScript packages, CDN, external fonts, network calls, or new Python
dependencies are needed. If uv's usual cache is restricted, prefix commands with
`UV_CACHE_DIR=/private/tmp/stowlm-ui-uv`.

The default is `experiments/instances-v2/random_yard/small/seed-00100001.json`,
with discovery of another v2 small instance or deterministic generation as a
fallback. To replay other inputs:

```sh
uv run python -m app --instance experiments/instances-v2/random_yard/small/seed-00100001.json --port 8000
uv run python -m app --instance /path/to/instance.json --plan /path/to/plan.json
uv run python -m app --instance /path/to/instance.json --plan /path/to/planner-result.json
uv run python -m app --run-folder experiments/eval-runs/my-run
uv run python -m app --run-folder experiments/eval-runs/my-run --entry results/example.json
uv run python -m app --instance /path/to/instance.json --plan /path/to/solver.json --compare-plan /path/to/baseline.json
uv run python -m app --run-folder experiments/eval-runs/solver-run --entry instances/example.json --compare-run-folder experiments/eval-runs/baseline-run --compare-entry instances/example.json
uv run python -m app --import-plan /path/to/stowlm-result.json
uv run python -m app --instance /path/to/current-small.json --no-discovery
```

Inputs use `stowage.io` and its versioned `Instance`, `MovePlan` and `PlannerResult`
contracts, including `.json.gz`. A supplied plan must match the instance and call.
Illegal plans retain their successfully replayed prefix and show full checker
diagnostics, including the failed lift's original zero-based move index. No
uploaded or client-selected filesystem paths are accepted by the HTTP API.

Run-folder discovery supports separate instance/plan/result files and per-instance
records wrapping a `result` or `planner_result`, optionally with an embedded
`instance`. Automatic resolution requires a verified complete-world content
binding or the evaluation record's original-file `input_sha256`. It searches
current simulator/fix-wave, v3 and historical v2 corpora. Names and seed filenames
are search hints; they never establish world identity. Legacy unbound summaries
and bare plans require explicit `--instance`. An explicit world must agree with
any selected embedded instance and available hashes. Conflicting same-ID worlds
are rejected unless verified content distinguishes the selected input.
A folder containing several results
requires `--entry`, whose error lists candidate paths. A PlannerResult without a
plan cannot be replayed. Current eval harness records at
`experiments/eval-runs/<run>/instances/<spec_key>.json` are supported: their
compact `instance` summary is a search hint, and the actual instance is loaded
and validated against its content binding. Stored checker verdicts are ignored and
recomputed. Imported result provenance, status and later-call plans are retained
for fresh shipping checks. Campaign seed filenames accelerate matching without substituting
for checking the actual instance id. `--compare-plan`/`--compare-run-folder`
register a reference for the selected input; repeat `--import-plan` to register
other outputs. All registered run-folder plans are also discovered automatically.

For the three-minute demo, choose an instance and click **Plan & compare**.
That single action runs the selected scalable/exact solver and two-step baseline serially,
then opens their side-by-side counts. The planner switch chooses which is the
primary plan. The picker offers a compact, deterministic representative per
campaign family/rung, plus every CLI-selected and discovered eval-plan instance.
Live solving supports tiny/small; larger instances use imported output. The
catalogue exposes content digests and disables live solving for conflicting
same-ID sources. Registered worlds are snapshots; changed source files or
mutated inputs fail closed. `--no-discovery` keeps explicitly selected inputs
isolated from historical corpora while preserving all identity/checker gates.

To avoid waiting during the recording, launch with `--plan` and `--compare-plan`,
choose **Imported plans / StowLM**, then **Compare saved plans**. Selecting the
imported pair needs no solver run. StowLM is imported output only at this stage;
the UI makes no model inference or Spark API calls.
With a saved pair already registered, clicking **Moves vs baseline** also opens
the default comparison directly; `?view=compare` opens it at page load.

The comparison shows loads, discharges, yard rehandles, restows and hatch lifts
now, projected later-port restows/hatch lifts, total lifts and the D11 weighted
objective. It refuses different instance contents even when ids match. A saving
is presented only when both freshly checked `PlannerResult.shippable` properties
are true and neither plan was repaired. A repaired plan retains its physical
checker verdict but receives a **REPAIRED · solver.scalable** badge and its repair
note; the comparison remains diagnostic. This includes all required checker parts, complete load lists and clean
recursive provenance. Required multi-call results remain ineligible until the
shared planner supports sequential checking. Checker pass and departure approval
are displayed separately, with provenance and stub reasons visible. Failed plans remain visible as diagnostics.
Each card states how many supplied moves executed. An illegal/stopped replay
shows **executed-prefix totals**; executing every supplied move gives **complete
supplied-sequence totals**, which alone does not certify cargo completion.
These are observed plan differences, not optimality or industry-performance claims.

Expand **unmatched nonproductive lifts** to inspect extra actual lifts; **Show
before lift** opens that plan immediately before the event and highlights the
box being lifted. Lifts match by move type and container/cover, retaining repeated
occurrences; a changed relocation destination alone is not a saving. This evidence
does not prove a causal claim such as a bad yard pick. Projected lifts are counts,
not fictitious playback events. Each plan has a collapsible deterministic template
explanation from task 5.6, using the existing report with no remote calls.

Live jobs use `stowage.planner.run_planner`, including ballast, checker and repair.
The two-step baseline explicitly sets `allow_repair=False`, so a failed baseline
cannot silently become a scalable-solver fallback.
One background worker admits one job at a time; a comparison runs both planners
serially. The browser polls actual stage, status and elapsed time; it does not
invent solver completion percentages. The UI requests a 15-second limit per
planner, and the API accepts positive limits up to 30 seconds. These are nominal
pipeline limits: individual oracle/checker calls and subsequent pipeline phases
can overrun them. A timeout/no-plan result is shown explicitly. The UI never
relabels a partial or rejected result as approved. No cancellation or hard thread
termination is attempted.
If a planner produces no plan, its actual status/message is shown and no replay
or comparison is invented. Live IDs are `solver.scalable`, `solver.exact` and the frozen
`baseline.twostep`. A baseline primary is compared with the scalable solver.
Explanations use validated, prefetched Spark sidecars when available, otherwise
the deterministic template. Serving the page never calls Spark.

For an opt-in real integration smoke on a current generated small instance:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run python -m app.smoke --generate-small --seed 1 --time-limit-s 5 --max-wait-s 55 --output /private/tmp/stowlm-live-smoke
```

This records requests, actual outputs when available, source pins and failures;
it never treats a timeout or upstream qualification error as successful planning.
Current bounded smoke evidence and exact browser steps are in
`experiments/review/fixes/fix-app.md`, section **6.5**. At that source epoch both
planners returned no plan because the production linear policy needed
requalification; successful real-plan comparison remains unverified.

Without a supplied plan, the deterministic placeholder uses fixed-order first-fit
ship placement and the existing nearest-stack yard dig-out. **APPROXIMATION:**
this greedy demonstration has no optimality claim, does not finish ballast, and
does not certify cargo rules or naval safety. It can leave explicit shut-outs.
Physical legality means the crane can perform each lift; it does not mean the
departure passes the full checker. The placeholder is now checked and its real
failures are shown. It remains a playback demonstration, not an operational plan.

Use the yard and bay views to inspect POD colours and cargo markers; hover a box
for its identifier, gross mass and cargo details. The bay view uses BAPLIE bay,
row and tier addresses with a separate hold/deck region and hatch covers. Playback
supports stepping, play/pause, speed and scrubbing. Highlighting follows the next
lift and boxes blocking the next load target. A labelled quay buffer keeps
temporarily restowed boxes visible while they are off the ship.

One move is one crane lift. A yard rehandle moves a blocker to expose a buried
box (the block relocation problem; Tanaka & Voss, 2019,
doi:10.1016/j.ejor.2019.06.014, also documented in `stowage.yard.dig_out`). The
D11 value shown is `MoveCount.weighted_nonproductive`, the engine's weighted sum
of rehandles, restows and hatch lifts now and projected at later calls. Productive
loads and discharges are counted separately. Simulator gate moves are displayed
separately from crane lifts; operational planning replay rejects gate events.
Projected later-port hatch lifts and restows have separate counters.
Future projections are recalculated at each frame;
they are not executed lifts.

The Voyage checker workspace preserves space for legible charts at 1080p:

- Stability: GZ (the restoring lever as the ship heels), applied criteria and
  margins, GM (initial restoring stiffness), trim/list and mean/aft/forward draught.
  Separate departure, recorded mid-voyage and arrival sections identify each
  condition and its failed parts. Code GM and the vessel loading GM minimum are separate criteria.
  The checker selects **IS Code B 2.3** for these feeders over 100 m, instead of
  A 2.2.1–3, per D21. A 2.2.4 initial GM and A 2.3 weather remain active. The UI
  reads that selection from checker metrics rather than selecting rules itself.
- Hull strength: shear force and bending moment along the hull, with separate
  signed lower/upper permissible envelopes from the vessel's supplied stations.
- Dangerous goods: aboard DG cargo, pair diagnostics and general segregation
  table requirements, special provisions, and declared Document of Compliance
  (DoC) areas. A table requirement alone is not the full pair verdict.
- Violations: the complete checker list, stable catalogue ids, severity,
  plain-language messages and involved containers/locations.

Sources for these concepts and criteria: IMO MSC.267(85), IS Code B 2.3/A 2.3;
IACS UR S11A; IMDG 7.2 and the checker module docstrings. Browser JavaScript only
renders server data. GZ uses the checker's curve builder and the same table/direct
choice as its verdict; SF/BM samples come directly from checker metrics. Plotting
failures appear as unavailable data and never replace or approve a verdict.
The GZ adapter uses the recorded liquid method, soundings, small-tank exemption
and checked table selection. D38 CSC ISO fallback and D42 background-box evidence
notes appear as notes, without adding hard failures.

Panels describe the **reached departure and declared arrival endpoints** under
D50, plus any explicitly reported mid-voyage condition under D57. Each has its
own tank state and checker evidence. Missing condition evidence is explicitly
unavailable. The checker scope and evaluated frame stay visible during playback.
Plots reconstruct only the conditions reported by the checker; they do not
certify other intermediate voyage or crane states.
The server calls `stowage.checker.full.check` when materializing each selected
replay and caches it; `report_matches_inputs` must bind the report to the complete
displayed instance and plan. An unbound fresh report is retried once; if still
unbound, the UI shows **Unbound report** and suppresses report evidence and
approval. Comparisons also verify binding before using report counts or text.
Full pass/fail and part availability are authoritative.
Live plans also pass through the `run_planner` pipeline's own checker before
fresh display panels are built. Checker version is
displayed from `CHECKER_VERSION`.

Both schema 1.8 and 1.9 inputs are supported. A legacy content hash remains valid
only against the validated original payload that normalizes to the exact selected
world; it never grants current safety approval. Port context shows crane height,
berthing side and yard truck-lane side when available. Direct-delivery exports
appear beside the yard as ship-side trucks. Killed slots and heavy boxes on deck
are displayed only when the bound checker reports those soft preference metrics.
The middle ship's current family is `feeder_800`; `feeder_1000` is a legacy alias.

The always-visible model panel explains Muse Glimmer 30B + LoRA (MLX) and the
feasibility mask. Without a model run it says **no Glimmer run imported yet**.
Import a run using the leader's `w2-stowlm-run-contract.md` layout:

```sh
uv run python -m app --run-folder /path/to/glimmer-run --entry instances/INSTANCE_ID/result.json --instance /path/to/bound-instance.json
```

The manifest requires model/revision, adapter/lineage, runtime versions, all
decoding settings and epoch declarations. The result needs a direct checker
instance binding or an original-file checksum with its actual file witness.
Stored verdicts are rechecked. Valid adapter runs and base-model runs receive
different labels; self-claimed or fixture sources stay **Imported (unverified
source)**. Manifest/trace/source-file changes invalidate cached replay. The trace
viewer shows chosen actions, legal/total counts and the top five before masking,
striking through masked choices. Epoch/root hashes are declarations: independent
authentication needs a producer-specified root inventory and hash definition.
The fixture under `app/tests/fixtures/model_run/` is for tests only.

An adapter receives the clean StowLM label only with `adapter_clean=true`,
production-gold data and no lineage stubs. Pipeline-test or tainted adapters say
**StowLM (diagnostic adapter, not evidence)**. A readable lineage sidecar is
checked against its manifest and weight hashes and can tighten these declarations;
its taint also reaches the canonical shipping gate.
The trace caption is **selected sample, replayed**. Each action's `decisions`
shows top-five preferences before masking at the box, bay, row and tier tokens,
with masked choices struck through. The row also shows the chosen action and
recorded legal out of physically possible action counts. Missing exact replay
counts say **not recorded**. Older top-five lists remain visible with an explicit
legacy token-position label, never presented as new box/slot evidence.

The override rate counts only the first token of box/bay/row/tier fields, across
**all samples**; opcode `format_override` is displayed separately. The all-token
statistic is also separate. Original draft metrics survive solver repair.
Historical ambiguous opcode-based rates are not relabelled as planning rates,
and selected-sample decisions never establish an all-samples rate. Missing new
fields say **not recorded**. Run fallback/repair rates appear beside overrides,
using the producer's `summary.json`: its `fallback_rate` counts repaired
instances divided by all run instances, including failed repairs.

Terminal `dead_end` rows show their depth, **rules active at the dead state**,
unattributed count and recorded screen diagnostics. These are active-rule and
probe records, not a minimal cause or a count of rejected actions. The manifest's
run-level dead-ended-sample and unattributed counts are displayed separately;
one sample can cite several rules. **mask time limit** is its own draft outcome,
including when a solver subsequently repairs it. Stored outcomes never approve
the displayed plan; the app still checks it afresh. Optional summary/outcome
files join the fingerprints checked on every replay.

The `stowlm-ui-trace` entry in `.claude/launch.json` opens the recorded
`decisions-smoke` run on port 8771 with one-thread numeric libraries and discovery
disabled. This is a diagnostic base-Glimmer run, not a shippable plan. Its actual
box/slot token preferences and run fallback rate are shown without inference.
`trace-smoke-b4` predates the per-field evidence; its missing values remain
explicitly labelled. Actual dead-end replay is covered separately by
`mask-diag-smoke`, whose recorded rule attribution is absent (unattributed),
while its screen diagnostics retain their own rule IDs.

The page, JavaScript and stylesheet responses send
`Cache-Control: no-cache, no-store`, including `/index.html`, `/` and asset query
URLs. API responses retain `no-store`. Reloads re-fetch the current UI instead
of reusing stored presentation assets.

The historical pipeline-test folder is a real failed-run diagnostic:

```sh
uv run python -m app --run-folder experiments/sft-pilot/pipeline-test/c1-loop --entry instances/v3.random_yard.tiny.110280000/result.json --no-discovery --port 8765
```

Its config's original-file SHA binds the world. The UI shows its actual error and
selected draft trace, with no invented plan, playback, counts or checker approval.
This historical run predates the aggregate override statistic, so its rate is
explicitly unavailable. Inspecting these artifacts does not run Glimmer or Spark.

Declare the illustrative case selection before seeing solver outcomes:

```sh
uv run python -m app --storyboard-root /path/to/current-epoch-instances --storyboard-split val --storyboard-rung small --storyboard-rule first-dg-direct-delivery --no-discovery
```

Campaign roots use the shared frozen-inventory validation and default pinned
`experiments/splits/v2/manifest.json`; `--storyboard-split-manifest` selects a
different explicit inventory. The rule chooses the first instance in id order
containing at least one DG box and one direct-delivery box, without solving.
No qualifying member is an error, never a reason to substitute a case. Simple
non-campaign roots can declare splits in instance family metadata; that evidence
is labelled **instance family declaration**, rather than frozen inventory.
The UI says **1 instance, illustrative**. The leader selects the regenerated root.

Crane H lines, quay orientation and truck lanes come from instance geometry;
crossing highlights explicitly say when move-engine crossing evidence is absent.
A **crane-height shift** tag requires a reported reason. The in-port stability
panel says **not yet checked during cargo operations (D64 pending)** until the
checker supplies `port.stability.cargo_operations`; reported per-lift metrics
and ballast changeover k then appear. These displays never approve a lift.
Stored Muse Spark explanations are shown only after validation against the
current bound checker report. Missing, rejected or stale artifacts fall back to
the deterministic template. The page never calls Spark. Both replay and
comparison show **Explained by Muse Spark; the checker's verdict is authoritative**
when using accepted stored text, together with its model, date and validator
verdict. Explanations are collapsed by default; the explainer's verdict and text
are displayed verbatim.

Prepare sidecars explicitly, after the normal Spark credit gate is approved:

```sh
uv run python -m app.prefetch_explanations --instance /path/to/instance.json --plan /path/to/primary.json --plan /path/to/baseline.json
```

This command fresh-checks and binds each saved plan before calling PlanExplainer.
It writes `primary.json.spark-explanation.json` beside its plan, containing the
validated explanation, UTC date, raw response, validator verdict and complete
instance/plan/report digests. Rejected responses are recorded but never shown as
Spark text. Every replay rechecks the sidecar with the current validator using
an in-memory response client, so changed facts, validator versions or artifacts
fall back without any network access. Live unsaved plans use the template until
saved and prefetched. No prefetch or Spark request was run for this change.

A stub, explicit not-validated marker, missing required part or any check that
did not run makes the overall status **NOT VALIDATED**, even if a raw report
claims PASS. The raw checker report stays available for diagnostics. Completed
clean rejections still say FAIL; only the canonical shipping gate can approve.

`GET /api/demo` returns the original instance and plan, complete precomputed world
states, engine counts, highlighting, the full checker report and chart data.
`GET /api/health`
returns server health. Frame zero is the initial world; frame N follows N lifts.
The browser renders data and never implements move legality. The metadata and
separate replay transport drives task 6.5 planner/baseline comparisons.
Use `?view=checker&panel=stability` (or `strength`, `dg`, `violations`) to open a
specific panel; `?frame=N` selects a playback frame.

`GET /api/catalog` lists allowlisted instance/plan ids, `GET /api/replay?plan_id=...`
loads and caches a fresh checked replay, and
`GET /api/comparison?left=...&right=...` returns server-generated deltas/evidence.
`POST /api/solve` accepts JSON `{instance_id, planner, compare, seed, time_limit_s}`;
planner ids are `solver.scalable`, `solver.exact` and `baseline.twostep`. It returns a job id for
`GET /api/jobs/<id>`. Imported-only StowLM cannot be started as a live planner.
HTTP requests cannot supply arbitrary filesystem paths. `?view=compare` opens
the comparison workspace; selecting a saved pair or finishing a job populates it.

Verification:

```sh
uv run pytest -q app/tests
uv run pytest -q
```

The second command follows the repository's `testpaths = ["tests"]`; run both to
include the application tests. Screenshot output belongs in `experiments/ui-shots/`.
To capture operations and checker panels at 1920×1080 with an installed Chrome/Chromium:

```sh
uv run python -m app.capture
uv run python -m app.capture --instance /path/to/instance.json --plan /path/to/solver.json --compare-plan /path/to/baseline.json
```

See `experiments/ui-shots/README.md` for this sandbox's capture limitation.

For an authorized browser check where listening sockets are unavailable, the
optional Playwright driver sends browser requests through the production HTTP
handler in process, including real live planners and the full checker:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --with playwright python -m app.browser_verify --instance experiments/ui-shots/w2/fresh-tiny.json --output experiments/ui-shots/w2/scalable
```

It uses an installed Chrome, records screenshots and actual plan/checker evidence,
and fails when either plan is unshippable. Repeat with `--primary solver.exact`
or `--primary baseline.twostep` and a separate output directory to cover the switch.
No browser execution, screenshot or passing plan is implied by this command's presence.

## Stage page (demo UI v2, `/stage`)

The judges' view animates one plan lift by lift. Contract: `experiments/mvp/ui/CONTRACT.md` (stage-v1); recording
guide: `experiments/mvp/ui/RECORDING.md`.

- `app/stage.py` re-shapes a validated replay into the bundle (geometry, pods, boxes, steps with running counts, rule
  ticks from the bound checker report, x-ray from `trace.jsonl`, KPI from `app/kpi_config.json`). No verdicts here.
  The KPI is this slice's measured difference from a simple two-step planner, modelled from one field expert's description, not a
  per-call figure; `app/prize_config.json` holds the labelled size-of-the-prize line (assumptions, not a StowLM result).
- `app/stage_physics.py` computes physics keyframes with the checker's own stability/strength parts on plan prefixes;
  `experiments/mvp/ui/precompute_timeline.py` caches them (`experiments/mvp/ui/cache/`, bound to both input SHA-256s).
- `app/stage_service.py` serves `/api/stage`, `/api/stage/physics`, `/api/stage/explain` with one bounded background
  worker (live physics; the live two-step baseline is off unless `--live-baseline`, shown as "measured live").
- `app/stage_extras.py` (29 Oct data pass): no-argument default = frozen `random.tiny.8000004.0.2` (exact solver,
  proved optimal at 5 weighted lifts = lower bound) vs the frozen `test-twostep` run (7); companion imports for the
  same instance (base Glimmer + mask run with its trace, Muse Spark zero-shot plan; `--no-companions` to skip);
  `comparison.spark` (Spark plan replayed + freshly checked; file counts never used); x-ray `legal_slots` recomputed
  on the feasibility engine (must equal the trace's `n_legal`), `overridden`/`decisions` from the trace,
  `xray.alternate` (base-mask run for the same instance) and `xray.highlights` (recorded Spark-without-mask
  refusals, verbatim); cached Spark explanations from `experiments/mvp/explain-cache/` only when bound to the plan.
- `app/stage_stowlm.py` (29 Oct): real StowLM run folders. `--stowlm-run <folder>` registers every shipped instance;
  `--entry` also takes the eval-harness record `instances/<spec_key>.json`. Dead ends / time limits get a "not shown as a
  plan" card (no replay, decoded prefix dropped); backtracks from trace events or `result.json` metrics; D99
  `preview_label` unless `FINAL_RULE` holds (`experiments/mvp/runs/stowlm-showcase*` on a showcase call). Recipe:
  `experiments/mvp/ui/RECORDING.md` "FINAL SWAP".
- `app/stage_fast.py` is the lite replay for plans over 600 lifts (full ship): one `apply_plan` for legality, running
  counts per lift, later-port projections at 40 keyframes, the full checker once. `--lite-replay on|off|auto`.
- Placeholder plans (`make_demo_plan`, id `*.ui-placeholder`) are shown as `ui-placeholder`, never approved.
- Live solves (`solver.scalable`, `baseline.twostep`) run with C2 off per D79 (`app/flow.py::_make_planner`).
- `app/check_cache.py` (30 Oct): exact on-disk memo of `full.check` keyed by instance content + plan content + the
  checker source epoch (sha256 of `src/stowage`, `params/`, pinned hull offsets, Python/numpy/scipy/pydantic versions,
  `STOWAGE_*` env). A hit replaces only the `full.check` call; binding, validation and the shipping gate run as before.
  Files in `experiments/mvp/ui/cache/checks/`; reports with part errors or budget/timeout diagnostics are never stored.
  `--no-check-cache` (or `STOWLM_NO_CHECK_CACHE=1`) disables it. App tests run with it disabled (autouse fixture).
