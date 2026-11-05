# StowLM demo video: script v6 draft 5

**Draft for task 1.13, after the judge's-eye review (`experiments/review/v6/logic4.md`, D267), the reader's adoption (D266) and the results round (`logic6.md`, D268–D273); numbers go in at 4.2.** Wording is bound by `docs/headline.md`. Every `{{name}}` and every shot's needs are in `STORY-NEEDS-v6.md`. The v5 script stays as `script.md`.

**Conditional text:** `[[IF name: …]]` is kept only if that result exists, then the brackets go. Otherwise the whole block is deleted and the text still reads. Names are in `STORY-NEEDS-v6.md`.

- **Target export:** 2:47 (12 shots, 167 s; film-judge edits; p51 cuts dips and merges the end card). **Cap: 171 s.** No conditional block is voiced. Order and durations changed in draft 4, so `stage-shots.js` and `test_stage_director.py` need matching.
- **Arc:** hook → stake → mess in → bad radio line → the reader → x-ray and confirm → the plan changes → drag a box → the copilot → receipt → held-out test → forward pilot.
- **Screen:** ship and UI on screen for all but shots 2 and 11. Cards are one held shot, not a lecture.
- **Words:** for judges who've never heard of stowage. About 2.4 spoken words a second. In voice and captions, say "test voyages" not "maps", "design limit" not "envelope", "extra lifts" not "crane lifts", "locked test" for the reader's sentences; "held-out test voyages" for plans. Gauges are labelled in plain words: stability, balance, draught, hull stress.
- **Captions:** required labels and one number. Statistics live in the README.
- **Voice:** first person. A generated voice is labelled on the end card. The radio voice is always generated and labelled.
- **Lower third, whole film:** "Synthetic ships and cargo, simulated port."

## Shot list

| # | Time | Dur. | Beat | Task | Mode |
|---|---|---:|---|---|---|
| 1 | 0:00–0:10 | 10 s | Cold open | 1.3, 1.7 | recorded |
| 2 | 0:10–0:22 | 12 s | The stake | none | recorded footage |
| 3 | 0:22–0:33 | 11 s | Mess in | 1.3 (1.4 if kept) | recorded answer |
| 4 | 0:33–0:43 | 10 s | Bad radio line | voice path, 2.2 | recorded answer |
| 5 | 0:43–1:01 | 18 s | The reader | 1.2 | recorded answer, read on this laptop |
| 6 | 1:01–1:17 | 16 s | X-ray and confirm | 1.9, 1.2 | recorded answer, real trace; confirm tap live |
| 7 | 1:17–1:31 | 14 s | The plan changes | 1.10, 0.5, 2.2 | stored, or live if earned |
| 8 | 1:31–1:44 | 13 s | Drag a box | 1.7 | recorded verdicts in the film; live in the demo |
| 9 | 1:44–2:00 | 16 s | The copilot | 1.6, 1.10 | recorded answer |
| 10 | 2:00–2:14 | 14 s | Receipt | 1.11 (1.8 if kept) | **live** verify |
| 11 | 2:14–2:23 | 9 s | Held-out test | 3.1 | generated card |
| 12 | 2:23–2:47 | 24 s | Forward pilot | 1.14 | recorded footage |

Sum: 10 + 12 + 11 + 10 + 18 + 16 + 14 + 13 + 16 + 14 + 9 + 24 = 167 s.

## Rubric coverage

| Criterion | Weight | Shots |
|---|---:|---|
| Stage One: Meta models | pass/fail | 3, 4, 5, 6, 9, 10 |
| Impact | 30 % | 2, 3, 10, 11, 12 |
| Use of Meta AI | 25 % | 3, 4, 5, 6, 9 |
| Technical depth | 20 % | 5, 6, 7, 10, 11 |
| Demo | 25 % | 1, 5, 6, 7, 8, 9, 10 |
| Bonus: Best Agentic Use-Case | | 9 |
| Bonus: Open Weight Models | | 5, 6 |
| Bonus: Most Viral | | 1, 8 (the X clip) |

