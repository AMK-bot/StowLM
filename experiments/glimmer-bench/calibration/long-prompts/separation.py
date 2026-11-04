"""Does the conversion threshold still separate the known-bad variants at full-ship length? (w2-rev-calib gap 1)

Scores the 3-bit model and the layer-swapped 4-bit control on the ~7.3k prompt (measure_full_6k), on exactly the
positions the 4-bit run was scored on, against the same PyTorch bf16 reference; identity checks as in analyze.py.

    uv run python experiments/glimmer-bench/calibration/long-prompts/separation.py OUT separation.json
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze  # noqa: E402

PROMPT = "measure_full_6k"
VARIANTS = ("mlx_bf16", "mlx_q4", "mlx_q3", "mlx_q4_swap")


def main():
    out, result_path = Path(sys.argv[1]), Path(sys.argv[2])
    prompts = {PROMPT: json.loads((out / "prompts.json").read_text())[PROMPT]}
    meta = json.loads((out / "meta.json").read_text())["prompts"]
    analyze.TAGS = ("pt_bf16", *VARIANTS)
    arrays, identities = analyze.load(out, prompts, meta)
    m = meta[PROMPT]
    L, keep = m["total_tokens"], m["keep"]
    prompt_tail = np.arange(L - keep, L) < m["prompt_tokens"] - 1
    ref = arrays[("pt_bf16", PROMPT)]
    lp = analyze.log_softmax(ref)
    rows = {}
    for tag in VARIANTS:
        x = arrays[(tag, PROMPT)]
        lq = analyze.log_softmax(x)
        kl = (np.exp(lp) * (lp - lq)).sum(-1)
        top1 = ref.argmax(-1) == x.argmax(-1)
        rows[tag] = {part: analyze.stats(kl, top1, mask) for part, mask in
                     (("all", np.ones_like(prompt_tail)), ("prompt_tail", prompt_tail), ("continuation", ~prompt_tail))}
    thr = analyze.THRESHOLD
    sep = {}
    for part in ("all", "prompt_tail", "continuation"):
        good = rows["mlx_q4"][part]["mean_kl"]
        bad = min(rows[t][part]["mean_kl"] for t in ("mlx_q3", "mlx_q4_swap"))
        sep[part] = {"q4": good, "nearest_bad": bad, "margin": bad / good,
                     "fixed_threshold_separates": good <= thr < bad,
                     "length_fitted_threshold": math.sqrt(good * bad) if bad > good else None}
    result = {"prompt": PROMPT, "tokens": {k: m[k] for k in ("prompt_tokens", "continuation_tokens", "keep")},
              "threshold": thr, "rows": rows, "separation": sep, "identities": identities}
    result_path.write_text(json.dumps(result, indent=1, allow_nan=False))
    for tag in VARIANTS:
        print(tag, {p: (round(rows[tag][p]["mean_kl"], 4), round(rows[tag][p]["top1"], 3)) for p in rows[tag]})
    print(json.dumps(sep, indent=1))


if __name__ == "__main__":
    main()
