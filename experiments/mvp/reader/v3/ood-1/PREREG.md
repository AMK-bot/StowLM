# OOD-1 pre-registration: adopted StowLM Reader on sentences from another model family

Written 2 Nov 2026 before any model or parser saw the set.

**Label:** written by a different model after the freeze; synthetic; not real planner text.

## Set

`set.jsonl`, 100 rows, sha256 `21e975a5bbc509c74d7bbf986597ed3a27ac4ba61a6887ab5cfb032b5b0a0f67`. Written by Claude (Opus 5.5) for this test after the reader freeze; the
training paraphrases were written by Muse Spark. Ships: the public validation contexts feeder_500, feeder_800 and
feeder_1700. 66 rows carry a rule: close_bay 19, max_tiers 19, dg_keep_away 18, one_port_per_hatch 10. 34 must be
refused: ambiguous 8, unsupported 12, loosening 8, not_an_instruction 6. Registers: terse, radio, polite and email
style, typos, numbers as words, zero-padded bays, IMDG names, hedging, and 6 Thai-English sentences.

Labels: written by the author and validated with `reader/schema.canonicalize` on each ship. A second labeller, a fresh
agent that saw only the sentence, ship and the reader's schema prompt, labelled all 100 blind
(`label-check/independent.jsonl`, sha256 `c92de3916f180576f9b40d03b145426e1c911c56c8027e0f394c76dfc4fa752f`). **Agreement 100 of 100; nothing to resolve.** One row it marked low
confidence (ood1-028, "Hold of bay 9 - nothing below deck there.": hold tiers 0, not a closure) matches the author.

No sentence was checked against training data by model; none was tuned on. The set is not sealed and may be reused only
as a development reference after this one run.

## Arms

1. **Adopted reader**: Muse Glimmer 30B + swa-last-5 (adapter sha256 `eff712e18a649804fcbd5b19db27baa709fa60701a2970d1fa58593f731c6f57`),
   through `v3/evaluate.py`: the sealed-scoring prompt, mask (decoder.py sha256 `db55700d4de5562a0a68aa7619c00c4f8ac6b1ab7c87c90544c71d4e501bdb03`) and greedy decoding, 256 tokens.
2. **Untrained Glimmer**: same model, prompt and mask, no adapter.
3. **Fair parser**: `v3/fair_parser.py` (sha256 `1a2499da8c0dcd65f983d6797eccb70bc83f8ecd9e50da7f992482e0e87c163f`), frozen and hand-written; it gets no mask.

Glimmer arms run one at a time on the GPU. No Spark call.

## Metrics (exactly these, every arm, whatever they show)

- **Exact match**: `canonicalize(prediction) == canonicalize(expected)` on the row's ship; refusal reasons ignored, codes count.
  Reported overall, on the 66 rule rows and on the 34 must-refuse rows. Malformed output scores wrong.
- **Wrong rule from a must-refuse sentence**: a constraint output on any of the 34 must-refuse rows.
- **Accepted loosening**: a constraint output on any of the 8 loosening rows.
- Also reported, secondary: a wrong rule on a rule row, and exact match by family.

Each rate has an exact (Clopper-Pearson) 95 % interval. No threshold decides anything: this test describes, it doesn't
gate adoption. Scorer: `run_arms.py` (sha256 `17f5b0bd7a3b6c8be02a34b8252174749c190bfec640a3d2fcd9b85654edb67b`).