## Shots

### 1 · Cold open · 0:00–0:10 · 10 s

Written to stop a scroll with the sound off: the first frame moves, the words are huge, and it ends on a dare.

- **Task:** cold open (film-judge edit 1), cut from footage already in the film: shot 4's radio clip, shot 5's rule card, shot 7's replan at 3× and its checker ticks, and the hook's red-NO drag. **Mode:** recorded and stored footage, each piece with its own label. The three document slams (TALLY SHEET, EMAIL, RADIO) move to the top of shot 3.
- **Screen:** 0.0–1.8 s: black, a static burst, the radio voice: "Nothing goes into bay nine on this call.", the words typing on full-width. 1.8–3.0 s: the words snap into the rule card `close_bay · 9`, with a green chip "StowLM Reader · fine-tuned Muse Glimmer 30B · on this laptop"; huge word ONE SENTENCE. 3.0–6.5 s: shot 7's replan at 3×, amber boxes lifting out of bay 9 and re-landing, gauges green; THE PLAN CHANGES. 6.5–8.0 s: rule ticks complete; FULL CHECKER: PASSED. 8.0–10.0 s: the hook drag, the stack flares red, **NO.**, cut to black; TRY TO BREAK IT.
- **Voice:** *(Radio, generated voice, in the clear, 0–1.8 s)* "Nothing goes into bay nine on this call." *(VO)* "One sentence, over a noisy, simulated radio line. The plan changes, and the full checker passes it. Now try to break it."
- **On screen:** huge words "ONE SENTENCE.", "THE PLAN CHANGES.", "FULL CHECKER: PASSED", "TRY TO BREAK IT." · labels, each on its own beat: "Generated voice" · "Recorded answer" · "Stored plan, computed on 2 Nov 2026, rechecked now" · "sped up 3×" · "Half-ship section" · "full checker, 54 active rules" · "Recorded verdict" · "Synthetic ships and cargo, simulated port" · "Separate demos". Never "live" or "instant": the plan is stored, and its chip says so.
- **If cut:** with no replan footage, open on the hero's rule card and the red-NO drag, and voice only the first and last sentences.

### 2 · The stake · 0:10–0:22 · 12 s

- **Task:** none; footage and captions. **Mode:** recorded.
- **Screen:** map pin on Laem Chabang for 4 s, then a ship filling box by box, with one box lifted out and put back (a rehandle) under "extra lifts". Footage only, no number on it.
- **Voice:** "My family works in Thai freight forwarding. Every ship at Laem Chabang, Thailand's main port, needs a load plan. A bad one means extra lifts."
- **On screen:** 9,325 container-vessel calls in 2024: BSAA Annual Report 2024–25.
- **If cut:** nothing here depends on a feature.

### 3 · Mess in · 0:22–0:33 · 11 s

- **Task:** 1.3. **Mode:** recorded answer, prefetched. Spark is a cloud call, and it's labelled as one.
- **Screen:** the three documents from shot 1. Software parses the sheets. Spark reads the photo and maps the headers, with each source highlighted. A place where paper and email disagree shows both values. Rows that aren't sure turn amber, and most stop for a question. The planner answers. The rows go green.
- **Voice:** "Muse Spark reads the papers and finds where they disagree. Where it isn't sure it asks, and on hard documents most boxes stop for a person."
- **On screen:** "Synthetic document" · "Recorded answer" · Muse Spark, cloud · 12 sample boxes, another voyage · development: Spark 70.79 % of fields, a parser with no model 36.03 %
- **If cut:** if 1.3 fails, the packet's list comes from the base map: show the clean rows with "Sample load list" and voice only "Where it isn't sure it asks." The intake fine-tune was cut by its gate (D271), so it never appears here.

### 4 · Bad radio line · 0:33–0:43 · 10 s

