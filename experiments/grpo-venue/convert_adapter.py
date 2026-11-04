# Convert a LoRA adapter between mlx-lm and PEFT formats for Muse Glimmer (plan 0.8 / 6.4).
#
# What a LoRA adapter is: for each adapted linear layer W (out x in), two small matrices A (r x in) and B (out x r);
# the layer computes W x + s * B A x. Only A and B are trained. The two libraries store the same thing differently:
#   mlx-lm : key model.layers.N.<sub>.lora_a  shape (in, r);  .lora_b (r, out);  y += scale * (x @ a) @ b
#   PEFT   : key base_model.model.model.language_model.layers.N.<sub>.lora_A.weight (r, in); lora_B.weight (out, r);
#            y += (lora_alpha / r) * B(A(x))
# so  A = a.T,  B = b.T,  lora_alpha = scale * r  (use_rslora=False). Mapping researched in cuda-stack.md.
#
# Usage:
#   uv run python experiments/grpo-venue/convert_adapter.py mlx2peft MLX_ADAPTER_DIR PEFT_OUT_DIR
#   uv run python experiments/grpo-venue/convert_adapter.py peft2mlx PEFT_DIR MLX_OUT_DIR
import json
import re
import sys
from pathlib import Path

import numpy as np
from safetensors.numpy import load_file, save_file

MLX_PREFIX = "model.layers."
PEFT_PREFIX = "base_model.model.model.language_model.layers."
BASE = "meta-models/Muse-Glimmer-30B"


def _to_np(path):
    # mlx saves bf16 sometimes; safetensors.numpy can't read bf16, so go through mlx if needed.
    try:
        return load_file(str(path))
    except Exception:
        import mlx.core as mx
        return {k: np.array(v.astype(mx.float32)) for k, v in mx.load(str(path)).items()}


def mlx2peft(src: Path, dst: Path):
    cfg = json.loads((src / "adapter_config.json").read_text())
    lp = cfg["lora_parameters"]
    w = _to_np(src / "adapters.safetensors")
    out, subs, layers = {}, set(), set()
    r = None
    for k, v in w.items():
        m = re.fullmatch(r"model\.layers\.(\d+)\.(.+)\.lora_([ab])", k)
        if not m:
            raise ValueError(f"unexpected MLX adapter key {k}")
        n, sub, ab = m.groups()
        subs.add(sub)
        layers.add(int(n))
        if ab == "a":
            r = v.shape[1]
        out[f"{PEFT_PREFIX}{n}.{sub}.lora_{ab.upper()}.weight"] = np.ascontiguousarray(v.T.astype(np.float32))
    assert r == lp["rank"], (r, lp["rank"])
    # Anchored full-match regex so the vision tower's q/k/v_proj are never wrapped (see cuda-stack.md, PEFT traps).
    sub_re = "|".join(sorted(re.escape(s) for s in subs))
    layer_re = "|".join(str(n) for n in sorted(layers))
    peft_cfg = {
        "peft_type": "LORA", "task_type": "CAUSAL_LM", "base_model_name_or_path": BASE,
        "r": r, "lora_alpha": lp["scale"] * r, "lora_dropout": lp.get("dropout", 0.0), "bias": "none",
        "use_rslora": False, "use_dora": False, "fan_in_fan_out": False, "inference_mode": True,
        "target_modules": rf"model\.language_model\.layers\.({layer_re})\.({sub_re})",
    }
    dst.mkdir(parents=True, exist_ok=True)
    save_file(out, str(dst / "adapter_model.safetensors"))
    (dst / "adapter_config.json").write_text(json.dumps(peft_cfg, indent=2))
    print(f"wrote {len(out)} tensors, r={r}, alpha={peft_cfg['lora_alpha']}, layers {min(layers)}..{max(layers)}, "
          f"modules {sorted(subs)} -> {dst}")


def peft2mlx(src: Path, dst: Path):
    cfg = json.loads((src / "adapter_config.json").read_text())
    if cfg.get("use_rslora"):
        raise ValueError("rsLoRA scaling (alpha/sqrt(r)) not handled")
    w = _to_np(src / "adapter_model.safetensors")
    out, subs, layers = {}, set(), set()
    for k, v in w.items():
        m = re.fullmatch(re.escape(PEFT_PREFIX) + r"(\d+)\.(.+)\.lora_([AB])\.weight", k)
        if not m:
            raise ValueError(f"unexpected PEFT adapter key {k}")
        n, sub, ab = m.groups()
        subs.add(sub)
        layers.add(int(n))
        out[f"{MLX_PREFIX}{n}.{sub}.lora_{ab.lower()}"] = np.ascontiguousarray(v.T.astype(np.float32))
    r = cfg["r"]
    # mlx-lm adapts the LAST num_layers blocks, so the adapted set must be a contiguous tail of the 52 layers.
    n_layers = 52
    if sorted(layers) != list(range(n_layers - len(layers), n_layers)):
        raise ValueError(f"PEFT layers {sorted(layers)} are not a tail block; mlx-lm can't express that")
    mlx_cfg = {"fine_tune_type": "lora", "num_layers": len(layers),
               "lora_parameters": {"rank": r, "scale": cfg["lora_alpha"] / r, "dropout": cfg.get("lora_dropout", 0.0),
                                   "keys": sorted(subs)}}
    dst.mkdir(parents=True, exist_ok=True)
    save_file(out, str(dst / "adapters.safetensors"))
    (dst / "adapter_config.json").write_text(json.dumps(mlx_cfg, indent=2))
    print(f"wrote {len(out)} tensors -> {dst}")


if __name__ == "__main__":
    {"mlx2peft": mlx2peft, "peft2mlx": peft2mlx}[sys.argv[1]](Path(sys.argv[2]), Path(sys.argv[3]))
