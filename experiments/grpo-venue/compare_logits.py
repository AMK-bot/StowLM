# Compare the adapter's effect across stacks: delta = logits(base + adapter) - logits(base), in MLX 4-bit vs
# PyTorch bf16 + PEFT. If the conversion is right, the two deltas agree up to the base-quantisation difference.
# Also reports the base-vs-base shift (MLX q4 vs bf16) so the two error sources can be told apart.
# Usage: uv run python experiments/grpo-venue/compare_logits.py OUT_DIR [MLX_BASE_TAG MLX_ADAPTER_TAG ...]
import json
import sys
from pathlib import Path

import numpy as np

out = Path(sys.argv[1])
pairs = sys.argv[2:] or ["mlx_q4_base", "mlx_q4_adapter"]


def logsoftmax(z):
    z = z - z.max(-1, keepdims=True)
    return z - np.log(np.exp(z).sum(-1, keepdims=True))


def kl(lp, lq):  # KL(p || q) per position, nats
    return (np.exp(lp) * (lp - lq)).sum(-1)


rows = []
prompts = json.loads((out / "prompts.json").read_text())
for i in range(0, len(pairs), 2):
    mb, ma = pairs[i], pairs[i + 1]
    for name in prompts:
        f = lambda t: np.load(out / f"{t}__{name}.npy").astype(np.float64)
        pb, pa, xb, xa = f("pt_bf16_base"), f("pt_bf16_adapter"), f(mb), f(ma)
        dp, dx = pa - pb, xa - xb
        lpb, lpa, lxb, lxa = map(logsoftmax, (pb, pa, xb, xa))
        base_kl = float(kl(lpb, lxb).mean())
        adapted_kl = float(kl(lpa, lxa).mean())
        rows.append(dict(
            mlx=ma, prompt=name,
            delta_cos=float((dp * dx).sum() / np.linalg.norm(dp) / np.linalg.norm(dx)),
            delta_rel_err=float(np.linalg.norm(dx - dp) / np.linalg.norm(dp)),
            delta_rms_pt=float(np.sqrt((dp ** 2).mean())),
            kl_adapter_effect_pt=float(kl(lpa, lpb).mean()),       # how much the adapter changes the model
            kl_direction="PyTorch bf16 || MLX (precision identified by mlx tag)",
            kl_base_bf16_to_mlx=base_kl,
            kl_adapted_bf16_to_mlx=adapted_kl,
            # Compatibility aliases: historical names were ambiguous and incorrectly labelled in README.
            # Both contain KL(PyTorch bf16 || MLX), including when MLX is bf16 rather than q4.
            kl_base_q4_vs_bf16=base_kl,
            kl_adapted_q4_vs_bf16=adapted_kl,
            top1_agree_adapted=float((pa.argmax(-1) == xa.argmax(-1)).mean()),
            top1_agree_base=float((pb.argmax(-1) == xb.argmax(-1)).mean()),
        ))
for r in rows:
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}))
(out / "compare.json").write_text(json.dumps(rows, indent=1))
