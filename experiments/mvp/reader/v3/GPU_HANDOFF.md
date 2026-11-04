# Reader v3 — D224 GPU handoff

**V3 runs whether or not v2 passes.** GPU order: v2, v3, then 8.5a.
Choose the source once at launch: finalized, hash-verified `../adapter-v2` if
available; otherwise stopped `../adapter-v1`. No v2 gate or sealed-v2 score is
consulted. Preparation uses no GPU. Authored files stay within this v3 directory.

## Data and public wording

The active corpus is `data-domain/`, built by `build_domain.py` from immutable
`data/` plus D224 grammar and task-authored paraphrase families. Original train
and validation bytes remain exact prefixes, with unchanged original contexts.
New construction families have fixed, disjoint train/valid ownership and no
sentence overlap. No sealed input is used to choose words or labels.

Delivered: **4,121 train / 1,476 validation**, with44 new domain construction
families (22 train,22 valid). Original3,417/772 rows remain unchanged.

The builder adds 704 training and 704 heldout rows: corrosive/corrosives, toxic
substances versus toxic gases, flammable liquids versus flammable gases,
gas/gases, explosives, oxidisers/oxidizers and other public IMDG names; spelled
numbers, terminal/radio wording; all four rule types and all four refusal codes.
Bare toxic/flammable remain ambiguous. The review's exact hero construction is
excluded from training. `domain_words.py` and the manifest cite official public
classification sources. Paraphrases are authored for this task, **not Spark
output: new Spark spend $0 of the $1 allowance**.

`data-domain` freezes after its first successful build. Its manifest pins source
bytes, construction families, vocabulary, counts and hashes. Existing `data/`
and every original `sealed/` artifact remain unchanged.

## Decoder and reference policy

Tuned and untrained Glimmer use the identical current logits processor,
tokenizer, prompt, raw context and256-token cap. Stopped v2 uses that same mask
for the adoption comparison. Tokens outside the compact JSON grammar receive
-infinity before sampling. EOS is permitted only after a complete answer.
There is no repair, class substitution or bay remapping; truncation scores wrong.

The mask allows only real context bays, physical bay/zone tier caps, available
accommodation/hatch references and context class values. Class declarations
`allowed_imdg_classes`, `imdg_classes`, `declared_imdg_classes` take priority,
including inside `vessel`/`ship_context`; otherwise non-null cargo annotations
can define available classes. Explicit `[]` removes DG outputs. Legacy contexts
without class metadata retain the public contract's class vocabulary: missing
metadata does not prove zero permitted classes. New domain contexts declare
class lists explicitly. Neither sentence nor target enters the mask.

**Existing Spark126/128 (98.4375%) stands as a fixed reference.** Its original
predictions, provenance and decoder hash are preserved. It was schema-constrained
with the earlier mask; it is not a rerun under the D224 decoder. The sealed
population remains128 sentences,48 loosening. Prior preparation cost $0.3619705
combined Claude/Spark, $0 held. Never rerun Spark or regenerate the sealed set.

## CPU preparation and verification

One local worker; native threads one; offline uv; no GPU, git, src edits or nice.
From repository root:

```sh
export UV_CACHE_DIR="$PWD/.uv-cache"
export PYTHONDONTWRITEBYTECODE=1
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export RAYON_NUM_THREADS=1
uv run --offline --no-sync python -m experiments.mvp.reader.v3.admission --wait-seconds 600 --timeout 90 -- python -m experiments.mvp.reader.v3.build_domain
uv run --offline --no-sync python -m experiments.mvp.reader.v3.admission --wait-seconds 600 --timeout 110 -- python -m pytest -q -p no:cacheprovider experiments/mvp/reader/v3/
uv run --offline --no-sync python -m experiments.mvp.reader.v3.admission --wait-seconds 600 --timeout 180 -- python -m experiments.mvp.reader.v3.train --data-only --tokenize-check
```

Admission requires fresh `experiments/fleet/core-state.json`: age<120s, at least8
set mask cores, load1<=45. Waiting never bypasses admission. CPU data/tokenizer
preflight does not read mutable adapter weights or choose a source. Delivered
verification is recorded in `tokenizer-preflight-d224.json` and `READINESS.json`.
GPU throughput/training quality remain unverified until execution.

Delivered CPU verification: **124 current tests passed** (123 passed in the
full run; the manifest return-type fix then passed all9 domain tests).
Actual Glimmer tokenizer fixtures and all5,597 train/valid prompt-prefix checks
passed, with maximum763 train /767 valid tokens, below2048. No model/device was
loaded. Source selection remains deferred until the explicit GPU-launch freeze.
The original overlap audit covers `data/`; no sealed-driven deduplication of
`data-domain/` was performed.

