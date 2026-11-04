# Headline contract v6 (task 0.3; leader, 1 Nov 2026; open for veto)

Every story, demo label and README line builds on this. A number in the story comes from task 3.1 or is named here. v5 is at `docs/archive/headline-v5-2026-10-31.md`.

**Product name:** StowLM.

**The sentence:** messy voyage documents and a planner's own words go in, a checked plan and a handover package come out, with a fine-tuned Meta model on the laptop reading every instruction.

## Who does what

| Name on screen | What it is | What it's credited with |
|---|---|---|
| **StowLM Reader** | Fine-tuned Muse Glimmer 30B (LoRA), on device | Reads the planner's words into rules. It's the required fine-tune |
| **StowLM Intake** | Cut (D271). Fine-tuned Muse Glimmer 30B for private intake: 127 of 200 sealed rows against 132 for untrained Glimmer | Not on screen. Write-up only, as a fine-tune that didn't beat its base |
| **Muse Spark** | Meta Model API, cloud | Reads paper and pictures, maps headers, finds where documents disagree, chooses tools in the copilot, writes short gated summaries. Where it only selects and orders checked statements, say exactly that |
| **Muse Voice Transcribe** | Meta Model API, cloud | Turns speech into an editable transcript |
| **SAM 3.1 + Muse Spark** | Meta Model API, cloud. Named only if 1.5 ships | Read a yard picture |
| **Muse Image** | Meta Model API, cloud. Named only if 1.5 ships | Repaints test pictures from our own yard state, so accuracy is measurable. It's a test tool, not a step on real input |
| **Frontier Engine 0.13.0** | Software: search, assignment LP, tier ladder | Plans the ship. Called "search software" or "the engine" |
| **Full checker** | Software, 54 active rules, taken from the receipt. Never "62 rules": 8 catalogue entries are off as run (6 disabled, 2 solver-only; D78, D79) | Decides. Every plan shown has been through it |

**Counting Meta models:** default to "three Meta models" (Glimmer, Spark, Voice). Say "five" only if SAM and Muse Image are both in the shipped demo, and always say what each does; SAM without Muse Image is four; if voice is cut, two. Never "StowLM planned the ship" for an engine plan: the engine is software, and the LLMs read, explain and argue.

## Best plan

The fewest-lift plan Engine 0.13.0 finds in the pipeline's time budget that passes the full checker and obeys every instruction the planner confirmed. If none passes, the pipeline may return a relaxed plan naming every rule it breaks, and that never counts as a pass.

The standing "one port per hatch" sentence is a preference, not a rule: never show it as an instruction, and don't claim it steers Engine 0.13.0 until a task proves it does. A planner who says "one port per hatch" as a voyage instruction is giving the fourth kind of rule, and that binds. With an empty brief, the pipeline plans normally and the reader has nothing to do: say so.

## The envelope, and how to say it

**Designed for:** up to 195 boxes in a half-ship section of feeder ships of 500–800 TEU, up to 70 % of that section, an empty ship on arrival and one loading call, including small and tiny sections. Outside that, the pipeline says so and gives the reason. Medium maps are half the bay pairs: never say "the whole ship".

- Every medium claim names the envelope beside it.
- It's a design limit, stated openly. Say "the engine isn't competitive at large scale yet".
- "Empty ship, one loading call" is an assumption open for the user's veto. Layover and multi-port aren't in the pipeline.

## Lead claims, in order

1. **From mess to a checked plan.** Documents and a sentence go in; a plan the full checker passes and a handover package come out. Show the intake accuracy and the held-out envelope result beside it.
2. **The planner stays in charge.** A planner says a limit in their own words, confirms how it was read, and the plan changes and stays legal. Quote the reader's sealed accuracy, with the fair comparison: same model, same laptop, with and without our fine-tune. Argue for working without a network, never for price.
3. **You can argue with it.** The copilot reads, checks, replans and negotiates through real tools, and can't accept a plan the checker hasn't passed. Drag any box and the checker answers.
4. **Fewer extra crane lifts than a simple two-step planner, inside the envelope.** Only from the final held-out run (D284), only if the registered rule clears (below), always with the map size and with this: most of the saving on small maps is projected later-port lifts (D15). Lifts are extra lifts: productive lifts aren't in the measure, so never say "fewer crane moves".

