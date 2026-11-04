# Story needs v6 (task 1.13, for 2.2 and 4.2)

Updated after logic reviews 1 and 3 (`experiments/review/v6/`) and the demo as built (`experiments/mvp/ui/STATUS.md`, "Task 2.2 v6").

What the v6 drafts need from the demo and from measurement. Drafts: `experiments/mvp/pitch-v6.md` (P), `experiments/mvp/video/script-v6.md` (V), `experiments/mvp/README-v6-draft.md` (R), `experiments/mvp/writeup/story-v6.md` (W), `experiments/mvp/social/x-post-v6.md` (X). Wording is bound by `docs/headline.md`. v5 files stay as they are, and nothing reads these drafts.

## Two kinds of blank

- **`{{name}}`:** always filled from the source below. No draft ships with one left.
- **`[[IF name: …]]`:** kept only if that result exists, then the brackets go. Otherwise delete the whole block, including its placeholders. Table rows in a block go with their row.

| Name | Keep when | If not |
|---|---|---|
| `thai-note` | 1.8 ships Thai output, reviewed for sense | Delete; no time changes |
| `real-planner` | Deleted (D311): no real-planner data exists | Blocks removed from every draft; one can return if task 0.8 data arrives |

## Risks to settle first

1. **Fine-tune in the hero path: done.** The hero package was rebuilt with a live on-device reading (3.1 on v3 (a11b4202…); demo and package on v4 (acf2f275…)); confirmation was a user-authorised batch rebuild; reading time not re-measured. Known gap D281: the runner's inner receipt lacks the pipeline hash; the outer signed package carries it.
2. **Film is four demos (F2).** The hook says "Watch each one get checked" and carries "Separate demos" until one run does them all. Intake stays 12 sample boxes on another voyage.
3. **Radio clip of the hero sentence exists** (`pipeline/radio/`). Its radio transcript equals the clean one, so shot 4 takes the clean-clip branch.
4. **Hero may change (D267).** A rule forcing more than one box is wanted; `max_tiers-14` is a candidate. Compare the 15 stored plans' hashes before the demo offers them as alternatives: 14 show exactly 70 lifts.
5. **Hero sentence is fixed (D272):** "Nothing goes into bay nine on this call." Selected by a rule set before reading: the first of 8 wordings read exactly typed and over radio. Of the 8 (7 written and the template), it read 5 typed and 3 radio transcripts exactly and refused the rest; none became a wrong rule. Template fallback: "Do not load any containers in bay 09." The plan, forced count and heel for this rule still need filling from its stored plan.
6. **X-ray (logic6 R2).** Shot 6 plays untrained Glimmer's real trace beside the adopted reader's. The before-trace's blocks are all inside fixed refusal text; prefer one where the base model reaches for a wrong field or value, chosen by a rule extended in advance. Always with the caveats: output format, no illegal bay or tier; the 2 after were sentences it should have refused.
7. **Copilot.** Shot 9's first beat is the stored refusal `lattice/hero-coverage/hero-impossible-plain.json` (D292): "In every bay, at most one tier on deck", 143 needed > 96 allowed, 47 short, by arithmetic, no checker called. The film needs the delivered take on screen; it isn't yet. It has two scripted replies, and its reader is cloud Spark. A loosely worded summary passed the gate on that take, so "software catches everything" stays unsaid. 7 of 8 frozen development variants replaces 38 of 42; if the old figure returns, 26 of 42 sits beside it.
8. **Spark's intake is a cloud call.** "A fine-tuned Meta model on the laptop reads every instruction" means instructions, not documents.
9. **3.1 done (D288, D289).** Filled from `sealed/runs/final-3/story-figures.json`: envelope 24 of 24, tiny 15 of 15, small 29 of 30, instruction arm 18 of 24; two-step 0 everywhere, 1 of 24 at 300 s. No lift saving is quotable. The film reads `fill-v6.json`; p22 regenerates `stage-fillv6.js` from it. p22's film shows a pooled overlap of 127 of 129; the drafts report 36 of 38 and 91 of 91 separately.
10. **Spoken figures.** 47 and 92 are rounded aloud, with 118 of 128 on screen.
11. **Three Meta models.** Two if voice is cut.
12. **Terminal-revenue range:** $63–76 billion, from analyst estimates. Open them before they go on screen.
13. **No hosted demo.** F12's static replay page of 25 pinned drags needs building and hosting. Hosting it publicly is the user's call. `{{replay_url}}` goes if it isn't hosted.
14. **Laem Chabang line is in,** from `docs/impact.md`: $125 million a year of the mid-case pool, a projection on the same global inputs. It assumes an average call, which the port's feeder mix may not match.
15. **Active rule count is 54, from the receipt:** 8 off as run. Stability during cargo work is among them, and it's T1.
16. **Intake is development only (60 boxes, five hard cases); the 6-case set has no run claim.** Every intake figure sits beside the parser.
17. **Reader timing is measured** (`offline-timing.json`): 4.1 s warm median, 6.7 s cold for model load plus the first sentence, 15.7 GB, loaded laptop, network blocked for the reader. Launch to first reader call: 10.96 s median (`runner/fixtures/cold-start.json`, task 2.3), inference excluded. No single run measures launch to first answer. logic6's "about a minute" read `startup_to_first_answer_s` as 53.6 s; the file now says 53.6 s is the whole 12-sentence run, so it isn't used.
17a. **"Wi-Fi off" means Wi-Fi off.** The timing run blocks the process's network; it doesn't turn Wi-Fi off. Drafts say what was done beside `reader_seconds`, and shot 5 must really be filmed with Wi-Fi off, or say "network blocked for this process".
18. **Film order and durations changed.** `stage-shots.js` and `test_stage_director.py` need the 12 shots. Shot 8's planner finding is one clause in shot 5, and stays in full in the pitch, README and write-up.
19. **Break-it audit is off the demo plan.** 0 of 40 is on one other plan. The film's drags are recorded verdicts. Under-2-second fast screen is unmet, so the voice says "in seconds". The hook's own drag shows 7.04 s.
20. **Bindings count.** `binding_dev_*` needs defining before it's filled; the review points to 21 of 32 rows passed and 13 binding at a 900 s deadline.
21. **Label conflict.** Headline's "limits tightened" label is kept, but the sandbox says no limit was tightened. The disclosure is in the pitch, README and write-up, and off the film caption by F11. The headline label should change.

