# Sealed reader evaluation v2

The reviewer runs as a separate Claude Opus CLI process in a fresh empty private
temporary directory. Safe mode disables local instructions, hooks, memories,
skills, and custom agents. Empty tools and strict empty MCP configuration prevent
tool access. Only the frozen public contract, synthetic world vocabulary, and
test-authoring request are supplied on stdin. Both sentences and expected labels
are authored by Claude. Ingestion adds the fixed context and provenance only.

Do not read the reviewer response, cases, prediction outputs, or row-level
scoring to select data, prompts, model settings, checkpoints, or thresholds.
`manifest.json` binds the case bytes and provenance cryptographically. Retain raw
reviewer output unchanged. No prior sealed set is read by this harness.

Run commands from the repository root with one CPU worker:

```sh
UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 uv run --offline --no-sync python experiments/mvp/reader/sealed_v2.py acquire
UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 uv run --offline --no-sync python experiments/mvp/reader/sealed_v2.py ingest
UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 uv run --offline --no-sync python experiments/mvp/reader/sealed_v2.py spark
```

Spark receives the identical frozen zero-shot schema prompt and context as the
other arms. Credentials move only from the external parent `.env` into process
environment. Every call uses the shared project spend ledger and a unique
`reader-d219-sealed-v2-zeroshot` purpose, with a total allocation of $2 including
unknown billing holds. Raw malformed output is scored incorrect without repair.
`spark-summary.json` contains aggregate metrics and private row-level diagnostics;
only the aggregate fields may be reported to tuning agents.

The harness does not load or invoke Glimmer. After the training recipe and final
checkpoint are fixed, the authorized GPU executor can generate untrained and
tuned predictions using `cases.jsonl`, then use the `score` action with
`--predictions PATH --arm untrained` or `--arm tuned`. Fresh arm names are required;
existing results are never overwritten. The `audit --other PATH ...` action checks
new training/validation/test sentence-context overlap without exposing sentences.
