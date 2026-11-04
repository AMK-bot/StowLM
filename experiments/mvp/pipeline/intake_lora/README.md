# Private intake adapter preparation

Task 1.4 is prepared on CPU. No adapter has been trained or adopted. Spark intake
remains the fallback. Everything here is synthetic. No model accuracy is claimed.

One booking line, email sentence or dump row produces one explicit JSON proposal:
`id`, `iso_type`, `length_ft`, `gross_mass_kg`, `pod`, `reefer`,
`reefer_setpoint_c`, `dg` (`null` or `{un_number,imdg_class}`). Unknown or conflicting
facts produce `{ "refusal": { "code": "MISSING_FIELD" } }`; the other codes are
`AMBIGUOUS`, `CONFLICT`, `OUT_OF_CONTEXT`, `UNSUPPORTED`. Units can be converted
only when declared. Absent DG/reefer declarations are never interpreted as false.

The decoder reuses reader v3's byte-token trie and logits processor, intersecting
it with a bounded JSON grammar. Container identities and numbers are generated
from grammar, never enumerated from answers. Only rotation LOCODEs, declared ISO
vocabulary and public DG classes enter the mask. Ship permission remains the
shared validator's job. Spark receives the same JSON Schema; exact probability
or byte-level token-mask parity across providers is not claimed.

From the repository root:

```sh
export UV_CACHE_DIR="$PWD/.uv-cache"
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.admission --timeout 110 -- pytest -q -p no:cacheprovider experiments/mvp/pipeline/intake_lora
uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.demo
```

`fixtures/recorded_complete_mock_run.json` replays a recorded mock proposal through task
1.3's real deterministic validator. Its label says mock; it is not an adapter or
Spark result. `fixtures/synthetic_examples.json` contains authored expected
answers, explicitly unmeasured. Replay needs no network, GPU or paid calls.
The older `recorded_mock_run.json` preserves the original unsupplemented run.

The API imports shared `IntakeResult`, `Question`, and `StageRecord`:

```python
from experiments.mvp.pipeline.intake_lora import PrivateIntake, LocalGlimmerModel
# After all gates pass, on the GPU owner's runtime only:
model = LocalGlimmerModel('experiments/mvp/pipeline/intake_lora/adapter-intake-v1')
result = PrivateIntake(model).read_text(text, base_instance=instance)
wire = result.model_dump(mode='json')
```

`PrivateIntake()` without a model fails closed with `MODEL_UNAVAILABLE`. Tests
inject a local mock callable and, where needed, a validator with the same protocol.
The default validator is imported lazily from
`experiments.mvp.pipeline.intake.validation.validate_reading`. A missing or failing
validator returns questions. There is no network fallback. The optional local
model verifies the eligible adoption record, final-freeze digest, all registered
reports, and the complete frozen inference artifact set before loading MLX.
This includes adapter configuration, base weights/configuration, tokenizer and
imported decoder dependencies. Missing or changed artifacts refuse loading.

The eight extracted fields are never filled from the base. Other explicitly
declared fields in the matching base container (tare, height, loading port, plate
rating, flags and related facts) are added afterward with separate `base-container`
source evidence. The shared validator receives a single-row load-list scope with
the original ship, yard and container catalogue intact. A complete valid reading
can therefore return `complete`. Unknown plate ratings produce a question naming
`max_gross_mass_kg`; incomplete base declarations name their unavailable field
(for example `tare_kg`). Nested DG metadata is inherited only when both UN and class
match; new DG declarations require their missing shipment fields, including
`dg.stowage_category` and `dg.packing_group`, explicitly. No supplement enters
the model prompt or mask, and no missing extraction field is guessed.

The corpus has 3,000 train / 400 validation / 200 sealed fragments. Families,
container identities, gross-mass pools (including conflicting alternatives), and
reefer setpoints are split-disjoint. Public categorical values (ports, lengths,
ISO types, three DG UN/class pairs) and refusal terminology are intentionally
shared. Half the examples are refusals. Thai-English text is authored synthetic
wording and has not had independent Thai review. Three UN/class pairs exercise
extraction; this is not a complete IMDG clearance dataset.

`data/manifest.json` records counts and SHA-256 computed during exclusive writes.
Sealed bytes remain unopened until the one final score. Never print, inspect,
regenerate or include them in tokenizer checks. Existing corpus builds verify
only development bytes and sealed-file existence.

The actual local Glimmer tokenizer verified all 3,400 development chat prefixes:
maximum 436 training tokens and 454 validation tokens, below the 2,048 cap.
`tokenizer-preflight.json` records these measured counts and hashes. No model
weights or device were loaded. Training uses the fixed seed-199 subset of 900
rows, 225 optimizer updates, bare Glimmer, fresh rank-8 LoRA and fresh Adam.
The 45-minute budget is a projection from reader v3 throughput, not an intake
measurement. The supervisor enforces 2,700 seconds including load and saves.

`GATE.md` fixes I1–I4 before training. `evaluate.py` accepts only registered,
resolved `data/train.jsonl` and `data/valid.jsonl` inputs and verifies their hashes
before parsing. All other paths are refused before input bytes are read.
It runs development baselines;
`score_final.py --execute-once` freezes artifacts, consumes the one-shot marker,
then scores Spark, bare Glimmer and tuned Glimmer. Failed/incomplete arms cannot
pass. Partial predictions persist without retries. Any failed gate or missing
result by freeze cuts the adapter. Spark calls go through the shared SparkClient
and ledger with an additional cumulative $2 task cap; preparation spend is $0.

See `GPU_HANDOFF.md` for exact GPU-owner commands. CPU tests do not verify MLX
training, provider schema acceptance, GPU throughput, or adapter quality.