## Calls made in these drafts

| Call | Why |
|---|---|
| Opening is the mess, then a dare | Spine's order, and "try to break it" is the rubric's "holds up" put as a challenge. Works muted |
| Shot 2's voice is the review's replacement; family line once there and once on the end card | It's the only sourced fact: `docs/pitch.md` and CLAUDE.md. No anecdote, no named relative |
| Planner finding is one clause in shot 5 | D267. It stays in full in the pitch, README and write-up |
| Chance numbers are development figures, literal | 1.12 drops the planner, chance and fitted-rule arms, so v5's `first-plan clear %` placeholders can't be filled from a sealed run |
| Spark and parser figures on sealed v3 are literal | 126 of 128 and 90 of 128 are finished (D225); headline names the first. The reader's cell stays blank |
| The hand-written parser is back in the tables | v5 dropped a strawman. D225's is real effort, and showing it is the honest size of the gain |
| Holdout appears only in the write-up, with all its caveats | Headline. The earlier in-envelope count appears nowhere |
| v5's scope line rewritten as "research pilot, not for use on a real ship" | Says the same with no banned word |
| Lifts against two-step sits on shot 11's card and in a block, never in the voice | Lead claim 4 is last; the film spends its seconds on the wow moments |
| Hire figure is on no film caption | It's on ships outside our design limit, so it stays in the pitch and README with that note |
| Film at 2:51, capped at 171 s, no block voiced | Leader's order. Shot 12 got the review's sentence whole |
| Two shots are truly live: 5, 10 | Shot 8 plays recorded verdicts in the film, as built. Everything else carries its label |
| 12 shots in 171 s | Shot 8 cut; reader and copilot got the seconds, and the x-ray's retelling took two from hook-side shots |
| Reader figures are literal | Final, by D266. Only v3's training and timing cells stay blank |
| Film captions cut to required labels and one number | F11. Statistics moved to the README |
| "Negotiates" and "real tools" return, only over the delivered take with its labels | F7 and headline |
| Stale v5 needs dropped | Below |

