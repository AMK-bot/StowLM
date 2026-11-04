# Long-output batched generation benchmark for Glimmer in MLX (feeds plan 3.8 / 4.5 / 8.1 / 0.8).
# One run per (prompt_len, batch, processor) generates max_out tokens per sequence with EOS disabled and records the
# decode speed at checkpoints (2k / 5k / 10k), so one 10k run gives all three output lengths.
# Results are appended as JSON lines to OUT; configs already in OUT are skipped (resumable).
#
# Processor modes (per-sequence logits processors, as the feasibility mask will be):
#   none  - no processor (the fast path: one batched sampler call, fully async pipeline)
#   lazy  - adds a precomputed additive mask (mx.where on a constant bool array): GPU-only, no host sync.
#   sync  - reads the last token on the host (.item()), looks up a mask in a Python dict keyed by token and builds it
#           as an mx.array: forces a GPU->CPU sync every step for every sequence, which is what a real
#           state-dependent mask (Python feasibility engine) must do. Upper bound on mask overhead, minus the
#           engine's own compute time.
#
# Usage: uv run python experiments/glimmer-bench/bench_long.py MODEL OUT PROMPT_LEN BATCH MAX_OUT [MODE]
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path


def software_versions():
    return {"python": platform.python_version(), **{
        name: importlib.metadata.version(name) for name in ("mlx", "mlx-lm", "numpy", "transformers", "tokenizers")
    }}


def model_fingerprint(model_path):
    """Hash names and complete contents, including tokenizer/config and weight shards.

    Streaming keeps RAM bounded. Intentionally no stat-only cache: replacing bytes
    while retaining size/timestamps must invalidate the measurement.
    """
    root = Path(model_path).resolve(strict=True)
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        raise ValueError(f"No model files found in {root}")
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        file_hash = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                file_hash.update(chunk)
        digest.update(file_hash.digest())
    return digest.hexdigest()


def build_key(model_path, prompt_len, batch, max_out, mode, text, versions=None):
    return dict(
        model=Path(model_path).name, model_path=str(Path(model_path).resolve()),
        model_sha256=model_fingerprint(model_path), prompt_len=prompt_len,
        batch=batch, max_out=max_out, mode=mode, benchmark_schema=2,
        prompt_identity={"kind": "text" if text else "synthetic-random-v1",
                         "sha256": hashlib.sha256(text.encode()).hexdigest() if text else None,
                         "seed": 0, "slice_stride": 997},
        software=software_versions() if versions is None else versions,
    )


def throughput_metrics(batch, first, marks):
    """Decode excludes each sequence's first token; a one-token run has no rate."""
    result = {}
    prev_c, prev_t = 1, first
    for c in sorted(marks):
        elapsed, segment = marks[c] - first, marks[c] - prev_t
        result[f"tok_s_agg_to_{c}"] = round(batch * (c - 1) / elapsed, 1) if elapsed > 0 and c > 1 else None
        result[f"tok_s_agg_seg_{c}"] = round(batch * (c - prev_c) / segment, 1) if segment > 0 and c > prev_c else None
        prev_c, prev_t = c, marks[c]
    return result


def main():
    model_path, out_path, prompt_len, batch, max_out = sys.argv[1], Path(sys.argv[2]), *map(int, sys.argv[3:6])
    mode = sys.argv[6] if len(sys.argv) > 6 else "none"
    if min(prompt_len, batch, max_out) < 1:
        raise ValueError("PROMPT_LEN, BATCH and MAX_OUT must be positive")
    if mode not in {"none", "lazy", "sync"}:
        raise ValueError("MODE must be none, lazy or sync")
    prompt_path = Path("data/glimmer-bench/train.jsonl")
    text = prompt_path.read_text() if prompt_path.exists() else None
    key = build_key(model_path, prompt_len, batch, max_out, mode, text)
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip() and all(json.loads(line).get(k) == v for k, v in key.items()):
                print("already done", key)
                sys.exit(0)

    import mlx.core as mx
    import numpy as np
    from mlx_lm import load
    from mlx_lm.generate import BatchGenerator
    from mlx_lm.sample_utils import make_sampler

    model, tok = load(model_path)
    vocab = model.args.text_config["vocab_size"] if hasattr(model.args, "text_config") else 202048

    # Prompt text: the synthetic plan text from make_data.py, re-tokenized; each sequence gets a different slice.
    rng = np.random.default_rng(0)
    if text:
        ids = tok.encode(text)
    else:
        ids = rng.integers(1000, 100000, size=200000).tolist()
    prompts = [ids[(i * 997) % (len(ids) - prompt_len):][:prompt_len] for i in range(batch)]

    rng_mask = np.random.default_rng(1)
    const_mask = mx.array(rng_mask.random(vocab) < 0.5)  # half the vocabulary legal
    neg = mx.array(-1e9, dtype=mx.float32)


    def lazy_proc(context, logits):
        return mx.where(const_mask, logits, neg)


    _cache = {}


    def sync_proc(context, logits):
        last = context[-1].item() if context.size else 0  # host sync
        m = _cache.get(last % 64)
        if m is None:
            m = _cache[last % 64] = rng_mask.random(vocab) < 0.5
        return mx.where(mx.array(m), logits, neg)


    procs = {"none": None, "lazy": [lazy_proc], "sync": [sync_proc]}[mode]
    gen = BatchGenerator(model, stop_tokens=None, sampler=make_sampler(temp=1.0), completion_batch_size=max(32, batch),
                         prefill_batch_size=min(batch, 8))
    mx.reset_peak_memory()
    t0 = time.perf_counter()
    gen.insert(prompts, [max_out] * batch, logits_processors=[procs] * batch if procs else None)
    counts = {}
    first = None
    marks = {}
    checkpoints = [c for c in (500, 2000, 5000, 10000) if c <= max_out] + [max_out]
    while responses := gen.next_generated():
        now = time.perf_counter()
        for r in responses:
            counts[r.uid] = counts.get(r.uid, 0) + 1
        n = min(counts.values()) if len(counts) == batch else 0
        if first is None and n >= 1:
            first = now
        for c in checkpoints:
            if c not in marks and n >= c:
                marks[c] = now
                print(f"{key} step {c}: {(now - first) / max(c - 1, 1) * 1000:.1f} ms/step, "
                      f"peak {mx.get_peak_memory() / 1e9:.1f} GB", flush=True)
    gen.close()
    peak = mx.get_peak_memory() / 1e9
    if first is None:
        raise RuntimeError("Generator produced no complete first-token batch")
    res = dict(key, prefill_s=round(first - t0, 2), peak_gb=round(peak, 2), total_s=round(time.perf_counter() - t0, 1))
    res.update(throughput_metrics(batch, first, marks))
    res["date"] = time.strftime("%Y-%m-%d")
    print(json.dumps(res), flush=True)
    with open(out_path, "a") as f:
        f.write(json.dumps(res) + "\n")


if __name__ == "__main__":
    main()
