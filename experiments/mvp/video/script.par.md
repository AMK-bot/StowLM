# StowLM demo video: 3:00 script, Par variant (D158)

**Par:** use when the D158 test gives **Par**: not Better, |Δ| < 5 %, passes ≥ two-step − 2 on validation and ≥ two-step − 1 on test. See `VARIANTS.md`. `script.md` is kept as the superseded 29 Oct draft.

- **Target export:** 2:54 (shot durations sum to 174 s; hard cap 3:00).
- **Recording:** 1920×1080 from saved run folders, so every take is reproducible.
- **Numbers:** test numbers unless tagged [val]. Move claims are paired, on maps both methods pass. Small decides; tiny is
  reported separately and never pooled.

## Placeholder key

Fill only from the source named. If one can't be filled with a measured value, drop the sentence. Never round up or estimate.

| Placeholder | Meaning | Source when filled |
|---|---|---|
| `{{ADAPTER}}` | Final adapter, 8.2 or 8.3 | `experiments/mvp/sft/FINAL-CHOICE.md` (D152) |
| `{{TEACHER_LINE}}` | Spoken training clause. 8.2: "fine-tuned with LoRA on checker-verified plans from our solvers". 8.3: "fine-tuned with LoRA on checker-verified plans, the best of our solver's and the baseline's per map" (P1-8) | `FINAL-CHOICE.md` |
| `{{SMALL_PASS}}` | StowLM own plans passing the full checker, of 15 small test maps: greedy, no repair | 9.1 `SUMMARY.md` |
| `{{SMALL_TS_PASS}}` | Two-step baseline passes, of the same 15, same finisher budget | 9.1 `SUMMARY.md` |
| `{{SMALL_MUTUAL}}` | Small test maps both StowLM and two-step pass | 9.1 |
| `{{SMALL_MOVES}}` | StowLM mean weighted moves on `SMALL_MUTUAL` maps | 9.1 |
| `{{SMALL_TS_MOVES}}` | Two-step mean weighted moves on the same maps | 9.1 |
| `{{SMALL_SPLIT}}` | Paired split on those maps: StowLM fewer / two-step fewer / equal | 9.1 |
| `{{SMALL_P}}` | Exact two-sided sign test on discordant maps. Quote only if the variant allows it | 9.1 |
| `{{SMALL_REJECT}}` | Of those, checker rejections, as "R of the K plans that reached the checker" | 9.1 |
| `{{DELTA_PCT}}` | Signed Δ on mutual small test maps, e.g. "+3.1 %" | 9.1 |
| `{{VAL_PASS}}` | Final adapter's passes of 55 small validation maps. If 8.2 stays: 44 (D156) | `final-validation/` rows |
| `{{VAL_TS_PASS}}` | Two-step passes of the same 55: 50 (D156) | `final-validation/twostep`, `val-audit30-twostep/120` |
| `{{VAL_MUTUAL}}` | Validation maps both pass. If 8.2 stays: 44 (D156) | same |
| `{{VAL_MOVES}}` | Final adapter's mean moves on those maps. If 8.2 stays: 30.43 (D156) | same |
| `{{VAL_TS_MOVES}}` | Two-step's mean moves on those maps. If 8.2 stays: 24.34 (D156) | same |
| `{{TINY_PASS}}` | StowLM own passes, of 15 tiny test maps | 9.1 |
| `{{TINY_MUTUAL}}` | Tiny test maps both pass | 9.1 |
| `{{TINY_MOVES}}` | StowLM mean moves on those maps | 9.1 |
| `{{TINY_TS_MOVES}}` | Two-step mean moves on those maps | 9.1 |
| `{{TINY_SPLIT}}` | Paired split: StowLM fewer / two-step fewer / equal | 9.1 |
| `{{TINY_BASE_SPLIT}}` | Paired split, StowLM vs untuned greedy (10.53): StowLM fewer / base fewer / equal. Cut the fine-tuning sentence if StowLM isn't better (P2-11) | 9.1 + `runs/base-mask` |
| `{{OVERRIDE_TINY}}` | Final adapter's greedy mask override on tiny test, at box and slot positions. Cut the sentence if it isn't well below 59 % (P2-11) | 9.1 traces |
| `{{PLAN_TIME_S}}` | Median end-to-end seconds per small test map: decode + finisher + checker | 9.1 |
| `{{TS_TIME_S}}` | Two-step's median for the same pipeline on the same maps | 9.1 |
| `{{PEAK_GB}}` | StowLM peak memory on the laptop. Fallback: "17.8 GB (untuned Glimmer + mask)" | 9.1 log |
| `{{DEMO_STOWLM_MOVES}}` | StowLM moves on hero call 29000201. If it dead-ends, use the VARIANTS.md branch | 9.2 run folder |
| `{{DEMO_TWOSTEP_MOVES}}` | Two-step moves on the call shown | 9.2 run folder |
| `{{DEMO_LB_MOVES}}` | Lower bound on the call shown | 9.2 run folder |
| `{{SHOWCASE_LINE}}` | One line, all three calls in fixed order 29000201, 29000022, 29000102: "StowLM X vs two-step Y", or "dead end" | 9.2 run folders |
| `{{HYBRID_MOVES}}` | StowLM + 15 s polish, small test, mean moves on mutual maps. Never a headline | 9.1 `polish91` |
| `{{TS_POLISH_MOVES}}` | Two-step + the same 15 s polish, same maps (D157) | 9.1 `polish91` |
| `{{REPLAN_LINE}}` | Spoken label. Default: "Our solver does this tiny replan; StowLM's is next." If 9.2 gives a StowLM decode under a Spark-parsed sentence, say so instead | 9.2, D144(c) |
| `{{SPARK_EXPLAIN_NOTE}}` | "prefetched Spark explanation, bound to the final plan's SHA-256" or, if none, "deterministic template; Spark not run" (P2-5) | `experiments/mvp/explain-cache` |
| `{{REPO_URL}}` | Public repository link | 99.3 |