**Retired from v5:** feasible-cohort and brief-map cells (replaced by the voyage instruction block in 1.12), binding replan counts in the story, search-only yardstick cells, scale cells (replaced by the envelope), first-plan clear rates as placeholders, `reader-mask` and `fair-parser` blocks (both now settled: mask is in v3, parser scored), reader Spark paraphrase rows. **Cut in v6:** `yard-picture` blocks (D257); `intake-adapter` blocks, as the fine-tune was cut by its gate (D271).

## Shots the demo must provide (for 2.2)

`test_stage_director.py` and `stage-shots.js` hold the old shots and need matching.

| Shot | Dur. | Needs | Mode | Label on screen | Depends on | If cut |
|---|---:|---|---|---|---|---|
| 1 Hook | 9 s | Three document hits, full-bleed type, a drag that flares red | recorded | "Synthetic document", "Recorded", "Separate demos" | 1.3, 1.7 | Refusal card; typed note |
| 2 Stake | 12 s | Pin, simulated yard, tally sheet beside a filling ship | recorded | Source caption | Exists in part | – |
| 3 Mess in | 11 s | **Sheets parsed, photo read, a conflict with both values, amber rows stopping for questions, answered** | recorded answer | "Synthetic document", "Recorded answer", "12 sample boxes, another voyage" | 1.3 | Sample load list |
| 4 Radio | 10 s | Waveform, transcript word by word of the hero clip, intact | recorded answer | "Generated voice, simulated radio" | Voice path, 2.2 | Typed; 4 s to shot 5 |
| 5 Reader | 18 s | **Adopted reader reading the hero sentence, rule building, two bars** | recorded answer | "Recorded answer · swa-last-5", "On this laptop, no cloud call" | 1.2, 4.3 | Recorded take, never cut |
| 6 X-ray and confirm | 16 s | **Two real traces split screen: untrained Glimmer with the mask spelling out the format, the adopted reader with class 5.2 struck out; a hold; confirm on the hero** | recorded answer | "Recorded answer", header "Sentence" | 1.9, 1.2 | Card |
| 7 Plan changes | 14 s | **Whole planned section, only forced boxes glowing, the rest labelled, plain-word gauges, rule ticks** | stored, or live if earned | "Stored plan, computed on <date>, rechecked now", repair note | 1.10, 0.5, 2.2 | Backup voyage |
| 8 Drag | 13 s | **Recorded verdicts on the hero: fast screen, three outcomes with the deciding cause first, a legal drag** | recorded verdicts | "Fast screen, full check running", "Recorded verdict", "Demo cargo and limits tightened for this challenge" | 1.7 | Drop the replay link |
| 9 Copilot | 21 s | **"No" beat on test tools, then the delivered take's ribbon, Spark's raw text beside what software let through** | recorded answer | Labels for each beat | 1.6, 1.10 | Stop at the arithmetic |
| 10 Receipt | 14 s | Regenerated hero package, half-ship banner, weight table, reader line naming the fine-tune, verify filmed live, one digit changed, verify fails | live | "Our own signature" | 1.11, 1.2 | Checker report |
| 11 Locked test | 9 s | One held card | generated | Limits on the card | 3.1 | Never cut |
| 12 Close | 24 s | Pull-back from ship to lanes, "Projection" held with inputs, conditions and the revenue check, end card | recorded | "Projection", scope line verbatim, generated voice | 1.14, 0.7 | First and last lines |
| X clip | 15 s | 1080×1920 crops of shots 8, 3, 5, 7 | recorded | As in `x-post-v6.md` | 2.2 | – |

## Placeholders

Provisional values are for orientation only: **don't publish them from here.** A dash means none exists yet.