## Numbers we may quote, and how

- **Held-out envelope cohort (0.6):** the only medium pass rate we claim. 3.1 result (D288): "24 of 24 held-out maps inside the envelope, all full checker passes, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim". Wherever a 3.1 number appears, the partial-run sentence goes with it (logic7 L1): "Two earlier attempts on the same maps stopped early when our own frozen-code guard tripped. In the first, a test rewrote a pinned fixture; in the second, the engine wrote scratch files inside its pinned folder. No planning code changed. They completed 36 and 91 rows; both are published as they are and not pooled with the final run. Where the same rows ran again in the final run, attempt 1 matched the final run on 36 of the 38 rows it opened (status and lifts; 37 of 38 on verdict alone) and attempt 2 on 91 of 91, reported separately." Film-card short form: "third attempt; two earlier runs stopped on our own guard, both published". Never call the final run "the replacement". **No lift saving is quotable.** The simple two-step planner, modelled from one field expert's description, never produced a plan that passed the full checker (0 of 15 tiny, 0 of 30 small, 0 of 24 inside the envelope), so there are no mutual passes and no like-for-like lift comparison. Confirmation in this run is exact expected-rule match, not a human planner. Instruction claim (D258, D291): "Of the 18 voyages whose first plan broke the rule, 13 got a checked plan that obeys it (46.5–90.3 % exact 95 % interval); on the other 6 the first plan already obeyed it, which proves nothing." 18 of 24 is the broader count only.
- **Test v3 tiny and small:** as 3.1 reports them, split by ship: 500–800 TEU apart from 1,700 TEU, which is outside the envelope wording. Never pooled with the envelope cohort.
- **Registered lift rule:** primary measure unweighted `lifts_total`; primary cohort the envelope 24 if it has at least 8 mutual passes, otherwise no medium lift claim; quotable only if the paired 95 % interval excludes zero; current-port lifts always beside it.
- **What each held-out result lets us say:** the table at the end of `experiments/review/v6/logic2.md` binds. A bad result is reported, not reworded. The held-out number is the planning step from a clean load list, so caption it that way beside the mess-to-plan claim.
- **Reader, intake, yard:** each model's own figure, beside its baseline. "Sealed" means a set locked before any tuning and scored once; "held-out" means set aside during development. Use the word that's true. Spark's 126 of 128 is the named comparison for the reader. Reader figures (D266): v3, adopted, 118 of 128 (92.19 %), 1 of 48 loosening accepted; v2 120 of 128, 2 of 48; untrained Glimmer under the same mask 60 of 128 (46.88 %). Show v2 beside v3 wherever the choice is discussed. It passed the 90 % gate and missed two stricter ones (one loosening accepted, none allowed; 6 points below Spark, 3 allowed): say so, and never call it passed. Quote the same-set figures only: tuned against untrained on sealed v3, never v2's old 80.47 % as a baseline.
- **Development figures:** allowed where the shot needs them, always labelled "development", never as the claim. This covers radio, copilot, break-it and verify times.
- **Lay wording:** "test voyages" for maps; "a locked test" only for the reader's sentences; for plans, "held-out test voyages, never used in development".
- **Holdout, 58 of 72:** allowed only in the technical write-up, with all of this beside it: 72 medium maps across three ships at 60–80 % fill, all rechecked, on a set already read once, so not a fresh sealed number. Never in the pitch or the video.
- **Never:** 32 of 32. The envelope was chosen after that read, with no comparator.

Lifts include projected later-port lifts (D15). Medium lift means are over passing maps only, so they aren't comparable across cells.

## The two bars

