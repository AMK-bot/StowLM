# OOD-1: adopted StowLM Reader on 100 sentences from another model family

**Label: written by a different model after the freeze; synthetic; not real planner text.** One run, as pre-registered
(`PREREG.md`, sha256 `3718f2d7…`; set sha256 in it). Ships: public validation contexts feeder_500, 800 and 1700.
Rows and counts: `results/scores.json`. Raw outputs: `results/*.predictions.jsonl`. GPU run 2 Nov, about 22:03–22:13 EST.

PREREG says it was written "before any model or parser saw the set"; in fact PREREG was written after the blind label check
and before any scored arm ran: file times 22:02:48 for PREREG and for the parser's output, 22:07 and 22:13 for the Glimmer
arms. These are file modification times, not an external timestamp. PREREG stays as hashed.

| Arm | Exact, all 100 | Exact, 66 rule rows | Exact, 34 must-refuse | Wrong rule from must-refuse | Accepted loosening (of 8) |
|---|---|---|---|---|---|
| Adopted reader, swa-last-5 + mask | **86** (77.6–92.1 %) | **61** (83.2–97.5 %) | 25 (55.6–87.1 %) | **6** (6.8–34.5 %) | 0 (0–36.9 %) |
| Untrained Glimmer, same mask and prompt | 45 (35.0–55.3 %) | 18 (17.0–39.6 %) | 27 (62.1–91.3 %) | 2 (0.7–19.7 %) | 0 (0–36.9 %) |
| Fair hand-written parser | 68 (57.9–77.0 %) | 44 (54.0–77.8 %) | 24 (52.5–84.9 %) | 1 (0.1–15.3 %) | 0 (0–36.9 %) |

Exact 95 % (Clopper-Pearson) intervals in brackets. No arm produced malformed output. No arm turned any of the 66 rule
sentences into a different rule, except the parser once (0 of 66 for both Glimmer arms).

## What it shows

On new wording from one other model, on the same ships and four kinds of rule, the fine-tune read 61 of 66 rules
exactly, against 18 untrained and 44 for the parser, with every max_tiers, one_port_per_hatch, loosening and not_an_instruction row right. **Its weak side is
refusing:** it made a rule from 6 of 34 must-refuse sentences, more than the untrained model (2) or the parser (1).
The intervals overlap, so the ordering isn't established, but 6 is the number to quote.

The adopted reader's six wrong rules, all on rows a planner must confirm before anything is planned:
- 069 "Limit bay 13 to two tiers." (no zone): read as hold 2.
- 074 "Keep class 3 well away from bay 9." (no distance): read as 1 bay.
- 077 "Close bays 9 and 11." (two rules): read as close bay 9 only.
- 078 "Max 4 tiers in the hold of bay 9." (above feeder_500's hold cap of 3): read as hold **2**, a value nobody said.
  The mask kept the number legal and the model picked a different one; this is the X4 risk in tier form.
- 082 "Put all heavy boxes low in bay 13.": read as hold 3 in bay 13.
- 084 "Class 3 must be stowed under deck only.": read as hold 4 everywhere, which is no restriction on this ship.

Two of the six change what the planner meant: 077 drops bay 11, which is less restrictive than the order, and 078
invents a cap of 2. A person confirming the reading is what catches these.

Its other misses refused a rule: "bay 09" (004), the typo "baay 11 closd" (009), "bay one-five" by radio (011),
"class five point one" by radio (050) and one Thai DG sentence (053). It read 4 of the 5 Thai-English rows.
It also gave the wrong refusal code on 3 rows (070, 072, 086); a wrong code still refuses.

## Two kinds of "constraint output" (code review 14, P3-2)

The registered safety counts score only schema-valid constraints: malformed output is an exact-match failure and, as
first scored, isn't counted as a rule. A parseable but invalid constraint (say, a bay the ship lacks) is still an
emission, so `results/scores.json` now also reports `raw_constraint_from_must_refuse` and `raw_constraint_from_loosening`,
which count any parseable constraint. The registered metric's definition is unchanged. Re-scoring the 300 recorded
responses, with no GPU, gave identical counts: all 300 are schema-valid, so raw and valid are equal in every arm
(6, 2 and 1 from must-refuse; 0 accepted loosenings). `run_arms.py` now differs from the hash in PREREG for this reason only.

## Limits

- 100 sentences from one author model; the intervals are wide. Not real planner text, not sealed.
- The pre-registration says 6 Thai-English sentences; the set has 5 (013, 032, 053, 064, 094). The set hash binds the rows,
  so no row changed; the count in the prose was wrong.
- The label check was a blind second labeller (100 of 100 agreement), not a domain expert. Both the author and the blind
  second labeller are Claude models, so their agreement can share blind spots.
