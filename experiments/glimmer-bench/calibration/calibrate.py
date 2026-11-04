"""Compare every MLX variant with the independent PyTorch bf16 reference and derive the conversion tolerance (0.1).

KL(p‖q) = Σ p log(p/q) per position, in nats per token (Kullback & Leibler 1951; Cover & Thomas, *Elements of
Information Theory*, ch. 2): the information lost when the candidate's next-token distribution q stands in for the
reference p. Reference = PyTorch bf16 (transformers' own Glimmer implementation and weight loading), so the check is
independent of mlx-lm's model code and sanitizer — the gap fix-t0-exp left open.

Tolerance rule (calibrated, not invented): a correct 4-bit affine-g64 conversion must sit in the band measured for the
shipped 4-bit model, whose on-disk weights are shown equal to a fresh quantisation of the source; a coarser (3-bit)
quantisation and a layer-mapping bug must fall outside. The threshold is placed at the geometric mean of the q4
band's maximum and the smallest disqualified value, per metric, and is accepted only if it separates them on every
prompt. Usage: uv run python calibrate.py OUT_DIR RESULT.json
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

VARIANTS = ["mlx_bf16", "mlx_q4_fresh", "mlx_q4", "mlx_q3", "mlx_q4_swap"]
TAGS = ["pt_bf16", *VARIANTS]
KEEP = 64


def load_collection(out: Path, prompts: dict) -> tuple[dict, dict]:
    """Every (tag, prompt) array with its identity record, validated before any metric (code review w2-rev-c1):
    present and complete; the array's bytes, shape and prompt token-ID digest match its record; one model
    identity, settings and software per tag across all prompts (no mixed or partial collections); finite,
    2-D, positions = min(prompt length, 64) and one vocabulary size for all tags."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from identity import file_sha256, ids_sha256

    arrays, identities = {}, {}
    for tag in TAGS:
        per_tag = set()
        for name, ids in prompts.items():
            path, ident_path = out / f"{tag}__{name}.npy", out / f"{tag}__{name}.identity.json"
            if not path.is_file() or not ident_path.is_file():
                raise ValueError(f"{tag}/{name}: missing logits or identity record (partial collection)")
            ident = json.loads(ident_path.read_text())
            if ident.get("tag") != tag or ident.get("prompt") != name:
                raise ValueError(f"{tag}/{name}: identity record names another array")
            if ident.get("prompt_ids_sha256") != ids_sha256(ids):
                raise ValueError(f"{tag}/{name}: logits were collected for different prompt token IDs")
            if ident.get("array_sha256") != file_sha256(path):
                raise ValueError(f"{tag}/{name}: array bytes differ from its identity record")
            arr = np.load(path)
            if arr.ndim != 2 or list(arr.shape) != ident.get("shape") or arr.shape[0] != min(len(ids), KEEP):
                raise ValueError(f"{tag}/{name}: malformed array shape {arr.shape}")
            if not np.isfinite(arr).all():
                raise ValueError(f"{tag}/{name}: non-finite logits")
            per_tag.add(json.dumps({k: ident.get(k) for k in ("model", "settings", "software")}, sort_keys=True))
            arrays[(tag, name)] = arr
        if len(per_tag) != 1:
            raise ValueError(f"{tag}: mixed model/settings/software identities across prompts")
        identities[tag] = json.loads(per_tag.pop())
    if len({a.shape[1] for a in arrays.values()}) != 1:
        raise ValueError("vocabulary sizes differ between collections")
    return arrays, identities


def log_softmax(z):
    z = z.astype(np.float64)
    z = z - z.max(-1, keepdims=True)
    return z - np.log(np.exp(z).sum(-1, keepdims=True))


