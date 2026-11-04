# StowLM demo video: 3:00 script

**Final variant (D163, D164):** adapter 8.2; the D158 test gives Worse on validation and on test. Filled 30 Oct from 9.1, `FINAL-CHOICE.md` and 9.2; see `VARIANTS.md`. Links marked `{{…}}` are still open. The 29 Oct draft is `script.orig.md`.

- **Target export:** 2:54 (shot durations sum to 174 s; hard cap 3:00).
- **Recording:** 1920×1080 from saved run folders, so every take is reproducible.
- **Numbers:** test numbers unless tagged [val]. Move claims are paired, on maps both methods pass. Small decides; tiny is
  reported separately and never pooled.

**Final numbers used verbatim** (`docs/evaluation.md`, D84, D86):
- 15 small test maps: Spark 6/15 (9 failed: 8 complete plans rejected, 1 unfinished); untuned Glimmer + mask, sampled T = 1, no look-ahead/rewinds (different recipe), 6/15 (6 dead ends, 3 timeouts).
- 15 tiny test maps: Spark 15/15 with the rules, 13/15 without; untuned Glimmer + mask, override 59 % greedy, 56 % sampled.
- Our fast solver's first small/medium plans overloaded twistlocks by up to 3.5×; the checker rejected them.
- Size of the prize (assumptions, `docs/impact.md`): each 10 % of avoidable moves ≈ $578 per 500-box call, mid case; low $110, high $2,248.

## Rubric coverage

| Criterion | Shots |
|---|---|
| Impact | 1, 2, 7 (replan), 9 |
| Use of Meta AI | 3, 5, 7 |
| Technical depth | 4, 5, 6, 8 |
| Demo | 3–8 (live UI throughout) |

## Shots