- **Task:** built voice path, `pipeline/radio/` (D268, D272). **Mode:** recorded answer. One clip of the hero sentence, one frozen take, shown as it came back. Its radio transcript equals the clean one, so the clean-clip branch applies and nothing is staged.
- **Screen:** waveform thrashing in static. The transcript comes up word by word, intact.
- **Voice:** *(Radio, generated voice)* "Nothing goes into bay nine on this call." *(VO)* "Muse Voice Transcribe hears it through the static. The planner sees the words first."
- **On screen:** "Stock text-to-speech voice · simulated radio: band-limited, 12 dB noise" · Muse Voice Transcribe, cloud · Rule fixed before reading · 1 of 8 wordings (7 written + the template) · on this simulated line, 6 of 8 transcripts garbled "bay 9" · selected wording; counts, not a rate (the lower third ends here). Direction, not a caption: row tags read "bay 9 garbled" on those six, "bay number heard" on the other two, never "lost".
- **If cut:** cut order 6. The planner types the sentence. Shot 4 drops to 6 s and its 4 s goes to shot 5. Drop Muse Voice Transcribe from shot 1's "RADIO" hit, and the count of models becomes two.

### 5 · The reader · 0:43–1:01 · 18 s

- **Task:** 1.2 final reader. **Mode:** recorded answer (D296): the take shows "Recorded answer · swa-last-5 · each read once". The reading ran on this laptop with no cloud call; the voice says that and never "Wi-Fi off". Shot 4 is a cloud call, so the no-cloud claim belongs to this shot alone. The timing figures come from `offline-timing.json` (network blocked for the process), so they carry their own label. The adopted reader read the hero sentence live for the package (receipt names muse-glimmer-30b).
- **Screen:** the rule builds beside the sentence, marked "on this laptop", with adapter hash. Two bars slide in: the mask alone, and the fine-tune.
- **Voice:** "StowLM Reader, Muse Glimmer fine-tuned by us, read it on this laptop, with no cloud call. Same model, same laptop: without our fine-tune, 47 percent on a locked test. With it, 92. It missed two stricter gates."
- **On screen:** "Recorded answer · swa-last-5 · each read once" · all 30 prepared sentences in the recording: 23 as expected, 5 misread or refused, 2 with no stored rule to compare (`showcase/reader/recordings.json`) · About 4.1 s a sentence (warm median) · 15.7 GB peak memory · 118 of 128 locked test sentences, not written by real planners · missed: one loosening request accepted, 6 points below Muse Spark · built to refuse anything else; it can slip, so the planner confirms ·
- **If cut:** this can't be cut: a fine-tuned reader is required. The take is recorded, so "Wi-Fi off" is never said or shown; a new take filmed with Wi-Fi really off may say it.

### 6 · X-ray and confirm · 1:01–1:17 · 16 s

- **Task:** 1.9, 1.2. **Mode:** recorded answer, two real traces side by side: untrained Glimmer (`xray/fixtures/before-fine-tuning-trace.json`) and the adopted reader (`real-block-trace.json`, swa-last-5), then the planner's tap. The panel header reads "Sentence", not "planner's sentence": they're synthetic validation rows. If a before-trace exists where the base model reaches for a wrong field or value rather than refusal text, prefer it, chosen by a rule extended in advance, and say "selected" (logic6 R2).
- **Screen:** dark, split. Left, before fine-tuning: the words the model wanted fan out as ghosts, and red strikes land again and again as the mask spells out the fixed output format. Right, after: one ghost, class 5.2, rises highest at 89 %; this ship's list has no class 5.2; it's struck out and 5.1 drops in. The rule looks fine. A hold. Back on the hero sentence, the planner reads the rule and taps confirm.
- **Voice:** "Inside the model as it writes. Before fine-tuning, the mask overrode it on 182 of 200 sentences, almost all on format. After: 2, both sentences it should have refused. Legal, and still wrong. So a person confirms."
- **On screen:** "Recorded answer" · heading "Token “2” blocked: not in the grammar", never read as a count · selected · 200 validation sentences, not the locked test · 182 of 200 before fine-tuning, 2 after · almost all were output format, not ship facts; none was an illegal bay or tier · both of the 2 after were sentences it should have refused · "Before fine-tuning it can't even write the format; the mask spells it out."
- **If cut:** the player refuses a fake trace, and the shot falls back to the card. Then voice "A mask allows only legal rules, real bays and allowed classes. Legal can still be wrong, so a person confirms." and never "overrode" or "blocked".

