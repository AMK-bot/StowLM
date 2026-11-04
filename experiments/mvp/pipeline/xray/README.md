# Reader x-ray

Task 1.9. Watch the reader's schema mask block a token before sampling. Everything here is offline; the checked-in demo uses a scripted fake model, labelled **FAKE** in its trace and on screen. Its probabilities are scripted, not measurements of Muse Glimmer. Synthetic ships and cargo, simulated port.

The mask enforces a JSON language and public ship bounds. It does not prove that the model understood a sentence. A reading still needs deterministic validation and planner confirmation before the engine uses it.

## Offline demo

Open `demo.html` in a browser. It loads local JS and CSS, including the fixture, without a server, network, build step or model. Arrow keys step backward/forward, Space toggles playback, and the speed control changes playback rate. The player scales its 1920 × 1080 frame to its container.

The demo opens on token 5: the scripted model ranks bay `99` first, the real v3 mask blocks it because this ship has bays `3, 7, 11`, and the chosen token is `7`. Press Home and Play to replay all seven generated tokens, including EOS. Regenerate the fixture with `uv run --offline --no-sync python -m experiments.mvp.pipeline.xray.fixture` using the environment below; its measured recorder wall time will change.

Embed the two assets and supply a trace object:

```html
<link rel="stylesheet" href="player.css">
<div id="reader-xray" style="width:100%;height:100vh"></div>
<script src="player.js"></script>
<script>
  const player = StowLMXray.mount(document.getElementById('reader-xray'), trace,
    {initialStep: 1, speed: 2, autoplay: false});
</script>
```

The returned controller exposes `seek(step)`, `play()`, `pause()`, `nextBlock()`, `setSpeed(tokensPerSecond)`, `getState()` and `destroy()`. Step zero is the empty prefix. Controls affect only their own player, so focus the frame before using its keyboard shortcuts. `fit: false` opts into the fluid layout.

## Python API and recording

`load_trace(path)` and `validate_trace(trace)` from `experiments.mvp.pipeline.xray` return detached JSON-able dictionaries after checking replay invariants and shared stage hashes. `fixture.make_fixture()` produces the scripted run. `recorder.RecordingLogitsProcessor(processor, sentence=..., top_k=8, fake=False, model=..., adapter_sha256=..., prompt_sha256=...)` wraps a fresh v3 `ReaderLogitsProcessor` and returns its exact masked logits.

Callbacks commit samples observed in the following token prefix. For a synchronous driver, call `observe_token(actual_sample)` after sampling. Finish with `finish(exact_output, final_token_ids=generated_ids)`; the IDs exclude the prompt and include EOS when emitted. This authoritative sequence captures the final token and discards an unused lookahead callback. Do not infer samples with a new argmax. In MLX, collect streamed token IDs and pass them at the end: its processor runs ahead of streamed responses. Missing final samples and mismatching text fail explicitly. `require_complete=False` preserves a truncated run with `complete: false`.

For the GPU owner only, after the adopted adapter is fixed and the GPU slot is granted, record one real run (replace the adapter path):

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab uv run --offline --no-sync python -m experiments.mvp.pipeline.xray.record --adapter /path/to/adopted-adapter --sentence 'Close bay 7.' --output experiments/mvp/pipeline/xray/fixtures/real-trace.json
```

This command loads only local weights; it makes no API call. Supply `--context public-context.json` to use a particular synthetic ship; otherwise it uses the fixture's public ship context. Capturing a trace does not adopt an adapter or authorize using its reading in a plan.

## Trace contract

`format: "xray.trace/1"` contains the original `sentence`, public ship `context`, explicit `source` provenance, ordered `steps`, final `output`, `complete` and the shared pipeline `stage_record`. A fake source has `kind: "fake"`; a captured model run has `kind: "recorded"` and displays “Recorded answer”. Never relabel a fake trace as a model recording.

Before rendering, the player checks source, stage mode, model provenance and SHA-256 together, then keeps a detached snapshot. Recorded traces require a model identifier and adapter, prompt and decoder source hashes; fake/scripted provenance cannot display as recorded. `payload_json` retains the exact shared canonical JSON bytes, avoiding Python/JavaScript float spelling differences during offline hashing. It contains the trace, including stage provenance, excluding `payload_json` and the stage's own `output_sha256`. `trace.seal_trace(trace)` updates both fields after capture metadata changes. Old traces without these bytes must be resealed before replay. This is a consistency checksum, not an authenticity signature.

The recording CLI resolves supplied context paths before admission or model imports. Both the supplied and resolved paths are screened for protected sets; reads use the resolved path. Tests use synthetic file and parent-directory symlinks.

Each step has a 1-based `step`, ranked `top_k`, actual sampled `chosen` token, exact `json_so_far` string, and `is_stop`. Each candidate has `token_id`, display `token`, pre-mask `probability`, `blocked` and `reason`. Reason codes are `not_in_grammar`, `bay_not_on_ship` and `tier_over_cap`. Token fragments may span several JSON fields. The growing JSON may be an incomplete prefix.

Reason labels are inferred by replaying the entire prefix and candidate token through the v3 grammar with only the bay list or only tier caps widened. A candidate that still fails both receives `not_in_grammar`; compound violations are intentionally not assigned a single specific cause. The original mask alone decides whether a token is blocked. Other ship restrictions, such as unavailable IMDG classes, also use the general grammar reason.

Probabilities are the full-vocabulary softmax before masking, not renormalized over the displayed top-k. The chosen token may be outside that list. A blocked candidate's reason explains a grammar restriction, not a model intention or a separate semantic verdict.

## Verification and limits

Run from the repository root after fleet admission (fresh state, at least eight available cores, load at most 45):

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab uv run --offline --no-sync pytest experiments/mvp/pipeline/xray
```

The parity test compares masked logits and generated output with and without recording. Tests use the real v3 decoder and a fake vocabulary/model; no MLX, Metal, network or paid API is needed. Real GPU parity and final-adapter recording remain for the GPU owner. No speed or model-quality claim is made from this fixture.

Verified on 1 November 2026: **20 tests passed offline**, including the Node headless DOM harness executing the static page's actual scripts and checked-in fixture. It covers playback, provenance/hash rejection, keyboard, speed, fitting, labels, teardown and synthetic context symlinks. Node must be installed for the DOM test; otherwise pytest explicitly skips it. The leader visually checked the player and approved its appearance; these fixes preserve the CSS and layout. No GUI was launched for this verification. API spend: **$0**.

`app/brief.py` currently constructs its Glimmer reader using the older unmasked generation path. Demo integration must select the v3 masked path; this folder does not edit the app.