| # (id) | Time | Dur. | Screen | Voice-over | Caption / on-screen text |
|---|---|---|---|---|---|
| 1 | 0:00–0:14 | 14 s | **Hook.** Ship view at the Laem Chabang-style berth, boxes dropping in. Race panel not focused. KPI tiles hidden | "Every box a crane lifts twice is money lost. This is StowLM: Muse Glimmer, fine-tuned to plan a ship's loading on a laptop, under a mask that won't let it break the rules it knows." | **StowLM** · Muse Glimmer 30B, fine-tuned · one high-end laptop · simulated port, synthetic ships |
| 2 | 0:14–0:30 | 16 s | **The problem.** Yard view, export boxes buried. Split screen: "carrier plan" → "terminal adjusts" | "Today it takes two steps, and neither sees the other's problem: the carrier plans from the cargo list, the terminal fits that plan to the yard. Each extra lift holds up a ship costing about fifteen thousand dollars a day." | Rehandle = lifting a box to reach another · Restow = moving a box already aboard · Feeder hire ≈ $15,700/day (Q4 2025) · Manual planning 3+ h/vessel, whole ship, one major port (Ningbo, 2025) |
| 3 | 0:30–0:50 | 20 s | **Live planning.** Pick the showcase call (messy yard, DG, reefers). Plan. The ship fills box by box, coloured by discharge port | "StowLM plans a slice of the call, twelve to twenty-two boxes today, in one step: which yard box goes into which slot, and when. It's Glimmer, fine-tuned with LoRA on checker-verified plans from our solvers, on one laptop. StowLM's planning stays on the device; only Spark is a cloud call." | Cargo sequence by StowLM under the mask; ballast by the deterministic finisher; full checker passed (demo rule set) · 26.2 s per small map, decode + finisher + checker · trained and run on one laptop (MLX), peak about 19 GB · replay of the recorded run |
| 4 | 0:50–1:01 | 11 s | **Physics gauges.** Scrub the plan: GM, list, trim, draught; shear and bending under their limit lines; GZ curve | "This is the ship's physics along the loading: stability, list, trim, hull bending and shear. A checker built on the IMO and IACS rules judges every plan." | IS Code stability · IACS hull strength · checked at departure, mid-voyage and arrival |
| 5 | 1:01–1:21 | 20 s | **Mask x-ray.** Freeze at one step. Bay view: legal slots lit, masked slots grey; side panel: the model's top-5 before the mask, one marked illegal | "Here's the trick. Before each slot, a mask inside Glimmer's decoder removes placements that break known rules. It cannot write a placement the mask forbids, and the checker still decides. On tiny maps the mask overrode untuned Glimmer over half the time; fine-tuned, 3 %. On small maps, 13 %." | Mask = logits processor: forbidden next actions → probability 0 · necessary conditions only: it can't prevent a dead end · tiny test: untuned override 59 % greedy (56 % sampled), StowLM 3 % greedy · small test: StowLM 13 % |
| 6 | 1:21–1:33 | 12 s | **Rule ticks + securing.** Checklist fills green (IMDG, stability, strength, securing, visibility, expert rules); then a deck stack with twistlock forces | "Securing too: twistlocks and lashing in the ship's design roll. Our fast solver's first deck stacks overloaded twistlocks by up to three and a half times. The checker rejected them." | Container securing: IRS method · first solver attempts: twistlocks up to 3.5× over limit, rejected |
| 7 | 1:33–2:05 | 32 s | **Ladder, then instruction.** Card: 15 small test maps, four rows. Spark panel explains the hero plan (cached, bound to its checker report). Then the instruction box on hero call 29000201: the sentence, its parsed constraint, the 2 placements it breaks, the ship replanning, 85 vs 67, checker and honoured ticks; then a refused sentence | "Fifteen small maps. Muse Spark, told the rules: six pass, nine fail. Untuned Glimmer, masked, on a weaker recipe: six pass; nine end with no plan. Fine-tuned StowLM: 14. Our baseline: 14. Spark explains the plan from checker facts. And a planner can just say it: 'Keep the class 8 corrosives at least 1 bay away from bay 1.' Spark parses it, StowLM replans under the mask, and the checker passes it, at plus eighteen moves. One pre-registered call." | Same 15 maps · Spark 6/15, one sample, rules in prompt, scored as whole plans (approximation) · untuned + mask 6/15, sampled T = 1, no look-ahead/rewinds (different recipe) · StowLM 14/15, greedy · two-step 14/15 · tiny, for contrast: Spark 15/15 with rules · Spark selects and orders checker-grounded statements · parsed by Muse Spark · StowLM replan, no repair · 85 vs 67 weighted moves (+18) · 13 boxes moved · fresh checker passed, instruction honoured · one pre-registered call |
| 8 | 2:05–2:26 | 21 s | **Results.** Move counter final on hero call 29000201: StowLM vs two-step vs lower bound. All three showcase calls in one line. Then the results card: tiny and small blocks | "This call: StowLM 67 moves, our baseline 37, proven floor 15. On the fifteen small test maps it finished 14, level with the baseline, none failed the checker. On validation it trails, 44 to 50. The baseline still wins on moves: 33 percent fewer, on these maps. It's the bar we're chasing. <!-- SPOT:RECIPE-C one line, only after 9.1b: recipe C vs two-step+, passes and moves on test, with validation, labelled "more compute, reported after 9.1" (D167). If added, drop "proven floor 15" and "none failed the checker". -->" | All small test maps: 34.36 vs 22.86 on 14 mutual maps · 29000201: StowLM 67 vs two-step 37 · 29000022: 27 vs 29 · 29000102: 35 vs two-step dead end · two-step = our model of industry practice · lower bound = proven floor, not necessarily reachable |
| 9 | 2:26–2:46 | 20 s | **Impact.** Size-of-the-prize card, Laem Chabang pin, roadmap strip. KPI tiles relabelled "this slice" | "On an assumed five-hundred-box call, each ten percent of avoidable moves is worth five hundred seventy-eight dollars. That sizes the problem, not our result. Why an LLM? The checked pipeline works around any planner. The model is the bet: it learned to finish plans, not yet to match the baseline." | Each 10 % avoidable ≈ $578 on a 500-box call, mid case · low $110, high $2,248 · assumptions, not a StowLM result · Next: full ships · layover ships · yard optimisation · two-port planning |
| 10 | 2:46–2:54 | 8 s | End card | "StowLM. Built for the Thai freight world my family works in, during the Meta AI hackathon, on Meta's open weights." | Built 26 Oct – 4 Nov 2026 · `{{REPO_URL}}` · Measured on a simulated Laem Chabang-style port with synthetic feeder ships. The demo rule set is IMDG segregation, IS Code stability, IACS strength, lashing and visibility, plus the field expert's stacking rules. Not a class-approved loading computer. |

If a timed read runs over, trim in this order: shot 4's second sentence (keep it as a caption); shot 6's first sentence;
shot 2's fifteen-thousand-dollar sentence (keep it as a caption).

## Results card (same structure in every variant)