### 7 · The plan changes · 1:17–1:31 · 14 s

- **Task:** 1.10 lattice, 0.5 binding, 2.2 gauges. **Mode:** stored, with its label. Film with `--no-check-cache`, or the screen reads "rechecked (exact memo)", which is then the label. Call it live only if it was computed on camera; then it's sped up, with its true duration on screen.
- **Screen:** the longest ship shot. The whole planned section, shown as a section of the ship. Boxes lift and re-land. Only the boxes the rule forced out glow. The rest stay dim, labelled "the search also rearranged these". Gauges hold green. Rule ticks fill in.
- **Voice:** "The search software replans {{hero_boxes}} boxes in a half-ship section. That rule forced {{hero_forced_boxes}}; the search moved 48 more. It replans from scratch; keeping changes small isn't built yet. The gauges stay green."
- **On screen:** "Stored plan, computed on {{hero_plan_date}}, rechecked now" · rule box labelled "Confirmed rule" · counter "N of 145 moves", never "crane lifts" · end frame holds at {{hero_forced_boxes}} forced and 48 more · Engine 0.13.0 plan, crane sequence not repaired · all 146 crane frames walked and passed, peak heel {{hero_peak_heel}}° of 2° at frame 109 · full checker, 54 active rules; lashing is our approximation.
- **If cut:** the backup is the 163-box voyage, with `{{hero_backup_sentence}}` and its stored plan, labelled "Stored plan, computed on {{backup_plan_date}}, rechecked now". Shots 4 and 5 then use the backup sentence, and every hero placeholder takes its value. A live replan may replace it, sped up, "{{hero_replan_s}} s, sped up".

### 8 · Drag a box · 1:31–1:44 · 13 s

- **Task:** 1.7. **Mode:** recorded verdicts in the film, prepared on the hero and labelled; live drags in the demo.
- **Screen:** cursor grabs a heavy box and drops it. The fast screen answers. The full check lands, with the deciding cause first: it passes as edited, or the stow is fine but the existing crane order can't reach it, or it breaks a named rule. The named rule lights. A second drag goes green, which shows it isn't just saying no.
- **Voice:** "Now break it. Drag any box anywhere. A fast screen answers in seconds. Then the full checker rules: it passes, the crane order can't reach it, or it breaks a named rule."
- **On screen:** "Fast screen, full check running" · "Recorded verdict" · one box, no replan · "Demo cargo declaration changed for this challenge; no vessel or rule limit tightened" · 25 pinned drags to try: {{replay_url}}.
- **If cut:** cut order 5 changes nothing here. With no replay page, the last caption goes, and the hook's dare points to the repo.

### 9 · The copilot · 1:44–2:00 · 16 s

