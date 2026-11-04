# Task 1.4 — Claude GPU executive handoff

Preparation only. Codex ran no Metal, training, baseline inference or paid calls.
Reader v3 owns the GPU until its executive releases it. Do not use a reader
adapter: this recipe starts from bare `models/glimmer-30b-4bit` into the fresh
`adapter-intake-v1` folder. Never resume or overwrite a nonempty destination.

Run from `/Users/anda/Projects/meta-hackathon-stowage`:

```sh
export UV_CACHE_DIR="$PWD/.uv-cache"
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 RAYON_NUM_THREADS=1
uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.admission --timeout 110 -- pytest -q -p no:cacheprovider experiments/mvp/pipeline/intake_lora
```

CPU readiness is already recorded in `tokenizer-preflight.json`: 3,000 train,
400 validation, 900 selected training examples; actual maxima 436/454 tokens;
all chat-prefix checks passed; no truncation. If any recipe/data/tokenizer changes
before launch, rerun this CPU command (never after the recipe freeze):

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.admission --timeout 180 -- python -m experiments.mvp.pipeline.intake_lora.train --data-only --tokenize-check
```

Do not open `sealed/sealed.jsonl`. Its write-time SHA-256 is
`297e9a34cbaa53f7d51bdf103948a2fa3b5cc7e8fe6eebae0fb8ce6a00afbe16`.
`data/manifest.json` binds all counts and split provenance. The split-disjoint
values are identities, masses and setpoints; fixed public categorical vocabulary
is shared. Gates and scoring definitions are fixed in `GATE.md`.

After obtaining exclusive GPU use, create this lease in the owned folder. The
lease is an explicit handoff record, not a GPU lock or a process detector; the
Claude executive must ensure no other GPU job runs. It is valid for six hours
so admission waits plus final scoring can fit. Reissue a lease only while the
executive still owns the GPU. It grants no permission to restart training.

```sh
uv run --offline --no-sync python -c 'import json,time; from pathlib import Path; t=time.time(); p=Path("experiments/mvp/pipeline/intake_lora/claude-gpu-lease.json"); p.write_text(json.dumps({"task":"intake_lora","approved":True,"authorized_by":"claude","issued_at":t,"expires_at":t+21600})+"\n")'
sh experiments/mvp/pipeline/intake_lora/launch.sh --gpu-lease "$PWD/experiments/mvp/pipeline/intake_lora/claude-gpu-lease.json"
```

The launcher uses `nohup`, records its own PID in `gpu-run.pid`, and logs to
`gpu-run.log`. It waits indefinitely for both the explicit GPU lease and fresh
fleet CPU admission: age <120 seconds, at least eight mask cores, load1 <=45.
Malformed/missing/stale state waits rather than raising. No GPU slot is inferred
from CPU admission. `recipe-freeze.json` is written exclusively at launch and
binds source, imported decoder/trainer, tokenizer, configuration, development
data and preflight. No sealed rows enter that freeze.

Recipe: rank8/scale20/dropout0/last16 layers, Adam5e-5, seed199,
batch1/accumulation4, completion-only loss, checkpointing, 2,048-token cap.
Shuffle 3,000 rows once, train the first 900 once: 225 updates. Reader v3 measured
0.3687–0.4009 rows/s over iterations1060–1200 (`reader/v3/results/gpu-run.jsonl`).
At 0.38 rows/s, 900 rows project to2,368.4s, leaving331.6s for load, validation
and saves. This is a projection; intake runtime and quality are unmeasured.
The supervisor kills only its exact owned worker at2,700s. The worker reserves
30s for a cooperative final save. Checkpoints/progress and `STOPPED.json` report
actual work; a hard stop is never called a completed epoch. A partially written
checkpoint cannot be scored. Preserve failures; there is no silent optimizer
resume, fallback to reader weights, or second training run.

Do not stop a process by name/pattern. If manual interruption is necessary,
use only a PID/owned child handle from this launch and preserve the artifacts.
A killed parent must not leave a worker running: use the recorded child handle
from the supervising process when available. Prefer letting the hard stop finish.

Final scoring is separate from the45-minute training budget. First confirm the
worker stopped and the weight hash in `STOPPED.json` matches its checkpoint.
Spark is the only paid arm and only synthetic data is sent. Its shared client
writes the project accounting ledger; the task wrapper enforces an additional
cumulative$2 including pending/uncertain charges, with retries disabled.
Do not run development Spark baselines first if that would consume its allowance.
Source the key into the process environment; never print it or pass it as an arg:

```sh
set -a
source /Users/anda/Projects/.env
set +a
export SPARK_CREDIT_READY=1
uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.score_final --execute-once --gpu-lease "$PWD/experiments/mvp/pipeline/intake_lora/claude-gpu-lease.json"
```

This freezes recipe, stopped adapter, model weights, tokenizer, prompt and mask
before consuming `sealed/SCORE_CONSUMED.json` exclusively. It opens sealed bytes
once, verifies their registered hash, and saves all three arms' raw predictions,
metrics and gates under `results/sealed-final/`. Partial predictions and error
classes survive a failed arm, and remaining arms still run. Never remove the
marker, re-score, repair, choose a checkpoint from sealed, or train afterward.
An interrupted whole scoring process remains consumed and must be reported cut.
Only an eligible result writes `adoption.json`, binding the exact final freeze,
all arm reports and gates. Runtime verifies that binding and every frozen artifact
before loading: adapter weights and configuration, base shards/index/configuration,
tokenizer/template files, and decoder dependencies. Source or artifact changes
require refusal; an old gate cannot authorize changed inference files.

For development only, without ever opening sealed (these commands are optional
and were not run in preparation):

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.evaluate --backend glimmer --gpu-lease "$PWD/experiments/mvp/pipeline/intake_lora/claude-gpu-lease.json" --output experiments/mvp/pipeline/intake_lora/results/valid-untrained.json
uv run --offline --no-sync python -m experiments.mvp.pipeline.intake_lora.evaluate --backend spark --output experiments/mvp/pipeline/intake_lora/results/valid-spark.json
```

The only allowed development inputs are the registered `data/train.jsonl` and
`data/valid.jsonl` files. Resolution and registration checks precede input reads;
their bytes must match `data/manifest.json`. Arbitrary files, foreign sealed sets,
and symlinks redirected outside that allowlist are refused before reading them.

I1 >=180/200 exact, I2 zero silent DG errors (wrong UN also fails), I3 >=20 more
correct than bare Glimmer, I4 no more than10 below Spark. Missing/incomplete arms
fail closed. Report results whatever they are. Any failed gate or no complete
score by2026-11-02 21:00 America/New_York cuts the adapter; Spark intake stands.
Do not advertise StowLM Intake or private intake before all gates pass.

The API uses task1.3's actual `validate_reading`, with mock-model offline tests.
After extraction, non-extraction Container fields are supplied from the matching
base container with distinct source evidence. Validation is scoped to this row;
the full ship and yard remain available for physical checks. Valid complete
readings now return `complete`. Missing CSC ratings name `max_gross_mass_kg`;
missing required base declarations name their field, such as `tare_kg`;
new DG identities require missing DG shipment metadata explicitly. The eight
extraction fields are never taken from base cargo; no supplement reaches the
model prompt or mask. No contract or other executive's files were changed here.