def main():
    out, result_path = Path(sys.argv[1]), Path(sys.argv[2])
    prompts = json.loads((out / "prompts.json").read_text())
    if not prompts:
        raise ValueError("no prompts")
    arrays, identities = load_collection(out, prompts)
    rows = []
    for name, ids in prompts.items():
        ref = arrays[("pt_bf16", name)]
        lp = log_softmax(ref)
        for v in VARIANTS:
            x = arrays[(v, name)]
            if x.shape != ref.shape:
                raise ValueError(f"{v}/{name}: shape {x.shape} != reference {ref.shape}")
            lq = log_softmax(x)
            kl = (np.exp(lp) * (lp - lq)).sum(-1)
            rows.append({"prompt": name, "tokens": len(ids), "positions": int(ref.shape[0]), "variant": v,
                         "mean_kl": float(kl.mean()), "max_kl": float(kl.max()),
                         "top1": float((ref.argmax(-1) == x.argmax(-1)).mean())})
    by = {(r["prompt"], r["variant"]): r for r in rows}
    names = list(prompts)
    q4_vs_fresh = max(float(np.abs(arrays[("mlx_q4", n)] - arrays[("mlx_q4_fresh", n)]).max()) for n in names)
    tolerance = {}
    for metric, worse_is_higher in (("mean_kl", True), ("max_kl", True), ("top1", False)):
        good = [by[(n, "mlx_q4")][metric] for n in names]
        bad = [by[(n, v)][metric] for n in names for v in ("mlx_q3", "mlx_q4_swap")]
        if worse_is_higher:
            edge_good, edge_bad = max(good), min(bad)
            separable = edge_good < edge_bad
            thr = math.sqrt(edge_good * edge_bad) if separable and edge_good > 0 else None
        else:
            edge_good, edge_bad = min(good), max(bad)
            separable = edge_good > edge_bad
            thr = (edge_good + edge_bad) / 2 if separable else None
        tolerance[metric] = {"q4_band_edge": edge_good, "nearest_disqualified": edge_bad, "separable": separable,
                             "threshold": thr, "accept_if": ("<=" if worse_is_higher else ">=")}
    # KL scale differs by prompt (long in-domain prompts sit higher), so one global per-prompt threshold can't
    # separate the bands; per-prompt thresholds and the prompt-averaged KL can. Both are geometric means between the
    # q4 value and the nearest disqualified value (q3 or the layer-swap control).
    per_prompt = {}
    for n in names:
        good, bad = by[(n, "mlx_q4")]["mean_kl"], min(by[(n, v)]["mean_kl"] for v in ("mlx_q3", "mlx_q4_swap"))
        per_prompt[n] = {"q4": good, "nearest_disqualified": bad, "margin": bad / good,
                         "threshold": math.sqrt(good * bad) if bad > good else None}
    avg = {v: float(np.mean([by[(n, v)]["mean_kl"] for n in names])) for v in VARIANTS}
    avg_bad = min(avg["mlx_q3"], avg["mlx_q4_swap"])
    tolerance["per_prompt_mean_kl"] = per_prompt
    tolerance["prompt_averaged_mean_kl"] = {
        "values": avg, "separable": avg_bad > avg["mlx_q4"], "margin": avg_bad / avg["mlx_q4"],
        "threshold": math.sqrt(avg["mlx_q4"] * avg_bad) if avg_bad > avg["mlx_q4"] else None, "accept_if": "<="}
    # Per-prompt separation of q3 alone (a 3-bit model is a weaker but legitimate model; the swap is a bug).
    q3_separable_each = all(by[(n, "mlx_q3")]["mean_kl"] > by[(n, "mlx_q4")]["mean_kl"] for n in names)
    result = {"reference": "PyTorch bf16 (transformers, MPS) — independent implementation",
              "direction": "KL(reference || variant), nats/token, last 64 positions (all if shorter)",
              "rows": rows, "max_abs_logit_diff_q4_vs_fresh_quantisation": q4_vs_fresh,
              "implementation_floor_mean_kl": max(by[(n, "mlx_bf16")]["mean_kl"] for n in names),
              "q3_above_q4_on_every_prompt": q3_separable_each, "tolerance": tolerance,
              "collection": {"prompts": {n: {"ids_sha256": __import__("identity").ids_sha256(ids), "tokens": len(ids)}
                                         for n, ids in prompts.items()}, "identities": identities}}
    if any(not math.isfinite(r[k]) for r in rows for k in ("mean_kl", "max_kl", "top1")):
        raise ValueError("non-finite metric")
    result_path.write_text(json.dumps(result, indent=1, allow_nan=False))  # strict JSON: no NaN/Infinity
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=1))
    for n in names:
        print(n, {v: round(by[(n, v)]["mean_kl"], 4) for v in VARIANTS},
              {v: round(by[(n, v)]["top1"], 3) for v in VARIANTS})


if __name__ == "__main__":
    main()
