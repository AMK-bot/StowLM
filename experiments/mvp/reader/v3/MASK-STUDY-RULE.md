# Mask study rule (logic review 3, X3 and X4)

Fixed on 1 Nov 2026 about 22:00 EST, before training ended and before any real logits from a final adapter.

**Sentences.** `mask-sample.jsonl`: 200 rows of `data-domain/valid.jsonl`, unedited, seed 199, spread evenly across
families by `fixed_sample`. Its first 80 are the check sample, so these are not fresh selection rows; nothing here
selects an adapter. Never sealed. No sentence or prompt is built to force a block.

**Decoding.** Same as scoring: stored prompt, frozen v3 mask, greedy, 256 tokens, through `RecordingLogitsProcessor`.

**Measure.** The mask changes the top token when the raw full-vocabulary top token is refused. A *real block* has
a raw probability of at least 1 %. Reported: tokens and sentences changed, by reason.

**Decision.** Adapter: the one `select_adapter.py` picked, whether or not adoption passed. With real blocks in 2 or more
of 200 sentences, save the best as `real-block-trace.json`, labelled "selected from 200 sentences". Below that, record
untrained base Glimmer under the same mask on the same 200 and save its best as `before-fine-tuning-trace.json`, labelled
"Glimmer before fine-tuning". The base trace's `adapter_sha256` holds the base config hash, because the format requires
a digest; its notes say so.

**Best.** Complete traces only. `bay_not_on_ship`, then `tier_over_cap`, then `not_in_grammar`; then highest blocked
probability; then lowest row id.

**X4.** On the 84 original `unsupported` rows and the 28 absent-bay rows inside them, through the adapter:
`refused`; `model_real_bay` (names a real bay, no bay block); `mask_real_bay` (names a real bay after the mask refused
a top token as `bay_not_on_ship`); `other_rule`; `unfinished`; `error`. Say "illegal bays", never "wrong bays".
A believable real bay from this count is a misreading the checker can't catch.

**Limits.** Every block shown is selected. Blocks on this row mix don't give a rate for real planners' sentences.
Output: `results-final/mask-study/report.json`.

**Amendment, 2 Nov 2026 about 01:00 EST, after 17 rows were seen.** All 17 showed one "block" at the last step: the answer was
finished, the model's top token was `<|eot|>` at about 100 %, and the mask admits only the other end token. That is one end token
swapped for another, not a forbidden token, so at a stopping step a blocked `<|...|>` token no longer counts as a block. The count
is reported as `end_token_swaps_excluded`. Wanting to stop early, while the answer is unfinished, still counts. No other rule
changed. The first arm's 269 rows are kept and resumed, not rerun.
