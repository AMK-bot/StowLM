# Stage demo: recording guide

## Demo v6 (plan v6 task 2.2): the film and the flow

**Hero by config.** `app/v6_hero.json` names `hero_source` and `hero_slot`; every ship fact, plan, date and sentence comes from there. `instructions` maps a slot to its rule fixture. A slot with no fixture yet plays the plan before and the reading chapter is a clean gap. `readings` maps a slot to a recorded take of its sentence; `live_reading` names the off-lattice take "Confirm and run live" uses. The old `experiments/mvp/ui/v6-hero` stand-in is read by nothing now.

**Catalogue pin (decision, 2 Nov, open for veto).** `lattice/store/catalogue.json` is rewritten whenever the campaign saves an entry, so a pin on the whole file would break on every landing and teach people to re-pin blindly. `catalogue_sha256` therefore pins the hero's own rows: the launch writes those rows alone into a scoped store view and runs `lattice.regate(instance_sha256, store_dir=view, expected_catalogue_sha256=pinned)`. The digest moves only if an entry this hero shows changes, and the regate costs two plans rather than every plan on the instance. A mismatch shows the plan as not shown and the launch line prints both digests. To re-pin, after a hero switch or a deliberate rerun of an entry:

    uv run --offline --no-sync python -m app.v6 --repin

**Launch regate.** It runs in a child process, off the request thread, because the gate patches cache paths process-wide. Allow about 10 minutes (two plans, about 5 each, more under load) before the `warm` line. Until it ends the chapter waits; a plan shows only if the regate passed that exact plan, and "rechecked now" comes from that run. A change to the binding runtime closure withholds every stored plan (`rejected` in `cache/v6-hero/regate-*.json`): that's the lattice's own rule working, not a demo bug. With no fresh fleet telemetry the worker skips admission, which would otherwise wait forever.

**Regate memo (leader, 2 Nov).** The launch regate's verdict is kept in `cache/regate/<key>.json`, keyed on instance, each hero plan, rule set, scoped lattice entries, catalogue pin, checker and physics epochs, binding fingerprint, engine manifest, reader identity and the lattice gate sources. A hit skips the full check but reruns lookup's identity checks read-only (`stored_identity_holds`); the label then gives the real check's time, "(exact memo)". A cached failure stays withheld. `--no-check-cache` forces a real regate. Measured on the 87-box hero, load about 35: cold 620 s, warm 39 s. Until the gate passes, every route withholds the hero plans (HTTP 403 with the reason). The package papers need a passing verification of their exact files: a bound recording (`python -m app.v6_package --record`) or a live run here.

**Radio and reading (D268 as amended, D270).** The hero sentence and both its clips come from files named in `app/v6_hero.json` `"radio"`; a swap is an edit there, never code. `readings` is `radio/readings.json` (`stowlm.radio-readings/1`: `rows` of `{sentence, condition: clean|radio, transcript, expected_rule, wav_path, wav_sha256, binds, verdict, evidence}`, file order = wording order, template last). `typed` lists the typed readings, first file that holds a sentence wins (a row whose `case` is the hero slot beats another): `reader_final/hero-candidates.json` if it lands at the top level, else `reader_final/readings/hero-candidates.json` (`kept`/`misread` rows of `{sentence, rule, binds, verdict, reading, evidence}`), then `reader_final/readings/hero-readings.json` (`readings` rows with `correct`) for the template. The hero is the first wording read exactly both typed and from its radio transcript, else `fallback_sentence` (`app.v6.select_hero_wording`, pure, `app/tests/test_v6_radio.py`). The reading chapter takes it only when its typed reading is the adopted adapter's, masked, and reads to this hero's own instruction rule; otherwise the hero keeps its stored instruction and the radio take plays as a separate clip under `stand_ins.radio_wording`. Without `readings.json` the old separate clip (`clip`) plays under `stand_ins.radio`, and shots 4 and 5 drop their counts in final (chips in draft). Captions follow script-v6.md (D272). Shot 4: "Rule fixed before reading · 1 of 8 wordings (7 written + the template) · on this simulated line, 6 of 8 transcripts garbled "bay 9""; 8, 6 and the bay are counted from the radio transcripts themselves (`bay` then the number as digits or a word), from `manifest.json` until `readings.json` exists; "12 dB" comes from the take's `.audio.json`. Shot 5: "Read on the laptop: N of 8 typed and M of 8 radio transcripts exact; every other one refused", shown only if every miss in the files really is a refusal; "about 4 s a sentence" is the timing run's warm median. Cross-checked against the leader's `radio/hero-selection.json`: same sentence, 5, 3, 6. No staged fix: the planner sees the words first, and may edit them in the live flow. Never a rate.