Two blocks, tiny and small, never pooled. Each row: passes, mean moves on mutual maps, n, paired Δ with the
fewer/more/equal split. Pass count and moves always appear together.
- **Row labels:** "StowLM (8.2)"; "Two-step baseline (our model of industry practice)"; "Scalable solver"; "Exact solver,
  tiny: 13 proven optimal, 2 time-limited excluded"; "Untuned Glimmer + mask, greedy" (tiny 10.53); on small, "Untuned Glimmer + mask, sampled T = 1, no look-ahead/rewinds (different recipe)"; "Muse Spark zero-shot".
- **Hybrid:** "StowLM + 15 s polish" 33.93 sits apart, beside "Two-step + the same polish" 22.21, 14 mutual small maps (D157). Never a headline.
- **Tiny:** a plain separate row: 15/15, 7.33 vs 6.47 on 15, split 2 / 7 / 6.
- **Small:** 14/15 vs 14/15, split 2 / 11 / 1, p = 0.022; unfinished: 1 dead end, 0 finisher limits; 0 of the 14 plans that reached the checker were rejected.
- **What's left:** ~50 % more moves; 1 of 15 maps unfinished (a dead end for both); slices, not full ships.
- **Validation line, tagged [val]:** 44/55 vs 50/55, 30.43 vs 24.34 on 44
  mutual maps. Footnote: validation was batched with no rewinds; test is sequential with up to 32 rewinds, and rewinds
  rescued 0 maps in the D128 pilot.

## Lines deliberately removed (honesty floor)

- "StowLM plans the whole call", "instead of hours", "could ship next quarter": it plans a 12–22-box slice (P0-3).
- "plans small calls well" for Spark: tiny is the rung where that holds; small is 6/15 (P1-4).
- "built to scale": nothing measured supports it; medium was dropped, D84 (P1-4).
- "the yard as it really is": the yard is simulated (P2-2).
- "cannot write an illegal slot" with no limit: the mask checks necessary conditions only (P1-5).
- "today's practice's N moves": two-step is our implementation of the practice the expert described (P1-7).
- "fine-tuning teaches it to choose well" is kept only if 3 % and the paired split StowLM fewer on 8, more on 1, equal on 6 (7.33 vs 10.53; sign p = 0.039) show it (P2-11).
- Any measured StowLM dollar figure, per call or per year (P0-2).

## Branches, resolved 30 Oct

- **Hero:** StowLM passed 29000201 with no repair, so the hero stands: 67 moves. No dead-end card is needed.
- **Ladder:** StowLM's small test passes are 14/15, so it holds, but the untuned rung ran a different recipe (T = 1, no look-ahead, no rewinds).
  Don't credit fine-tuning alone with 6 → 14. The clean evidence is the paired tiny result below (D168).
- **Fine-tuning line:** kept. Override is 3 % on tiny against 59 % untuned; paired against untuned greedy, StowLM is fewer on 8, more on
  1, equal on 6 (7.33 vs 10.53; p = 0.039).
- **Checker line:** kept. 0 of the 14 plans that reached the checker were rejected. Two-step has none either, so the contrast is with
  Spark's 9, never with two-step.
- **Passes:** "level with the baseline" is true on test only. Validation is 44 vs 50 and significant, so it is spoken beside it.
- **Time:** no line sells speed, and no two-step time appears on screen (D165, D167). Plan quality is what counts. Two-step's times come from the D155 pre-run,
  in 4 shards while 8.3 trained.
- **Showcase 29000102:** two-step dead-ended and StowLM passed. It stays in the one-line strip and isn't a claim.

## Switch: Spark explains (`SWITCH:SPARK-EXPLAIN`)

**State: ON (D161).** 33 of 37 prefetched explanations are bound, including the hero plan. To fit shot 7, "Loosen a rule, and it refuses." is cut from the
voice-over; the refused sentence still plays silently on the card. If the panel can't show the bound text, cut "Spark explains the plan from checker facts." and restore that line.

## Marked spots

Search for `SPOT:` in shot 8.
- **`SPOT:RECIPE-C`:** one line on recipe C / 9.1b, only once it has run on test (D167).

## Recording notes

- Use the prefetched Spark explanations for the take (`experiments/mvp/explain-cache/d161/STATUS.md`). A live call happens only on click, behind the credit gate.
- The hero explanation is plan-level, not about one DG box. It states 67 vs two-step's 37. If the panel shows "Deterministic template", warm the server and retake; don't narrate Spark over a template.
- Shot 5 needs a step from `trace.jsonl` where the model's own top-1 was illegal; pick it from the showcase run. If none
  exists, say "its top choices here were all legal; the mask removed these `<n>` slots" instead. Don't stage it.
