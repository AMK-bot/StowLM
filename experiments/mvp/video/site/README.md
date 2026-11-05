# StowLM landing page

A static, single-page site for GitHub Pages: the StowLM pitch (the AI pipeline around a ship-planning engine, as an app), the launch video, the pipeline diagram and x-ray clip, the engine slot and its status (only Engine 0.13.0 is wired in; no third-party engine has been ported), the three Meta models and what each does, the held-out results with their labels and the recorded-results charts, the Impact projection, a printed handover sample, links (including the play page, a recorded run of the Functional app), and credits. Plain HTML and CSS, no JavaScript, no fonts or files from the network, no build step. It works opened straight from this folder. Layout adapted from brag's docs page (`experiments/mvp/video/brag-vendor/docs/`, latent-spaces/brag, MIT).

Every word is bound by `docs/headline.md` and `experiments/mvp/video/stowlm-brag/references/honesty.md`. Re-read both and re-run `check.py` before changing any text.

## Files

| Path | What |
|---|---|
| `index.html` | The page. Number sources are listed in a comment at the top |
| `styles.css` | Dark navy theme from the film, light variant via `prefers-color-scheme`; system fonts |
| `media/` | Launch video and poster, x-ray clip, pipeline diagram, `charts/` (four results SVGs), `hero-bay-plan.pdf`, `favicon.svg`; sources in `media/README.md` |
| `PAGES-DRY-RUN.md` | Sub-path (`/stowlm/`) crawl and headless-Chrome check, 2 Nov 2026 |
| `replay/index.html` | Byte-for-byte copy of the 25-drag break-it replay (self-contained, no script, no network). Don't edit it here |
| `play/` | A recorded run of the Functional app in the app's own UI (D365): fresh synthetic inputs precomputed for it (not the film's call), every request served from recordings, no API calls, no key panel, nothing installed; changing an input says to install the app. Built separately; its own README says how. JavaScript is allowed here under its own policy (D319), which `check.py` enforces. Don't edit it here |
| `check.py` | Stdlib-only check: offline, banned wording, "sealed", music credit, held-out label, HTML well-formedness, replay hash, no root-relative URLs, social-card meta URLs; `play/` policy. Tests: `../tests/test_site_*.py` |

## Replay copy

- Source: `experiments/mvp/ui/replay/index.html`
- sha256: `b850a4c8920faf1972cbdf86b460597f0b3f2c840e787a7efb7ca179cdcc71b1`

If the source page is rebuilt, copy it again and update the hash here; `check.py` fails on a mismatch.

## Check

```bash
cd .
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-project --python 3.12 python experiments/mvp/video/site/check.py
```

## Preview locally (later)

```bash
cd experiments/mvp/video/site
python3 -m http.server 8000
# then open http://localhost:8000
```

## Publish on GitHub Pages (the user's step)

1. Fill the placeholders below and add the media files.
2. Run `check.py`.
3. Either copy this folder's contents to a `docs/` folder on the published branch, or push it to a `gh-pages` branch root. (This repo's own `docs/` holds project notes and isn't committed yet, so a `gh-pages` branch is the cleaner route.)
4. In the repo's Settings → Pages, choose "Deploy from a branch" and pick that branch and folder.
5. All links are relative, so the site works under `https://<user>.github.io/<repo>/`.

## Placeholders to fill

The repo URL (topbar and "Source code and both apps" card) and the Pages URL in the social-card meta (`og:url`, `og:image`, `twitter:image`: `https://amk-bot.github.io/StowLM/...`) are filled. Two remain:

| Placeholder | Where | Replace with |
|---|---|---|
| `{{demo_video_url}}` | Filled 4 Nov 2026: the "Watch the launch cut" card links the demo video, https://youtu.be/fTb7Hn0OjTY (youtu.be is in `check.py`'s allowed hosts). |

If a new external host is added (say, a video host), add it to `ALLOWED_HOSTS` in `check.py` on purpose.

## Number sources

| On the page | Value | Source |
|---|---|---|
| 24 of 24 held-out maps pass the full checker | 24 / 24 | `experiments/mvp/pipeline/sealed/runs/final-3/story-figures.json` `/cohorts/envelope24/arms/pipeline/passes_L0`, `/rows` |
| 85.8–100 % | [0.8575, 1.0] | same file, `/cohorts/envelope24/arms/pipeline/passes_L0_exact95` (exact two-sided Clopper-Pearson 95 %) |
| 13 of 18 obey the rule | 13, 18 | same file, `/instruction/binding_obedient_passes`, `/instruction/rule_broken_maps` |
| 46.5–90.3 % | [0.4652, 0.9031] | same file, `/instruction/binding_obedient_exact95` (recomputed independently: matches) |
| 6 already obeyed | 6 | same file, `/instruction/already_obeyed` |
| Two-step 0 of 24, 0–14.2 % | 0, [0.0, 0.1425] | same file, `/cohorts/envelope24/arms/twostep/passes_L0`, `/passes_L0_exact95` |
| No lift saving is quotable | false | same file, `/lift_claim/quotable`, `/lift_claim/envelope_mutual_passes` = 0 |
| Held-out label | text | same file, `/run/label`; `docs/headline.md` (D284, D286, D288) |
| Partial-run paragraph (36 and 91 rows; 36 of 38, 37 of 38; 91 of 91) | text | `docs/headline.md`, "Numbers we may quote" |
| Envelope: 195 boxes, 500–800 TEU, 70 % | limits | `docs/headline.md`, "The envelope" |
| Reader 118 of 128 (92.19 %), untrained 60 of 128 (46.88 %), Spark 126 of 128, gates | figures | `docs/headline.md`, "Numbers we may quote" (D266) |
| 54 active rules, 8 off | counts | `docs/headline.md`, "Who does what" |
| Music credit, build dates | text | `experiments/mvp/video/stowlm-brag/shots.json` (`music.credit`, `credits.lines`) |
| Impact: about $90 billion a year exposure, every 1 % about $1 billion; about $2.3 billion a year pool (mid), low $0.3 billion; $40 million at Laem Chabang (9,325 of about 520,000 calls, 1.8 %); check $63–76 billion; $0.42 billion restow line | projection | `docs/impact.md`; `experiments/mvp/ui/fixtures/film-figures.json` `/figures/impact_*`, `/figures/stake_calls`; wording `docs/headline.md` "Impact (D254)", D256, D286 |
| Impact inputs (920M TEU, 520k calls, 0.9 % avoidable restows at $75, 5 % less berth time) and what would have to be true | text | same; the terms line per D316 (`docs/decisions.md`) |
| Charts | SVG | `experiments/mvp/charts/*.svg`, copied byte for byte (hashes match its README); captions from `docs/headline.md` |
| Handover sample: 87 boxes in 6 of 12 bays, 168 background boxes, confirmed-instruction line, v4 acf2f275… | text | `experiments/mvp/pipeline/package/fixtures/hero-run/package/` (`note-to-master.md`, `package.json`, `receipt.json`, `bay-plan.html`) |
| Engine slot: one binding, plan format, recheck on the original instance, manifest pin; two-step plans judged by the same gate in the evaluation; only Engine 0.13.0 wired in | text, no numbers | `experiments/mvp/pipeline/binding/adapter.py` (`execute_brief`, `plan_with_rules`, `verify_manifest`), `experiments/mvp/pipeline/runner/core.py` (`engine_call`, checker and ladder stages), `src/stowage/model`, `experiments/mvp/pipeline/sealed/worker.py` (`_twostep`, `_audit`); D364 |
