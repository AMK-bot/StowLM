# Story needs (task 1.6, for 2.2 and 4.2)

What the v5 drafts need from the demo and from measurement, after the sceptic's review (`experiments/review/v5/logic-story.md`, D224). Drafts: `docs/pitch.md`, `experiments/mvp/video/script.md`, `experiments/mvp/README-draft.md`, `experiments/mvp/writeup/training-story.md`, `experiments/mvp/social/x-post.md`. Wording is bound by `docs/headline.md`.

Old versions stay as they are: `*.worse.md`, `.par`, `.better`, `.orig`. `training-story.md` had no archived copy, so it's also `writeup/training-story.worse.md`. Nothing in `app/` or `tests/` reads these story files; `video/fill.json` is untouched.

## Two kinds of blank

- **`{{NUMBER: what}}`:** always filled from the source below.
- **`[[IF name: …]]`:** kept only if that result exists, then the brackets go. Otherwise delete the whole block, including its numbers. The text reads correctly either way, and no draft may be published with a block still in brackets.

| Name | Condition | If it doesn't happen |
|---|---|---|
| `real-planner` | 0.11: the field expert's 10–20 sentences arrive, are frozen before running, and are scored once. Whatever comes out is reported | Delete every block. Nothing else changes, and "written by a separate AI reviewer" stands |
| `reader-mask` | 1.1: reader v3's schema-constrained decoding is adopted, measured no worse on its own sealed set | Delete every block. The mask is then described only as measured on the planner |
| `fair-parser` | D224: a serious hand-written parser, frozen before scoring, blind to sealed sets | Delete every block. No rules baseline appears anywhere. If it does well, the claim shrinks and the row stays |

## Risks to settle first

1. **Headline wording conflicts with the review in two places.** `docs/headline.md:10` still says "common two-step practice" and "what we beat"; `:32` still says "the job software can't do", against its own `:23`. The drafts follow P1-5 and P0-2 as ordered. The contract needs those two lines changed, or P1-5 vetoed (it's listed as open for veto; D177's direction supports it).
2. **The hero sentence is a candidate, not a measured result.** The script's default is "Keep class 8 at least one bay from bay 1." "Corrosive" and "bay one" appear in no training row, so "Keep the corrosives…" may misread on camera. `reader/hero/HERO.md` is queued, reads through Spark and uses the corrosives wording, so it doesn't settle this. Task 3.2 must try the exact sentence on the frozen reader. Don't train on the hero sentence.
3. **The second line of the note is unmeasured.** "In bay 2, no more than 4 high on deck." needs a checked result on the hero map, and a plan that it actually binds. `plan_brief` takes several lines, but no multi-line hero has been run.
4. **A spoken "bay one" may reach the reader as words.** Whatever Muse Voice returns goes in as text, so 1.3 must test spelled numbers. Reader v3's data adds them.
5. **Bay closure stays off screen.** Every closure on small sections is impossible, an artefact of the section size. D222's screen leaves 0 of 30. Shot 5 uses the impossible-tier case with its alternative instead.
6. **Shot 5's recordings used Spark as reader, and the impossible sentence is stilted.** `REFUSALS.md` records "Across the vessel, use at most zero occupied tiers per deck stack." They must be rerecorded through the final reader (P1-8). A plainer wording needs its own recorded reading.
7. **"Obeys" must mean intended, not parsed.** `brief maps with a checked plan that obeys` and the feasible-cohort cells must count intended-instruction passes.
8. **The reader hasn't passed.** v1 failed all three gates; v2 has stopped training and isn't scored. Confirmation is now the main path, so a miss changes numbers, not the script.
9. **Voice is unprobed (0.9).** If it's cut, remove it from the script, pitch §2–3, the README table and diagram, and the credits. Never say "offline" near it.
10. **Money left the voice-over.** The sizing is on shot 8's card only, to reach 13 s and 25 s for shots 7 and 8. Impact now rests on shots 2, 3a and 3b. If 1.5 finds something well sourced, it can come back.

## Calls made in these drafts