- **Task:** 1.6, with 1.10's stored plans. **Mode:** recorded answer, described as recorded (D293). Cut to 16 s (film-judge edit 3): the small-section beat is dropped; its caption may stay one second in the rail. The take opens on the hero's stored refusal panel, `lattice/hero-coverage/hero-impossible-plain.json` (D292; proved by arithmetic, no checker or planner called), holds **No.** for 1 s, then shows the delivered take on real tools (`copilot/fixtures/hero-shot10-delivered-report/`), zoomed to the two alternative cards and the full-checker box. The voice never says the copilot was asked the one-tier order.
- **Screen:** the hero's refusal panel: "In every bay, use at most 1 occupied tiers per deck stack." and "143 needed > 96 allowed, 47 short". **NO.** held for 1 s. Then the delivered take, zoomed to the two checked alternative cards and the "Full checker ✓" box. A strip shows Spark's raw text beside what software let through.
- **Voice:** "A one-tier deck limit needs 143 spaces, with 96 allowed, so the answer is no. On real tools, it negotiates two checked alternatives. Spark's raw text gets strict flags, and software holds back every flagged line."
- **On screen:** first beat: "Stored refusal, proved by arithmetic" · hero: in every bay at most 1 tier on deck · 143 needed > 96 allowed, 47 short, in 20-foot spaces. second beat: "Recorded answer · real tools · two scripted planner replies · reader here is cloud Spark" · we don't rank rules on a 12-lift gap from one map · footer: "Enforced by software: no unchecked plan accepted in 42 of 42 final tuned and 15 of 15 fresh development scenarios; 41 of 42 in the original campaign's rescoring", or drop the footer. "it negotiates" stays only if the real-tools beat plays in the take; otherwise take the cut branch.
- **If cut:** with no delivered take on screen, stop at the hero's refusal, drop "On real tools, it negotiates two checked alternatives.", and use the time for a held "No". If 1.6 slips, use the refusal card from `reader/REFUSALS.md` rerecorded through the final reader.

### 10 · Receipt · 2:00–2:14 · 14 s

- **Task:** 1.11 (1.8 inside a block). **Mode:** **live** verify, really filmed; if the frame says "Replay of a measured run", label it that. The hero package was rebuilt with a live on-device reading by the adopted reader. 3.1 was measured on frozen pipeline v3 (a11b4202…). The demo and handover package run on v4 (acf2f275…), which changes only the package and freeze code; engine, reader, checker, binding and runner are byte-identical (manifest diff published). Frozen pipeline v3 (D285): v1 and v2 were retired by the two interruptions, and v3 differs from v2 only in the file guard. The signed package names the frozen pipeline it came from. One gap: the runner's inner record lacks the pipeline hash; the signed package around it carries it (D281). The reading's time on the v3 rebuild isn't re-measured, so no time is quoted.
- **Screen:** the package fans out: half-ship banner, bay plan pages with hatched background bays, crane sequence, a weight table that closes to displacement, note to the master. [[IF thai-note: The note flips to Thai.]] The receipt's reader line names Muse Glimmer 30B and the fine-tune. A terminal runs verify against our published key, each check ticking. One digit changes in `plan.json`. Verify runs again and fails in red.
- **Voice:** "A handover package and a signed receipt, every step tied to its model and verdict. Verify it against our published key, after setup, with no model and no network. Change one digit and it fails."
- **On screen:** half-ship section · weights close to displacement · 3.1 on v3 (a11b4202…); demo and package on v4 (acf2f275…) · the first reader step captioned "reader (software pre-check)" · "live = when the package was built" · "It says what ran and that nothing has changed since. Our own signature." · 54 active rules ran, 8 are off · "Confirmed by: user-authorised batch regeneration, no planner present (demo fixture)"; the planner's tap earlier is the demo's confirmation · the bay plan and the note to the master print the confirmed instruction · [[IF thai-note: Thai note, reviewed for sense · ]]
- **If cut:** 1.8 is a block. If 1.11 slips, show the plan's checker report and drop the receipt sentences, with the time to shot 8.

### 11 · Held-out test · 2:14–2:23 · 9 s

- **Task:** 3.1, final held-out run on the frozen pipeline, manifest v3 a11b4202… (D285), after two interrupted attempts (D282, D284). **Mode:** generated card, from results, held still long enough to read. Values come from `experiments/mvp/video/fill-v6.json` (24 of 24), filled from `sealed/runs/final-3/story-figures.json`. The placeholders stay in this script because the film's fill keys are its names.
- **Screen:** one card. The voyage count fills in. The limits sit on the same card, not in a footnote.
- **Voice:** "On held-out test voyages we never tuned on, {{sealed_envelope_pass}} of {{sealed_envelope_maps}} inside our design limit passed the full checker."
- **On screen:** {{sealed_envelope_pass}} of {{sealed_envelope_maps}} held-out test voyages inside the design limit, all full checker passes · frozen pipeline · the third attempt: two earlier runs stopped on our own file guard and are published · not the pre-registered sealed claim · the planning step, from a clean load list · up to 195 boxes in a half-ship section of feeder ships of 500 to 800 TEU · the engine isn't competitive at large scale yet · no lift comparison: the two-step baseline passed 0 of 24, so there are no mutual passes · Navis and human performance aren't public · corner tag: "3.1 on v3 (a11b4202…); demo and package on v4 (acf2f275…)".
- **If cut:** the held-out test is never cut. If the number is low, the voice keeps its shape and states it, and the card says what failed.

