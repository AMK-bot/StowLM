"""MLX side of the long-prompt calibration: the source in MLX bf16 (implementation floor) and the shipped 4-bit model,
chunked prefill with a KV cache, logits kept for the scored positions only (same positions as the reference).

    HF_HUB_OFFLINE=1 uv run python experiments/glimmer-bench/calibration/long-prompts/mlx_long.py OUT
"""
import gc
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))
REPO = HERE.parents[3]
CHUNK = 2048


def main():
    from importlib.metadata import version

    import mlx.core as mx
    import numpy as np
    from mlx_lm import load
    from mlx_lm.models import muse_glimmer

    from convert import SOURCE_REPO, SOURCE_REVISION
    from eval.harness_integrity import _path_digest
    from identity import write_identity
    from validate_conversion import resolve_cached_snapshot

    out = Path(sys.argv[1])
    prompts = json.loads((out / "prompts.json").read_text())
    only = sys.argv[sys.argv.index("--prompt") + 1] if "--prompt" in sys.argv else None
    if only:
        prompts = {only: prompts[only]}
    meta = json.loads((out / "meta.json").read_text())["prompts"]
    software = {"mlx": version("mlx"), "mlx-lm": version("mlx-lm")}
    timings = {}

    def dump(model, tag, model_identity):
        for name, ids in prompts.items():
            keep = meta[name]["keep"]
            mx.reset_peak_memory()
            t = time.perf_counter()
            cache, parts, L = model.make_cache(), [], len(ids)
            for s in range(0, L, CHUNK):
                e = min(L, s + CHUNK)
                need = max(0, e - max(s, L - keep))
                logits = model(mx.array([ids[s:e]]), cache=cache)
                if need:
                    part = logits[0, -need:].astype(mx.float32)
                    mx.eval(part)
                    parts.append(np.array(part))
                else:
                    mx.eval([c.state for c in cache])
                del logits
            path = out / f"{tag}__{name}.npy"
            np.save(path, np.concatenate(parts))
            write_identity(out, tag, name, ids, path, model=model_identity,
                           settings={"keep_positions": keep, "prefill_chunk": CHUNK, "kv_cache": "mlx-lm make_cache"},
                           software=software)
            timings[f"{tag}/{name}"] = {"s": round(time.perf_counter() - t, 1),
                                        "peak_gb": round(mx.get_peak_memory() / 1e9, 1)}
            print(tag, name, timings[f"{tag}/{name}"], flush=True)

    if "--bad-variants" in sys.argv:
        # the original calibration's known-bad variants (calibration/mlx_variants.py), same positions as q4
        q4, q3 = REPO / "models/glimmer-30b-4bit", REPO / "models/glimmer-30b-3bit"
        model, _ = load(str(q3))
        dump(model, "mlx_q3", {"path": str(q3), "content_sha256": _path_digest(q3)})
        del model
        gc.collect()
        mx.clear_cache()
        model, _ = load(str(q4))
        layers = model.model.layers if hasattr(model, "model") else model.layers
        layers[20], layers[21] = layers[21], layers[20]
        dump(model, "mlx_q4_swap", {"path": str(q4), "content_sha256": _path_digest(q4),
                                    "control": "decoder blocks 20 and 21 swapped in memory"})
        (out / "mlx_timings_bad.json").write_text(json.dumps(timings, indent=1))
        return
    original = muse_glimmer.Model.sanitize

    def sanitize(self, weights):
        return original(self, {k: v for k, v in weights.items() if not k.startswith("model.vision_")})

    muse_glimmer.Model.sanitize = sanitize
    snapshot, _ = resolve_cached_snapshot(SOURCE_REPO, SOURCE_REVISION)
    model, _ = load(str(snapshot))
    dump(model, "mlx_bf16", {"source": SOURCE_REPO, "revision": SOURCE_REVISION,
                             "loader": "mlx_lm.load, vision tower dropped"})
    del model
    gc.collect()
    mx.clear_cache()
    muse_glimmer.Model.sanitize = original
    q4 = REPO / "models/glimmer-30b-4bit"
    model, _ = load(str(q4))
    dump(model, "mlx_q4", {"path": str(q4), "content_sha256": _path_digest(q4)})
    (out / "mlx_timings.json").write_text(json.dumps(timings, indent=1))


if __name__ == "__main__":
    main()
