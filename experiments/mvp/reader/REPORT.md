# D199 reader CPU handoff

Ready: 4,325 train, 772 family-heldout validation, 746 generator-family test, and 120 independent sealed sentences. All targets pass strict schema and real-vessel context checks. Cross-split sentences and paraphrase families are disjoint. The separate Claude Opus reviewer received only the public contract and ship context, with tools disabled and working directory `/private/tmp`; it never saw the grammar. Sealed sentences were not supplied to the data or training agents. The prompt, optimizer settings, epochs, and adapter selection policy were fixed before evaluation; no training or tuning uses sealed data or its scores.

Hash-only decontamination removed 8 accidental exact-sentence matches from generated data, using no sealed labels; the sealed artifact remained byte-identical. Original generator metadata and final file hashes are retained in `data/manifest.pre-decontamination.json`, `data/manifest.json`, and `results/decontamination.json`.

CPU verification: 36 reader tests pass, including actual train-instance mask/static-table checks. Full tokenizer preflight validates every train/validation completion boundary without model weights or a device. Maximum example length is 717 tokens; two epochs are approximately 5.71M training tokens. Diagnostic validation batches were capped at 20 after the tokenizer exposed roughly 23.0M full-validation forward tokens under the initial cadence. This resource-only amendment does not select a different optimizer, epoch count, checkpoint, or prompt and uses no sealed labels or performance feedback; old/new config hashes are in `results/validation-budget-amendment.json`. At the inherited SFT estimate of 230 tokens/s, training alone is approximately 6.9 GPU hours before overhead; actual short-sequence throughput remains unmeasured.

Vocabulary retains CloseBay, MaxTiers, DgKeepAway, Refusal and adds only OnePortPerHatch. Its hard rule applies across the hold and deck of a physical cover. `constraints.py` compiles an explicit cover-to-POD allocation into a restriction usable by the production mask and Frontier's copied static table. The allocation is conservative; stock `plan_and_check` is not automatically instruction aware. A finished plan still requires the independent instruction audit and full checker. No binding replan or plan saving is claimed here.

| Sealed arm | Exact structured match | Loosening accepted |
|---|---:|---:|
| rules.parse_rules | 12/120 (10.00%) | 0/25 |
| Zero-shot Muse Spark | 116/120 (96.67%) | 0/25 |
| Untrained Glimmer | Pending GPU executive | Pending |
| Fine-tuned Glimmer | Pending GPU executive | Pending |

Exact match validates the full structured envelope and refusal code; only free-text refusal reason is excluded. Invalid or missing outputs are wrong. Any emitted constraint on a loosening sentence counts as false acceptance, including invalid-context constraints. Rules has 2 context-invalid/malformed outputs; Spark has 0, with 0 missing. The Spark arm is zero-shot with the same frozen prompt/context and no examples; its 1,024-token cap includes reasoning, while Glimmer's cap is 256 direct completion tokens.

Spark calls are recorded on the authoritative shared ledger and its managed `docs/compute.md` row. Data: $0.01342375 against $5; sealed Spark: $0.15594075 against $2; total $0.16936450. `results/spend-audit.json` binds request IDs to ledger records. Claude reviewer usage is separately retained in `sealed/provenance.json` and is not Meta API spend.

Exact CPU, train, and evaluation commands are in [GPU_HANDOFF.md](GPU_HANDOFF.md). Glimmer base only; no Llama, local model inference, training, or GPU execution was performed in this CPU task. The fixed recipe selects the final adapter after two epochs. R1/R2/R3 remain pending until the GPU arms are scored. R3 interprets “within 3 of Spark” as no more than 3 percentage points below Spark; outperforming Spark by more than 3 does not fail.

Assumptions/limits: English synthetic/public planner language; context from manifest-listed train ships only; at most one supported firm restriction per sentence. Entire paraphrase templates are held out by family. No general multilingual or real-company performance claim. D166/D170 require a violating unconstrained plan and a changed, freshly checked constrained plan before any later binding-replan claim.