### Reader

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---:|---|
| `untrained_loosening_accepted` | same | 0.4, 1.2 | FILLED 0 | P R W |
| `reader_silent_wrong` | sealed rows: a wrong rule emitted | 0.4, 1.2 | FILLED not split in the sealed summary; dropped | P R W |
| `reader_safe_refusals` | sealed rows: refusal of a valid sentence | 0.4, 1.2 | FILLED not split; dropped | P R W |
| `reader_adapter_mb` | final adapter file | 1.2 | FILLED 64.5 | P R W |
| `reader_train_rows` | v3 data manifest | 1.2 | FILLED 4,121 | P R W |
| `reader_train_hours` | finish run log | 0.4 | FILLED finish run 11,596 s; first run not recorded | W |
| `real_planner_sentences` | frozen expert set | 0.8 | – | P V R W |
| `real_planner_exact` | one scored run | 0.8 | – | P V R W |
| `field_expert_quote` | the expert's one line, verbatim, no name or company | 0.8 | – | P V R |

### Intake, yard, voice

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---:|---|
| `radio_clips` | radio cohort, rerun through the final reader | 3.2 | FILLED 8 (hero wordings) | P R W |
| `radio_exact` | same, no correction | 3.2 | FILLED 3 | P R W |
| `radio_refusals` | same | 3.2 | FILLED every other one (file note) | P R W |
| `radio_wrong_rules` | same | 3.2 | FILLED 0 | P R W |

### Hero voyage

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---:|---|
| `hero_backup_sentence` | the 163-box voyage's stored rule | 1.10 | – | V |
| `backup_plan_date` | 163-box stored plan | 1.10 | – | V |
| `hero_boxes` | hero voyage | 1.10 | 87 | V W |
| `hero_lifts_before` | hero run | 3.2 | FILLED 66 | W |
| `hero_lifts_after` | hero run | 3.2 | FILLED 82 | W |
| `hero_forced_boxes` | boxes the rule forced out; same counting as the demo | 1.10, 2.2 | FILLED 36 | V W |
| `hero_rearranged_boxes` | placements changed, less forced | 1.10, 2.2 | FILLED 48 | W |
| `binding_dev_passes` | `binding/measurements/live-fixed-20261102T0220/RESULTS.md`, at the freeze | 0.5 | FILLED per type, deadline-live RESULTS.md | R W |
| `binding_dev_cases` | same | 0.5 | FILLED per type, no total | R W |
| `hero_peak_heel` | repaired sequence, largest absolute heel | 1.10 | FILLED 1.64 (frame 109, not repaired) | V W |
| `replay_url` | static replay page of 25 pinned drags | 2.2, user for hosting | – | P V R X |
| `hero_plan_date` | stored plan's stamp | 3.2 | FILLED 2 Nov 2026 | V W X |
| `hero_replan_s` | a replan off the lattice, on a quiet machine | 0.5, 4.3 | FILLED batch 1,159 (lattice/progress.json); live only if on camera | V W |

### Break-it, copilot, package

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---:|---|
| `verify_seconds` | `verify_package` on the hero | 1.11 | FILLED 9.87 (tamper 3.69) | P W |

### Held-out test (D284: frozen pipeline v3, after two interrupted attempts; not the pre-registered sealed claim)

| Placeholder | Source | Task | Provisional | In |
|---|---|---|---:|---|
| `interrupted_2_rows` | completed rows in `runs/interrupted-2`, filled by the leader | 3.1 | FILLED 91 (D288) | P R W |
| `overlap_same` | D282's published reproducibility comparison | 3.1 | FILLED filled separately: 36 of 38 (attempt 1), 91 of 91 (attempt 2) (D288) | P R W |
| `overlap_rows` | same | 3.1 | FILLED see overlap_same (D288) | P R W |
| `sealed_envelope_pass` | held-out envelope cohort, final run | 3.1 | FILLED 24 (D288) | P V R W X |
| `sealed_envelope_maps` | same; 24 by PREREG | 3.1 | FILLED 24 (D288) | P V R W X |
| `tiny_pass` | test v3 | 3.1 | FILLED 15 (D288) | P R W |
| `tiny_maps` | test v3 | 3.1 | FILLED 15 (D288) | P R W |
| `small_pass` | test v3 | 3.1 | FILLED 29 (D288) | P R W |
| `small_maps` | test v3 | 3.1 | FILLED 30 (D288) | P R W |
| `instruction_obeyed` | voyage instruction block | 1.12, 3.1 | FILLED 18 (D288) | P R W |
| `instruction_maps` | same | 1.12, 3.1 | FILLED 24 (D288) | P R W |
| `relaxed_summary` | one line, by level, or "none" | 3.1 | FILLED filled in text (D288) | P R W |