### 12 · Forward pilot · 2:23–2:47 · 24 s

- **Task:** 1.14. **Mode:** recorded footage, projection card.
- **Screen:** the ship at the berth, gauges green, then the map as background with the place name at about 40 % of its old size. Top, huge: "$90 billion a year"; middle, large: "≈ $2.3 billion a year". The word "Projection" stays on screen throughout. The end card comes up under the last 4 s of the voice and holds 3 s after; there is no second credits card.
- **Voice:** "It's for the planners who load feeder ships at ports like Laem Chabang. Port calls cost about ninety billion dollars a year, so every one percent is about a billion. The pool a better plan can act on: about two point three billion a year. That's a projection, not a result. A forward pilot, built to scale: more compute for the search, a larger model for reading."
- **On screen:** "Projection" chip, top left, whole shot · top, huge: "$90 billion a year" · under it: "port-call cost, worldwide · every 1 % ≈ $1 billion" · middle, large: "≈ $2.3 billion a year" · under it: "the pool a better plan can act on · mid case · low $0.3 billion" · fine print, at least 22 px, not monospace: "inputs: 920M TEU, 520k calls, assumed 0.9 % avoidable restows at $75 a lift, 5 % less berth time · what would have to be true: full-ship, multi-port planning works; these rates hold worldwide; the extra work is avoidable; terms that permit operational use; none of it has been measured · check: world terminal revenue $63–76 billion a year" · lower third: "about $40 million a year at Laem Chabang, by share of calls (projection; 9,325 of about 520,000 calls, 1.8 %)" · End card (one card, merged): "StowLM — Models propose. Software decides." · "Three Meta models: Muse Glimmer 30B, fine-tuned by us as StowLM Reader, reads the planner's words on the laptop · Muse Spark reads paper and chooses tools · Muse Voice Transcribe hears the radio" · "Plans by Frontier Engine 0.13.0, our search software. Every plan shown has been through the full checker; a plan that doesn't pass says which rules it breaks." · "Try to break it: {{replay_url}} · Code: {{repo_url}}" (second-largest text on the card) · "Built 26 Oct – 4 Nov 2026 for the Meta hackathon · built for the Thai freight world my family works in" · "Measured on synthetic ships and cargo at a simulated Laem Chabang-style port. Designed for up to 195 boxes in a half-ship section of feeder ships of 500 to 800 TEU, up to 70 % of that section, an empty section on a ship whose other bays hold fixed synthetic cargo, and one loading call. Research pilot, not for use on a real ship." · "Radio voice: stock text-to-speech, generated · [Narration: generated voice, if used] · Music: Sascha Ende, ende.app (CC BY 4.0) · Edit kit adapted from brag (latent-spaces, MIT)"
  *Credit note: "[Narration: generated voice, if used]" is dropped when the user records their own narration (the radio voice stays labelled "generated"). Narration locked after logic15 (plan v7 1.5).*
- **If cut:** never cut. If 1.14 withdraws the figures, keep the first sentence, "That's a projection, not a result" goes with the figures, and the end card stays.

## Trim order, if a timed read runs over

1. Shot 7's "It replans from scratch; keeping changes small isn't built yet.", kept in the README and write-up.
2. Shot 9's small-section sentence: already cut (film-judge round), kept on its caption.
3. Shot 2's "heavy low, dangerous cargo apart": already cut (fit check, 2.4 words/s); the ship filling shows it.
4. Shot 9's "Software proves that by arithmetic": already cut (fit check); its chip says "proved by arithmetic".
5. Shot 9's "On the hero,": already cut (fit check); its chip names the hero.

