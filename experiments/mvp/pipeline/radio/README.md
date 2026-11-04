# Hero voice queue

Eight synthetic hero wordings, each clean and through a simulated radio channel.
The seven candidates retain `lattice/hero-wordings.json` order; the template is
last. Everything in this folder is development evidence. See [RESULTS.md](RESULTS.md)
for the verbatim transcripts, disclosure, measured spend, and GPU handoff.

From the repository root:

```sh
export UV_CACHE_DIR=$PWD/.uv-cache
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
uv run --offline --no-sync pytest experiments/mvp/pipeline/radio -q
```

Generation uses the unchanged `experiments.mvp.voice.audio` and `.asr` APIs.
Credentials stay in the process environment; load them only for generation:

```sh
set -a
source /Users/anda/Projects/.env
set +a
SPARK_CREDIT_READY=1 uv run --offline --no-sync python -m experiments.mvp.pipeline.radio.make_clips
```

The command waits for CPU admission. It resumes verified completed clips without
TTS or ASR calls. Before each paid attempt it durably reserves an upper bound;
unknown or failed attempts keep that hold. It stops after two attempts per clip,
including across restarts. The order cap is $0.05, and may only be lowered.
`spend.jsonl` and its summary stay inside this folder to respect the write fence.
Do not delete accounting files to retry an unresolved call.

The plain Python API is `make_clips.generate(output_dir, ...) -> manifest`;
injectable transcriber and audio functions support offline tests.
`read_transcripts.read_transcripts(manifest, reader=None, context=None)` returns a
JSON-able object with `rows` and condition `summary`. Expected answers enter only
scoring, never the reader. The CLI writes `readings.json`.

Only the leader runs this on Metal, with the environment above:

```sh
uv run --offline --no-sync python -m experiments.mvp.pipeline.radio.read_transcripts
```

For offline playback, load `fixtures/recorded.json`; its WAV paths resolve relative
to `fixtures/`. These are recorded ASR answers and generated voice assets. No model,
GPU, network, or paid call is needed for playback. `manifest.json` is the read queue;
its WAV paths resolve relative to this folder. Each clip carries its registered
expected rule, audio hash, duration, seed, ASR text, latency, cost, and API evidence.

Limitations: stock public TTS and an artificial channel do not measure real radio
recognition. Each clip was transcribed once. Number spelling, punctuation, and case
can change without changing intent; other outputs contain substantial errors.
There is no reading accuracy result until the leader runs the queued reader.
The reader result measures agreement with the registered rule; it is not a plan
check or planner confirmation.
