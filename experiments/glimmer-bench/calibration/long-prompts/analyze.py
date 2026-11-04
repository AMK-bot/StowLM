"""Long-prompt calibration analysis (0.1 extension): KL(PyTorch bf16 || MLX variant) per scored position, split into
the prompt tail and the answer continuation, against the unchanged 0.1376 nats/token threshold (calibration.json).

Identity checks as in calibrate.py: every array has a record naming it, for these prompt token IDs, with matching bytes
and shape (scored positions x vocabulary), finite values, and one model/settings/software identity per tag.

    uv run python experiments/glimmer-bench/calibration/long-prompts/analyze.py OUT RESULT.json
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from identity import file_sha256, ids_sha256  # noqa: E402

TAGS = ("pt_bf16", "mlx_bf16", "mlx_q4")
THRESHOLD = json.loads((HERE.parent / "calibration.json").read_text())["tolerance"]["prompt_averaged_mean_kl"]["threshold"]


def load(out: Path, prompts: dict, meta: dict) -> tuple[dict, dict]:
    arrays, identities = {}, {}
    for tag in TAGS:
        seen = set()
        for name, ids in prompts.items():
            path, ident_path = out / f"{tag}__{name}.npy", out / f"{tag}__{name}.identity.json"
            if not path.is_file() or not ident_path.is_file():
                raise ValueError(f"{tag}/{name}: missing logits or identity record")
            ident = json.loads(ident_path.read_text())
            if (ident.get("tag"), ident.get("prompt")) != (tag, name):
                raise ValueError(f"{tag}/{name}: identity names another array")
            if ident.get("prompt_ids_sha256") != ids_sha256(ids):
                raise ValueError(f"{tag}/{name}: different prompt token IDs")
            if ident.get("array_sha256") != file_sha256(path):
                raise ValueError(f"{tag}/{name}: array bytes differ from the identity record")
            arr = np.load(path)
            if arr.ndim != 2 or arr.shape[0] != meta[name]["keep"] or list(arr.shape) != ident.get("shape"):
                raise ValueError(f"{tag}/{name}: malformed shape {arr.shape}")
            if not np.isfinite(arr).all():
                raise ValueError(f"{tag}/{name}: non-finite logits")
            seen.add(json.dumps({k: ident.get(k) for k in ("model", "software")}, sort_keys=True))
            arrays[(tag, name)] = arr
        if len(seen) != 1:
            raise ValueError(f"{tag}: mixed model/software identities across prompts")
        identities[tag] = json.loads(seen.pop())
    return arrays, identities


def log_softmax(z):
    z = z.astype(np.float64)
    z = z - z.max(-1, keepdims=True)
    return z - np.log(np.exp(z).sum(-1, keepdims=True))


def stats(kl, top1, mask):
    k = kl[mask]
    return {"positions": int(mask.sum()), "mean_kl": float(k.mean()), "max_kl": float(k.max()),
            "p95_kl": float(np.percentile(k, 95)), "positions_over_threshold": int((k > THRESHOLD).sum()),
            "share_over_threshold": float((k > THRESHOLD).mean()), "top1": float(top1[mask].mean())}


def main():
    out, result_path = Path(sys.argv[1]), Path(sys.argv[2])
    prompts = json.loads((out / "prompts.json").read_text())
    meta = json.loads((out / "meta.json").read_text())["prompts"]
    arrays, identities = load(out, prompts, meta)
    rows = []
    for name, ids in prompts.items():
        m = meta[name]
        ref = arrays[("pt_bf16", name)]
        lp = log_softmax(ref)
        # kept position i is sequence position L - keep + i and predicts token i+1 of it; positions before the last
        # prompt token predict prompt tokens (prompt tail), the rest predict continuation (action) tokens
        L, keep = m["total_tokens"], m["keep"]
        pos = np.arange(L - keep, L)
        prompt_tail = pos < m["prompt_tokens"] - 1
        for tag in ("mlx_bf16", "mlx_q4"):
            x = arrays[(tag, name)]
            lq = log_softmax(x)
            kl = (np.exp(lp) * (lp - lq)).sum(-1)
            top1 = ref.argmax(-1) == x.argmax(-1)
            rows.append({"prompt": name, "variant": tag, "prompt_tokens": m["prompt_tokens"],
                         "continuation_tokens": m["continuation_tokens"], "total_tokens": L,
                         "all": stats(kl, top1, np.ones_like(prompt_tail)),
                         "prompt_tail": stats(kl, top1, prompt_tail), "continuation": stats(kl, top1, ~prompt_tail)})
    q4 = [r for r in rows if r["variant"] == "mlx_q4"]
    floor = [r for r in rows if r["variant"] == "mlx_bf16"]
    averaged = float(np.mean([r["all"]["mean_kl"] for r in q4]))
    result = {"reference": "PyTorch bf16 (transformers, MPS), chunked prefill with KV cache (2,048-token chunks)",
              "threshold_mean_kl": THRESHOLD, "threshold_source": "calibration.json (6 prompts <= 573 tokens), unchanged",
              "prompt_averaged_mean_kl_q4": averaged, "passes_primary_threshold": averaged <= THRESHOLD,
              "per_length_pass": {r["prompt"]: r["all"]["mean_kl"] <= THRESHOLD for r in q4},
              "implementation_floor_max_mean_kl": max(r["all"]["mean_kl"] for r in floor),
              "rows": rows, "collection": {"meta": meta, "identities": identities}}
    if any(not math.isfinite(v) for r in rows for part in ("all", "prompt_tail", "continuation")
           for v in (r[part]["mean_kl"], r[part]["max_kl"])):
        raise ValueError("non-finite metric")
    result_path.write_text(json.dumps(result, indent=1, allow_nan=False))
    for r in rows:
        print(r["prompt"], r["variant"], {p: {k: round(v, 4) if isinstance(v, float) else v
                                              for k, v in r[p].items()} for p in ("all", "prompt_tail", "continuation")})
    print("prompt-averaged q4 mean KL", round(averaged, 4), "threshold", round(THRESHOLD, 4))


if __name__ == "__main__":
    main()