| Call | Why |
|---|---|
| Shot ids stay; 3 splits into 3a, 3b, 3c | So shot 7 stays the failure shot, and other documents' references hold. 11 segments, 174 s |
| Shots 6, 7, 8 are 18 + 13 + 25 = 56 s, down from 76 s | Shot 7 is 13 s: 12 s can't hold the required sentence at reading speed. Obeyed plans moved to shot 6's caption so it fits |
| New shot 3c: Wi-Fi off on camera | The review's answer to "is it really running?", and the argument for working without a network |
| "Fine-tuning's gain" leads shot 6 and every reader table; Spark is a reference line | Lead claim 1. Price arguments are gone everywhere |
| The "10 %" in impact sizing stays | The order's "10 % baseline" is the rule-matching one. "Each 10 % of avoidable lifts" is a sizing unit |
| The 51-line parser is dropped entirely, including from the training story | Named as withdrawn it would still quote a strawman. Fine-tuned gain and Spark carry the point |
| The mask leads pitch §3 and the README's "What we tested", on planner numbers only | "Only as far as measured": 59 % and 3 % on 15 tiny test maps. On the reader it's a `reader-mask` block |
| "Without the reader" stays, last and captioned | P1-6: it sizes the problem and doesn't credit the reader. A form would fix it too |
| `headline.md` labels otherwise win over `SWAP-LIST.md` | The list's rows for the five files are applied by rewrite; app and test rows go to 2.2 |
| Frontier-alone percentages appear only in the pitch and README, with the full qualification | They can't fit a shot or a post |
| "Frontier" is "our search software"; "Frontier engine" appears only in the README's table and text | Plain words |
| "Lifts" in place of "moves" | Plain words. 4.2 shouldn't change the measure |
| No personal anecdote | None is sourced. The user can add a true one at 0.10 |

## Shots the demo must provide (for 2.2)

`test_stage_director.py` and `stage-shots.js` still hold the old 10 shots and need matching.

| Shot | Dur. | Needs | Live or recorded | Label on screen | Depends on |
|---|---:|---|---|---|---|
| 1 Hook | 10 s | Waveform and words; reading; confirm tap; boxes lift and re-land; gauges hold green. Full-bleed | Recorded replan, sped up | "recorded, sped up", replan minutes, section size, "simulated port, synthetic ship" | 3.2 hero, 1.3 |
| 2 Why | 21 s | Laem Chabang pin; simulated yard; ship filling box by box | Recorded | Source captions | Exists in part |
| 3a Say and confirm | 24 s | **Multi-line brief box; each line's strict rule beside it, marked on device; a confirm step per line** | Reading live; speech cloud | Which model did each step; the four kinds | 1.3, 2.2 |
| 3b The ship moves | 16 s | **Before and after side by side, every moved box highlighted**; gauges; rule ticks; lift counter | Recorded | "search software, recorded" | 2.2, 3.2 |
| 3c Wi-Fi off | 11 s | **Wi-Fi visibly turned off; typed sentence read live; adapter hash, seconds, memory on screen** | Live | "Live, Wi-Fi off" | 2.2, published adapter (5.2), 4.3 |
| 4 Why | 9 s | Spark panel beside the checker report, statements lighting their source lines | Prefetched and bound | "Muse Spark" | 3.2; don't narrate over a template |
| 5 When it says no | 17 s | Refusal card; question card (`unclear-distance`); arithmetic card, then the checked alternative (`impossible-tier-with-alternative`) | Rerecorded through the final reader | Accepted count; "this section"; "no guarantee for a new plan" | 1.4, 1.1 |
| 6 Evidence | 18 s | Two bars for one model, Spark as a reference line; a caption line on obeyed plans | Generated from results | Exact-match definition, split of misses, sentence authorship, retired sealed set | 1.1, 0.4 |
| 7 Honest beat | 13 s | Mask x-ray, a real step only; three bars | Recorded | Sealed-test labels | Exists in part, 3.1 |
| 8 Worth | 25 s | Lifts card with the limits on the same card; Laem Chabang per-call card | Generated from results | Limits and assumptions | 3.1, 1.5 |
| 9 End | 10 s | Ship, gauges, note; repo link; scope and build dates; generated-voice label if used | Recorded | Scope line | 0.10 |
| X clip | 15 s | 1080×1920 crops of shots 1, 3a, 3b, 5 | Recorded | As in `x-post.md` | 2.2 |

App labels: apply `SWAP-LIST.md`'s app rows, using "our two-step baseline, built from one field expert's description" and "our emulation of published methods". No plan from the search may be labelled "StowLM planned". Never show "One port per hatch" as an instruction.

## Numbers needed (for 4.2)

Provisional values are for orientation only: **don't publish them from here.** A dash means none exists yet. Files: P pitch, V video, R README, T training story, X X post.