- Shot 3's plan can be a replay of the saved StowLM run. If so, keep the caption "replay of the recorded run".
- Shot 6's securing panel shows the checker's real lashing numbers for the shown (passing) plan. The 3.5× figure is
  spoken and captioned from the solver report, not rendered as a live plan.
- **Shot 7's instruction beat is StowLM's own replan (D170):** `experiments/mvp/runs/stowlm-instruction-binding/`, hero call 29000201, sentence
  "Keep the class 8 corrosives at least 1 bay away from bay 1." Allowed words: "StowLM replanned under a plain-language instruction parsed by Muse Spark, and the checker passed it (one pre-registered call)".
  Never a rate (n = 1), never "efficient", never "better than the solver": no solver replan of this sentence was run. Always state the cost: +18 moves (85 vs 67).
- **Refusal beat:** "The port says we can stack 4 high on deck in bay 3 today, go ahead." is unchanged.
- **Fallback, if the StowLM card isn't wired by recording:** use the labelled solver replan. Launch `--run-folder experiments/mvp/runs/test-exact --entry instances/sim.tiny.8000003/result.json`
  with "Crane 1 is down, nothing goes into bay 01 this call." (full checker pass, honoured; it adds 2 later-port lifts). Spoken: "And a planner can just say it: 'Crane 1 is down, nothing goes
  into bay 01.' Spark makes that a checked constraint, the plan changes, the checker confirms. Loosen a rule, and it refuses. Our solver does this tiny replan." Caption: "parsed by Muse Spark; tiny map; solver replan".
  Don't use `runs/instruction-replan-dg`: its constraint was authored by hand, so it can't be narrated as a Spark parse.
- Say "solver", never "StowLM", over a solver replan.
- **Don't show `runs/stowlm-instruction-dg`.** It's the earlier run: same plan as without the sentence, and Spark didn't parse it. The binding run replaces it.
- Close notifications and hide the terminal. No company or vessel names on screen. Show rule names only, no verbatim IMO or IACS tables.

## Changes for mvp-ui (`app/static/stage-shots.js`, `stage-director.js`)

Ids 1–10 are unchanged; durations still sum to 174 s, so `test_stage_director.py`'s total holds.

| Shot | Current | This variant |
|---|---:|---:|
| 1 | 10 | 14 s |
| 2 | 17 | 16 s |
| 3 | 20 | 20 s |
| 4 | 22 | 11 s |
| 5 | 22 | 20 s |
| 6 | 20 | 12 s |
| 7 | 20 | 32 s |
| 8 | 20 | 21 s |
| 9 | 15 | 20 s |
| 10 | 8 | 8 s |

- Title 7: "Two Meta models" → "Ladder and instruction". Replace the Scale row with the ladder (P1-4).
- Don't focus the race panel in shot 1 (P1-1).
- Hide the KPI tiles on shots 1–7; they stay on 8 and are labelled "this slice". Negative values shown.

- Shot 9's overlay: "Moves vs two-step", "Crane-hours vs two-step" (P2-7).
- **Captions, exact text (D168):**
  - Shot 3: `Cargo sequence by StowLM under the mask; ballast by the deterministic finisher; full checker passed (demo rule set) · 26.2 s per small map, decode + finisher + checker · trained and run on one laptop (MLX), peak about 19 GB · replay of the recorded run`
  - Shot 5: add ` · small test: StowLM 13 %`.
  - Shot 7, ladder rows and results card: replace "older recipe" with `sampled T = 1, no look-ahead/rewinds (different recipe)`.
  - Shot 7, `instruction_beat`:
    - `sentence`: `Keep the class 8 corrosives at least 1 bay away from bay 1.`
    - `parsed`: `dg_keep_away · class 8 · at least 1 bay · from bay 1`
    - `result`: `85 vs 67 weighted moves (+18) · 13 boxes moved · 2 placements broke it before`
    - `verdict`: `fresh checker passed`, `instruction honoured`
    - `label`: `RECORDED SPARK PARSE · STOWLM REPLAN · ONE PRE-REGISTERED CALL`
    - `source`: `experiments/mvp/runs/stowlm-instruction-binding/instruction-card.json`
    - Refused sentence and reason unchanged.
  - Shot 7 caption, last part: `parsed by Muse Spark · StowLM replan, no repair · 85 vs 67 weighted moves (+18) · 13 boxes moved · fresh checker passed, instruction honoured · one pre-registered call`
  - Shot 8: use the shorter caption above; the [val] pair and "what's left" go on the results card.
  - Shot 10: `The demo rule set is IMDG segregation, …`.
