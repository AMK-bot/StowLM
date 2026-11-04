# Intake adapter gates — fixed before training

Task 1.4, preparation only. Registration date: 2026-11-01.

The only scored population is the 200 synthetic fragments in `sealed/sealed.jsonl`,
written once by `build_data.py` and SHA-256 bound in `data/manifest.json`. No sealed
fragment or label is opened during preparation, tokenizer checks, or training.
Freeze this file, recipe, training subset, prompt, decoder and adapter before the
single scoring run. All three arms see identical inputs, ship/voyage context,
JSON language, vocabulary and output-token cap. Glimmer arms use the same local
logits mask; Spark uses the same exported JSON Schema at the API. The mask never
receives text, target values, a manifest row, or an expected answer.

| Gate | Fixed requirement |
|---|---|
| I1 | Tuned row-exact accuracy >=90%: at least 180/200 |
| I2 | Zero silent DG errors: on a gold DG row, an emitted row drops DG or changes its declared class (or UN number) |
| I3 | Tuned exact accuracy at least 10 percentage points above bare, untrained Glimmer: at least 20 more correct rows |
| I4 | Tuned accuracy no more than 5 percentage points below Spark: no more than 10 fewer correct rows |

Row exact means every registered field equals gold after JSON parsing (numeric
JSON equivalents compare equal). Refusals must match their code exactly. Missing,
malformed, extra-key, duplicate-key, truncated and context-invalid answers score
wrong; no repair, guessed defaults, repeated attempts or best-of selection.
A refusal on a DG row is wrong for I1 but does not silently accept dangerous
cargo. Wrong UN numbers also fail I2, a conservative addition to the stated bar.

Reports include every arm and gate whatever the outcome. Missing arms or incomplete
artifacts cannot pass. Any failed gate, or missing complete result by the pipeline
freeze (2026-11-02 21:00 America/New_York), cuts the adapter and leaves Spark intake
as the fallback. No quality or private-intake claim is made from preparation.
The 900-row seeded training subset and 45-minute budget are fixed in config.json;
the remaining training corpus is prepared for audit, never selected using sealed.