- **Two-step:** "a simple two-step planner, modelled from one field expert's description". It isn't a measure of how good planners work; experienced planners may already block by port. It's the only comparison on medium maps.
- **Frontier 0.9:** our own earlier engine, a yardstick for search, not a rival. Comparisons need a fresh arm at the same load (D237). Never "industry standard" unqualified, never "beats Navis". Navis and human performance aren't public.

## Impact (D254)

**Lead with potential, as a forward pilot.** This is a pilot built to scale: the path from this envelope to larger ships is more compute for the search, a larger model for reading. Today's pipeline is the proof that the idea is worth scaling. Sourced market sizes are fair game.

- **Say:** "a forward pilot", "built to scale: more compute for the search, a larger model for reading", "if this scales to X, the potential is Y (projection; here's what would have to be true)".
- **The numbers (logic review, D256):** lead with exposure, which nobody can attack: about $90 billion a year of port-call cost, so every 1 % is about $1 billion. Then the pool a better plan can act on: about $7 billion a year (projection; low $1.2B). Never headline the high figure. No capture figure in the video.
- **Show beside every projection:** the word "projection", the inputs, what would have to be true, and the terminal-revenue check beside any global dollar figure.
- **Today's proof, with measured numbers:** a planner's real kinds of input become a legal plan and a handover package inside the envelope; instructions read on the laptop, without a network.
- **Never present potential as a result.** No projected saving written as if achieved. Nothing that implies a ship has been planned for real.

## Labels

| Thing on screen | Label |
|---|---|
| Plan computed earlier | "Stored plan, computed on <date>, rechecked now" |
| Model answer replayed | "Recorded answer" |
| Demo document, picture or voice | "Synthetic document", "Generated picture", "Generated voice" |
| Screen short of a full check | "Fast screen, full check running" |
| Break-it on demo cargo | "Demo cargo declaration changed for this challenge; no vessel or rule limit tightened" (D311) |
| Anything estimated | "Projection" |
| Everything | Synthetic ships and cargo, simulated port |

A live replan is called live only if it was computed on camera. "Looks instant" is fine for the lattice only with its "stored" label showing.

## Relaxed plans (D241, D242, `docs/rule-tiers.md`)

"Passes", "solved" and pass rates mean L0 only, a full checker pass. Relaxed plans are counted separately, by level, and never added to a pass rate.

- **L1 to L3:** "relaxed plan", naming what it gives up and how many boxes stay ashore.
- **L4:** "breaks a class or statutory limit, needs a person's sign-off".
- **L5:** "not safe to sail, shown only to say what would have to change".
- **Completion:** "completed at T1" and so on means every box aboard. A plan that leaves boxes ashore is "partial", with the % aboard beside it.
- Never "no plan is possible" unless proved.

## Say freely

- A fine-tuned Glimmer reads every instruction on the laptop.
- Every plan shown has been through the full checker, and a plan that doesn't pass says which rules it breaks.
- Models propose, software decides.
- We tested a fine-tuned planner against chance, found a 12-weight rule matched it, and moved the fine-tune to reading planners' instructions, where it changes the plan. Keep this short.
- The receipt ties every step to its model, hash and checker verdict. Say "verify it against our published key, after setup, with no model and no network", never just "anyone can verify it".

## Don't say

- "Negotiates", "argues" or "with real tools" for the copilot, unless that take really ran so. Safety that software enforces is called "enforced by software", never a measured model behaviour.

