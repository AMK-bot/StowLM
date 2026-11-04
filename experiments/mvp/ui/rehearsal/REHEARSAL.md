# Live rehearsal, plan task 4.3 (2 Nov 2026, 14:39–15:15 EST)

## How it ran

- **Start command** (`launch.sh`): the README's v6 command from a clean `env -i` shell (HOME, USER, PATH, TERM only; no PYTHONPATH, no thread variables): `uv run python -m app --v6 --manifest experiments/mvp/pipeline/freeze/manifest-pipeline.json --port 8951 --reader glimmer --reader-adapter experiments/mvp/reader/v3/candidates/swa-last-5`. The adapter is the D266 adoption (`final-adoption-d266.json`: swa-last-5, eff712e1…). `--brief-live` was left off because it only adds Spark speech, which needs the cloud. `--brief-live-replan` was left off because the README's judge path doesn't include it.
- **Network block:** the server process and all its children ran under `sandbox-exec`. The profile was offline_timing.py's `(version 1)(allow default)(deny network*)` plus `(allow network-bind (local ip "localhost:8951"))(allow network-inbound (local ip "localhost:8951"))`, so the browser could reach it. I checked the profile first: outbound `connect` to 1.1.1.1:443 failed with EPERM, and localhost serving answered 200. **The laptop's Wi-Fi stayed on**: only the server process was blocked. The headless Chrome driving the page was not sandboxed.
- **Cache state:** the regate memo, check memo and physics caches were warm, as on a rehearsed laptop. The Glimmer weights were in the OS file cache.
- **PIDs:** I started and stopped only my own (`server.pid`, `poll.pid`). The server is stopped.

## Timings (cold relaunch, wall clock, t = 0 at launch; `t_launch.txt`, `t_state200.txt`, `server.log`, `walk2/walk.json`)

| Milestone | t |
|---|---|
| `/api/v6/state` 200 | 6.7 s |
| warm line ("warm in 6 s: gate shown, before/after plan physics ready, package passed") | 12.4 s |
| stage open and rail ready (opened right after warm) | 13.5 s |
| first live reader answer on the hero sentence | 68.6 s: 4.9 s from confirm to answer (parse 4.62 s), first read in this process; warm reads 1.6–2.2 s |
| first checked plan shown (replan chapter result, gate "Full checker passed: 54 active rules, 8 off as run") | 81.0 s, after 55 s of presenter pacing through hook, intake, radio and reading |

- **Live replan:** I didn't run it. The figures come from the recorded run in `fixtures/live-replan-run.json`: first checked plan at 1030.5 s, done at 1817.3 s, with load at about 27–29.
- **Other live jobs:** a free break-it drag took 1.3 s to the fast screen and 5.5 s to the full checker on screen. The package verify passed in 8.3–8.7 s, and the tamper failed it in 2.6–2.8 s. No click took longer than 3 s to respond, and no job took longer than 60 s. Details for every step are in `walk.md`.

## Failures and fixes (app/ only; nothing under src/ or experiments/mvp/pipeline/ touched)

1. **The README launch withheld the hero plans.** The regate child died on `import engine` (`attempt1-regate.log`). The reason was that `app/v6.py` started its regate and live-replan workers without the engine and lab paths. `v6_package` and `v6_sandbox` already pass those paths to their children. The warm line read "gate not_shown… before plan withheld, after plan withheld" (`attempt1-server.log`). **Fix:** added `_worker_env()` to `app/v6.py`, which gives both workers `.`, `src`, `experiments/mvp/frontier` and `experiments/mvp/frontier/lab` on top of the caller's PYTHONPATH. Thread variables are unchanged for the regate. After the fix the cold launch was warm in 12.4 s with the gate shown.
2. **"Replan under this rule" couldn't be clicked.** The replan chapter's label row wraps to three rows for this hero, and its caveat line covered the button at its fixed `top: 148px`. Playwright reported the click as intercepted, and a presenter would have been stuck at the hold. **Fix:** `app/static/stage-v6replan.js` places the button 14 px below the label row when it appears (at least 148 px down; the layout is read once, on show). `stage-v6replan.css` adds `z-index: 2`. Film frames don't change, because the button only appears in interactive mode.
3. Fixes to my own walk script, not the app: the brief panel is covered (see B), and the verify-end detection was wrong. That detection bug left a 150 s idle in walk 2's take, which is kept as `walk2/take-with-idle/`.

After both fixes, `app/tests/test_v6_gates.py`, `test_v6_flow.py`, `test_v6_second_pass.py` and `test_v6_third_pass.py` pass (129 tests). `test_stage_director.py` has one failing caption test that this rehearsal didn't cause (finding D in `walk.md`). These edits change `app/`, so the D295 film inventory (`film-inventory-20261102.json`, f47cd1da…) no longer matches `app/v6.py`, `stage-v6replan.js` or `stage-v6replan.css`. It needs regenerating before it's quoted. The pipeline manifest doesn't pin `app/`.