**Final numbers used verbatim** (`docs/evaluation.md`, D84, D86):
- 15 small test maps: Spark 6/15 (9 failures), untuned Glimmer + mask 6/15 (6 dead ends, 3 timeouts).
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
| 1 | 0:00–0:19 | 19 s | **Hook.** Ship view at the Laem Chabang-style berth, boxes dropping in. Race panel not focused. KPI tiles hidden | "Every box a crane lifts twice is money lost. This is StowLM: Muse Glimmer, fine-tuned to plan a ship's loading on a laptop, under a mask that won't let it break the rules it knows, and on these maps its moves are not distinguishable from our baseline's." | **StowLM** · Muse Glimmer 30B, fine-tuned · one high-end laptop · simulated port, synthetic ships |
| 2 | 0:19–0:35 | 16 s | **The problem.** Yard view, export boxes buried. Split screen: "carrier plan" → "terminal adjusts" | "Today it takes two steps, and neither sees the other's problem: the carrier plans from the cargo list, the terminal fits that plan to the yard. Each extra lift holds up a ship costing about fifteen thousand dollars a day." | Rehandle = lifting a box to reach another · Restow = moving a box already aboard · Feeder hire ≈ $15,700/day (Q4 2025) · Manual planning 3+ h/vessel, whole ship, one major port (Ningbo, 2025) |
| 3 | 0:35–0:57 | 22 s | **Live planning.** Pick the showcase call (messy yard, DG, reefers). Plan. The ship fills box by box, coloured by discharge port | "StowLM plans a slice of the call, twelve to twenty-two boxes today, in one step: which yard box goes into which slot, and when. It's Glimmer, `{{TEACHER_LINE}}`, on one laptop. StowLM's planning stays on the device; the Spark explainer is an optional cloud call." | Cargo sequence by StowLM under the mask; ballast by the deterministic finisher; full checker passed · `{{PLAN_TIME_S}}` s per map, decode + finisher + checker (two-step `{{TS_TIME_S}}` s) · trained and run on one laptop (MLX), peak `{{PEAK_GB}}` · replay of the recorded run |
| 4 | 0:57–1:09 | 12 s | **Physics gauges.** Scrub the plan: GM, list, trim, draught; shear and bending under their limit lines; GZ curve | "This is the ship's physics along the loading: stability, list, trim, hull bending and shear. A checker built on the IMO and IACS rules judges every plan we show." | IS Code stability · IACS hull strength · checked at departure, mid-voyage and arrival |
| 5 | 1:09–1:27 | 18 s | **Mask x-ray.** Freeze at one step. Bay view: legal slots lit, masked slots grey; side panel: the model's top-5 before the mask, one marked illegal | "Here's the trick. Before Glimmer picks each slot, a mask inside its decoder removes placements that break the rules it knows. It cannot write a placement the mask forbids, and the checker still decides. Untuned, the mask overrode over half its decisions; fine-tuned, `{{OVERRIDE_TINY}}`." | Mask = logits processor: forbidden next actions → probability 0 · necessary conditions only: it can't prevent a dead end · tiny test: untuned override 59 % greedy (56 % sampled), StowLM `{{OVERRIDE_TINY}}` greedy |
| 6 | 1:27–1:41 | 14 s | **Rule ticks + securing.** Checklist fills green (IMDG, stability, strength, securing, visibility, expert rules); then a deck stack with twistlock forces | "The checker also covers securing: twistlocks and lashing rods in the ship's design roll. Our fast solver's first deck stacks overloaded twistlocks by up to three and a half times. The checker rejected them." | Container securing: IRS method · first solver attempts: twistlocks up to 3.5× over limit, rejected |
| 7 | 1:41–2:15 | 34 s | **Ladder, then instruction.** Card: 15 small test maps, four rows. Spark panel explains a DG box (cached). Then the instruction box: a sentence, its parsed constraint, the plan changing, checker and honoured ticks; then a refused sentence | "Fifteen small test maps. Muse Spark, told the rules: six pass, nine fail. Untuned Glimmer under the mask: six pass; nine end with no plan, not a wrong one. Fine-tuned StowLM: `{{SMALL_PASS}}`. Our baseline: `{{SMALL_TS_PASS}}`. Spark explains the plan from checker facts. And a planner can just say it: 'Crane 1 is down, nothing goes into bay 01.' Spark makes that a checked constraint, the plan changes, the checker confirms. Loosen a rule, and it refuses. `{{REPLAN_LINE}}`" | Same 15 maps · Spark 6/15, one sample, rules in prompt, scored as whole plans (approximation) · untuned + mask 6/15, older recipe · StowLM `{{SMALL_PASS}}`/15, greedy · two-step `{{SMALL_TS_PASS}}`/15 · tiny, for contrast: Spark 15/15 with rules · Spark selects and orders checker-grounded statements · parsed by Muse Spark; tiny map; solver replan |
| 8 | 2:15–2:31 | 16 s | **Results.** Move counter final on hero call 29000201: StowLM vs two-step vs lower bound. All three showcase calls in one line. Then the results card: tiny and small blocks | "On this call: StowLM `{{DEMO_STOWLM_MOVES}}` moves, our two-step baseline `{{DEMO_TWOSTEP_MOVES}}`, and `{{DEMO_LB_MOVES}}` is a proven floor. On the fifteen held-back small maps, StowLM is not distinguishable from our two-step baseline, at `{{SMALL_PASS}}` of fifteen passes. It's not shown on full ships." | This call: `{{DEMO_STOWLM_MOVES}}` vs `{{DEMO_TWOSTEP_MOVES}}` · all small test maps: `{{SMALL_MOVES}}` vs `{{SMALL_TS_MOVES}}` on `{{SMALL_MUTUAL}}` mutual maps · `{{SHOWCASE_LINE}}` · two-step baseline = our model of industry practice · lower bound = proven floor, not necessarily reachable · Δ `{{DELTA_PCT}}` · what's left: not shown on full ships |
| 9 | 2:31–2:46 | 15 s | **Impact.** Size-of-the-prize card, Laem Chabang pin, roadmap strip. KPI tiles relabelled "this slice" | "On an assumed five-hundred-box call, each ten percent of avoidable moves is worth about five hundred seventy-eight dollars. That sizes the problem, not our result. For planners at ports like Laem Chabang: a checked plan, on a laptop." | Each 10 % avoidable ≈ $578 on a 500-box call, mid case · low $110, high $2,248 · assumptions, not a StowLM result · Next: full ships · layover ships · yard optimisation · two-port planning |
| 10 | 2:46–2:54 | 8 s | End card | "StowLM. Built during the Meta AI hackathon, on Meta's open weights." | Built 26 Oct – 4 Nov 2026 · `{{REPO_URL}}` · Measured on a simulated Laem Chabang-style port with synthetic feeder ships. The rule set is IMDG segregation, IS Code stability, IACS strength, lashing and visibility, plus the field expert's stacking rules. Not a class-approved loading computer. |