- "Solves real ships", "industry level", "industry grade", "production ready", any full-ship claim.
- "Beats Spark", "beats Navis", "learned sight", "learned physics", "beyond simple rules".
- "Instructions nobody wrote code for", "the job software can't do", "software alone can't read a brief". Say: "it reads free wording of four kinds of instruction and is built to refuse the rest; on the locked test it wrongly made a rule from 4 of 76 such sentences, so the planner confirms every reading". Never "never guessed". Screen version: "built to refuse anything else; it can slip, so the planner confirms".
- "Every plan" as a selling point: lead with instructions, where the reader changes the plan.
- "Fine-tuning cut moves", unless a sealed test shows it.
- "Real-time" or "instant" for a stored plan.
- "Take the model out" test: never quote a model's figure where a no-model parser matches it. Every intake figure sits beside the parser baseline.
- "Empty ship" for a medium map: it's an empty section of a ship whose other bays hold fixed synthetic cargo.
- A per-rule cost in lifts: per-type held-out samples are 2-6 maps and same-map spread is about ±20 lifts, so we don't quote a cost for any one rule. Don't call the changes noise either: on held-out maps closing a bay raised extra lifts by a mean +18.8 (paired 95 % 2.0-35.5) and capping tiers by +13.2 (2.0-24.4), both excluding zero (final-3 REPORT.md; D311).
- "Spark explains" or "Spark writes the note" for a statement Spark only selected.
- "The mask blocked" on a fake or unlabelled trace. Say "illegal bays", never "wrong bays".
- "Private" or "stays on the laptop" for anything sent to Spark. Only the on-device steps earn those words. Private intake isn't claimed: the adapter was cut by its gate (D271).
- "Certified", "approved", "class-approved" for a plan or a receipt. The receipt says what ran and that nothing has changed since. It's our own signature.
- "A crane fails" on small sections: every bay closure on a small map is impossible, an artefact of the section size.
- That the Proposer is in the pipeline, or has skill beyond the 12-weight rule. It's a finding for the write-up.
- "No plan exists", unless proved.
- Reader validation accuracy as if it were sealed: v2 read 91.25 % on validation and 80.47 % sealed.
- "A reader mistake can only over-restrict." The format can't express a relaxation, but a misreading can apply the wrong rule and the checker can't catch that, which is why the planner confirms it.
- "The checker answers in N seconds" for the fast screen. "The whole ship" for a section. "62 rules".
- Lashing as a full check: it's our approximation, with synthetic gear, because class rules aren't public. Say so wherever securing is claimed.

## Plain truth for the write-up

Search, finishing and checking are software on purpose. The LLMs read language, read documents, explain and argue, because that's where they win. A rule the reader can't express is refused, and a plan the checker rejects isn't shown.

## Wording fixed by the fourth review (D267)

- **Mask:** "Before fine-tuning, the mask overrode the model's first choice in 182 of 200 validation sentences. After: 2." Always beside it: "almost all were the output format, not ship facts; none was an illegal bay or tier", and "both of the 2 were sentences it should have refused; what's left is legal and still wrong, which is why a person confirms." Never "blocked", "stops wrong bays" or "stepped in" without that meaning.
- **Copilot:** "Spark's raw text gets strict flags, and software holds back every flagged line." Never "about half of turns" without its denominators, and never "software caught each one": prose errors that raise no flag aren't caught. "Negotiates" only on the real-tools take, labelled with its scripted replies and reader.
- **Hero:** the bay-9 hero is "Engine 0.13.0 plan, crane sequence not repaired; all 146 crane frames walked and passed" wherever frames are claimed (`lattice/hero-facts-close-bay-9.json`; intermediate frames are informational and model no ballast transfers). The no-rule comparison plan was the repaired one; never mix the two. It replans from scratch; keeping changes small isn't built yet.
- **Scale:** "more compute for the search, a larger model for reading".
- **Hook:** the documents, the voice and the plan come from separate demos unless a single run did them all; caption the 12-box intake as such.
- **Terminal revenue:** $63-76 billion, the verified range.
- **End to end (D272):** "On 12 cases (5 sentences across ships), 8 were read exactly and replanned to checked plans; three were one self-correction, refused as unclear; one deck-only limit was read as a whole-bay closure and stopped by a simulated planner holding the answer key." Never "no wrong reading ran", never a rate.
- **Reader timing:** "about 4 s a sentence, 15.7 GB, on a loaded laptop; 6.7 s cold for model load plus the first sentence" (`offline-timing.json`). From process launch, the first reader call comes at about 11 s (`runner/fixtures/cold-start.json`, inference excluded), so state the two apart. Not "about a minute": that file's 53.6 s is the whole 12-sentence run. "Network blocked for the reader" for the measured run; "Wi-Fi off" only on a take filmed so.
- **Hero:** "Selected by a rule set before reading: the first of 8 wordings read exactly typed and over radio. Of the 8, it read 5 typed and 3 radio transcripts exactly and refused the rest." Counts, never a rate. The 8 are 7 written wordings and the template. On screen beside the counts: "selected wording; counts, not a rate".
- **Intake fine-tune:** "We trained a second adapter for private intake. On sealed rows it didn't beat the untrained model, so it didn't ship." D273 allows: "On private intake rows, the open-weights model on the laptop beat the cloud model in this row-level test, and our extra fine-tune didn't help, so it didn't ship", as a pilot direction, labelled potential. The 52 % isn't the shipped Spark intake's accuracy.
- **Reader misses:** logic6's "4 of its 10 sealed misses were rules made from sentences it should have refused" isn't quoted: `sealed-summary.json` and `final-adoption-d266.json` give no split of the misses. D266's "4 of 76" and "1 of 48" stand on their own.

