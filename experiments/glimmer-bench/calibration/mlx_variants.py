"""MLX side of the conversion-tolerance calibration (task 0.1): last-KEEP-position logits of every fixed-ID prompt for

  mlx_bf16      the pinned source checkpoint loaded by mlx-lm (vision tower dropped, as in convert.py)
  mlx_q4_fresh  that same in-memory model quantised now with mlx_lm.utils.quantize_model (affine, g64, 4 bit) —
                the procedure convert.py records; equality with mlx_q4 shows the saved artifact is that procedure's output
  mlx_q4        the shipped conversion, models/glimmer-30b-4bit
  mlx_q4_swap   NEGATIVE CONTROL: mlx_q4 with decoder blocks 20 and 21 swapped (a plausible tensor-mapping bug)
  mlx_q3        models/glimmer-30b-3bit, a known coarser quantisation

Usage: HF_HUB_OFFLINE=1 uv run python mlx_variants.py OUT_DIR   (one GPU job; ~65 GB peak while bf16 is loaded)
"""
import gc
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
REPO = HERE.parents[2]
KEEP = 64


def main():
    import mlx.core as mx
    import numpy as np
    from mlx_lm import load
    from mlx_lm.models import muse_glimmer
    from mlx_lm.utils import quantize_model

    from convert import SOURCE_REPO, SOURCE_REVISION
    from validate_conversion import resolve_cached_snapshot

    from importlib.metadata import version

    from eval.harness_integrity import _path_digest
    from identity import write_identity

    out = Path(sys.argv[1])
    prompts = json.loads((out / "prompts.json").read_text())
    timings = {}
    software = {"mlx": version("mlx"), "mlx-lm": version("mlx-lm")}
    q4_dir, q3_dir = REPO / "models/glimmer-30b-4bit", REPO / "models/glimmer-30b-3bit"
    source = {"source": SOURCE_REPO, "revision": SOURCE_REVISION, "loader": "mlx_lm.load, vision tower dropped"}
    models = {"mlx_bf16": source,
              "mlx_q4_fresh": {**source, "quantisation": "mlx_lm.utils.quantize_model affine g64 4-bit, in memory"},
              "mlx_q4": {"path": str(q4_dir), "content_sha256": _path_digest(q4_dir)},
              "mlx_q4_swap": {"path": str(q4_dir), "content_sha256": _path_digest(q4_dir),
                              "control": "decoder blocks 20 and 21 swapped in memory"},
              "mlx_q3": {"path": str(q3_dir), "content_sha256": _path_digest(q3_dir)}}

    def dump(model, tag):
        t = time.perf_counter()
        for name, ids in prompts.items():
            logits = model(mx.array([ids]))[0, -KEEP:].astype(mx.float32)
            mx.eval(logits)
            path = out / f"{tag}__{name}.npy"
            np.save(path, np.array(logits))
            write_identity(out, tag, name, ids, path, model=models[tag], settings={"keep_positions": KEEP},
                           software=software)
        timings[tag] = {"logits_s": round(time.perf_counter() - t, 2), "peak_gb": round(mx.get_peak_memory() / 1e9, 1)}
        print(tag, timings[tag], flush=True)

    original = muse_glimmer.Model.sanitize

    def sanitize(self, weights):
        return original(self, {k: v for k, v in weights.items() if not k.startswith("model.vision_")})

    muse_glimmer.Model.sanitize = sanitize
    snapshot, _ = resolve_cached_snapshot(SOURCE_REPO, SOURCE_REVISION)
    model, _ = load(str(snapshot))
    dump(model, "mlx_bf16")
    model, _ = quantize_model(model, {}, group_size=64, bits=4, mode="affine")
    dump(model, "mlx_q4_fresh")
    del model
    gc.collect()
    mx.clear_cache()
    muse_glimmer.Model.sanitize = original

    model, _ = load(str(REPO / "models/glimmer-30b-4bit"))
    dump(model, "mlx_q4")
    layers = model.model.layers if hasattr(model, "model") else model.layers
    layers[20], layers[21] = layers[21], layers[20]
    dump(model, "mlx_q4_swap")
    del model, layers
    gc.collect()
    mx.clear_cache()
    model, _ = load(str(REPO / "models/glimmer-30b-3bit"))
    dump(model, "mlx_q3")
    (out / "mlx_timings.json").write_text(json.dumps(timings, indent=1))


if __name__ == "__main__":
    main()
