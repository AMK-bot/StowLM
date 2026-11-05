# Honesty rules

`docs/headline.md` binds every word on screen, spoken, in the subtitles and in the share copy. This file collects what applies to the video. When the two disagree, headline.md wins; re-read it before any change, since it moves. `tools/check_honesty.py` enforces the mechanical parts; the rest is on whoever edits.

## 1. Numbers

- Every number shown or spoken has an entry in its block's `numbers[]` in `shots.json` (`text`, `means`, `source`), and the source file holds it. Spelled-out numbers ("two", "two point three billion") count.
- Sources are repo files: `docs/headline.md`, `fill-v6.json`, `story-figures.json`, `film-figures.json`, `hero-facts-close-bay-9.json`, `docs/impact.md` and the like. "I remember it" is not a source.
- Development figures are labelled "development", never shown as the claim.
- Never: "32 of 32", the holdout "58 of 72", a lift saving, a capture figure, the high impact figure, a cost in lifts for obeying a rule.

## 2. Labels (headline.md Labels table, word for word)

| Thing on screen | Label |
|---|---|
| Plan computed earlier | "Stored plan, computed on <date>, rechecked now" |
| Model answer replayed | "Recorded answer" |
| Demo document, picture or voice | "Synthetic document", "Generated picture", "Generated voice" |
| Screen short of a full check | "Fast screen, full check running" |
| Break-it on demo cargo | "Demo cargo declaration changed for this challenge; no vessel or rule limit tightened" |
| Anything estimated | "Projection" |
| Everything | Synthetic ships and cargo, simulated port |

"Looks instant" is fine for the lattice only with its "stored" label showing. A replan is "live" only if it was computed on camera.

## 3. The held-out result (3.1, D284, D286, D288)

Wherever 24 of 24 appears (shot 11, the launch end card, any post), it carries:

> "held-out maps, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim"

and the film-card short form of the partial-run sentence:

> "third attempt; two earlier runs stopped on our own guard, both published"

headline.md's full result: "24 of 24 held-out maps inside the envelope, all full checker passes, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim". Never call the final run "the replacement". Never "sealed" or "run once" for plans; "sealed" is only for the reader's v3 (118 of 128) and the intake-adapter rows. Every envelope claim names the envelope (up to 195 boxes, half-ship section, 500-800 TEU feeders) and "the engine isn't competitive at large scale yet". No lift comparison: "the two-step baseline passed 0 of 24, so there are no mutual passes".

## 4. Music credit

"Music: Sascha Ende, ende.app (CC BY 4.0)" in the video credits, on the launch end card, and in any post or description text (CC BY 4.0 attribution; D298).

## 5. The three Meta models

headline.md: 'default to "three Meta models" (Glimmer, Spark, Voice)'. Each named for what it does:

| Model | What it does in the film |
|---|---|
| Muse Glimmer 30B, fine-tuned by us (LoRA) as StowLM Reader | reads the planner's words into rules, on device |
| Muse Spark, Meta Model API, cloud | reads paper and pictures, finds where documents disagree, chooses tools in the copilot |
| Muse Voice Transcribe, Meta Model API, cloud | turns speech into an editable transcript |

"Private" or "stays on the laptop" only for the reader. Shot 3 and shot 4 are cloud calls and are labelled so.

## 6. Plans are the engine's

headline.md: 'Never "StowLM planned the ship" for an engine plan: the engine is software, and the LLMs read, explain and argue.' Say "search software" or "the engine" (Frontier Engine 0.13.0). The full checker decides: "54 active rules", never "62 rules".

## 7. Don't say (from headline.md, the ones a video reaches)

| Don't | Say instead |
|---|---|
| "industry level", "industry grade", "production ready", "solves real ships" | "a forward pilot", "research pilot, not for use on a real ship" |
| "sealed" for plan results | "held-out test voyages" with the 3.1 label |
| "real-time" or "instant" for a stored plan | "Stored plan, computed on <date>, rechecked now" |
| "certified", "approved", "class-approved" | "It says what ran and that nothing has changed since. Our own signature." |
| "whole ship", "empty ship" for a medium map | "half-ship section" |
| "Wi-Fi off" (no take was filmed so) | "on this laptop, with no cloud call"; "network blocked for the reader" for the timing run |
| "beats Spark", "beats Navis", "learned sight", "beyond simple rules" | "6 points below Muse Spark"; "Navis and human performance aren't public" |
| "62 rules" | "54 active rules" |
| "fewer crane moves", "fine-tuning cut moves" | nothing; no lift saving is quotable |
| "The mask blocked" on an unlabelled trace, "wrong bays" | "illegal bays"; the shot 6 wording with "almost all were the output format" |
| "Spark explains" for a statement Spark only selected | "selects and orders checked statements" |
| "negotiates", "with real tools" | only over the delivered real-tools take (D293) |
| "anyone can verify it" | "Verify it against our published key, after setup, with no model and no network" |
| "never guessed" | "built to refuse anything else; it can slip, so the planner confirms" |
| "no plan is possible", "no plan exists" | only if proved |
| "passed" for the reader's gates | "It missed two stricter gates" |

## 8. Generated voice

The radio voice is stock text-to-speech over a simulated radio line. Shot 4 shows "Generated voice"; the credits say "Radio voice: stock text-to-speech, generated". Any scratch narration (macOS `say`) is named in the credits and watermarked SCRATCH; it never appears in a final.

## 9. New work only

The credits say "Built 26 Oct – 4 Nov 2026". Nothing in the film predates the build window; the brag edit kit is credited as adapted third-party tooling.

## 10. Impact

"Projection" stays on screen through the whole of shot 12. Lead with exposure (about $90 billion a year of port-call cost), then the mid pool (about $2.3 billion, projection), Laem Chabang about $40 million by share of calls (projection). The inputs and "what would have to be true" sit beside them. Never present potential as a result.

## Checklist before any render or post

- [ ] `check_honesty.py` passes on the composition and on the share copy.
- [ ] Every number traced to a source file.
- [ ] Labels from the table above, word for word.
- [ ] 3.1 label and short partial-run form beside every 24 of 24.
- [ ] Music credit present.
- [ ] Three Meta models named for what they do; plans credited to the engine.
- [ ] No "Don't say" wording in voice, captions, chips, credits or share copy.
- [ ] Radio voice labelled generated; no scratch in a final.