## Wording fixed by the seventh review (D286, `experiments/review/v6/logic7.md`)

- **Plan results:** "held-out test voyages" (lay) or "held-out maps", always with D284's label: "frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim", and the partial-run sentence above. "Sealed" stays only for the reader's sealed v3 (118 of 128) and the intake-adapter rows (D271, D273), each scored once. Never "run once" for plans.
- **Freeze:** "frozen pipeline v3 (D285); v1 and v2 were retired by the two interruptions, and v3 differs from v2 only in the file guard". For the demo and package (D313): "3.1 was measured on frozen pipeline v3 (a11b4202…). The demo and handover package run on v4 (acf2f275…), which changes only the package and freeze code; engine, reader, checker, binding and runner are byte-identical (manifest diff published)." Short form: "3.1 on v3 (a11b4202…); demo and package on v4 (acf2f275…)". 3.1 figures keep citing v3. Never "frozen once". Never "every receipt names the frozen pipeline"; say "The signed package names the frozen pipeline it came from." One gap: "the runner's inner record lacks the pipeline hash; the signed package around it carries it (D281)."
- **Hero package confirmation:** The v4 hero package reads 'Confirmed by: user-authorised batch regeneration, no planner present (demo fixture)'; the user authorised the batch rebuild; no planner confirmed it live. Never call the receipt "planner-confirmed". The bay plan and the note to the master both print the confirmed instruction.
- **Laem Chabang:** "about $125 million a year at Laem Chabang if scaled by its share of calls (projection)". Lifts scale with TEU, and the port's calls skew to feeders.
- **Intake:** the 6-case, 72-box Spark intake set has no run claim. Its lines are dropped until it runs.
- **Pilot:** "Today's pilot runs the loop", never "proves the loop".

## Fresh held-out extension (ext-1, D333)

- **Label, every time:** "fresh held-out extension, run after the freeze; not the pre-registered sealed claim". 24 new envelope maps from new seeds, pre-registered (PREREG sha256 ba3dcc46…), run on manifest v4 (planning code byte-identical to v3). Source: `experiments/mvp/pipeline/sealed/measurements/ext-1/FIGURES.json`; independent recount `experiments/review/v6/code15.md` agrees.
- **Say:** "On 24 fresh held-out maps, the pipeline returned a full-checker pass on all 24 (exact 95 %: 85.8-100 %); the simple two-step planner on 1 (0.1-21.1 %). With one instruction, 18 of 22 voyages whose first plan broke the rule got a checked plan that obeys it (59.7-94.8 %)."
- **Pooled, only as secondary and labelled so:** "Pooled with the first held-out run (secondary figure): 48 of 48 envelope maps passed the full checker (92.6-100 %); binding 31 of 40 (61.5-89.2 %)." Never lead with the pooled figure, and never present it as one experiment.
- **Never:** "sealed" for ext-1; a lift saving; wall times; ext-1 in place of 3.1. 3.1 stays the headline result, and ext-1 is reported beside it. Binding by type is small samples (close_bay 4 of 6, one_port_per_hatch 3 of 5); quote it only with its denominators.