## Findings not fixed

These are detailed in `walk.md`.
- **A.** In a v6 launch the live reader only serves the brief panel's six slice ships. It refuses the hero sentence on them as `unsupported`. The stage's reading is always the recorded one, labelled "Recorded answer".
- **B.** In v6 the brief panel (key V) sits under the chapter overlay, and its chips stay at "CHECKING".
- **C.** The console shows a `favicon.ico` 404.

There were no page errors and no gap warnings in any of the three walks.

## Backup take

- **File:** `backup-take/take.webm`, 1920x1080 at 30 fps, VP9, 59.6 MB, with `backup-take/timing.json`.
- **Capture:** tab capture, the same approach as `record_shots.py` (`getDisplayMedia` with `preferCurrentTab`, then MediaRecorder, in headless system Chrome with `--auto-accept-this-tab-capture`).
- **Duration:** **161.6 s** of wall clock, from stage open to after the tamper. The webm header has no duration field, so this figure is the recorder's start and stop times. `timing.json` gives each step's offset.
- **Content:** the whole walk, live (warm server): mess in, radio, live reader call, confirm, replan, x-ray, a live drag and a prepared drag, copilot refusal, live verify and tamper.

## Drift check

I ran `experiments/mvp/pipeline/freeze/verify.py --require-frozen experiments/mvp/pipeline/freeze/manifest-pipeline.json` after the walks and again after the tests. Both returned `"ok": true` in about 4 s, with manifest a11b42021a33… and tree 80df1b64…. **Zero drift.** The launcher's own manifest guard also passed on every launch, showing "Frozen pipeline a11b42021a33".

## Finding A fixed: the live reader reads the hero sentence on stage (2 Nov 2026, 16:03 EST)

**Fix (app/ only).** In a `--v6` launch the brief service now has the hero as a ship (`HERO_CALL = "v6:hero"`, `app/brief.py`). Its reader context is the one `reader_final/hero_candidates.py` read the hero on: `read_all.instance_of(<hero path>, {})`, run as written in a child process, because importing the runner package in the server sets `STOWAGE_VESSEL_CACHE_DIR`. The path is the slot `app/v6_hero.json` names. That context must also equal the hero source's own context and `context_from_instance` of the file, or nothing is read (`app/v6.py` `hero_reader_context`, `hero_ship`). Outside `--v6` the hero ship doesn't exist, and the six brief ships are unchanged.
- `POST /api/v6/read {sentence}` sends the sentence on screen through the brief service's one reader, using its draft and confirm gates, on the hero ship. A reading is kept as confirmable only if it was live, read on the hero's context, and has a rule (`LiveReads`). A reading on any other ship's context is refused.
- `POST /api/v6/confirm` takes an optional `reading`. Its token is bound to that live reading's sentence, rule and instance hashes, and is refused if the hero context has changed since.
- `GET /api/v6/replan?token=…` looks the confirmed live reading's canonical rule up in the lattice (`entry_key(instance_sha256, [rule])`). The plan under the rule is sent only when that key is the regated after-entry and the rule canonicalises to the stored rule. Otherwise the gate drops to the plan before, the reason is on screen ("The live reading is …, not the stored rule …; no plan is stored for it, so none is shown"), and no figures for the plan under the rule are sent.
- Reading chapter (`stage-v6reading.js`): in interactive mode with a live reader, it holds at 0.9 s with "Read it live" and "Use the recorded reading". A live answer redraws the chapter from the server's reply. The film path doesn't change.
- Replan chapter (`stage-v6replan.js`): it sends the token only for a live reading's confirmation, and reloads if that token changes.
- Tests: `app/tests/test_v6_live_read.py` (12, fake reader, no GPU).

**One live read, on the GPU.** `live-read/launch.sh` is launch.sh's command and its `sandbox-exec` network block, on port 8971. The profile denies all network except localhost:8971; I checked it first, and curl to 1.1.1.1:443 failed under it. Wi-Fi stayed on. Only the server process was blocked; headless Chrome was not. The server was warm in 7 s: "gate shown, before/after plan physics ready, package passed, live reader context ready". `live-read/walk_live.py` drove headless system Chrome at 1920×1080 (`live-read/walk.json`, screens `01`–`04`). This was the first read of the process, and the weights were in the OS file cache.

