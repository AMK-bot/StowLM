# Independent PyTorch bf16 reference at long context (0.1 extension): chunked prefill with a KV cache, logits kept only
# for the scored positions (meta.json "keep"), so a 14k-token context fits in memory. --check-unchunked also runs the
# first prompt in one forward and saves it, to verify the chunking against the plain forward.
#   HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 experiments/grpo-venue/.venv-pt/bin/python pt_reference_long.py OUT [mps]
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import transformers
from transformers import AutoModelForImageTextToText

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identity import write_identity  # noqa: E402

REVISION = "cec94fcfd6512f2cfe708138c32533b346ea4f69"
CHUNK = 2048
out = Path(sys.argv[1])
device = next((a for a in sys.argv[2:] if a in ("mps", "cpu")), "mps")
check = "--check-unchunked" in sys.argv
prompts = json.loads((out / "prompts.json").read_text())
meta = json.loads((out / "meta.json").read_text())["prompts"]
t0 = time.time()
model = AutoModelForImageTextToText.from_pretrained("meta-models/Muse-Glimmer-30B", revision=REVISION,
                                                    dtype=torch.bfloat16, device_map=device, attn_implementation="sdpa")
model.eval()
print("loaded", round(time.time() - t0), "s", flush=True)
timings = {}


def chunked(ids, keep):
    L, cache, parts = len(ids), None, []
    first_kept = L - keep
    for s in range(0, L, CHUNK):
        e = min(L, s + CHUNK)
        need = max(0, e - max(s, first_kept))
        x = torch.tensor([ids[s:e]], device=device)
        o = model(input_ids=x, past_key_values=cache, use_cache=True, logits_to_keep=max(need, 1))
        cache = o.past_key_values
        if need:
            parts.append(o.logits[0, -need:].float().cpu().numpy())
        del o
    return np.concatenate(parts)


with torch.no_grad():
    for name, ids in prompts.items():
        keep = meta[name]["keep"]
        t = time.time()
        logits = chunked(ids, keep)
        path = out / f"pt_bf16__{name}.npy"
        np.save(path, logits)
        write_identity(out, "pt_bf16", name, ids, path,
                       model={"source": "meta-models/Muse-Glimmer-30B", "revision": REVISION, "dtype": "bfloat16",
                              "implementation": "transformers AutoModelForImageTextToText"},
                       settings={"keep_positions": keep, "prefill_chunk": CHUNK, "device": device,
                                 "attn_implementation": "sdpa", "kv_cache": "transformers default"},
                       software={"torch": torch.__version__, "transformers": transformers.__version__})
        timings[name] = {"s": round(time.time() - t, 1),
                         "mps_driver_gb": round(torch.mps.driver_allocated_memory() / 1e9, 1) if device == "mps" else None}
        print(name, len(ids), "tokens", timings[name], flush=True)
        if check and name == next(iter(prompts)):
            full = model(input_ids=torch.tensor([ids], device=device), logits_to_keep=keep).logits[0].float().cpu().numpy()
            np.save(out / f"pt_bf16_unchunked__{name}.npy", full)
            print("chunked vs unchunked max |dlogit|", float(np.abs(full - logits).max()), flush=True)
(out / "pt_timings.json").write_text(json.dumps(timings, indent=1))