If a timed read runs over, trim in this order: shot 4's second sentence (keep it as a caption); shot 6's first sentence;
shot 2's fifteen-thousand-dollar sentence (keep it as a caption).

## Results card (same structure in every variant)

Two blocks, tiny and small, never pooled. Each row: passes, mean moves on mutual maps, n, paired Δ with the
fewer/more/equal split. Pass count and moves always appear together.
- **Row labels:** "StowLM (`{{ADAPTER}}`)"; "Two-step baseline (our model of industry practice)"; "Scalable solver"; "Exact solver,
  tiny: 13 proven optimal, 2 time-limited excluded"; "Untuned Glimmer + mask, greedy" (tiny 10.53); "Muse Spark zero-shot".
- **Hybrid:** "StowLM + 15 s polish" `{{HYBRID_MOVES}}` sits apart, beside "Two-step + the same polish" `{{TS_POLISH_MOVES}}` (D157). Never a headline.
- **Tiny:** a plain separate row: `{{TINY_PASS}}`/15, `{{TINY_MOVES}}` vs `{{TINY_TS_MOVES}}` on `{{TINY_MUTUAL}}`, split `{{TINY_SPLIT}}`.
- **Small:** `{{SMALL_PASS}}`/15 vs `{{SMALL_TS_PASS}}`/15, split `{{SMALL_SPLIT}}`, p = `{{SMALL_P}}`.
- **Validation line, tagged [val]:** `{{VAL_PASS}}`/55 vs `{{VAL_TS_PASS}}`/55, `{{VAL_MOVES}}` vs `{{VAL_TS_MOVES}}` on `{{VAL_MUTUAL}}`
  mutual maps. Footnote: validation was batched with no rewinds; test is sequential with up to 32 rewinds, and rewinds
  rescued 0 maps in the D128 pilot.