### Impact

No placeholders: the review's values are written in. `docs/impact.md` confirms them at 1.14, and the drafts' `sizing_*` cells are deleted because they sat on a 500-box call outside the design limit.

### Text and links

| Placeholder | Source | Task | Default | In |
|---|---|---|---:|---|
| `meta_model_count` | by `docs/headline.md`: Three, or Two if voice is cut | 2.1 | Three | P V |
| `video_url` | final upload | 5.1 | – | R X |
| `repo_url` | public repo | 5.2 | – | V X |
| `demo_gif_path` | shot 8 crop | 2.2 | – | R |
| `reader_adapter_url` | published adapter | 5.2 | Hugging Face | R |
| `user_name` | form field | 0.7 | – | R |
| `code_license` | | 0.7 | Apache-2.0 | R |
| `verified_pipeline_command` | tested from a fresh copy | 1.1, 5.2 | – | R |
| `verified_reader_command` | same | 1.2, 5.2 | – | R |
| `verified_package_command` | same | 1.11, 5.2 | – | R |

## Fixed facts, safe as written

Sourced or finished: 9,325 calls (BSAA 2024–25, p. 6); $14,333 and $15,700 a day, ships of 900–2,000 TEU (Contships); over three hours and 20 minutes (Ningbo, port's own report); a fire about every 60 days (TT Club, 2019); 54 active rules, 8 off as run, 6 disabled and 2 solver-only (receipt); reader v3 118 of 128, 92.19 %, 1 of 48; v2 120 of 128, 2 of 48; untrained 60 of 128, 46.88 %; 4 against 6 of 76 refusal sentences; 4 against 27 of 84 out-of-scope [validation]; Spark 126 of 128, 0 of 48; parser 90 of 128, 1 of 48; mask 2 of 200 sentences, 2 of 6,980 words, 1 of 46 [validation]; fast screen 5.4 s median, full check 34.8 s median, 42.2 s p95, under load, 0 of 40 fresh frozen clears overturned, on one other plan [development]; copilot 7 of 8 frozen variants, flags 32 of 60 and 2 of 12, 26 of 42 goals [development]; intake 70.79 % vs 36.03 %, 18 vs 0 of 60, 12 vs 4 of 15, 70 % vs 100 %, spent recordings [development]; first held-out intake score, 52 of 251 fields; envelope wording; about $7 billion, low $1.2 billion, about $90 billion, about $125 million at Laem Chabang, the hero sentence's counts (5 of 8 typed, 3 of 8 radio, 6 of 8 transcripts garbled) [demo counts]; mask 182 and 2 of 200 sentences, 1,259 of 7,346 and 2 of 6,980 tokens, exact 90 vs 183 of 200 [validation]; 12 end-to-end cases, 5 sentences, 8 exact, 3 refused, 1 stopped by a simulated planner [development]; reader 4.1 s warm, 6.7 s cold, 15.7 GB; first reader call 10.96 s from launch; intake fine-tune 127 vs 132 of 200, Spark 104 with 1 silent DG error [sealed rows]; $350 million at 5 % capture, $63–76 billion and their inputs; four kinds of instruction; mask overrides of 59 % on 15 tiny maps and 3.8 % in a tiny pilot; 12 weights; 45, 48 and 23 of 55, and 19.47 vs 19.31, p = 0.77 [development]; 0 of 18 medium maps for the planner; reader v1's and v2's own sealed results; the write-up's holdout figure, with its caveats beside it.