## Fair hand-written baseline

`fair_parser.py` is self-contained linguistic code developed from the public
contract, vocabulary, grammar and public terminology, blind to sealed sentences.
It handles all four rule types, request wrappers, domain aliases, spoken/radio
numbers, context checks and refusal precedence. Public synthetic fixtures are
tested before freezing. This deterministic baseline is not a learned arm and
is not forced into a refusal by the model logits mask.

The package owner runs these **once**, after parser tests pass:

```sh
uv run --offline --no-sync python -m experiments.mvp.reader.v3.admission --timeout 90 -- python -m experiments.mvp.reader.v3.parser_baseline freeze
uv run --offline --no-sync python -m experiments.mvp.reader.v3.admission --timeout 90 -- python -m experiments.mvp.reader.v3.parser_baseline score
```

The freeze binds source/public dependencies before cases are opened. Scoring
verifies original case integrity, consumes an exclusive one-shot marker, saves
raw ID/prediction records and writes `parser/sealed-v3-report.json`. It prints
aggregates only. Never tune from that score, change sentences or score again.

**Completed once,2026-10-31:** fair parser **90/128 exact (70.3125%)**, **1/48
accepted loosenings**, zero malformed/missing outputs. Frozen source SHA-256:
`1a2499da8c0dcd65f983d6797eccb70bc83f8ecd9e50da7f992482e0e87c163f`.
Source and predictions remain frozen. The commands above are an audit recipe,
not a request to rerun the completed baseline. This replaces the thin parser's
10% comparison; it does not establish that these four rule types require an LLM.

## GPU launch, stop and resume

After CPU readiness and exclusive GPU admission, freeze the recipe at launch,
then train. **Do not choose the source speculatively during preparation.**

```sh
uv run --offline --no-sync python -m experiments.mvp.reader.v3.freeze --stage recipe
uv run --offline --no-sync python -m experiments.mvp.reader.v3.freeze --stage recipe --check
uv run --offline --no-sync python -m experiments.mvp.reader.v3.gpu_run --train-only
```

`results/source-selection.json` is exclusive and binds adapter/configuration/
stop-report hashes. V2 needs a matching finalized weight hash. Legacy v1's stop
contains reason/time, so its stopped bytes are bound by computed selection
hashes. The source never changes if v2 finishes later. Fresh Adam resumes adapter
weights, not optimizer/scheduler state. Source adapters are never written.

Recipe: Glimmer30B4-bit, LoRA rank8/scale20/dropout0/last16 blocks, Adam5e-5,
seed199, batch1/accumulation4, completion-only loss, checkpointing,2048-token cap.
One epoch, each row once, actual divisor for the final short group. Validation
uses20 loss batches and fixed80-row seed199 masked checks. Stop after95% then
two non-improving checks, or10800 elapsed seconds including load and validation.
`adapter-v3/STOPPED.json` records actual progress: early stop is not a completed
epoch. The device deadline is cooperative.

A nonempty destination rejects training restart. Preserve a crash: no Adam
continuation or silent budget reset exists. Stop only exact owned subprocess
handles. Supported warm-start is the frozen v2/v1 selection above. Never train
after final sealing.

## Final scoring and adoption

```sh
uv run --offline --no-sync python -m experiments.mvp.reader.v3.gpu_run --score-final --spark-summary experiments/mvp/reader/v3/sealed/spark-summary.json
uv run --offline --no-sync python -m experiments.mvp.reader.v3.freeze --stage final --check
```

Before local sealed inference the runner freezes recipe, stopped adapter,
original sealed artifacts and historical Spark reference. It scores tuned and
untrained Glimmer and, when finished, stopped v2 on **identical sealed-v3 cases
with the same current mask**. V2 reference bytes receive a separate immutable
freeze before inference. V2's original scoring is untouched. Interrupted final
scoring reuses verified complete arms and runs only missing arms. Raw artifacts
are never overwritten; a partial artifact without its summary fails explicitly.

| Gate | Requirement on fixed sealed v3 |
|---|---|
| R1 | Tuned exact>=90% (at least116/128) |
| R2 | Zero accepted loosenings (0/48) |
| R3 | Within3pp of fixed Spark126/128 (at least123/128) |

Running is unconditional; adoption is separate. `adoption.py` requires all gates,
no lower exact-match count and no higher accepted-loosening count than stopped
v2 on the same cases/current decoder. This conservatively interprets "no worse
on the gates". If v2 is unfinished, v3 training/scoring proceed while
`adoption-pending.json` prevents adoption. Rerun final scoring once v2 finishes
to add its bound reference and write `adoption-ready.json`; v3 is never retrained
and earlier predictions are never changed.

No v3 adoption or quality result is claimed before those GPU measurements exist.