## Lines deliberately removed (honesty floor)

- "StowLM plans the whole call", "instead of hours", "could ship next quarter": it plans a 12–22-box slice (P0-3).
- "plans small calls well" for Spark: tiny is the rung where that holds; small is 6/15 (P1-4).
- "built to scale": nothing measured supports it; medium was dropped, D84 (P1-4).
- "the yard as it really is": the yard is simulated (P2-2).
- "cannot write an illegal slot" with no limit: the mask checks necessary conditions only (P1-5).
- "today's practice's N moves": two-step is our implementation of the practice the expert described (P1-7).
- "fine-tuning teaches it to choose well" is kept only if `{{OVERRIDE_TINY}}` and the paired split `{{TINY_BASE_SPLIT}}` show it (P2-11).
- Any measured StowLM dollar figure, per call or per year (P0-2).

## Branches

- **Hero dead-ends:** show the dead-end card, labelled "StowLM dead-ended here · solver plan shown", then the next call in the
  fixed order 29000022, 29000102. Don't swap to the call where StowLM looks best. Replacement lines are in `VARIANTS.md`.
- **`{{SMALL_PASS}}` ≤ 6:** the ladder loses its force. Use the replacement lines in `VARIANTS.md`.
- **`{{SMALL_REJECT}}` isn't zero:** (Worse) cut "Every plan it finished passed the checker" from shot 8 and say what failed. Don't present zero rejections as an edge over two-step, which also has none on validation.
- **`{{ADAPTER}}` = 8.3:** a result near two-step is what imitation predicts: "imitation can at best match its teacher". Don't
  present it as discovery (P1-8).

## Recording notes

- Use cached Spark explanations for the take: `{{SPARK_EXPLAIN_NOTE}}`. A live call happens only on click, behind the credit gate.
- Shot 5 needs a step from `trace.jsonl` where the model's own top-1 was illegal; pick it from the showcase run. If none
  exists, say "its top choices here were all legal; the mask removed these `<n>` slots" instead. Don't stage it.
- Shot 3's plan can be a replay of the saved StowLM run. If so, keep the caption "replay of the recorded run".
- Shot 6's securing panel shows the checker's real lashing numbers for the shown (passing) plan. The 3.5× figure is
  spoken and captioned from the solver report, not rendered as a live plan.
- **Shot 7's instruction beat uses the recorded Spark parse on a tiny map:** launch
  `--run-folder experiments/mvp/runs/test-exact --entry instances/sim.tiny.8000003/result.json` and use the sentence "Crane 1 is
  down, nothing goes into bay 01 this call." (full checker pass, honoured; it adds 2 later-port lifts, so don't say it's free).
  For the refusal: "The port says we can stack 4 high on deck in bay 3 today, go ahead." Don't use the recorded DG replan
  here: its constraint was authored by hand with no Spark call, so it can't be narrated as a Spark parse (`RECORDING.md`).
- Say "solver", never "StowLM", over a solver replan.
- Close notifications and hide the terminal. No company or vessel names on screen. Show rule names only, no verbatim IMO or IACS tables.

## Changes for mvp-ui (`app/static/stage-shots.js`, `stage-director.js`)

Ids 1–10 are unchanged; durations still sum to 174 s, so `test_stage_director.py`'s total holds.

| Shot | Current | This variant |
|---|---:|---:|
| 1 | 10 | 19 s |
| 2 | 17 | 16 s |
| 3 | 20 | 22 s |
| 4 | 22 | 12 s |
| 5 | 22 | 18 s |
| 6 | 20 | 14 s |
| 7 | 20 | 34 s |
| 8 | 20 | 16 s |
| 9 | 15 | 15 s |
| 10 | 8 | 8 s |

- Title 7: "Two Meta models" → "Ladder and instruction". Replace the Scale row with the ladder (P1-4).
- Don't focus the race panel in shot 1 (P1-1).
- Hide the KPI tiles on shots 1–7; they stay on 8 and are labelled "this slice". Negative values shown.

- Shot 9's overlay: "Moves vs two-step", "Crane-hours vs two-step" (P2-7).