### Reader

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---|---|
| sealed sentences | final sealed set | 1.1 | 128 (v2) | P V R T X |
| reader accuracy % | `results-v2/gates.json` or v3 | 1.1 | – (v1 83.33 %, failed) | P V R T X |
| untrained Glimmer accuracy % | gates | 1.1 | – (v1 37.5 %) | P V R T X |
| Spark accuracy % | `sealed-v2/spark-summary.json` or v3 | 1.1 | 94.53 % (v2) | P V R T |
| loosening requests accepted | gates | 1.1 | – (v1 4) | P V R T |
| loosening requests tested | gates | 1.1 | 40 (v2) | P V R T |
| loosening requests refused | gates; miss branch only | 1.1 | – | V |
| Spark loosening accepted | `sealed-v2/spark-summary.json` | 1.1 | – (v1 0 of 25) | P R T |
| untrained loosening accepted | gates | 1.1 | – | P R T |
| reader silent wrong rules | sealed rows: a wrong rule emitted | 1.1 | – | P V R T |
| reader safe refusals of good sentences | sealed rows: refusal of a valid sentence | 1.1 | – | P V R T |
| reader seconds per sentence | timed on the laptop, cold and warm | 2.2, 4.3 | – | P V R |
| reader peak memory GB | same | 4.3 | – | P V R |
| reader adapter MB | final adapter file | 1.1 | 64.5 | P V R T |
| reader training hours | final run log | 1.1 | – | T |
| reader training sentences | `data-v2/manifest.json` or v3 | 1.1 | 2,224 | P V R T |
| reader Spark paraphrase rows | `source_kind` count in final train file | 1.1 | 248 | P R T |

### Conditional results

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---|---|
| real planner sentences | frozen expert set | 0.11 | – | P V R T |
| reader exact on real planner sentences | one scored run | 0.11 | – | P V R T |
| reader malformed answers without mask | v3 sealed run | 1.1 | – (v1 5) | P R T |
| reader malformed answers with mask | v3 sealed run | 1.1 | – | P R T |
| fair parser accuracy % | fair parser, frozen, same sealed set | D224 | – | P R T |
| fair parser loosening accepted | same | D224 | – | P R |

`{{QUOTE: the field expert's one line}}` (P V R) is the expert's reaction, verbatim, whatever it says, with no name or company.

### Following instructions

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---|---|
| feasible-cohort maps | second cohort, `brief_cohort2.py` | 0.4 | – | P R |
| feasible-cohort reader exact | same, final reader | 0.4, 3.1 | – | P V R |
| feasible-cohort plans that obeyed | same, of the exact readings | 0.4, 3.1 | – | P V R |
| binding replans confirmed with final reader | rerun of D211's cases | 1.1, 3.2 | – (Spark 16) | P R |
| binding replans tried | same | 1.1, 3.2 | 20 | P R |
| brief maps | `reader/BRIEF.md` | 0.4 | 30 | P R |
| brief maps with a checked plan that obeys | same; intended passes, final reader | 0.4, 3.1 | pending | P R |
| instructions proved impossible before planning | same | 0.4 | 14 | P R |
| instructions with no matching cargo | same, cohort notes; confirm the count | 0.4 | 4 | P R |
| instructions broken without reader | same | 0.4 | pending | P R |
| plans returned without reader | same | 0.4 | pending | P R |
| mean extra lifts to obey | same | 0.4, 3.1 | pending | P R |
| replan minutes | same, on a quiet machine | 0.4 | about 3–5 | P R |

`BRIEF.md` today uses Spark as reader. These cells need the final fine-tuned reader before they count. If its parse equals Spark's, the plan may be reused, and the story says so.

### Hero and refusals

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---|---|
| hero replan minutes | hero run | 3.2 | – | V |
| hero lifts before | hero run | 3.2 | – | V |
| hero lifts after | hero run | 3.2 | – | V |
| hero extra lifts | after minus before; if 0, change to "cost no extra lifts" | 3.2 | – | V |
| hero boxes moved | hero run | 3.2 | – | V |
| impossible case space needed | `impossible-tier-with-alternative`, rerecorded | 1.4, 3.2 | 31 | P V |
| impossible case space left | same | 1.4, 3.2 | 12 | P V |
| alternative stack cap | same | 1.4, 3.2 | 2 | V |
| Spark explanations bound | final prefetch | 3.2 | – (old set 33) | P R |
| Spark explanations total | final prefetch | 3.2 | – (old set 37) | P R |