**Radio audio: your own voice.** Set `"radio": {"audio": "path/from/repo/root.wav", "audio_label": "…"}`. Format: WAV, 16-bit PCM, mono (a stereo file plays, peaks read its first channel), 16–48 kHz (the stock takes are 24 kHz), at most 6 s (`RADIO_MAX_S`; shot 4 is 10 s and also shows the other wordings). Put it under `experiments/mvp/pipeline/radio/audio/` beside the stock takes. Peaks are recomputed from whichever file is configured, on every request; `/api/v6/asset/radio.wav` serves it. Three things don't follow on their own: the transcript on screen is still the stock take's ASR (transcribe your file and read it with `radio/read_transcripts.py` into a new readings file, then point `readings` at it, or the words won't match the voice); `audio_label` replaces the stock-voice label in the chapter, but shot 4's caption still says "Stock text-to-speech voice", so edit it in `app/static/stage-shots.js`; and the "{{radio_snr_db}} dB noise" segment drops, because only a stock take carries a channel record (`<take>.audio.json`). To keep the simulated line, pass your clean 24 kHz mono WAV through `experiments.mvp.voice.audio.radio(source, destination, seed=...)` and save the dict it returns as `<name>.audio.json` beside the output; the segment then comes back from that record. `null` restores the chosen wording's own take (hash-checked against `wav_sha256`). Google Translate's public TTS has no redistribution licence (logic5 H3): the user decides before the WAVs or the film go public.

**Adopted reader (D266, 2 Nov).** The adopted StowLM Reader is **swa-last-5** (`experiments/mvp/reader/v3/candidates/swa-last-5`, adapter `eff712e1…`), not adapter-v2: D266 reversed D264 on the mask study's safety measure (27 of 84 out-of-scope sentences made rules under v2, 4 under swa-last-5) and is the newer record, so it's what this app follows (`app/v6.py` reads `final-adoption-d266.json` first). `"reader_recordings": null` in `app/v6_hero.json` is the one line that brings its reading in: set it to `experiments/mvp/pipeline/reader_final/readings` once `read_all.py` has written swa-last-5's reading of the hero sentence on this ship; anything from another adapter is refused. `xray_trace` names `real-block-trace.json`, the same adapter's recorded block (class 5.2 wanted at 89 %, masked to 5.1); the film refuses a trace from any other adapter, and an end-token swap at the stop step never counts as a block. The clip `en-reader-031e722e98863ad8` reads to a rule on this ship that the lattice doesn't hold; adding that rule to the lattice would give one real clip, reading and plan.

**Live replan.** `--brief-live-replan` offers "Confirm and run live" on `live_reading`. Measured about 30 minutes on the shared machine, first checked plan at about 17 (`fixtures/live-replan-run.json`); the 720 s profile deadline found none, so `LIVE_ENGINE_S` is 1800 s. The worker log, `events.jsonl` and `outcome.json` of each job sit under `experiments/mvp/ui/hero-live/`. It is not a camera take.


**Launch.** `python -m app --v6 --port 8875` (env as in `V6-BUILD.md`). It serves at once on a threaded server, warms in the background, and prints one line when the first page has nothing left to wait for: `StowLM v6 warm in N s: gate shown, before plan physics ready, after plan physics ready`. A warm memo takes well under a minute. "MISSING" means run `precompute_timeline.py` for that plan: physics caches depend on the checker epoch, and a full precompute takes about 6 minutes at 2 workers.

- **Live flow:** `/stage?v6=1` (`&chapter=replan`). Shift+arrows move chapters.
- **Film:** `/stage?video=1` needs no `?v6=1`: the director boots the flow. `&shot=N`, `&t=S`, `&draft=1`, `&hold=1`, `&from=S` as before. `&clip=x` is now shots 1 and 8. `&variant=v5` is a rehearsal of the old script and never starts the flow; it wants an ordinary launch, not `--v6`.
- **Length:** 12 shots, 171 s, from `experiments/mvp/video/script-v6.md` draft 4 (D267: "What we measured" cut; x-ray and the confirm tap are shot 6, after the reader). Shot 6 plays the x-ray in its first 9 s, then the reading chapter's confirm beat (a `hole`; `fit_s` fits the x-ray to the 9 s). `check_shots.py` and `record_shots.py` read the list from the page.
- **Numbers:** hero numbers come from the gate at boot. Everything else is `experiments/mvp/video/fill-v6.json` through `sync_fill.py`, then reload. Unfilled: chip in draft, gone in final. A card never says pending in final. `[[IF name: …]]` needs `"name": true`.
- **Fallbacks:** a chapter that's gone at boot plays its shot's "If cut" line, and its seconds go where the script says (`window.__director.gaps`, and `fell_back` on `window.__director.shots`). Before a take, check that `gaps` is empty.
- **Gauges:** `nice -n 10 uv run --offline --no-sync python experiments/mvp/ui/dev/v6_gauges.py` walks every crane step of both hero plans (the stage timeline is only keyframes) and writes `fixtures/hero-gauges.json`. The launch regate walks every step too, and the caption comes from that walk: "The gauges stay green" needs every step of the plan played, so a keyframe-only walk can say "Every gauge ends green" at most. Run `precompute_timeline.py --instance <hero> --plan experiments/mvp/ui/cache/v6-hero/plans/<sha16>.plan.json --workers 2` for each plan after a hero change.
- **Replan shot:** the plan before stands loaded and every moved box flies to its slot in the plan under the rule. Panels, gauges and the lift counter meanwhile read that plan's loading sequence lift by lift, so the counter is the sequence, not the flight order. Under `--brief-live-replan`, the live flow offers "Run it live": POST `/api/v6/replan`, a runner job in its own process, real seconds, incumbents as they arrive. Only then does a label say Live.
- **A trap:** don't import `experiments.mvp.pipeline.runner` inside the server. It sets `STOWAGE_VESSEL_CACHE_DIR`, the checker epoch reads that, and every plan then reports "checker sources changed". `app/v6.py` loads `envelope.py` by file for this reason; the live worker has its own process.

## Film freeze

In this order, once the sealed run (3.1) has ended and the GPU is free. **GPU** = renders the 3D stage page; nothing else may touch the GPU while it runs. Durations are from recorded timings where one exists (source in brackets), otherwise marked *estimate*. Env as in `V6-BUILD.md`.

| # | Step | GPU | Duration |
|---|---|---|---|
| 1 | `uv run --offline --no-sync python experiments/mvp/pipeline/freeze/verify.py --require-frozen experiments/mvp/pipeline/freeze/manifest-pipeline.json`: the tree still matches the pipeline manifest v4 (`acf2f275…`, D313; v3 `a11b4202…` is archived as `manifest-pipeline-v3-superseded.json` and is cited only as the provenance of the 3.1 numbers) | no | a few minutes, *estimate* (hashes the tree, not the weights) |
| 2 | Confirm `app/v6_hero.json` `package_dir` is `experiments/mvp/pipeline/package/fixtures/hero-run/package` and the package is the hero's: its `receipt.json` `plan_sha256` is the hero's after-plan and its `rules[].reading` is the hero rule (`v6_package.require_hero_plan` raises otherwise) | no | seconds |
| 3 | `uv run --offline --no-sync python -m app.v6_package --record`: one clean verify and one tamper of a temp copy, in child processes, bound to `package.json` and every file hash it lists. CPU only, nothing renders. Refuses if the package isn't the hero's, the clean run didn't pass, the tamper didn't fail, or a file changed during the run. Writes `experiments/mvp/ui/fixtures/verify-recorded.json` | no | about 1 min: verify 23–32 s, tamper 10–15 s (the 2 Nov recordings, load about 30) |
| 4 | Only if the hero plans changed: `python -m app.v6 --repin`, then `dev/v6_gauges.py` and `precompute_timeline.py --workers 2` per plan | no | about 6 min per full precompute at 2 workers (§ Demo v6 above) |
| 5 | Launch `python -m app --v6 --manifest experiments/mvp/pipeline/freeze/manifest-pipeline.json --port 8875`; wait for the `warm` line | no | regate memo hit about 39 s to 1 min; cold regate 620 s, 447–488 s under fleet load (STATUS.md third pass) |
| 6 | `/api/v6/package` shows the papers (not withheld) and the receipt line reads reader, adapter, pipeline `acf2f275efa7` (v4, D313; the app reads it from the package's `receipt.json`), "Confirmed by: user-authorised batch regeneration, no planner present (demo fixture)"; `window.__director.gaps` empty | **yes** (opens the stage page) | 2 min, *estimate* |
| 7 | `dev/check_shots.py` stills in final mode (banned-word scan) | **yes** | about 5 min, *estimate* |
| 8 | `dev/record_shots.py <base> --final` into `video-draft/v6/` (at least shots 5 and 10, which changed: rounded reader bars, the rebuilt receipt; all 12 for one consistent take) | **yes** | 171 s of director time (timing.json `director_wall_total_s` 171.24) plus page boot per shot: about 6–10 min, *estimate* |
| 9 | Check `video-draft/v6/timing.json`: `errors_last_pass` empty, each shot's `director_errors` empty. Stop the server (your own PID) | no | 1 min |
| 10 | `experiments/mvp/pipeline/freeze/freeze.sh --scope film` (last). Runs `suites.sh` (both suites, one core each), then `build.py --scope film --pipeline-manifest manifest-pipeline.json`, then `verify.py --require-frozen manifest-film.json`. Pins all of `app/` and `experiments/mvp/ui/` (generated data included: recordings, `verify-recorded.json`, takes) on top of the pipeline layer, weights inherited from it. Refuses if `manifest-film.json` already exists | no | suites about 45 min on a loaded machine (D278); build and verify a few minutes more, *estimate* |

Any edit to `app/` or `experiments/mvp/ui/` after step 10 needs a new film manifest.

### Film-judge recut (2 Nov, script-v6 draft 5 at 167 s)

- **Shot 1, cold open.** A shot can now cut between chapters: `v6.holes` (each `{from, to, chapter, start, rate}`; chapter time is `start + (t - from) * rate`, rate 0 holds a frame) beside the single `hole`, and `v6.at` starts the main span later. Shot 1 is the radio line at its own pace, the reading's rule card, the replan at rate 3 from its hold, its end frame, the hook's drag at 1.3×, then the dare on black. The huge words are `slam` overlays; "THE PLAN CHANGES." and "FULL CHECKER: PASSED" need `hero_shown` (this launch's gate passed the plan). "sped up 3×" is `{{cold_replan_speed}}`, the hole's own rate put by the director, so the label can't drift from the playback. Every chapter a shot cuts to must play, or the shot takes its fallback (`StageStory.shotChapters`).
- **Shot 3** opens on the hook chapter's three slams (hook tau 0.8 to 3.9 at 1.2×), then mess in fitted to the rest.
- **Shot 6** has no trace on screen: a `pair` card (the locked-test bars, 47 % and 92 %, then "182 of 200 → 2" with both caveats) covers the reading chapter's early beats until 12.6 s, then the confirm plays at its own pace. The bars' percentages and 60 of 128 are served by `/api/v6/figures` from the adoption record (`reader_untrained_exact`, `reader_*_shown_pct`). The script's "selected" labels a trace, so it isn't shown.
- **Shot 9** starts the copilot chapter at 4.6 s; in film mode the chapter hides the small section's panels (`v6c-herofirst`) and shows the hero's stored refusal alone, with its "No.", and no recorded-answer label until the real-tools take.
- **Shot 12** is a `pilot` card (exposure huge, pool large, fine print at 22 px, the map's place names at 40 %), a "Projection" chip until the merged end card covers the frame, and the end card with the link line second only to the name (absent in final until the release links are filled).
- **Fixed on the way:** the director asked `/api/v6/replan` without `?recorded=1`, which since the confirmation gate answers "before only", so "rechecked now", the walked-frames line and the heel limit silently dropped from shot 7 (and would have from shot 1). It asks for the recorded answer now, as the chapter does.
- **Lower-third probe:** `dev/lower_third_probe.py <base> --shots …` plays each shot and checks every 250 ms that no map label is drawn under "Synthetic ships and cargo, simulated port".

## Brief flow (plan v5 task 2.2): the live demo

`PYTHONPATH=.:src uv run python -m app --port 8871`, then open `/stage?brief=1` (button BRIEF, key V). In the panel: pick a prepared
sentence, or type, or use the microphone; confirm the words; read the rule; press **Plan this**. The stage then switches to the
after plan and replays it.

| Launch | Reading | Speech | Cost |
|---|---|---|---|
| default | recorded, labelled "recorded reading" | off; a sentence with no recording says so | none, no network |
| `SPARK_CREDIT_READY=1 ... --brief-live` | live Muse Spark, `voice-v5-1.3-parser` purpose ($2.50 lifetime) | live Muse Voice Transcribe, `voice-v5-1.3-asr` ($0.50 lifetime) | about $0.002 per spoken sentence (Spark ~$0.0015, measured over 160 calls; ASR ~$0.00005 per second) |
| `--reader glimmer --reader-adapter experiments/mvp/reader/v3/candidates/swa-last-5` | the adopted fine-tuned Glimmer (D266) on this laptop, MLX, loads on the first sentence | needs `--brief-live` as well | none for reading. Not yet run through the app: rehearse it first |
| add `--brief-live-replan` | any of the above | any | a real replan: about 5 min, one CPU worker, one process at a time |

- Start 5 to 10 minutes before you present on a cold machine: every stored plan goes through a fresh replay and the full checker on launch,
  then its physics keyframes are computed. With the check memo and physics caches warm this takes under a minute. Chips say "checking"
  until a plan has passed; a sentence picked sooner waits on the same gate.
- After any `src/` or `params/` change, restart and let the gates run again; rebuild the registry if a stored plan changed:
  `PYTHONPATH=.:src uv run python -m app.brief --build-registry`.
- The registry (`app/brief_prepared.json`) matches on instance digest and exact constraint. Different words for the same rule reuse the
  plan, and the panel says which sentence it was stored for. Task 3.2's hero plan enters the same way as the others: add its case to `BINDING_CASES`
  in `app/brief.py` (its plan and audit files in the binding format) and rebuild. Its slot reads as pending until then.
- `window.StageBrief` drives the same cards for the video: see `CONTRACT.md`.

All commands from the repo root; `uv` + Python 3.12 only. Long jobs: `nice` and single-threaded BLAS
(`OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1`).

## 1. Pick what to show

The server replays the plan with the move engine and re-runs the **full checker** at start-up (content binding and
fresh verdict, never the saved one). Start-up time is that replay: ~15 s tiny, ~2 min for a 450-lift medium plan,
longer for the full ship. After that, `/api/stage` answers from memory (bundle build ≈ 10 ms, measured on 450 lifts).

**Full-checker report memo (30 Oct, `app/check_cache.py`).** The fresh full check is memoised on disk, exactly:
same instance content + same plan content + same checker source epoch → the same `CheckReport` (no approximation;
binding, validation and the shipping gate still run on every load). Files: `experiments/mvp/ui/cache/checks/<key>.json`
(app cache only; written atomically, never hand-edit; delete the folder to start cold). Key = sha256(instance sha256 |
plan sha256 | epoch); epoch = sha256 over every `src/stowage/**/*.{py,toml,json}`, every file under `params/`, the pinned
hull offsets (`data/raw/kcs`, `data/raw/dtc`), the Python / numpy / scipy / pydantic versions and the `STOWAGE_*` /
`OPENBLAS_NUM_THREADS` / `OMP_NUM_THREADS` / `VECLIB_MAXIMUM_THREADS` env vars (~0.03 s per process). Any edit there
(e.g. tonight's TOML cache in `src/stowage`) changes the epoch, so old entries simply stop matching. A file is used only
if its stored key parts, a JSON round trip and `report_matches_inputs` all agree; anything else is a miss and the checker
reruns. Never stored: reports with a part error or time-budget / timeout / internal-error diagnostics.

**Editing `src/stowage`, `params/` or the hull offsets, or changing those env vars, under a running server withdraws
every approval (30 Oct, review P1-1).** Before any stored or memoised verdict is used the server compares disk and
environment with what it loaded (1.5 ms, stat-only; a changed stat re-hashes only that file, so `touch` is harmless). On
a difference every plan shows NOT VALIDATED, "checker sources changed since this process started: restart the app", with
no baseline, Spark, lower bound or physics: even a fresh check would run the old loaded code. **Restart the server after
any such change.** Restoring the original bytes also clears it. `python -m app` defaults the three thread limits to 1
before numpy loads (matching `precompute_timeline.py`); launching with a different value is honoured and is a cache miss.

The server log prints `check cache: hit …` per memoised plan. **Kill switch:** `--no-check-cache` (or
`STOWLM_NO_CHECK_CACHE=1`) always runs the checker fresh. For the final take, a fresh-check launch costs ~25 s more.

| Source | Command |
|---|---|
| **Video (showcase, 29 Oct): use this for `/stage?video=1`** | `PYTHONPATH=.:src uv run python -m app --video-showcase --no-discovery --port 8851` — registers the small Laem Chabang showcase calls, each freshly re-checked on load: `plan-1` D97 gold solver (two-step warm start + LNS, `showcase-v2-gold`) on `random.small.29000201.0.7` (feeder_1700-small, 3 DG + 1 reefer; active; StowLM stand-in, labelled in-frame) vs `plan-2` frozen two-step v4.4, `plan-3`/`plan-4` the same pair on `random.small.29000022.0.7` (feeder_500-small, picker), plus `plan-5` the recorded solver replan under the instruction, and the tiny default call (`plan-6` exact, `plan-7` two-step, `plan-8` base Glimmer + mask, `plan-9` Spark) for shots 5 and 7. Equivalent explicit form for the main pair: `--run-folder experiments/mvp/runs/showcase-v2-gold --entry instances/random.small.29000201.0.7/result.json --compare-run-folder experiments/mvp/runs/showcase-v2-twostep --compare-entry instances/random.small.29000201.0.7/result.json`. Start-up (30 Oct, after the start-up fixes below) ≈ 25 s to `/api/health` with an empty check cache, 12 s warm; race complete 38 s / 24 s; peak RSS 1.3 GB (was 8.8 min and 16.7 GB: the rival loads parsed ~81k run-local cache JSON files); physics comes from `experiments/mvp/ui/cache/` (section 2; the run-local `timeline-cache/` folders have no epoch binding and are now misses); the two-step rivals are replayed + checked in the background (~20 s each) before the race/KPI fill in |
| **Default (no args): the winning case** | `uv run python -m app --port 8841` — frozen `random.tiny.8000004.0.2`: exact solver (proved optimal, 5 wasted lifts = lower bound 5) vs frozen two-step (7) vs Muse Spark zero-shot (7, replayed + checked here); companions registered: `plan-3` base Glimmer + mask run (x-ray), `plan-4` Spark plan |
| Any MVP run folder | `uv run python -m app --port 8841 --run-folder experiments/mvp/runs/<run> --entry instances/<instance_id>/result.json` |
| …with the two-step baseline in the race | add `--compare-run-folder experiments/mvp/runs/test-twostep --compare-entry instances/<instance_id>/result.json` |
| Explicit files | `uv run python -m app --port 8841 --instance I.json --plan P.json` |

Run folders follow `experiments/fleet/orders/w2-stowlm-run-contract.md`. A `result.json` without its own binding is
located through its sibling `record.json` `input_sha256` (exact corpus file bytes). Model runs (`base-mask/`, StowLM)
also need `manifest.json` + `trace.jsonl`; the trace feeds the x-ray. Use `--no-discovery` for a faster start.

Data pass (29 Oct): the old smoke-tiny default (11 lifts vs 7, a loss) is no longer the default, so its frozen
two-step run is not needed. Every tiny test win of exact over two-step is in **projected later-port hatch-cover
lifts** (now-lifts tie at 7), so the race headline and KPI use wasted lifts now + later (D11). Since D85 hatch-cover
lifts are priced like quay-crane moves, so those wins carry a USD/THB value. No tiny test instance has DG or
reefer boxes. Any launch with a plan also attaches the base-mask run and Spark plan for the same instance when they
exist (`--no-companions` to skip).

The race needs a two-step baseline for the **same instance**: pass it with `--compare-*` (preferred: it is a frozen,
checked run) or, only with `--live-baseline`, let the server run `TwoStepPlanner` live (tiny/small only, D79 C2
off, ~30 s on one core; shown as "measured live … in this session"). The lower bound is `stowage.bounds.lower_bound`, the same call as the showcase
`lower-bounds.json`. The KPI appears only when both plans pass the full checker; it can be negative (we report it).

**Full ship (hero):** plans over 600 lifts use the lite replay automatically (`--lite-replay auto`): one engine pass,
running counts, the full checker once (21 s measured), bundle 0.8 s. The current hero input is the scalable solver's
timeout prefix (`experiments/mvp/runs/showcase-solver/hero-full-20261029/longest-prefix.result.json`, 1 486 lifts,
804/815 loads, checker FAIL) — record it only as a labelled partial replay:
`uv run python -m app --port 8841 --no-discovery --instance experiments/mvp/corpus/showcase/feeder_1700-full.json --plan experiments/mvp/runs/showcase-solver/hero-full-20261029/longest-prefix.result.json`.

## 2. Precompute the physics timeline (do this before recording)

```
PYTHONPATH=.:src nice uv run python experiments/mvp/ui/precompute_timeline.py \
    --run-folder experiments/mvp/runs/<run> --entry instances/<id>/result.json --workers 2
```

(or `--instance I.json --plan P.json`). Writes `experiments/mvp/ui/cache/<key>.physics.json`, checkpointed after every
keyframe; rerun to resume. The server uses it only if the instance and plan SHA-256, the format version and the
physics epoch all match and every frame validates. **Physics epoch** = sha256(format version | checker epoch (section 1) |
`app/stage_physics.py`, `app/stability.py`, `app/checker.py`), so any change there, in `src/stowage`, `params/`, the hull
offsets or the numerical environment makes every cache a miss (and old frames are recomputed, never resumed). Keyframes: every lift
up to 80 lifts, else ~40 evenly spaced + heavy-box frames (cap 80). Measured: tiny 12 frames 17.6 s (1 worker);
medium placeholder 68 frames 50 s (2 workers); full-ship prefix 76 frames 117 s (2 workers). Without a cache, `/api/stage/physics` computes it live in one
background thread and the gauges fill in as frames arrive — fine for rehearsal, not for a take.
`--max-frames N` is a timing probe.

**Where the server looks (same identity check everywhere):** first `experiments/mvp/ui/cache/`, then, read-only, every
`--timeline-cache DIR` (repeatable) and the plan's own run folder `timeline-cache/` (from the registry's run folder,
never a request path). A file is used only when it passes the identity check above and is complete; a run-local folder
written without our epoch (all of them, as of 30 Oct) is a miss, then live or precomputed into `experiments/mvp/ui/cache/`,
the only place frames are written. A miss with live compute off reads "no precomputed physics cache … at the current
checker and physics epoch"; with live compute on the gauges show "physics pending" and fill in. The bundle's `physics_progress.source` says
`cache (read-only <dir>)` when a fallback was used. Showcase caches (29 Oct, 1 worker, measured wall incl. input load):
solver feeder_1700 `88a665107de41c37` 46 frames 54.7 s · solver feeder_500 `ec935ca00ae2f0f3` 29 frames 25.7 s ·
two-step feeder_1700 `bd06ffda55b44c49` 42 frames 130.4 s · two-step feeder_500 `68428616e725e4d7` 27 frames 89.9 s;
all four are frame-for-frame identical to the owners' run-local `timeline-cache/` files, zero frame errors. These
numbers are history: see section 2a for the current set.

## 2a. Task 10.2: precompute again after 9.2 landing and the final freeze

**Any `src/` or `params/` change invalidates every timeline below, so this must run again after 9.2 lands and after the
final freeze** (`src/` is frozen today, but the epoch will legitimately move before 10.2). It is safe to rerun: a current
file answers "cache already complete" in about 1 s. Run one at a time, after the last source change, with no server running:

```sh
R=experiments/mvp/runs; S=random.small.29000201.0.7; F=random.small.29000022.0.7; T=random.tiny.8000004.0.2
pre() { PYTHONPATH=.:src nice uv run python experiments/mvp/ui/precompute_timeline.py --run-folder "$1" --entry "$2" --workers 1; }
pre $R/showcase-v2-gold       instances/$S/result.json   # feeder_1700 gold (fc77d0ec4d7930e8)
pre $R/showcase-v2-twostep    instances/$S/result.json   # feeder_1700 two-step (bd06ffda55b44c49)
pre $R/instruction-replan-dg  instances/$S/result.json   # DG instruction replan (73679d5f9812d4bf)
pre $R/test-exact             instances/$T/result.json   # tiny default exact (0bec83382c8f6eb2)
pre $R/base-mask/tiny         instances/$T/result.json   # tiny base Glimmer + mask (e7d9f63abe322149)
pre $R/test-twostep           instances/$T/result.json   # tiny two-step
pre $R/showcase-v2-gold       instances/$F/result.json   # feeder_500 gold (6510137df9bdf052)
pre $R/showcase-v2-twostep    instances/$F/result.json   # feeder_500 two-step (68428616e725e4d7)
# tiny Spark: no run manifest, so name the instance
PYTHONPATH=.:src nice uv run python experiments/mvp/ui/precompute_timeline.py --workers 1 \
    --instance experiments/mvp/corpus/test/tiny/feeder_800/random_yard/seed-008000004.json \
    --plan $R/spark-zeroshot/instances/$T/result.json
# After 9.2: the same line for each shipped StowLM showcase instance, e.g.
pre $R/stowlm-showcase        instances/$S/result.json
```

Measured 30 Oct, 1 worker: 42 frames 36–42 s, 27 frames 22 s, 16 frames 15 s, 8 frames 9 s (about 3 min for all nine).
The gold feeder_1700 and feeder_500 frames were identical to the run-local files. Any other plan you put in a shot needs
its own line. Keep the three thread limits unset or at 1 here and in the launch, or the epochs differ
(`write_fixture.py` needs them set by hand). Check: `uv run python -m app --video-showcase --no-discovery`, then every plan in
`/api/catalog` must report `physics_status: ready` with `physics_progress.source: cache` from `/api/stage?plan_id=…`.

## 3. Check before you record

```
curl -s http://127.0.0.1:8841/api/stage | python3 -c "import json,sys; b=json.load(sys.stdin); \
  print(b['meta']['planner'], b['meta']['checker_passed'], b['meta']['departure_approved'], b['physics_status'], \
        b['comparison']['reasons'])"
```

Record only when: `checker_passed` and `departure_approved` are true, `physics_status` is `ready`,
`meta.planner` is not `ui-placeholder`, and the baseline/lower-bound reasons are null (or you accept the gap).
Placeholder plans (`experiments/mvp/ui/placeholder-plans/`, from `make_placeholder.py`) are for render-scale testing
only; the bundle marks them `ui-placeholder`, never approved, no KPI.

## 4. Fixtures for frontend work (no server)

`PYTHONPATH=.:src uv run python experiments/mvp/ui/write_fixture.py --instance I --plan P [--live-baseline]
[--compare-run-folder R --compare-entry E] --out experiments/mvp/ui/fixtures/<name>.json` writes the exact bundle the
server would return (physics from the cache only).

<!-- Backend sections go above this line. The frontend owns only "## UI controls and recording" (source: dev/RECORDING-frontend.md). -->

## UI controls and recording

Page: `http://127.0.0.1:<port>/stage` (real server: `uv run python -m app --port 8841`).
Dev server with fixtures (no backend needed): `uv run python experiments/mvp/ui/dev/serve_dev.py --port 8817 --fixture stage-tiny,mock-full`.

### URL parameters (deterministic shots)

| Param | Effect |
|---|---|
| `plan_id=<id>` | plan from `/api/catalog` (default: active plan) |
| `step=N` / `step=end` | open after N lifts (no animation); `end` = plan complete, rule checklist revealed |
| `phase=0.0–0.99` | with `step=N`: freeze lift N+1 mid-animation (0.55 ≈ box hanging under the spreader) |
| `autoplay=0/1` | default: plays 0.9 s after load unless `step` is given |
| `speed=0.5/1/2/4/8` | start speed. 1× = `clamp(48 s / lifts, 0.22, 1.5)` s per lift (tiny 1.5 s, full ship 0.22 s) |
| `xray=1` | open the feasibility-mask x-ray |
| `xhl=1` | with `xray=1`: open the recorded "Spark without mask" highlight in the x-ray card |
| `view=bays` | start in the 2D bay-plan view (every bay seen from aft) |
| `present=1` | hide dev chrome (plan picker, view toggle, perf HUD) |
| `dev=1` | perf HUD + `[stage perf]` console lines (fps, JS ms per frame) |

### Real x-ray and the Spark highlight (data pass)

- **Mask x-ray on real decoding:** open `http://127.0.0.1:8841/stage?plan_id=plan-3&step=4&xray=1` (default launch;
  `plan-3` = "Base Muse Glimmer 30B + feasibility mask (x-ray run)", also in the plan picker). Per decision: legal
  count of all actions, the model's top-5 first tokens before masking (struck = masked), per-field chips (box / bay /
  row / tier, ✕ = the mask forbade the model's own top-1 → "OVERRIDDEN BY MASK"), emitted action, green legal slots in 3D
  and the bay plan (recomputed from the action space, equal to the trace's `n_legal`). On the exact plan the x-ray
  card offers the button "x-ray: base Glimmer + mask run on this instance".
- **Spark without the mask:** in the x-ray card press "Spark without mask ▸" (URL `xhl=1`): the recorded no-appendix
  Spark plan for `random.tiny.8000014.0.8`, the refused line `P002 0010102`, the move engine's verbatim refusal
  ("slot 0010102 is not a cell of this vessel") and the scoped claim "StowLM cannot write a placement the mask forbids · the checker still decides".
- **Spark as third racer:** the move-count table and bars show "Spark zero-shot" whenever a recorded Spark run exists
  for the instance (replayed and checked in the background, ~40 s after start-up).
- Screens: `screens/stage-default.png`, `stage-race-spark.png`, `stage-xray-real.png`,
  `stage-xray-spark-highlight.png`, `stage-hero-yard.png` (full-ship yard heat map, dev fixture).

### Keys

Space play/pause · → one lift (animated) · ← back one lift · Shift+→/← ±10 lifts · Home/End start/end · 1–5 speed
0.5×…8× · X x-ray · B 3D ↔ bay plan. Hover a box for its card; click it to ask Muse Spark (cached text; the
"Ask Spark live" button is enabled only when the bundle says `explanation.live_available`).

### Suggested 3-minute capture (1920×1080, `present=1`)

Launch with `--video-showcase`. Never open on the full-ship hero run: it is NOT APPROVED and never appears as a result
(hero policy in "Video mode").

1. Open on the checker-approved showcase call (the active plan, or StowLM's once 9.2 lands) at `step=0&autoplay=0`, let
   the header read (tiles for this slice, CHECKER PASS, Laem Chabang แหลมฉบัง).
2. Space at 1×: watch the first hatch covers lift off and boxes drop into the hold; gauges move with each lift.
3. Press 4 or 5 (4×/8×) to fill the ship. The move-count panel stays final vs final; only its "lift k of n" tick moves.
4. Pause on a load, press X: green = legal slots, red hatch = masked; card shows top-5 first-token preferences
   *before masking* with masked candidates struck through.
5. Click a box → Spark explanation. End → rule checklist ticks family by family.

### What the panels may claim (logic review, 30 Oct)

- **Header tiles are "this slice":** 12–22 boxes on the small maps, never a call. Names are "Moves vs two-step",
  "Crane-hours vs two-step", "USD vs two-step", "THB vs two-step". Values carry a word ("3 fewer", "$75 more", "0 · tie"),
  so "$−X saved" can't be drawn. Baht is "≈": the rate is an assumption, stated in the tooltip. The USD and THB tooltips
  carry the size of the prize from `app/prize_config.json` (pinned to `ship_time.py` by `test_story_panels.py`).
- **Move count is final vs final at every lift.** The plan's column and bar never show a prefix. Progress is the one
  "lift k of n" tick under the bars, which isn't a score.
- **Lower bound = proven floor, not necessarily reachable.** "= lower bound (optimal)" needs `meta.proved_optimal`
  (exact solver, `OPTIMAL`); any other plan on the bound reads "reaches the floor". The gap is information, not a target.
- **Two-step = "Two-step baseline (our model of industry practice)".** Cached explanation text still reads "two-step
  (industry practice)": it is part of the P1-8 re-render binding, so it stays until those entries are rebuilt.
- **Spark panel:** the heading is MUSE SPARK only for `spark-cached` text. A template reads "Plan explanation ·
  deterministic template (no Spark text bound to this plan)".
- **Securing card:** `StageRules.securingState` gives OVER LIMIT: <check>, VIOLATION: <rule>, NOT CHECKED: <reason> or ALL
  WITHIN LIMIT, from `securing.state` / `reason` (`build_securing`).

### Screenshot recipe

`experiments/mvp/ui/dev/shot.sh "<url>" out.png 4000` (headless Chrome, 1920×1080, console → `out.png.log`).
Frame-time check: `uv run --with playwright python experiments/mvp/ui/dev/perf.py "<url>"`.
Interaction smoke: `uv run --with playwright python experiments/mvp/ui/dev/smoke_stage.py "<url>"`.

Honesty rules the UI enforces: every number comes from the bundle (null → "—", reason on hover); placeholder plans
carry a PLACEHOLDER watermark and an amber "PLACEHOLDER · NOT APPROVED" badge; MOCK fixtures show "MOCK DATA — NOT
FOR JUDGES" and must never be recorded.

## Instruction box (task 9.3 prep)

A planner types one sentence; Muse Spark's parse becomes a tighten-only constraint (`src/llm/instructions`), the
scalable solver replans (D79 C2 off, no repair), a fresh replay + **full checker** judge the new plan, and an
independent check confirms the instruction holds. Backend `app/instruct.py`; UI `stage-instruct.js/.css`.

- Open: **INSTRUCT** button in the ship panel head, or key **I**. Hidden with `?present=1` unless `&instruct=1`.
- Chips: recorded sentences only (dashed chip = no recorded answer, refused offline on purpose).
- **Offline by default:** answers come from recorded Spark responses (`experiments/mvp/instruct/responses/*-v2*.json`,
  `demo/*.json`) and `experiments/mvp/ui/instruct-cache.json`, matched by normalised sentence. Any other sentence is
  refused ("no recorded response (offline mode)"); the parser has no rule fallback. Live parsing needs
  `uv run python -m app --instruct-live` **and** `SPARK_CREDIT_READY=1` + `MODEL_API_KEY` (spend cap applies); new
  live answers are appended to `instruct-cache.json` so a take can be replayed offline.
- Live replans: **tiny only** (anything else is refused with the reason), time limit 120 s, on the stage service's single
  worker (queued behind physics/baseline work). Measured 29 Oct: 40–55 s per job on one core.
- Result card: parsed constraint (labelled "recorded Spark answer" or "live Spark · $cost"), step ticks, original vs
  replanned lifts (+ later-port lifts), checker verdict, "instruction honoured". **Show replanned plan ▸** switches the
  stage to the registered plan (`?plan_id=plan-N&instruct=1`), labelled "Replanned: <sentence>", with a pill in the
  panel head and **◂ Back to the original plan**.
- Rehearsal / screenshots: `?instruct_job=<job id>` re-shows a finished job (read-only; a URL never starts a job).
- Instance choice: on `random.tiny.8000004.0.2` (6 cells, 4 TEU) "nothing in bay 01" is **refused as infeasible**
  (only 3 cells left) — good for the refusal beat. For the replan beat use the tier-limit chip there, or start on
  `--run-folder experiments/mvp/runs/test-exact --entry instances/sim.tiny.8000003/result.json`, where
  "Crane 1 is down, nothing goes into bay 01 this call." replans (6 → 6 lifts now, +2 later, checker PASS, honoured).
- Screens: `screens/stage-instruct.png` (close bay 01 replanned), `stage-instruct-replanned.png` (stage switched to it),
  `stage-instruct-refused.png` (loosening refused by the parser), `stage-instruct-infeasible.png` (capacity refusal).

## StowLM's instruction replan (D170): what the video shows

`uv run python -m app --video-showcase` registers `experiments/mvp/runs/stowlm-instruction-binding` as the plan-picker entry
**StowLM replan under the instruction**. `/stage?instruct_recorded=1` opens it; shot 7 selects it by run name.

- **Allowed wording, in full:** "StowLM replanned under a plain-language instruction parsed by Muse Spark, and the checker passed it (one
  pre-registered call)". Never a rate (n = 1), never "efficient", never "better than the solver": no solver replan of this sentence was run.
  Always state the cost.
- **Sentence:** "Keep the class 8 corrosives at least 1 bay away from bay 1." Spark's parse: `dg_keep_away`, class 8, min 1 bay, from bay 1
  (recorded in `PREREG.json`, one sentence tried, $0.00599375).
- **Numbers the app recomputes on every load:** 85 vs 67 weighted moves (+18), 13 boxes in a different final slot, 2 placements of the plan
  before break the instruction. `app/tests/test_stage_instruction.py::test_binding_numbers_are_computed_from_the_plans` replays both plans.
  The card (`STORY.instructionModel`) shows the script's words only if the bundle's recomputed values equal them.
- **Gate:** fresh full checker, `shippable`, honoured from a fresh `constrained.violations`. Any failure: "not shown as a plan", a director
  error, and the recorder exits 1.
- **Label rule, in code (`app/stage_instruction.py` `stowlm_replan`):** this folder only, verified clean adapter, the hero call, no repair. A
  StowLM card anywhere else isn't presented. `instruction-replan-dg` stays "solver replan under the instruction — not StowLM".
- **No two-step column on this plan:** the rival ran without the instruction, so the race and KPI show none (D170 quotes its 37 only in
  words). The card's source line is `instruction-card.json`.
- **Don't show `runs/stowlm-instruction-dg`** (D166): same plan as without its sentence, Spark didn't parse it. It isn't registered.
- **Screen:** `video/backup-take/shot-07-instruction.png`.

## Recorded DG solver replan (D144): fallback only

`uv run python -m app --video-showcase` also registers `experiments/mvp/runs/instruction-replan-dg` as the plan-picker entry
**Solver replan under the instruction**. Open `/stage?instruct_recorded=1` (it finds the plan id itself; add `&step=9999`
for the finished ship). The page shows the D99 pill and card label "solver replan under the instruction — not StowLM" in
every mode, including `video=1`.

- Sentence: "Keep class 8 at least 1 bay from bay 3." The constraint is authored by hand: no Spark call, and the card
  says so. Don't narrate it as a Spark parse.
- Card: box QZXU0000040, gold plan → 0030382 ✕ breaks instruction, solver replan → 0010382 ✓ honoured, cost ±0 wasted
  lifts (37 = 37), full checker PASS. The violations are recomputed on load from the recorded constraint.
- Say "solver", never "StowLM", over this plan. This cost-free result is one case: don't generalise it.
- Superseded for the video by the section above. Still shown only if the StowLM card fails its gate.
- The class 3 sentence the order named is unsatisfiable on this two-bay slice (see STATUS.md). `app/instruct.py` now
  refuses such an instruction as `infeasible`, but only on the live tiny path; the showcase replan stays refused as
  `too_large`.
- Rebuild: `make_dg_replan.py` (solve ~1 min, keeps a finished solve; `--force` replans), then `precompute_timeline.py`
  (84 s, 42 frames). A changed plan needs a fresh timeline cache.
- Screen: `screens/stage-instruct-dg.png`.

## Video v5 (31 Oct, plan task 2.2): what the video plays now

`/stage?video=1` plays the v5 script, draft 2 (`experiments/mvp/video/script.md`, 11 segments, 174 s, 2:54). Shot list: `app/static/stage-shots.js`
(`window.STAGE_VARIANT = 'v5'`). Story logic: `app/static/stage-story.js` (pure functions; tests run it under node). Director:
`stage-director.js` + `.css`. Wording is bound by `docs/headline.md`; it overrides `SWAP-LIST.md` wherever they differ.
Worse, Par and Better are retired from the page; their config and the old director are in `experiments/mvp/video/archive/`.

| URL | Effect |
|---|---|
| `/stage?video=1` | all 9 shots in order |
| `&shot=N` | shot N, including `3a`, `3b`, `3c`; `&shot=5a`, `5b`, `5c`: one beat of shot 5 |
| `&t=S` | freeze at S seconds within the shot |
| `&draft=1` | DRAFT mark, shot chip, `PENDING:` chips, "hero run pending (3.2)" |
| `&hold=1` | wait for Space (screen recorder) |
| `&clip=x` | the X clip: 15 s of shots 1, 3a, 3b, 5a in a 1080×1920 portrait layout (set the window or viewport to 1080×1920) |

**Launch:** `python -m app --video-showcase --no-discovery --port 8890`. Fetch `/api/brief/state` once: it starts the brief gates, which
take a few minutes and register the recorded brief plans. The director waits for them (up to 4 min) before it loads, so a take
starts only with settled evidence. Then load each shot once to warm its bundle and physics.

**Numbers:** `experiments/mvp/video/fill-v5.json`, keyed by the placeholder text (`{{NUMBER: reader accuracy %}}` is the key
`"reader accuracy %"`; `REPO_URL` is the one other key), then `uv run python experiments/mvp/ui/sync_fill.py`, which writes
`app/static/stage-fillv5.js`. It refuses keys not in `script.md`, non-numbers and leftovers, and leaves the old file. Reload the page.
**Nothing from STORY-NEEDS's provisional column goes in.** The file is empty today, so:
- draft: an unfilled caption segment shows a `PENDING:` chip;
- final: that ` · ` segment drops;
- a card reads "pending: 1.1", "pending: 0.4", "pending: test v3" or "pending: 1.5" in both modes, never a blank bar or a zero.
Bars are drawn from values only: width = value ÷ scale (0–100 %, or the larger mean on the lifts card).

**Hero (task 3.2):** shots 1, 3a, 3b, 4 and 9 look for a catalogue plan with `run` = `hero-v5` (`hero_plan` in `stage-shots.js`; one line to
change). Until it exists:
- shots 1, 3a, 3b, 9 show the prepared brief case `dg_keep_away-04` (`standin_case`), its recorded search plan, once its fresh gate passes;
- shot 4 shows `stowlm-instruction-binding` (`standin_plan`), the one plan with a bound Spark explanation and a short report;
- every frame carries "recorded stand-in · not the hero run", in final mode too; the hero's sentence never sits on a stand-in, the
  stand-in's own recorded sentence does.
When 3.2 lands: register the run under `--video-showcase` as `hero-v5`, give the brief state a `hero` case with a shown gate (before and after plans, for shot 3b), fill the `hero …` keys, and bind its Spark explanation.

| Shot | Dur | What plays | Waits on |
|---|---:|---|---|
| 1 Hook | 10 | Black, waveform, words, the reading, confirmed; ship full-bleed, bay lit, boxes lift and land, three gauges | 3.2 hero, 1.3 clip |
| 2 Why | 21 | Map pin with 9,325 calls; simulated yard; ship filling | nothing |
| 3a Say it, confirm it | 24 | `StageBrief.show` listening → transcript → reading (14 s on screen, 9.5 before the confirm); step strip reads each step's model from the evidence; confirm chip | 3.2, 1.3; multi-line note and per-line confirm from the brief flow |
| 3b The ship moves | 16 | Search, plan plays, then what moved (before and after, from the two plans' own steps, count checked against the gate), gauges, rule ticks | 3.2 |
| 3c Is it really here? | 11 | Holds the slot: card with the values or "pending: 4.3 live take". The take itself is a live capture with the Wi-Fi off, cut in at 4.3 | 4.3, 5.2 |
| 4 Spark says why | 9 | Spark's statements beside the checker report; each lights the report lines it was rendered from | 3.2 bound explanation |
| 5a, 5b, 5c When it says no | 6, 4, 7 | `StageBrief.show`: refusal, question back, impossible with the alternative | 1.4 rerecording, 1.1 numbers |
| 6 Evidence | 18 | One model without and with our fine-tune, one bar at a time; Spark as a reference line | 1.1, 0.4 |
| 7 Honest beat | 13 | Mask x-ray of a recorded step for three seconds (first recorded override, never staged); three bars | test v3 |
| 8 Worth | 25 | Lifts card with the limits held the whole shot; Laem Chabang per-call card beside it | test v3, 1.5 |
| 9 End | 10 | Ship, three gauges, tag line, sentence, scope; `generated_voice: true` adds the voice label | 0.10 repo URL, 3.2 |

**Check on the hero:** the script says gauges hold green. They show the checker's own frames at every lift, so a plan that goes outside a
limit mid-plan shows red. Shots 1, 3b and 9 must be looked at on the hero run before that sentence is kept.

**Stills:** `experiments/mvp/ui/screens/v5/shot-NN.png` (final) and `shot-NN-draft.png` (`experiments/mvp/ui/dev/shot.sh`, `&shot=N&t=S`).

**The X clip:** `/stage?video=1&clip=x` in a 1080×1920 window, or `record_shots.py <base> --clip x --whole --final` when Playwright is
available. The page lays out the same shots in portrait, spans in `clip_x`; nothing is cropped after the fact.

## Video mode (3-minute script, stage director): SUPERSEDED

**Superseded on 31 Oct by "Video v5" above; kept for history.** Everything below until "Final take" describes the 10-shot Worse, Par and
Better variants. Variants, `fill.json` wiring, the three-state StowLM label, the ladder, instruction and results cards no longer play.

`/stage?video=1` plays the 10-shot script of the chosen variant (`experiments/mvp/video/script.<variant>.md`, D158/D159:
174 s = 2:54 target export, hard cap 3:00) on a fixed timeline: `app/static/stage-director.js` + `stage-director.css`,
story logic `app/static/stage-story.js` (pure functions; the tests run this file under node), shot list
`app/static/stage-shots.js`.

**The variant is one line:** `window.STAGE_VARIANT = 'worse';` near the top of `app/static/stage-shots.js` (`worse`, `par`,
`better`). It picks durations, titles, captions, shot 8 and 9 lines and the KPI-tile rule. `?variant=par` overrides it for
rehearsal only and shows an orange REHEARSAL mark in every mode, so an override can't reach a final take by accident.
`record_shots.py --variant par` does the same. Base `shots` are authored for Worse; a variant entry overrides by id, and a
changed `duration_s` scales the shot's inner times.

**Filled numbers:** write `experiments/mvp/video/fill.json` (VARIANTS.md steps 1 and 2), then run
`uv run python experiments/mvp/ui/sync_fill.py`, which writes `app/static/stage-fill.js`. No restart needed; reload the
page. It refuses unknown keys, leftover placeholders and non-numbers in count or moves columns, and leaves the old file in
place. Absent fill.json = empty file: every unfilled caption segment and card row is PENDING in draft, dropped in final.
`SHOWCASE_LINE` isn't read from fill.json: shot 8 builds it from the run folders (below). The director runs in
the stage page itself (the server's CSP keeps `frame-ancestors 'none'`, so no iframes): it prefetches every shot's
stage bundle (waiting until physics and the background baseline/Spark replays have settled), swaps plans in place
under a 0.28 s dip to black between shots (`window.__stage.load`), and drives playback with `window.__stage.seek(liftPos)`
from its own clock. What is on screen at time t is a pure function of t; wall-clock drift only changes frame timing.

| URL | Effect |
|---|---|
| `/stage?video=1` | all shots in order; starts 0.6 s after every bundle is loaded |
| `&hold=1` | wait on a black screen until Space / click (use this with a screen recorder) |
| `&shot=N` | only shot N (1–10), with a dip in and out |
| `&t=S` | freeze at S seconds (global, or inside shot N with `&shot=N`) — stills |
| `&draft=1` | DRAFT watermark, shot chip (id, title, variant, script times, stand-in flag, plan id), `PENDING: NAME` chips |
| `&variant=par` | rehearsal only: override the variant, REHEARSAL mark in frame |

Durations, seconds (ids 1–10 sum to 174 in every variant):

| Variant | 1 Hook | 2 Problem | 3 Live | 4 Gauges | 5 X-ray | 6 Rules | 7 Ladder, then instruction | 8 Results | 9 Impact | 10 End |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Worse | 14 | 16 | 20 | 11 | 20 | 12 | 32 | 21 | 20 | 8 |
| Par | 19 | 16 | 22 | 12 | 18 | 14 | 34 | 16 | 15 | 8 |
| Better | 20 | 16 | 22 | 12 | 18 | 14 | 29 | 17 | 19 | 7 |

- **KPI tiles:** hidden on shots 1–7 in Worse and Par, shown throughout in Better.
- Shot 1: the race panel is not focused or glowing, so no prefix is compared with a finished plan (P1-1). The race is focused only
  in shot 8, with the plan complete.
- Shot 3: the D128 caption shows only when true of the plan shown (StowLM plan and checker approved). A stand-in gets its own
  caption saying it's a solver plan.
- Shot 6: rule ticks, verdict stamp, then the **securing** card from the bundle's `securing` block (load ÷ allowable per
  check, worst stack, limit line at 1.0). The "3.5× over limit" line is a caption only (solver report
  `experiments/mvp/solve47/physics/REPORT.md`; `experiments/mvp/pitch-notes/securing.md`), never rendered as a plan.
- Shot 7 (Worse, D170): the **two_models** card (ladder on the same 15 small test maps, then each model's job), the Spark panel for
  2.8 s when a Spark explanation is bound to the plan (`when: spark_explained`; template: no panel, no line), then the **instruction**
  card beside the replanning ship: StowLM's replan on hero 29000201 and a refused sentence. See "StowLM's instruction replan (D170)". Par and
  Better keep the labelled tiny solver replan (`demo{1,3}.json`).
- Shot 8: race panel plus the three-call strip, then the two-block results card. Shot 9: size of the prize in Worse and Par
  (`$578 / $110 / $2,248`, tested against `ship_time.py`); a saving only in Better, labelled "scaled from 12–22-box maps".
- **Hero policy:** `experiments/mvp/runs/showcase-hero-animation` (full ship) is NOT APPROVED and never appears as a
  result; if it is ever used it must be framed as "the checker rejecting an unfinished solver attempt". No shot uses
  it now (the test checks).

Without `draft=1` (final mode) a caption segment (split on ` · `) whose `{{PLACEHOLDER}}` has no measured value is
dropped, never filled. A card row with a missing value is dropped whole, so a pass count never appears without its moves.
There are no "Awaiting" cards any more; the in-frame label below is the only stand-in text.

### Three-state StowLM label and the fixed hero order (D158, P1-2)

`hero_order` in `stage-shots.js` is `29000201`, `29000022`, `29000102`, whatever StowLM does. Every shot with
`stowlm_call: true` resolves its label from data (`GET /api/stage/showcase`, `app/stage_showcase.py`, read at request time,
plus the catalogue and the fresh checker in the bundle):

| State of the hero | Shot shows | Label |
|---|---|---|
| No run | Stand-in | "solver stand-in — StowLM run pending" (shot 5: its own pending label) |
| Run failed, a later call shipped | Replay shots (3, 4, 5, 8, 9) move to the next shipped call; shot 3 opens with the dead-end card; shots 1, 2, 6 stay on the hero's labelled solver plan | "StowLM dead-ended here: 29000201 · next in the fixed order: 29000022" |
| Run failed, nothing shipped | Stand-in + dead-end card | "StowLM dead-ended here · solver plan shown" (time limit and rejection have their own wording) |
| Run ok, checker approved | StowLM plan | none (a non-final run keeps its PREVIEW label) |

A StowLM plan the fresh checker doesn't approve counts as failed. A run on disk that isn't in the catalogue, or an unreachable
status endpoint, shows a "not loaded" or "state unavailable" label and adds a director error, so `record_shots.py` exits 1:
restart the server. Shot 8's strip lists all three calls from the same source; `29000102` shows two-step "—" (dead end) and
"no approved gold plan".

### Editing the shot list (`app/static/stage-shots.js`, hand-editable JSON)

- `duration_s` per shot must sum to 174 and match each variant script's shot table (`app/tests/test_stage_director.py`
  checks all three).
- `plan: {run, instance, method}` is matched against `/api/catalog` (`run` = run folder relative to
  `experiments/mvp/runs`, new catalogue field; `instance` = instance id; `method` = catalogue `planner`, e.g.
  `stowlm`, `solver.exact`, `stowlm.base`). `stowlm_call: true` uses the three-state rule above; `replay: true` marks a
  plan-replay shot. `ui-placeholder` plans are never selected.
- `play: [[t, pos], …]` piecewise-linear lift position (fractional = mid-lift; `"end"`; `"xray"`/`"xray-3"` = the
  x-ray pick's plan step ± n). A flat integer segment parks the crane.
- `camera: [{t, zoom, focus}]`, focus `center` | `active` (the lift's ship slot, eased between lifts) | `xray` (the
  chosen slot of the x-ray pick) | `slot:<id>`. Zoom is a CSS transform on the ship canvas, rendered at 2× so it stays
  crisp; the oblique projection is fixed (no orbit/tilt).
- `focus: [{from, to, panel, mode, rect?, zoom?}]`, panel `ship|yard|spark|physics|race|rules|play|kpis`, mode `glow`
  (outline), `dim` (others dimmed), `big` (panel flies to `rect` [x, y, w, h] in 1920×1080 px; canvases are
  re-rendered at the new size; HTML panels can take a CSS `zoom`).
- `xray: [{from, to, hl}]`, `xray_pick: {trace_step}` (that trace row if its model top-1 on box/bay/row was masked,
  else the first such row; none → the "its top choices were all legal" sentence), `rules_reveal: {from, per_family_s}`,
  `select: {at, box}`, `still_t`.
- `overlays` types: `title`, `tag`, `twostep`, `facts`, `override` (from the bundle's x-ray trace), `verdict` (bundle meta
  + rules), `securing` (bundle `securing`), `two_models` (`ladder` + measured Spark pass rates + bundle `xray.highlights`),
  `instruction` (`instruction_beat`; Worse also gates it on the bundle's `instruction` block), `deadend` (`when: failed`), `showcase` (`/api/stage/showcase`), `spark_nomask` (unused),
  `results` (`results_card`), `impact` (Laem Chabang pin, inline SVG), `roadmap`, `end`.
- `captions: [{from, to, text}]`: `**bold**`, `{{NAME}}` (filled only from `measured`, each with `source` and a
  verbatim `match` string the test finds in that file, or from `stage-fill.js`), `[[bundle.path]]` (value from the shot's
  bundle), `when` / `otherwise` / `otherwise_when` (shown only if true of the plan shown). A caption
  that states a measured number literally carries `source` + `match` (list); the test checks every number in it is in
  a match line and every ` · ` segment is verbatim in the script. Numbers like ×, %, n/15 without a source fail.

### Showcase call now in the shot list (29 Oct; gold swap 29 Oct evening)

Launch with `--video-showcase` (section 1). Shots 1, 2, 3, 4, 6, 8, 9, 10 show the checker-approved D97 **gold** plan on
feeder_1700-small (`{run: "showcase-v2-gold", instance: "random.small.29000201.0.7", method: "solver.gold_warmstart"}`;
evidence `experiments/mvp/solve47/slack/showcase/GOLD-REPORT.md`); shots 1, 3, 4, 8, 9 keep StowLM on the same instance
as the primary selector, so the gold plan is a labelled stand-in until the StowLM run folder is registered. Shot 5
keeps the real base-mask tiny trace, shot 7 the tiny call with the cached Spark explanation. The gold result is the
retained, checked two-step warm start (LNS found no improvement), so it is the same plan quality as the rival.
Measured on this launch (fresh replay + full checker, PASS / departure approved for both gold plans):

| feeder_1700-small | lifts now | loads | yard rehandles | restows | hatch lifts | later restows / hatch | weighted |
|---|---:|---:|---:|---:|---:|---:|---:|
| gold solver (stand-in) | 41 | 22 | 15 | 0 | 4 | 10 / 8 | 37 |
| two-step v4.4 | 41 | 22 | 15 | 0 | 4 | 10 / 8 | 37 |
| lower bound | 37 | 22 | 15 | 0 | 0 | 0 / 0 | 15 |

feeder_500-small (picker, `plan-3` vs `plan-4`): gold 26 now (13 loads / 9 rehandles / 0 restows / 4 hatch) + later
10 / 6 = **29**, two-step the same **29**, bound 9. KPI (D85, `app/kpi_config.json` mid) on both: **a tie, on this
slice** — the tiles render "0 · tie", "0.00 · tie", "$0 · tie", "≈ ฿0 · tie" (not "—"). The race headline reads **"Tie with two-step (37 = 37 wasted lifts) · gap to bound 22"** (`stage.js` states a zero difference as
a tie instead of "−0 (−0 %)"). StowLM needs fewer than 37 weighted lifts on this call for the hook to show a win.

**In-frame stand-in label (D99).** Every shot that shows a stand-in where StowLM's plan belongs carries a dashed amber
pill in the header, right of the StowLM brand / planner subtitle, in final mode too (not only the draft chip):
"SOLVER STAND-IN — STOWLM RUN PENDING" on shots 1, 2, 3, 4, 6, 8, 9; "STAND-IN: BASE GLIMMER + MASK — STOWLM TRACE
PENDING" on shot 5. Shot 7 (tiny exact plan shown as itself) and shot 10 (full-frame end card, no plan visible)
carry none. Set per shot with `standin_tag` in `stage-shots.js`; the director (`standinTag()`) hides it by itself once
the shot resolves to a real StowLM plan. It sits above zoomed panels and cards, under the black dip between shots.
`app/tests/test_stage_director.py::test_label_is_set_in_every_mode_and_preview_label_survives` checks it with the
director's own function and that it is set outside the DRAFT-only code. The earlier scalable-solver stand-in (`showcase-v2-solver`,
63 weighted, 36 restows) is no longer registered.

### Real StowLM run folders (pilot 2 now; final showcase runs from task 9.2) — 29 Oct 23:50 EDT

Any StowLM run folder in the w2 run-contract layout loads with no code change (`app/stage_stowlm.py`):

```sh
# one plan (result.json, or the eval-harness per-instance record instances/<spec_key>.json)
uv run python -m app --run-folder experiments/mvp/sft/pilot2/val15/c --entry instances/random.small.40100007.0.5/result.json --no-discovery
uv run python -m app --run-folder experiments/mvp/sft/pilot2/val15/c --entry instances/0c2907dbb8345fe7.json --no-discovery
# every shipped (status ok) instance of a run in the plan picker; the first is active
uv run python -m app --stowlm-run experiments/mvp/sft/pilot2/val15/c --no-discovery
# a dead end as the active plan, the shipped ones in the picker
uv run python -m app --run-folder experiments/mvp/sft/pilot2/val15/c --entry instances/random.small.40100031.0.5/result.json --stowlm-run experiments/mvp/sft/pilot2/val15/c --no-discovery
```

- **Shipped plan:** replayed box by box by the move engine and re-checked by the full checker on load (stored
  `report.json`/`outcome.json` verdicts are never used). Move counter: StowLM vs two-step vs lower bound. Pilot 2 keeps
  only the two-step *score* (`pilot2/val15-twostep/<id>.json`, no plan), so the two-step column stays `—` and its
  tooltip quotes the recorded score as "not replayed or re-checked here"; the lower bound is computed. X-ray from the
  run's `trace.jsonl` (legal vs masked, top-5 before masking, overridden-by-mask flags, legal slots recomputed).
- **Dead end / finisher time limit / no plan:** card "StowLM dead end → not shown as a plan" over the starting world,
  with the run's reason, decoded actions, backtracks and rewound actions; no steps, no counts, no KPI, verdict
  "✕ STOWLM DEAD END · NO PLAN", rules badge "NO PLAN · NOT CHECKED". A dead end's decoded prefix in `result.json` is
  dropped at load and never replayed. A plan with status `ok` that fails the fresh checker is shown as a diagnostic with
  verdict "✕ REJECTED BY CHECKER · NOT APPROVED" (never as a result).
- **Backtracking:** pilot-2 traces keep only the surviving path (`action` + `end_of_plan` rows); rewinds are counts in
  `result.json` `metrics.gen_backtracks` / `gen_rewound_actions`. The x-ray card says "Backtracking: N backtracks, M
  actions rewound (counted in result.json; …)" and the dead-end card shows both numbers. If a future trace has rows with
  `event: "backtrack"` or `"rewind"`, each is drawn as an amber line + notch on the scrub bar (at the next decoded action's
  lift) and the x-ray card at that lift says "BACKTRACK before this lift". A recorded masked log p of −∞ is shown
  as "log p −∞ (recorded)".
- **D99 PREVIEW label (rule in code: `app/stage_stowlm.py` `FINAL_RULE`):** a StowLM plan is the *final showcase* only
  if it is a verified StowLM run (`planner_kind: stowlm_adapter`) **and** its run folder is
  `experiments/mvp/runs/stowlm-showcase*` **and** its instance is one of the three small Laem Chabang showcase calls
  (`random.small.29000201.0.7` feeder_1700, `random.small.29000022.0.7` feeder_500, `random.small.29000102.0.7`
  feeder_800). Every other StowLM plan carries the dashed amber in-frame label in every mode, e.g. "PREVIEW: PILOT 2 ON
  A VALIDATION MAP, NOT THE FINAL MODEL" (top-left of the ship panel; in video mode the director shows the same text in
  its stand-in pill). The 9.2 runs lose it automatically; pilot runs can never lose it.
- **Adapter label:** `mvp_gold` adapters (plan v4) and `mode: "mvp-loose (D79)"` run manifests now count as clean
  (`app/model_runs.py` `CLEAN_DATA_MODES`, `_mvp_loose_run`): label "StowLM (Muse Glimmer 30B + LoRA, MLX)", no stub
  taint. Before this, every plan-v4 StowLM run (pilot 2 and the 9.2 runs) was "diagnostic adapter, not evidence" and
  failed the departure gate. Leader decision open for veto.

### FINAL SWAP (task 9.2 StowLM showcase runs → video)

1. 9.2 writes its run folder as **`experiments/mvp/runs/stowlm-showcase/`** (exactly this name; the shot selectors use
   `run: "stowlm-showcase"`), same layout as pilot 2: `manifest.json` (`planner_kind: stowlm_adapter`, clean adapter
   lineage), `config.json` (`inputs` = the showcase corpus files + SHA-256), `instances/<instance_id>/{result.json,
   trace.jsonl,outcome.json,report.json}` for `random.small.29000201.0.7`, `…29000022.0.7`, `…29000102.0.7`.
2. Launch: `uv run python -m app --video-showcase --port 8851` — it now registers every shipped instance of
   `experiments/mvp/runs/stowlm-showcase*` by itself (beside the gold stand-ins and two-step rivals). Shots 1, 3, 4, 5,
   8, 9 select `{run: "stowlm-showcase", instance: "random.small.29000201.0.7", method: "stowlm"}` and switch to the
   StowLM plan automatically; their stand-in labels disappear because the run passes `FINAL_RULE` (shot 5 now points at
   the feeder_1700 showcase call, not a medium instance).
3. No shot-list hand edits: shots 2 and 6 now select StowLM too, and `stowlm-showcase*` matches any folder with that
   prefix. Fill the numbers instead: `fill.json`, then `sync_fill.py`.
4. A showcase call whose StowLM result isn't `ok` is not auto-registered, but it's no longer called pending:
   `/api/stage/showcase` reads the failed result and the director follows the table in "Three-state StowLM label". Don't add
   `--run-folder` for it; the dead-end card comes from the run's own records.

### Swapping in the showcase / StowLM runs (earlier notes)

1. Launch the server with the StowLM showcase run so it is in the catalogue, e.g.
   `uv run python -m app --port 8841 --run-folder experiments/mvp/runs/<stowlm-run> --entry instances/<id>/result.json
   --compare-run-folder experiments/mvp/runs/showcase-twostep --compare-entry instances/<id>/result.json`.
2. In `stage-shots.js` shots 1, 3, 4, 8, 9 already select `{run: "stowlm-showcase", instance:
   "random.small.29000201.0.7", method: "stowlm"}`: set `run` to the real StowLM run folder name (the gold
   `standin` can stay; its in-frame label disappears by itself). Shot 5 needs a StowLM `trace.jsonl` on that instance;
   shots 2, 6, 10 point at the gold plan directly: change them to StowLM too and drop their `standin_tag`.
3. Add measured values to `measured` (`C1_OWN_PASS`, `C1_FALLBACK`, `PLAN_TIME_S`, …) with `source` + `match`,
   add the StowLM row of `results_card` the same way, then `uv run pytest app/tests/test_stage_director.py`.
4. Precompute the physics timeline (section 2) so the gauges are measured in the take.

### Task 10.2 swap, as done (30 Oct)

- **Fill:** `fill.json` → `sync_fill.py` → `stage-fill.js`; sources in `fill-sources.md`, recomputed by `app/tests/test_fill_swap.py`. Worse's script is
  filled in place, so `sync_fill.py` keeps Worse's keys in `FILLED_IN_PLACE` and the caption tests compare after the same fill.
- **Column order:** 9.1's table is better/equal/worse, the card is fewer/more/equal, the hybrid table is fewer/equal/more. Convert; the test pins it.
- **Worse-only text:** base `shots` now serve Par and Better as they stand. Worse's final durations and captions (D168) sit under `variants.worse`:
  `shots`, `instruction_beat`, `results_note`, `results_left` (the [val] pair and "what's left" live on the results card, D168).
- **Instruction card (D170):** StowLM's replan from `runs/stowlm-instruction-binding`. D166's "not demonstrated" stands only for the earlier
  `runs/stowlm-instruction-dg`, which isn't registered and is never shown as a replan.
- **Shot 8 strip:** StowLM's moves come from the run's `report.json` when `outcome.json` has no count (9.2's don't).
- **Stills:** `dev/check_shots.py` takes mid and near-end stills in final mode and scans the page text for banned words.
- **Whole take:** `record_shots.py --final --whole` also writes one continuous `whole-take.webm` plus `whole-take.json`.

### Draft recording (no ffmpeg needed)

`PYTHONPATH=. nice uv run --with playwright python experiments/mvp/ui/dev/record_shots.py http://127.0.0.1:8851`
→ `experiments/mvp/ui/video-draft/shot-NN.webm` (1920×1080 VP9 30 fps, DRAFT), `shot-NN-mid.png` stills (via `&t=`),
`timing.json`. Options: `--shots 3,5`, `--stills-only`, `--video-only`, `--final`, `--at 2,7.5`. The page records its
own tab (getDisplayMedia with Chrome's `--auto-accept-this-tab-capture`, headless, into a MediaRecorder); Playwright's
built-in recorder is not used because it needs a downloaded ffmpeg. Each webm has ~0.3 s of black before the dip and
~0.4 s after. Draft quality only.

### Backup take (D171, 30 Oct)

No screen recorder and no ffmpeg needed. From a warmed `--video-showcase` server (each plan once, then until `/api/stage` reports
`explanation.source: spark-cached` for the three showcase plans and the binding run):

```sh
PYTHONPATH=. nice uv run --offline --no-sync --with playwright python experiments/mvp/ui/dev/record_shots.py http://127.0.0.1:8851 \
    --final --whole --out experiments/mvp/video/backup-take
```

`whole-take.webm` is the continuous take; `shot-NN.webm` are separate passes for retakes. Silent, tab capture, 1920×1080. See that folder's README.

### Final take

1. Close notifications, hide the terminal and the Dock; Chrome full screen (or a kiosk window) at exactly 1920×1080.
2. Warm the server (open `/stage?video=1&shot=1` once so physics and replays are cached), then open
   `/stage?video=1&hold=1` (no `draft=1`).
3. Start the recorder: macOS Cmd-Shift-5 → "Record Selected Portion" on the browser window at 1080p (or OBS, 1920×1080,
   60 fps, CRF ~18), then press Space. The take ends on the end card (2:54) and holds black.
4. Trim the head before the first fade-in. Check every caption: none may show `PENDING`; final mode drops unfilled
   segments, so read the dropped ones against the voice-over.
