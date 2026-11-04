# Yard picture experiment — cut from the shipped pipeline

The clean synthetic arm works on five of six recorded scenes. The realistic-photo arm did not clear its scene-preservation gate. Task 1.5 is cut; see [CUT.md](CUT.md). These results do not establish real-camera readiness.

Open [demo.html](demo.html) locally for the recorded player: generated input, actual SAM bounding boxes, recovered yard beside truth, questions, and discarded repaint. It uses local files, no network, GPU or paid call. The true yard is scoring data and never enters the reading prompt.

From the repository root:

```sh
UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --offline --no-sync pytest experiments/mvp/pipeline/yard_vision
node experiments/mvp/pipeline/yard_vision/test_demo.cjs
```

For fleet CPU admission before pytest, run the same environment with `uv run --offline --no-sync python -m experiments.mvp.pipeline.yard_vision.verify`.

Verification: 135 offline pytest cases, six-case Node player navigation/link/abstention checks, visual checks of the render and SAM overlay, independent held-out repaint audits, fixture hashes, and a credential artifact scan. The pytest output is retained in [verification.txt](verification.txt). No GUI browser was launched.

The API imports the shared contract, without schema changes:

```python
from experiments.mvp.pipeline.yard_vision import read_yard_picture

result = read_yard_picture(
    "experiments/mvp/pipeline/yard_vision/fixtures/case-01/clean.png",
    base_instance="experiments/mvp/pipeline/yard_vision/fixtures/case-01/base.json",
)
payload = result.model_dump(mode="json")  # shared contract.schema.YardRead
```

`base_instance` accepts an Instance, its JSON dictionary, or a JSON path. The default client replays only an exact image SHA256 plus static layout/inventory match; an unknown image returns `needs_answers`. Replay is labelled `recorded`, with zero new model cost. Original paid receipts remain in the fixtures.

An explicit `client=YardVisionClient(...)` from `clients.py` makes paid calls. `client.read(path, *, layout, inventory)` supplies observed stack keys, lengths, heights, bottom-to-top IDs (`null` when unreadable), visibility/quay flags, and SAM segments. Muse Image is a test-image synthesis tool, never an inference step. Its synthesis manifest contains truth; the reading prompt receives only the original picture, SAM outlines, static layout, and an unordered ID/length registry.

The reader checks stack addressing, block bounds, overlapping footprints, effective block/port tier limits, allowed lengths, registry membership, matching container lengths, duplicate identities, onboard/direct-delivery exclusions, complete registry coverage, SAM box geometry/duplicates/count, whole-view visibility and a visibly empty quay. Any uncertainty yields shared `Question` objects and no state. Complete reads produce a validated `YardState`; the planner must confirm the proposed reading before planning. Integer/count-only segmentation evidence is a recorded/testing seam; the paid adapter supplies actual boxes and encoded masks.

Measured results ([summary and denominators](fixtures/summary.json)):

| Split / arm | Scored images | Exact SAM counts | Stack heights | IDs at correct stack/tier | Complete states | Silent wrong stacks / accepted | Mean read seconds | Read dollars |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Development / clean | 2 | 2/2 | 36/36 (100%) | 64/64 (100%) | 2/2 | 0/36 | 17.15 | $0.05407350 |
| Held out / clean | 4 | 4/4 | 52/74 (70.27%) | 61/87 (70.11%) | 3/4 | 0/52 | 9.52 | $0.08851425 |
| Development / repainted photo | 0; 4/4 trials discarded | N/A | N/A | N/A | N/A | N/A | N/A | $0 recovery |
| Held out / repainted photo | 0; 4/4 trials discarded | N/A | N/A | N/A | N/A | N/A | N/A | $0 recovery |

SAM count agreement is `max(0, 1 - abs(observed-true)/true)` and was 100% on every clean image. Heights and IDs use all true stacks/tiers plus extra predictions as denominators. Unknown or malformed readings count as abstentions, never disappear from those denominators. A silent wrong stack is an accepted stack whose address, length or ordered IDs differ from truth. The held-out malformed JSON response abstained on all 22 stacks/26 IDs in case 04; its SAM count and full paid cost are retained. Zero wrong accepted stacks on this small synthetic pilot is not a reliability guarantee.

Repaints are a separate arm, not pooled with clean renders. Visual audits require exact counts, occupied positions/heights/lengths and unchanged identities. Changed or unverifiable scenes are discarded, with costs retained. Five of eight repaint trials changed counts; all eight changed painted IDs. Mean image-generation HTTP time was 13.89 seconds on four development trials and 14.84 seconds on four held-out trials. With zero valid photo pairs, the realism accuracy drop is unmeasured, not zero. The gate is stricter than count equality because changed IDs invalidate identity ground truth.

The first six paths in `quick_small.txt` supplied full known YardStates: first two development, next four held out. [frozen.json](fixtures/frozen.json) predates scoring and binds source hashes. After two development repaint prompts, [evaluation_lock.json](fixtures/evaluation_lock.json) fixes code and the manifest-enhanced prompt before the single held-out pass. Each held-out image received one clean read and one repaint, with no retries or repair call. No sealed folder was opened. The states span 16–32 boxes, one synthetic block, sparse stacks of at most two tiers. PIL separates rows so every tier is visible; this is an intentionally easy elevated drawing, not a calibrated camera or dense-yard test.

Spend: **$0.22258775 total**, including SAM six calls **$0.015**, Muse Image eight calls **$0.08**, and Spark six completed calls **$0.12758775**. SAM/Image used **$0.095 of the $5 cap** with zero unknown holds. The malformed JSON answer was billed and counted. SAM/Image prices are the order/probe's published per-call rates, not invoice verification. Spark usage is captured from the authoritative shared ledger in [spark_usage.json](fixtures/spark_usage.json); the local multimodal subclass preserves the shared `SparkClient` ledger, credit gate, reservations, errors and pricing because the shared client currently accepts text only. It makes no retries. [spend.jsonl](spend.jsonl) records SAM/Image accounting.

Weaknesses: a single view cannot establish occluded identities or hidden tiers; illegible IDs become questions. Registry IDs absent onboard/direct delivery are conservatively required in the yard; unlocated offsite registry extras therefore require an answer. Nonempty quay recovery is unsupported and asks for clarification. Snapshot time uses local epoch zero. SAM masks are retained in the provider's compressed encoding; the player draws returned bounding boxes. Count agreement cannot prove the candidate boxes correspond to the right stacks. Fixture hashes bind the recorded artifacts; they are not a provider signature. No engine/checker integration or real-photo accuracy claim is made.

`experiment.py` holds the one-attempt paid reproduction commands and guards; existing markers prevent duplicate scoring or repaint calls. `finish.py` rebuilds summaries, replay index, integrity manifest and player from saved evidence without a paid call. The fixtures include successful and failed cases, original raw responses, both development repaint variants, visual audit tallies, costs and timing. Do not tune against the held-out records or reuse them as a fresh holdout.