### Crane lifts

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---|---|
| lifts saved vs two-step % | test v3, pipeline vs two-step | 3.1 | 39 % blind, frontier 0.8.1, different arm | P V R |
| pipeline mean lifts | test v3 | 3.1 | 14.28 same arm | P V R |
| two-step mean lifts | test v3 | 3.1 | 23.40 same arm | P V R |
| maps both pass | test v3 | 3.1 | 25 same arm | P V R |
| test maps | test v3 | 3.1 | 30 same arm | P V R |
| pipeline passes | test v3 | 3.1 | – | P V R |
| two-step passes | test v3 | 3.1 | – | P V R |
| better / equal / worse maps | test v3 | 3.1 | – | P R |
| share of saving at later ports % | test v3 | 3.1 | about 81 % (8.1 of 10.0) | P V R |
| saving at this port only % | test v3 | 3.1 | 17 % | P R |
| saving from port blocking % | ablation on test v3, pipeline arm | 3.1, 4.1 | 76–92 % by counting | P R |
| search-only lifts saved vs two-step % | test v3 frontier-alone arm | 3.1 | 39 % blind; 44 % repeat, not blind | P R |
| search-only mean lifts | same | 3.1 | 14.28; 13.59 | P R |
| two-step mean lifts on search-only maps | same | 3.1 | 23.40; 24.19 | P R |
| search-only maps both pass | same | 3.1 | 25; 27 | P R |
| search-only repeat-run saving % | `frontier/RESULTS.md`, or drop if test v3 is the only run quoted | 3.1 | 44 % | P R |
| search-only share of saving at later ports % | same | 3.1 | about 81 % | P R |
| search-only saving from port blocking % | same | 3.1, 4.1 | 76–92 % by counting | P R |
| saving from search % | same | 3.1, 4.1 | at most 4.4 % | P R |
| search-only wall time vs two-step × | same | 3.1 | 9 to 10 | P R |

D222: the pipeline arm runs the standing brief, plus the Proposer if 1.2 keeps it. The saving is software's, and the drafts say so.

### Scale

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---|---|
| largest checked plan boxes | `scale/SCALE-SOLVER.md`, 1.7 | 1.7 | – | P R |
| medium maps passed | same; one cohort only | 1.7 | see note | P R |
| medium maps tried | same; one cohort only | 1.7 | see note | P R |

Note (P2-5): StowLM's planner passed 0 of 18 medium maps, and the 12-weight rule shows 8 of 18 resolved, partial. Those aren't the same claim. 1.7 picks the one cohort these two cells quote.

### Tested against chance (test v3, pre-registered)

| Placeholder | Source | Task | Provisional [development] | In |
|---|---|---|---|---|
| first-plan clear %: fine-tuned | test v3 | 3.1 | 45/55 | P V R T |
| first-plan clear %: random | test v3 | 3.1 | 23/55 | P V R T |
| first-plan clear %: untrained | test v3 | 3.1 | 16 greedy, 23 sampled | P R T |
| first-plan clear %: 12-weight rule | test v3 | 3.1 | 48/55 | P V R T |
| lifts vs random % | test v3 | 3.1 | +0.8 % (19.47 vs 19.31, p 0.77) | P V R T |

### Money (task 1.5)

| Placeholder | Source | Provisional, pre-rebase | In |
|---|---|---|---|
| $ per call per 10 %, low | `docs/impact.md`, rerun | 110 | P V R |
| $ per call per 10 %, mid | same | 578 | P V R |
| $ per call per 10 %, high | same | 2,248 | P V R |
| $ per year one weekly service, mid | same | 60,120 | P |
| $ per year all Laem Chabang calls, mid | same | 5.39 M | P |

`impact.md` still says "StowLM's saving is unknown until C1" and uses the old baseline. Its 500-box unit and all prices remain assumptions.

### Fixed facts, safe as written

Sourced or finished, so these aren't placeholders: 9,325 calls (BSAA 2024–25, p. 6); 104 calls for one weekly service (CNC flyer); $14,333 and $15,700 a day (Contships); over three hours per vessel and 20 minutes with AI (Ningbo, port's own report); a fire about every 60 days (TT Club, 2019); up to 3.5 times over twistlock limit (solver report); mask overrides of 59 % and 3 % on 15 tiny test maps; 12 weights; four kinds of instruction; 12–22 boxes, 2 ports, 2 hatch covers; 120 sealed v1 sentences and their results, apart from the withdrawn parser figure; the [development] numbers in the training story.

### Links and text blanks

`{{VIDEO_URL}}`, `{{REPO_URL}}`, `{{READER_ADAPTER_URL}}`, `{{USER_NAME}}`, `{{CODE_LICENSE}}` (default Apache-2.0), `{{DEMO_GIF_PATH}}`, `{{VERIFIED_READER_COMMAND}}`. All belong to 0.10 or 5.2.