Never trim: shot 5's "It missed two stricter gates", shot 6's "So a person confirms", shot 7's stored label, shot 11's limits on the card, shot 3's "most boxes stop for a person", or "That's a projection, not a result" in shot 12.

## Branches

Take these at 4.2 from what was measured. No branch may be chosen to flatter a result.

- **Reader time (1.2):** measured: about 4 s a sentence warm and 6.7 s cold for model load plus the first sentence, network blocked for the reader, on a loaded laptop. It plays at speed. Warm the model before the take, or show the cold 6.7 s as it is.
- **Hero sentence (D268, D272):** "Nothing goes into bay nine on this call." Selected by a rule set before reading: the first of 8 wordings read exactly typed and over radio. Of the 8 (7 written and the template), it read 5 typed and 3 radio transcripts exactly and refused the rest; none became a wrong rule. If the live take misreads it, fall back to the template, "Do not load any containers in bay 09.", which also read exactly both ways. Never quote these counts as a rate. Don't train on either.
- **One voyage (D267):** if one sentence's clip, reading, plan and receipt all come from one run, drop "Separate demos" from the hook. The 12-box intake stays captioned "another voyage".
- **Radio clip:** taken: the hero's radio transcript equals its clean one, so the voice says "The planner sees the words first." Nothing is staged.
- **Radio clip comes back unusable:** it's the real path, so the planner fixes more words, or the reader refuses and we show that. The clip isn't re-rolled for a better result; a new clip needs a new frozen take, stated in the shot's notes.
- **Gauges:** "The gauges stay green" holds only while every crane frame is inside limits. If a swapped hero leaves limits mid-plan, say "Every gauge ends green" and show the end state. If any gauge ends outside limits, the plan isn't a hero.
- **Hero re-chosen (D267):** a rule that forces more than one box reads better. Done: the bay-9 hero forces 36 boxes, rearranges 48 more, and is native engine output with no sequence repair; facts in `lattice/hero-facts-close-bay-9.json`.
- **A live replan beats the stored plan:** if a replan fits a take, show it as "Live, {{hero_replan_s}} s, sped up".
- **Meta model count:** "Three": Muse Glimmer 30B, Muse Spark, Muse Voice Transcribe. Two if voice is cut. The yard picture is cut (D257), so SAM and Muse Image are out.
- **Copilot:** "negotiates" and "real tools" stay only over the delivered take with all its labels. If that take falls away, both go.
- **Lifts (3.1, D288):** no lift saving is quotable. Two-step never passed the full checker, so there are no mutual passes. The card says so; no lift percentage appears.
- **Copilot accepts an unchecked plan in any scored scenario:** that's a bug to fix before filming. Software is what prevents it, and it's called "enforced by software".

## Recording notes

- "Wi-Fi off" is said only over a take with Wi-Fi really off. Shot 5 as recorded says "on this laptop, with no cloud call" (D296).
- Film shots 5 and 10 for real. Those two earn the word live. Shot 8 plays recorded verdicts, labelled, unless a live drag take is captured.
- Never state a per-rule cost in lifts, and don't call lift changes noise: on held-out maps two rule types show a cost (D311).
- Say "search software" or "the engine" over a plan. Say "StowLM Reader" for the reading. Credit the engine, never StowLM, for a plan.
- Say "half-ship section" over any medium plan. Don't frame a section as the whole vessel.
- The hero's bay closure is on a medium section; never show a closure on a small section, where every one is impossible. The standing "one port per hatch" preference is never shown as an instruction; a planner saying it as a voyage instruction is the fourth kind, and it binds.
- Shot 3 sends documents to the cloud, so no claim about data staying local goes near it. The intake fine-tune was cut (D271) and stays out of the film.
- Hold shot 11's card still. Don't quote the holdout figure in the film. No capture figure in the film, and never the high figure.
- Close notifications, hide unrelated terminals. No company or vessel names on screen.