| Step | Result |
|---|---|
| Click "Read it live" → "Live" tag on screen | **5.34 s** (HTTP response at 5.24 s; server wall 5.21 s; the reader's own parse **5.09 s**, shown as "Read in 5.1 s") |
| Reading | `{"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 9}}` → "Nothing loads into bay 09."; status constraint_ready; adapter eff712e1… (adopted: true) |
| Labels | "StowLM Reader · fine-tuned Muse Glimmer 30B · on this laptop" + "Live" (from evidence via `reader_label` and `readLive`) |
| Lattice lookup | key 72ccfc88… = the regated after-entry: shown |
| Confirm | token bound to the live reading (source `planner_pressed_confirm_in_reading_chapter`); replan hold button 2.8 s after the click (chapter change + bundles) |
| Replan → result | **plan shown**, 9.6 s of playback: "Stored plan, computed on 2 Nov 2026, rechecked 2026-11-02 20:30 UTC (exact memo)", "Frontier Engine 0.13.0 · search software", 66 → 82 extra lifts, "Full checker passed: 54 active rules, 8 off as run", gauges stay green |

Page errors: none. Console: only the `favicon.ico` 404 (finding C). I started and stopped only my own server (`live-read/server.pid`). After the walk, I renamed the button from "Read it live on this laptop" to "Read it live" (the location words come only from `reader_label`) and added a 14 px margin. I didn't rerun the live read for those two changes.

**Drift and suite.** `freeze/verify.py --require-frozen` returned `ok: true` before and after the suite: manifest a11b42021a33…, tree 80df1b64…, **zero drift**. The full app suite, run once after the rename (`live-read/app-suite.xml`), had **1538 tests: 1538 passed, 0 failed, 0 errors, 0 skipped**, in 144 s. The first run, before the rename, had 1 failure (`test_v6_intake::test_local_claims_only_behind_evidence`, caught by "on this laptop" in the button). The D295 film inventory no longer matches `app/v6.py`, `app/brief.py`, `stage-v6reading.js` or `stage-v6replan.js`.

## Plan 5.2 GPU check: the upload bundle as the live reader (2 Nov 2026, about 18:05 EST)

**Command** (`live-read-bundle/launch.sh`): the README's live-reading form, with the p52 upload bundle as the adapter: `uv run python -m app --v6 --reader glimmer --reader-adapter experiments/mvp/pipeline/repo/hf-reader-bundle --manifest experiments/mvp/pipeline/freeze/manifest-pipeline.json --port 9001`, from the same `env -i` shell and under live-read/launch.sh's `sandbox-exec` profile with the port changed to 9001. I checked the profile first: curl to 1.1.1.1:443 under it failed to connect. Wi-Fi stayed on. Only the server process was blocked; headless Chrome was not. I started and stopped only my own server (`live-read-bundle/server.pid`). No app code changed.

**Sha checks.** The bundle's `adapters.safetensors` is eff712e18a649804fcbd5b19db27baa709fa60701a2970d1fa58593f731c6f57, the same as `experiments/mvp/reader/v3/candidates/swa-last-5/adapters.safetensors`, as the x-ray trace's `adapter_sha256` and as `app/v6_hero.json` `reader_adopted` (eff712e1…). The bundle's `adapter_config.json` (and `STOPPED.json`) are byte-identical to the swa-last-5 candidate's.

**Launch.** Warm in 7 s: "gate shown, before plan physics ready, after plan physics ready, package passed, live reader context ready". The launcher's manifest guard showed "Frozen pipeline acf2f275efa7".

**One live read** of "Nothing goes into bay nine on this call." through the reading chapter, driven by `live-read/walk_live.py` (headless system Chrome, 1920×1080; `live-read-bundle/walk.json`, screens `01`–`04`). This was the first read of the process, with the weights in the OS file cache.

| Step | Result |
|---|---|
| Click "Read it live" → "Live" tag on screen | **5.33 s** (HTTP response at 5.26 s; server wall 5.23 s; the reader's own parse **5.10 s**, shown as "Read in 5.1 s") |
| Reading | `{"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 9}}` → "Nothing loads into bay 09."; status constraint_ready; adopted: true |
| Evidence | parser GlimmerParser, model muse-glimmer-30b, adapter_sha256 **eff712e18a649804fcbd5b19db27baa709fa60701a2970d1fa58593f731c6f57**; on screen "Adapter eff712e18a64" |
| Lattice lookup | key 72ccfc88… = the regated after-entry: shown |
| Confirm | token bound to the live reading (source `planner_pressed_confirm_in_reading_chapter`); replan hold button 2.85 s after the click |
| Replan → result | **stored bay-9 plan shown**, 9.6 s of playback: "Stored plan, computed on 2 Nov 2026, rechecked 2026-11-02 23:06 UTC (exact memo)", "Frontier Engine 0.13.0 · search software", 66 → 82 extra lifts, "Full checker passed: 54 active rules, 8 off as run", gauges stay green |

Page errors: none. Console: only the `favicon.ico` 404 (finding C).

**Drift.** After stopping the server, `uv run --offline --no-sync python experiments/mvp/pipeline/freeze/verify.py --require-frozen experiments/mvp/pipeline/freeze/manifest-pipeline.json` returned `ok: true` in 4.0 s: manifest acf2f275efa7…, tree 9685cd1f…, **zero drift** (`live-read-bundle/verify-manifest.out`). No tests were run.
